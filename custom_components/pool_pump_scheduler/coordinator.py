"""Update coordinator for the Pool Pump Scheduler."""
from __future__ import annotations

from datetime import datetime, time, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_RAW_TODAY,
    ATTR_RAW_TOMORROW,
    ATTR_TOMORROW_VALID,
    CONF_CONTROL_SWITCH,
    CONF_INVERTER_POWER_SENSOR,
    CONF_MAX_PRICE,
    CONF_MIN_BLOCK_MINUTES,
    CONF_PRICE_SENSOR,
    CONF_PUMP_POWER_W,
    CONF_PUMP_SWITCH,
    CONF_RECALC_TIME,
    CONF_RUNTIME_HOURS,
    CONF_SOLAR_CONSUMPTION_SENSOR,
    CONF_SOLAR_PRODUCTION_SENSOR,
    CONF_SURPLUS_HYSTERESIS_SECONDS,
    CONF_USE_MAX_PRICE,
    CONF_USE_SOLAR_SURPLUS,
    DEFAULT_CONTROL_SWITCH,
    DEFAULT_MAX_PRICE,
    DEFAULT_MIN_BLOCK_MINUTES,
    DEFAULT_PUMP_POWER_W,
    DEFAULT_RECALC_TIME,
    DEFAULT_RUNTIME_HOURS,
    DEFAULT_SURPLUS_HYSTERESIS_SECONDS,
    DEFAULT_USE_MAX_PRICE,
    DEFAULT_USE_SOLAR_SURPLUS,
    DOMAIN,
    SIGNAL_SCHEDULE_UPDATED,
    SLOT_MINUTES,
    STORAGE_VERSION,
)
from .scheduler import PriceSlot, ScheduleResult, compute_schedule
from .solar import SolarSurplusTracker

_LOGGER = logging.getLogger(__name__)


