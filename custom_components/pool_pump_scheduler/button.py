"""Button entity for Pool Pump Scheduler."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PoolPumpCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the recalculate button entity."""
    coordinator: PoolPumpCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PoolPumpRecalculateButton(coordinator, entry)])


class PoolPumpRecalculateButton(ButtonEntity):
    """Manually trigger a fresh schedule recalculation."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:refresh"

    def __init__(
        self, coordinator: PoolPumpCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_recalculate"
        self._attr_name = "Recalculate schedule"

    @property
    def device_info(self):
        from homeassistant.helpers.device_registry import DeviceInfo
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title,
            manufacturer="Pool Pump Scheduler",
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._coordinator.async_recalculate()
