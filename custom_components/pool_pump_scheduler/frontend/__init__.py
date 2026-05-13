"""JavaScript module registration for the Pool Pump Scheduler card."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from ..const import JSMODULES, URL_BASE

_LOGGER = logging.getLogger(__name__)


class JSModuleRegistration:
    """Register and update the bundled Lovelace card."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.lovelace = self.hass.data.get("lovelace")

    async def async_register(self) -> None:
        """Register the static path and (if applicable) Lovelace resources."""
        await self._async_register_path()
        if self.lovelace is not None and getattr(self.lovelace, "mode", None) == "storage":
            await self._async_wait_for_lovelace_resources()
        else:
            _LOGGER.debug(
                "Lovelace not in storage mode; users must add the card "
                "resource manually if needed."
            )

    async def _async_register_path(self) -> None:
        """Serve files from the frontend/ directory at URL_BASE."""
        try:
            await self.hass.http.async_register_static_paths(
                [StaticPathConfig(URL_BASE, str(Path(__file__).parent), False)]
            )
            _LOGGER.debug("Registered static path %s", URL_BASE)
        except RuntimeError:
            # Already registered (e.g. on reload).
            _LOGGER.debug("Static path %s already registered", URL_BASE)

    async def _async_wait_for_lovelace_resources(self) -> None:
        """Wait for Lovelace resources to be ready, then register modules."""

        async def _check_loaded(_now: Any) -> None:
            if self.lovelace.resources.loaded:
                await self._async_register_modules()
            else:
                async_call_later(self.hass, 5, _check_loaded)

        await _check_loaded(0)

    async def _async_register_modules(self) -> None:
        """Create or update Lovelace resource entries for our modules."""
        existing = [
            r for r in self.lovelace.resources.async_items()
            if r["url"].startswith(URL_BASE)
        ]

        for module in JSMODULES:
            url = f"{URL_BASE}/{module['filename']}"
            versioned_url = f"{url}?v={module['version']}"
            already = False

            for resource in existing:
                if resource["url"].split("?")[0] == url:
                    already = True
                    # Update version if needed.
                    current_version = "0"
                    if "?v=" in resource["url"]:
                        current_version = resource["url"].split("?v=", 1)[1]
                    if current_version != module["version"]:
                        _LOGGER.info(
                            "Updating Lovelace resource %s -> %s",
                            module["name"], module["version"],
                        )
                        await self.lovelace.resources.async_update_item(
                            resource["id"],
                            {"res_type": "module", "url": versioned_url},
                        )
                    break

            if not already:
                _LOGGER.info("Registering Lovelace resource %s", module["name"])
                await self.lovelace.resources.async_create_item(
                    {"res_type": "module", "url": versioned_url}
                )

    async def async_unregister(self) -> None:
        """Remove the Lovelace resource entries for our modules."""
        if self.lovelace is None or getattr(self.lovelace, "mode", None) != "storage":
            return
        for module in JSMODULES:
            url = f"{URL_BASE}/{module['filename']}"
            for resource in list(self.lovelace.resources.async_items()):
                if resource["url"].startswith(url):
                    try:
                        await self.lovelace.resources.async_delete_item(resource["id"])
                    except Exception as err:  # pragma: no cover
                        _LOGGER.debug("Could not delete resource: %s", err)
