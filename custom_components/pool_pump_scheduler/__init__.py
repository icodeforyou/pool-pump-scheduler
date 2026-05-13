"""The Pool Pump Scheduler integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, HomeAssistant, ServiceCall

from .const import DOMAIN, SERVICE_RECALCULATE
from .coordinator import PoolPumpCoordinator
from .frontend import JSModuleRegistration

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration (no YAML config supported)."""
    hass.data.setdefault(DOMAIN, {})

    # Register the bundled Lovelace card. Must run in async_setup, not
    # async_setup_entry, so it happens once globally.
    async def _register_frontend(_event=None) -> None:
        try:
            registrar = JSModuleRegistration(hass)
            await registrar.async_register()
            hass.data[f"{DOMAIN}_frontend"] = registrar
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.warning("Failed to register frontend card: %s", err)

    if hass.state == CoreState.running:
        await _register_frontend()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_frontend)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Pool Pump Scheduler from a config entry."""
    coordinator = PoolPumpCoordinator(hass, entry)
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register reload handler for options changes.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Register the recalculate service (only once, globally).
    if not hass.services.has_service(DOMAIN, SERVICE_RECALCULATE):

        async def _handle_recalculate(call: ServiceCall) -> None:
            """Handle manual recalculation requests."""
            entry_id = call.data.get("entry_id")
            for eid, coord in hass.data[DOMAIN].items():
                if entry_id is None or eid == entry_id:
                    await coord.async_recalculate()

        hass.services.async_register(DOMAIN, SERVICE_RECALCULATE, _handle_recalculate)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: PoolPumpCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_unload()
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_RECALCULATE)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)
