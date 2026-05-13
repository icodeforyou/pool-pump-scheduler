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
)
from .scheduler import PriceSlot, ScheduleResult, compute_schedule

_LOGGER = logging.getLogger(__name__)


class PoolPumpCoordinator:
    """Coordinates schedule computation and pump control."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self.schedule: ScheduleResult | None = None
        self.last_calculated: datetime | None = None
        self.schedule_covers: tuple[datetime, datetime] | None = None
        self._unsubs: list = []

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

    async def async_setup(self) -> None:
        """Schedule periodic tasks and listeners."""
        # Schedule recalculation at the configured time each day.
        try:
            h, m, *rest = self.recalc_time.split(":")
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

        # Initial computation.
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
        """Compute a new schedule using available price data."""
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

        # Strategy:
        # - If tomorrow is valid, plan for the full tomorrow window (00:00 -> 24:00).
        # - Otherwise, plan from "now" through end of today's data.
        # We also keep today's schedule alive: if we already had a schedule
        # covering "now", we keep it until tomorrow's plan takes over after
        # midnight (handled implicitly by the slot list).

        now = dt_util.now()

        if tomorrow_slots:
            # Plan tomorrow.
            target_slots = tomorrow_slots
            window_label = "tomorrow"
        elif today_slots:
            # Plan remainder of today.
            target_slots = [s for s in today_slots if s.end > now]
            window_label = "remainder of today"
        else:
            _LOGGER.warning("No usable price data available")
            self.schedule = None
            self.last_calculated = now
            async_dispatcher_send(self.hass, SIGNAL_SCHEDULE_UPDATED)
            return

        if not target_slots:
            _LOGGER.warning("No slots remain in target window")
            return

        # Adjust runtime if planning a partial day (remainder of today).
        runtime = self.runtime_hours
        if window_label == "remainder of today":
            hours_remaining = sum(
                (s.end - s.start).total_seconds() for s in target_slots
            ) / 3600.0
            if hours_remaining < runtime:
                # Scale proportionally so we don't try to run more hours than exist.
                runtime = min(runtime, hours_remaining * (runtime / 24.0))
                _LOGGER.info(
                    "Partial day planning: scaled runtime to %.2f h", runtime
                )

        result = compute_schedule(
            prices=target_slots,
            runtime_hours=runtime,
            min_block_minutes=self.min_block_minutes,
            slot_minutes=SLOT_MINUTES,
            max_price=self.max_price,
        )

        if result is None:
            _LOGGER.warning(
                "Schedule computation failed (window=%s, runtime=%s, "
                "min_block=%s, max_price=%s)",
                window_label,
                runtime,
                self.min_block_minutes,
                self.max_price,
            )
            return

        self.schedule = result
        self.last_calculated = now
        self.schedule_covers = (target_slots[0].start, target_slots[-1].end)

        _LOGGER.info(
            "Schedule computed for %s: %d blocks, %.2f h, total cost %.3f",
            window_label,
            result.block_count,
            result.total_slots * SLOT_MINUTES / 60.0,
            result.total_cost,
        )

        async_dispatcher_send(self.hass, SIGNAL_SCHEDULE_UPDATED)
        await self._apply_schedule()

    def is_active_now(self) -> bool:
        """Return whether the pump should be on right now."""
        if self.schedule is None:
            return False
        return self.schedule.is_active(dt_util.now())

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
        if self.schedule is None:
            return None
        now = dt_util.now()
        boundaries: list[datetime] = []
        for start, end in self.schedule.blocks:
            if start > now:
                boundaries.append(start)
            if end > now:
                boundaries.append(end)
        if not boundaries:
            return None
        return min(boundaries)
