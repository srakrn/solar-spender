"""Config flow for Solar Spender."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN


class SolarSpenderConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup of the singleton Solar Spender controller."""

    VERSION = 4

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Create the controller; detailed configuration lives in the panel."""
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Solar Spender", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> SolarSpenderOptionsFlow:
        """Provide a safe Settings fallback for the panel configuration."""
        return SolarSpenderOptionsFlow()


class SolarSpenderOptionsFlow(config_entries.OptionsFlow):
    """Direct users to the sidebar panel for the complete load editor."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Expose an intentionally small UI fallback."""
        if user_input is not None:
            return self.async_create_entry(data=self.config_entry.options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({}),
            description_placeholders={"panel": "/solar-spender"},
        )
