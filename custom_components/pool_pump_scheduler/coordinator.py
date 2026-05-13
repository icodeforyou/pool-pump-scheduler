"""Update coordinator for the Pool Pump Scheduler."""
from __future__ import annotations

from datetime import datetime, time
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_RAW_TODAY,
    ATTR_RAW_TOMORROW,
    ATTR_TOMORROW_VALID,
    CONF_CONTROL_SWITCH,
    CONF_MAX_PRICE,
    CONF_MIN_BLOCK_MINUTES,
    CONF_PRICE_SENSOR,
    CONF_PUMP_SWITCH,
    CONF_RECALC_TIME,
    CONF_RUNTIME_HOURS,
    CONF_USE_MAX_PRICE,
    DEFAULT_CONTROL_SWITCH,
    DEFAULT_MAX_PRICE,
    DEFAULT_MIN_BLOCK_MINUTES,
    DEFAULT_RECALC_TIME,
    DEFAULT_RUNTIME_HOURS,
    DEFAULT_USE_MAX_PRICE,
    DOMAIN,
    SIGNAL_SCHEDULE_UPDATED,
    SLOT_MINUTES,
    STORAGE_VERSION,
)
from .scheduler import PriceSlot, ScheduleResult, compute_schedule

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
        """Triggered at every 15-minute boundary to apply schedule."""
        self.hass.async_create_task(self._apply_schedule())

    @callback
    def _handle_price_change(self, event) -> None:
        """Triggered when the price sensor state changes."""
        # Recompute lazily; sensor often updates many times.
        self.hass.async_create_task(self.async_recalculate())

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

        Checks both today's and tomorrow's schedules so the boundary at
        midnight is handled naturally — at 23:59 today's last block may
        still be active, and at 00:01 tomorrow's first block (with a
        start time on the new calendar day) takes over.
        """
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
        """Restore persisted timestamps from disk."""
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

    async def _async_save_store(self) -> None:
        """Persist timestamps so diagnostic sensors survive a restart."""
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
                }
            )
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.warning("Failed to persist state: %s", err)
