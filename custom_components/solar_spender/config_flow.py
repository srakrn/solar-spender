"""Config flow for Solar Spender."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_FEEDBACK_SAMPLE_COUNT,
    CONF_FEEDBACK_TIMEOUT_MINUTES,
    CONF_INPUT_MAX_AGE_MINUTES,
    CONF_NEXT_LOAD_DELAY_MINUTES,
    CONF_SETTLING_SECONDS,
    DEFAULT_OPTIONS,
    DOMAIN,
)
from .models import SolarSpenderConfig


def _number_selector(
    minimum: float,
    maximum: float,
    step: float,
    unit: str | None = None,
) -> selector.NumberSelector:
    """Build one standard Home Assistant number selector."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement=unit,
        )
    )


class SolarSpenderConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup of the singleton Solar Spender controller."""

    VERSION = 5

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Create the controller; detailed configuration lives in the panel."""
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Solar Spender", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SolarSpenderOptionsFlow:
        """Provide a safe Settings fallback for the panel configuration."""
        return SolarSpenderOptionsFlow()


class SolarSpenderOptionsFlow(config_entries.OptionsFlow):
    """Expose synchronized runtime timing controls in Settings."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Edit timing options while retaining panel-only configuration."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options = {**DEFAULT_OPTIONS, **self.config_entry.options, **user_input}
            try:
                SolarSpenderConfig.from_options(options)
            except ValueError as err:
                errors["base"] = "invalid_config"
                error = str(err)
            else:
                return self.async_create_entry(data=options)
        else:
            error = ""
        current = {
            **DEFAULT_OPTIONS,
            **self.config_entry.options,
            **(user_input or {}),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SETTLING_SECONDS,
                        default=current[CONF_SETTLING_SECONDS],
                    ): _number_selector(0, 3600, 1, "seconds"),
                    vol.Required(
                        CONF_FEEDBACK_SAMPLE_COUNT,
                        default=current[CONF_FEEDBACK_SAMPLE_COUNT],
                    ): _number_selector(1, 9, 2),
                    vol.Required(
                        CONF_FEEDBACK_TIMEOUT_MINUTES,
                        default=current[CONF_FEEDBACK_TIMEOUT_MINUTES],
                    ): _number_selector(1, 1440, 1, "minutes"),
                    vol.Required(
                        CONF_INPUT_MAX_AGE_MINUTES,
                        default=current[CONF_INPUT_MAX_AGE_MINUTES],
                    ): _number_selector(1, 1440, 1, "minutes"),
                    vol.Required(
                        CONF_NEXT_LOAD_DELAY_MINUTES,
                        default=current[CONF_NEXT_LOAD_DELAY_MINUTES],
                    ): _number_selector(0, 60, 1, "minutes"),
                }
            ),
            errors=errors,
            description_placeholders={"error": error},
        )
