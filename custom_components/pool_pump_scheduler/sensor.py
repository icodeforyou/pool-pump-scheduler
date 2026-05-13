"""Sensors for Pool Pump Scheduler."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
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
    """Set up sensor entities."""
    coordinator: PoolPumpCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            NextChangeSensor(coordinator, entry),
            ScheduleCostSensor(coordinator, entry),
            ScheduleAveragePriceSensor(coordinator, entry),
        ]
    )


class _BaseSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._entry = entry

    @property
    def device_info(self):
        from homeassistant.helpers.device_registry import DeviceInfo
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title,
            manufacturer="Pool Pump Scheduler",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_SCHEDULE_UPDATED, self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class NextChangeSensor(_BaseSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_next_change"
        self._attr_name = "Next change"

    @property
    def native_value(self):
        return self._coordinator.next_change()


class ScheduleCostSensor(_BaseSensor):
    _attr_icon = "mdi:cash"
    _attr_native_unit_of_measurement = "SEK"
    _attr_state_class = "total"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_schedule_cost"
        self._attr_name = "Scheduled cost"

    @property
    def native_value(self):
        sched = self._coordinator.schedule
        if sched is None:
            return None
        return round(sched.total_cost, 3)

    @property
    def extra_state_attributes(self):
        sched = self._coordinator.schedule
        if sched is None:
            return {}
        return {
            "block_count": sched.block_count,
            "total_runtime_hours": round(sched.total_slots * 15 / 60.0, 2),
            "last_calculated": self._coordinator.last_calculated,
        }


class ScheduleAveragePriceSensor(_BaseSensor):
    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_state_class = "measurement"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_avg_price"
        self._attr_name = "Scheduled average price"

    @property
    def native_value(self):
        sched = self._coordinator.schedule
        if sched is None or sched.total_slots == 0:
            return None
        return round(sched.total_cost / sched.total_slots, 4)