class PoolPumpCoordinator:
    """Coordinates schedule computation and pump control.

    The coordinator keeps two independent schedules in memory — one for
    the remainder of today and one for tomorrow — so that a mid-day
    Home Assistant restart (or an integration reload after tomorrow's
    prices have published) doesn't lose today's plan. Each schedule is
    recomputed on its own trigger and `is_active_now()` checks both.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self.today_schedule: ScheduleResult | None = None
        self.tomorrow_schedule: ScheduleResult | None = None
        self.today_last_calculated: datetime | None = None
        self.tomorrow_last_calculated: datetime | None = None
        self._unsubs: list = []
        self._store: Store = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._solar_tracker: SolarSurplusTracker | None = None
        # Running cost accumulators. cost_today is sampled per quarter-hour
        # and reset at the first slot boundary of each local day; cost_total
        # never resets. Solar-driven slots add 0 (only grid runtime counts).
        self.cost_today: float = 0.0
        self.cost_total: float = 0.0
        self.cost_today_last_reset: datetime | None = None
        self._current_slot_state: dict | None = None

    @property
    def price_sensor(self) -> str:
        return self.entry.options.get(
            CONF_PRICE_SENSOR, self.entry.data[CONF_PRICE_SENSOR]
        )

    @property
    def pump_switch(self) -> str:
        return self.entry.options.get(
            CONF_PUMP_SWITCH, self.entry.data[CONF_PUMP_SWITCH]
        )

    @property
    def runtime_hours(self) -> float:
        return float(self.entry.options.get(
            CONF_RUNTIME_HOURS,
            self.entry.data.get(CONF_RUNTIME_HOURS, DEFAULT_RUNTIME_HOURS),
        ))

    @property
    def min_block_minutes(self) -> int:
        return int(self.entry.options.get(
            CONF_MIN_BLOCK_MINUTES,
            self.entry.data.get(CONF_MIN_BLOCK_MINUTES, DEFAULT_MIN_BLOCK_MINUTES),
        ))

    @property
    def recalc_time(self) -> str:
        return self.entry.options.get(
            CONF_RECALC_TIME,
            self.entry.data.get(CONF_RECALC_TIME, DEFAULT_RECALC_TIME),
        )

    @property
    def control_switch(self) -> bool:
        return bool(self.entry.options.get(
            CONF_CONTROL_SWITCH,
            self.entry.data.get(CONF_CONTROL_SWITCH, DEFAULT_CONTROL_SWITCH),
        ))

    @property
    def use_max_price(self) -> bool:
        return bool(self.entry.options.get(
            CONF_USE_MAX_PRICE,
            self.entry.data.get(CONF_USE_MAX_PRICE, DEFAULT_USE_MAX_PRICE),
        ))

    @property
    def max_price(self) -> float | None:
        if not self.use_max_price:
            return None
        return float(self.entry.options.get(
            CONF_MAX_PRICE,
            self.entry.data.get(CONF_MAX_PRICE, DEFAULT_MAX_PRICE),
        ))

    @property
    def use_solar_surplus(self) -> bool:
        return bool(self.entry.options.get(
            CONF_USE_SOLAR_SURPLUS,
            self.entry.data.get(CONF_USE_SOLAR_SURPLUS, DEFAULT_USE_SOLAR_SURPLUS),
        ))

    @property
    def solar_production_sensor(self) -> str | None:
        val = self.entry.options.get(
            CONF_SOLAR_PRODUCTION_SENSOR,
            self.entry.data.get(CONF_SOLAR_PRODUCTION_SENSOR),
        )
        return val or None

    @property
    def solar_consumption_sensor(self) -> str | None:
        val = self.entry.options.get(
            CONF_SOLAR_CONSUMPTION_SENSOR,
            self.entry.data.get(CONF_SOLAR_CONSUMPTION_SENSOR),
        )
        return val or None

    @property
    def pump_power_w(self) -> float:
        return float(self.entry.options.get(
            CONF_PUMP_POWER_W,
            self.entry.data.get(CONF_PUMP_POWER_W, DEFAULT_PUMP_POWER_W),
        ))

    @property
    def surplus_hysteresis_seconds(self) -> int:
        return int(self.entry.options.get(
            CONF_SURPLUS_HYSTERESIS_SECONDS,
            self.entry.data.get(
                CONF_SURPLUS_HYSTERESIS_SECONDS,
                DEFAULT_SURPLUS_HYSTERESIS_SECONDS,
            ),
        ))

    @property
    def solar_active(self) -> bool:
        """Whether the solar overlay is currently forcing the pump on."""
        return self._solar_tracker is not None and self._solar_tracker.active

    @property
    def inverter_power_sensor(self) -> str | None:
        """Optional secondary-load watts sensor (e.g. pool heat-pump inverter)."""
        val = self.entry.options.get(
            CONF_INVERTER_POWER_SENSOR,
            self.entry.data.get(CONF_INVERTER_POWER_SENSOR),
        )
        return val or None

    def _read_inverter_power_w(self) -> float:
        """Current inverter draw in watts; 0 if unset/unavailable."""
        sensor = self.inverter_power_sensor
        if not sensor:
            return 0.0
        val = self._read_power(sensor)
        return val if val is not None else 0.0

    @property
    def schedule(self) -> ScheduleResult | None:
        """Synthetic combined schedule across today and tomorrow.

        Returned as a single `ScheduleResult` so the entity classes can
        keep treating "the schedule" as one object. Blocks from today
        come first (they're earlier in time); totals are summed.
        """
        parts = [
            s for s in (self.today_schedule, self.tomorrow_schedule)
            if s is not None
        ]
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        return ScheduleResult(
            selected_starts=[s for p in parts for s in p.selected_starts],
            total_cost=sum(p.total_cost for p in parts),
            block_count=sum(p.block_count for p in parts),
            total_slots=sum(p.total_slots for p in parts),
            blocks=[b for p in parts for b in p.blocks],
        )

    @property
    def last_calculated(self) -> datetime | None:
        """Most recent of the two per-schedule timestamps."""
        times = [
            t for t in (self.today_last_calculated, self.tomorrow_last_calculated)
            if t is not None
        ]
        return max(times) if times else None

    async def async_setup(self) -> None:
        """Schedule periodic tasks and listeners."""
        # Restore persisted timestamps so the diagnostic sensors look
        # right immediately on restart, even before the first recalc.
        await self._async_load_store()

        # Schedule recalculation at the configured time each day.
        try:
            h, m, *_ = self.recalc_time.split(":")
            recalc_t = time(int(h), int(m))
        except (ValueError, AttributeError):
            recalc_t = time(14, 0)
            _LOGGER.warning("Invalid recalc_time, defaulting to 14:00")

        self._unsubs.append(
            async_track_time_change(
                self.hass,
                self._handle_recalc_time,
                hour=recalc_t.hour,
                minute=recalc_t.minute,
                second=0,
            )
        )

        # Re-evaluate pump state every quarter hour at slot boundaries.
        for minute in (0, 15, 30, 45):
            self._unsubs.append(
                async_track_time_change(
                    self.hass,
                    self._handle_slot_boundary,
                    minute=minute,
                    second=5,  # 5s after boundary for slack
                )
            )

        # React to changes in the price sensor (e.g. tomorrow becomes valid).
        self._unsubs.append(
            async_track_state_change_event(
                self.hass,
                [self.price_sensor],
                self._handle_price_change,
            )
        )

        # Solar surplus overlay: listen on both sensors and also poll at
        # a low frequency so the hysteresis timer advances even when no
        # state-change events fire.
        if (
            self.use_solar_surplus
            and self.solar_production_sensor
            and self.solar_consumption_sensor
        ):
            self._solar_tracker = SolarSurplusTracker(
                pump_power_w=self.pump_power_w,
                hysteresis_seconds=self.surplus_hysteresis_seconds,
            )
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass,
                    [
                        self.solar_production_sensor,
                        self.solar_consumption_sensor,
                    ],
                    self._handle_solar_change,
                )
            )
            self._unsubs.append(
                async_track_time_interval(
                    self.hass,
                    self._handle_solar_tick,
                    timedelta(seconds=30),
                )
            )
            self._update_solar_tracker(dt_util.now())

        # Initial computation — compute today and tomorrow so the pump
        # resumes correctly after a restart.
        await self.async_recalculate()

    async def async_unload(self) -> None:
        """Clean up listeners."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _handle_recalc_time(self, now: datetime) -> None:
        """Triggered at the daily recalc time."""
        self.hass.async_create_task(self.async_recalculate())

    @callback
    def _handle_slot_boundary(self, now: datetime) -> None:
        """Triggered at every 15-minute boundary.

        Two responsibilities in order:
        1. Close out the slot that just ended — if the pump ran on the
           grid during it, add to the daily and lifetime cost counters.
        2. Apply the schedule for the new slot.
        """
        if self._current_slot_state is not None:
            self._account_slot(self._current_slot_state)

        slot_start = self._snap_to_quarter(now)
        self._maybe_reset_today(slot_start)

        self._current_slot_state = {
            "start": slot_start,
            "grid_driven": (
                self._schedule_active_at(slot_start) and not self.solar_active
            ),
        }

        async_dispatcher_send(self.hass, SIGNAL_SCHEDULE_UPDATED)
        self.hass.async_create_task(self._apply_schedule())

    @staticmethod
    def _snap_to_quarter(dt: datetime) -> datetime:
        """Floor a datetime to the start of its 15-minute slot."""
        return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)

    def _maybe_reset_today(self, slot_start: datetime) -> None:
        """Reset the daily counter when the slot belongs to a new local day."""
        today = slot_start.date()
        if (
            self.cost_today_last_reset is None
            or self.cost_today_last_reset.date() != today
        ):
            self.cost_today = 0.0
            self.cost_today_last_reset = dt_util.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            )

    def _schedule_active_at(self, when: datetime) -> bool:
        if self.today_schedule is not None and self.today_schedule.is_active(when):
            return True
        if (
            self.tomorrow_schedule is not None
            and self.tomorrow_schedule.is_active(when)
        ):
            return True
        return False

    def _slot_price(self, slot_start: datetime) -> float | None:
        """Look up the Nord Pool price for a given slot start time."""
        state = self.hass.states.get(self.price_sensor)
        if state is None:
            return None
        for attr in (ATTR_RAW_TODAY, ATTR_RAW_TOMORROW):
            for entry in state.attributes.get(attr, []) or []:
                try:
                    start = entry["start"]
                    if isinstance(start, str):
                        start = dt_util.parse_datetime(start)
                    if start == slot_start:
                        return float(entry["value"])
                except (KeyError, TypeError, ValueError):
                    continue
        return None

    def _account_slot(self, slot_state: dict) -> None:
        """Add cost for the slot that just ended, if it was grid-driven.

        Total load = pump_power_w + inverter sensor's current reading (W),
        sampled at slot end. Solar-covered slots return early via the
        `grid_driven` flag and contribute zero.
        """
        if not slot_state.get("grid_driven"):
            return
        slot_start: datetime = slot_state["start"]
        price = self._slot_price(slot_start)
        if price is None:
            _LOGGER.debug(
                "Cost accounting: no price found for slot %s", slot_start
            )
            return
        inverter_w = self._read_inverter_power_w()
        load_kw = (self.pump_power_w + inverter_w) / 1000.0
        cost = price * load_kw * 0.25
        self.cost_today += cost
        self.cost_total += cost
        _LOGGER.debug(
            "Slot %s: price=%.4f pump_kw=%.3f inverter_w=%.0f "
            "cost+=%.4f today=%.4f total=%.4f",
            slot_start,
            price,
            self.pump_power_w / 1000.0,
            inverter_w,
            cost,
            self.cost_today,
            self.cost_total,
        )
        self.hass.async_create_task(self._async_save_store())

    @callback
    def _handle_price_change(self, event) -> None:
        """Triggered when the price sensor state changes."""
        # Recompute lazily; sensor often updates many times.
        self.hass.async_create_task(self.async_recalculate())

    @callback
    def _handle_solar_change(self, event) -> None:
        """Triggered when a solar production/consumption sensor changes."""
        self._update_solar_tracker(dt_util.now())

    @callback
    def _handle_solar_tick(self, now: datetime) -> None:
        """Periodic tick so hysteresis advances even without sensor events."""
        self._update_solar_tracker(now)

    def _read_power(self, entity_id: str | None) -> float | None:
        """Read a power sensor as a float, treating unavailable as None."""
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (
            "unknown", "unavailable", "none", "",
        ):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _update_solar_tracker(self, now: datetime) -> None:
        """Feed the tracker with the latest surplus reading.

        If the active state flips, fire the schedule signal and apply
        immediately so the pump reacts within one tick.
        """
        if self._solar_tracker is None:
            return
        prod = self._read_power(self.solar_production_sensor)
        cons = self._read_power(self.solar_consumption_sensor)
        surplus = (
            prod - cons if (prod is not None and cons is not None) else None
        )
        was_active = self._solar_tracker.active
        is_active = self._solar_tracker.update(now, surplus)
        if is_active != was_active:
            _LOGGER.info(
                "Solar surplus active=%s (surplus=%s W, threshold=%s W)",
                is_active,
                surplus,
                self.pump_power_w,
            )
            async_dispatcher_send(self.hass, SIGNAL_SCHEDULE_UPDATED)
            self.hass.async_create_task(self._apply_schedule())

    def _parse_slots(self, raw: list) -> list[PriceSlot]:
        """Parse the raw_today / raw_tomorrow attribute into PriceSlots."""
        out: list[PriceSlot] = []
        if not raw:
            return out
        for entry in raw:
            try:
                start = entry["start"]
                end = entry["end"]
                value = entry["value"]
                if isinstance(start, str):
                    start = dt_util.parse_datetime(start)
                if isinstance(end, str):
                    end = dt_util.parse_datetime(end)
                if start is None or end is None or value is None:
                    continue
                out.append(PriceSlot(start=start, end=end, value=float(value)))
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.debug("Skipping malformed price entry %s: %s", entry, err)
        return out

    async def async_recalculate(self) -> None:
        """Recompute today's and tomorrow's schedules from current price data."""
        state = self.hass.states.get(self.price_sensor)
        if state is None:
            _LOGGER.warning("Price sensor %s not found", self.price_sensor)
            return

        raw_today = state.attributes.get(ATTR_RAW_TODAY) or []
        raw_tomorrow = state.attributes.get(ATTR_RAW_TOMORROW) or []
        tomorrow_valid = state.attributes.get(ATTR_TOMORROW_VALID, False)

        today_slots = self._parse_slots(raw_today)
        tomorrow_slots = (
            self._parse_slots(raw_tomorrow) if tomorrow_valid else []
        )

        now = dt_util.now()

        changed_today = self._compute_today(today_slots, now)
        changed_tomorrow = self._compute_tomorrow(tomorrow_slots, now)

        if changed_today or changed_tomorrow:
            await self._async_save_store()
            async_dispatcher_send(self.hass, SIGNAL_SCHEDULE_UPDATED)
            await self._apply_schedule()

    def _compute_today(
        self, today_slots: list[PriceSlot], now: datetime
    ) -> bool:
        """Plan the remainder of today. Returns True iff state changed.

        Runtime is pro-rated by the fraction of the day still available,
        so a mid-day install or restart doesn't try to cram a full day's
        worth of hours into a few remaining slots.
        """
        future = [s for s in today_slots if s.end > now]
        if not future:
            return self._clear_today()

        hours_remaining = sum(
            (s.end - s.start).total_seconds() for s in future
        ) / 3600.0
        # Pro-rata: keep the full daily runtime if a full day is still
        # available, otherwise scale down so the DP target is feasible.
        runtime = self.runtime_hours * min(1.0, hours_remaining / 24.0)
        if runtime <= 0:
            return self._clear_today()

        result = compute_schedule(
            prices=future,
            runtime_hours=runtime,
            min_block_minutes=self.min_block_minutes,
            slot_minutes=SLOT_MINUTES,
            max_price=self.max_price,
        )
        if result is None:
            _LOGGER.warning(
                "Today schedule computation failed "
                "(runtime=%.2fh, min_block=%d, max_price=%s)",
                runtime,
                self.min_block_minutes,
                self.max_price,
            )
            return False

        self.today_schedule = result
        self.today_last_calculated = now
        _LOGGER.info(
            "Today schedule: %d blocks, %.2f h, cost %.3f",
            result.block_count,
            result.total_slots * SLOT_MINUTES / 60.0,
            result.total_cost,
        )
        return True

    def _compute_tomorrow(
        self, tomorrow_slots: list[PriceSlot], now: datetime
    ) -> bool:
        """Plan tomorrow. Returns True iff state changed."""
        if not tomorrow_slots:
            return self._clear_tomorrow()

        result = compute_schedule(
            prices=tomorrow_slots,
            runtime_hours=self.runtime_hours,
            min_block_minutes=self.min_block_minutes,
            slot_minutes=SLOT_MINUTES,
            max_price=self.max_price,
        )
        if result is None:
            _LOGGER.warning(
                "Tomorrow schedule computation failed "
                "(runtime=%.2fh, min_block=%d, max_price=%s)",
                self.runtime_hours,
                self.min_block_minutes,
                self.max_price,
            )
            return False

        self.tomorrow_schedule = result
        self.tomorrow_last_calculated = now
        _LOGGER.info(
            "Tomorrow schedule: %d blocks, %.2f h, cost %.3f",
            result.block_count,
            result.total_slots * SLOT_MINUTES / 60.0,
            result.total_cost,
        )
        return True

    def _clear_today(self) -> bool:
        """Clear today's schedule. Returns True iff something changed."""
        if self.today_schedule is None and self.today_last_calculated is None:
            return False
        self.today_schedule = None
        self.today_last_calculated = None
        return True

    def _clear_tomorrow(self) -> bool:
        """Clear tomorrow's schedule. Returns True iff something changed."""
        if (
            self.tomorrow_schedule is None
            and self.tomorrow_last_calculated is None
        ):
            return False
        self.tomorrow_schedule = None
        self.tomorrow_last_calculated = None
        return True

    def is_active_now(self) -> bool:
        """Return whether the pump should be on right now.

        Pump runs when EITHER the price-based schedule says so OR the
        solar surplus overlay is active. Today's and tomorrow's
        schedules are both checked so the midnight boundary is handled
        naturally.
        """
        if self.solar_active:
            return True
        now = dt_util.now()
        if self.today_schedule is not None and self.today_schedule.is_active(now):
            return True
        if (
            self.tomorrow_schedule is not None
            and self.tomorrow_schedule.is_active(now)
        ):
            return True
        return False

    async def _apply_schedule(self) -> None:
        """Turn the pump switch on or off according to the schedule."""
        if not self.control_switch:
            return

        should_be_on = self.is_active_now()
        switch_state = self.hass.states.get(self.pump_switch)
        if switch_state is None:
            _LOGGER.warning("Pump switch %s not found", self.pump_switch)
            return

        currently_on = switch_state.state == "on"
        if should_be_on and not currently_on:
            _LOGGER.info("Turning pump ON (%s)", self.pump_switch)
            await self.hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": self.pump_switch},
                blocking=False,
            )
        elif not should_be_on and currently_on:
            _LOGGER.info("Turning pump OFF (%s)", self.pump_switch)
            await self.hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": self.pump_switch},
                blocking=False,
            )

    def next_change(self) -> datetime | None:
        """Return the time of the next ON or OFF transition, if any."""
        now = dt_util.now()
        boundaries: list[datetime] = []
        for sched in (self.today_schedule, self.tomorrow_schedule):
            if sched is None:
                continue
            for start, end in sched.blocks:
                if start > now:
                    boundaries.append(start)
                if end > now:
                    boundaries.append(end)
        if not boundaries:
            return None
        return min(boundaries)

    async def _async_load_store(self) -> None:
        """Restore persisted timestamps and cost counters from disk."""
        try:
            data = await self._store.async_load()
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.warning("Failed to load persisted state: %s", err)
            return
        if not data:
            return
        today_iso = data.get("today_last_calculated")
        tomorrow_iso = data.get("tomorrow_last_calculated")
        if today_iso:
            self.today_last_calculated = dt_util.parse_datetime(today_iso)
        if tomorrow_iso:
            self.tomorrow_last_calculated = dt_util.parse_datetime(tomorrow_iso)
        self.cost_today = float(data.get("cost_today", 0.0) or 0.0)
        self.cost_total = float(data.get("cost_total", 0.0) or 0.0)
        reset_iso = data.get("cost_today_last_reset")
        if reset_iso:
            self.cost_today_last_reset = dt_util.parse_datetime(reset_iso)

    async def _async_save_store(self) -> None:
        """Persist timestamps and cost counters so they survive restart."""
        try:
            await self._store.async_save(
                {
                    "today_last_calculated": (
                        self.today_last_calculated.isoformat()
                        if self.today_last_calculated
                        else None
                    ),
                    "tomorrow_last_calculated": (
                        self.tomorrow_last_calculated.isoformat()
                        if self.tomorrow_last_calculated
                        else None
                    ),
                    "cost_today": self.cost_today,
                    "cost_total": self.cost_total,
                    "cost_today_last_reset": (
                        self.cost_today_last_reset.isoformat()
                        if self.cost_today_last_reset
                        else None
                    ),
                }
            )
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.warning("Failed to persist state: %s", err)
