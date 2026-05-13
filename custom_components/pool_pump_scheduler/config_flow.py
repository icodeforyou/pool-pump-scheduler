"""Config flow for Pool Pump Scheduler."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CONTROL_SWITCH,
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
)


def _build_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the form schema with provided defaults."""
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=defaults.get(CONF_NAME, "Pool Pump")
            ): str,
            vol.Required(
                CONF_PRICE_SENSOR,
                default=defaults.get(CONF_PRICE_SENSOR, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_PUMP_SWITCH,
                default=defaults.get(CONF_PUMP_SWITCH, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch")
            ),
            vol.Required(
                CONF_RUNTIME_HOURS,
                default=defaults.get(CONF_RUNTIME_HOURS, DEFAULT_RUNTIME_HOURS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.25, max=24.0, step=0.25,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="h",
                )
            ),
            vol.Required(
                CONF_MIN_BLOCK_MINUTES,
                default=defaults.get(
                    CONF_MIN_BLOCK_MINUTES, DEFAULT_MIN_BLOCK_MINUTES
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=15, max=720, step=15,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            ),
            vol.Required(
                CONF_RECALC_TIME,
                default=defaults.get(CONF_RECALC_TIME, DEFAULT_RECALC_TIME),
            ): selector.TimeSelector(),
            vol.Required(
                CONF_CONTROL_SWITCH,
                default=defaults.get(CONF_CONTROL_SWITCH, DEFAULT_CONTROL_SWITCH),
            ): bool,
            vol.Required(
                CONF_USE_MAX_PRICE,
                default=defaults.get(CONF_USE_MAX_PRICE, DEFAULT_USE_MAX_PRICE),
            ): bool,
            vol.Required(
                CONF_MAX_PRICE,
                default=defaults.get(CONF_MAX_PRICE, DEFAULT_MAX_PRICE),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=100.0, step=0.01,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="SEK/kWh",
                )
            ),
            vol.Required(
                CONF_USE_SOLAR_SURPLUS,
                default=defaults.get(
                    CONF_USE_SOLAR_SURPLUS, DEFAULT_USE_SOLAR_SURPLUS
                ),
            ): bool,
            vol.Optional(
                CONF_SOLAR_PRODUCTION_SENSOR,
                default=defaults.get(CONF_SOLAR_PRODUCTION_SENSOR, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_SOLAR_CONSUMPTION_SENSOR,
                default=defaults.get(CONF_SOLAR_CONSUMPTION_SENSOR, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_PUMP_POWER_W,
                default=defaults.get(CONF_PUMP_POWER_W, DEFAULT_PUMP_POWER_W),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50, max=5000, step=10,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="W",
                )
            ),
            vol.Required(
                CONF_SURPLUS_HYSTERESIS_SECONDS,
                default=defaults.get(
                    CONF_SURPLUS_HYSTERESIS_SECONDS,
                    DEFAULT_SURPLUS_HYSTERESIS_SECONDS,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=600, step=10,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
        }
    )


class PoolPumpSchedulerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pool Pump Scheduler."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Basic sanity checks.
            if user_input[CONF_RUNTIME_HOURS] <= 0:
                errors[CONF_RUNTIME_HOURS] = "runtime_must_be_positive"
            if user_input[CONF_MIN_BLOCK_MINUTES] < 15:
                errors[CONF_MIN_BLOCK_MINUTES] = "min_block_too_small"
            if user_input.get(CONF_USE_SOLAR_SURPLUS):
                if not user_input.get(CONF_SOLAR_PRODUCTION_SENSOR):
                    errors[CONF_SOLAR_PRODUCTION_SENSOR] = "solar_sensor_required"
                if not user_input.get(CONF_SOLAR_CONSUMPTION_SENSOR):
                    errors[CONF_SOLAR_CONSUMPTION_SENSOR] = "solar_sensor_required"
            if not errors:
                await self.async_set_unique_id(
                    f"{DOMAIN}_{user_input[CONF_PUMP_SWITCH]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return PoolPumpSchedulerOptionsFlow(config_entry)


class PoolPumpSchedulerOptionsFlow(config_entries.OptionsFlow):
    """Options flow for changing settings after setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Merge data + options as defaults.
        defaults = {**self.config_entry.data, **self.config_entry.options}
        # Don't allow changing the name in options.
        schema = _build_schema(defaults)
        schema = schema.extend({})
        # Remove the name field — that's only for initial setup.
        new_schema_dict = {
            k: v for k, v in schema.schema.items()
            if not (hasattr(k, "schema") and k.schema == CONF_NAME)
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(new_schema_dict),
        )
