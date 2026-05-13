"""Binary sensor for Pool Pump Scheduler."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_SCHEDULE_UPDATED
from .coordinator import PoolPumpCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor entity."""
    coordinator: PoolPumpCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PoolPumpShouldRunBinarySensor(coordinator, entry)])


class PoolPumpShouldRunBinarySensor(BinarySensorEntity):
    """Binary sensor indicating whether the pump should currently run."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:pump"

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_should_run"
        self._attr_name = "Should run"

    @property
    def device_info(self):
        from homeassistant.helpers.device_registry import DeviceInfo
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title,
            manufacturer="Pool Pump Scheduler",
            entry_type=None,
        )

    @property
    def is_on(self) -> bool:
        return self._coordinator.is_active_now()

    @property
    def extra_state_attributes(self) -> dict:
        sched = self._coordinator.schedule
        attrs: dict = {
            "solar_overlay_enabled": self._coordinator.use_solar_surplus,
            "solar_active": self._coordinator.solar_active,
        }
        if sched is None:
            attrs["schedule_available"] = False
            return attrs
        attrs.update({
            "schedule_available": True,
            "block_count": sched.block_count,
            "total_runtime_minutes": sched.total_slots * 15,
            "total_cost": round(sched.total_cost, 3),
            "average_price": round(sched.total_cost / max(sched.total_slots, 1), 4),
            "next_change": self._coordinator.next_change(),
            "blocks": [
                {"start": s.isoformat(), "end": e.isoformat()}
                for s, e in sched.blocks
            ],
        })
        return attrs

    async def async_added_to_hass(self) -> None:
        """Register dispatcher and time-based update listeners."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_SCHEDULE_UPDATED, self._handle_update
            )
        )

        # Also tick at slot boundaries so is_on stays current.
        from homeassistant.helpers.event import async_track_time_change
        self.async_on_remove(
            async_track_time_change(
                self.hass,
                lambda now: self.async_write_ha_state(),
                minute=[0, 15, 30, 45],
                second=6,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
