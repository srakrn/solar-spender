"""Authenticated WebSocket API used by the Solar Spender panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DATA_CONTROLLER, DEFAULT_OPTIONS, DOMAIN
from .controller import SolarSpenderController
from .models import ConfigurationError, SolarSpenderConfig


def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register commands once during component setup."""
    websocket_api.async_register_command(hass, websocket_get_status)
    websocket_api.async_register_command(hass, websocket_get_config)
    websocket_api.async_register_command(hass, websocket_update_config)


def _controller(hass: HomeAssistant) -> SolarSpenderController | None:
    controllers = hass.data.get(DOMAIN, {})
    return next(
        (value for key, value in controllers.items() if key != "panel_registered"), None
    )


@websocket_api.websocket_command({vol.Required("type"): "solar_spender/status/get"})
@websocket_api.async_response
async def websocket_get_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return live, read-only controller status."""
    controller = _controller(hass)
    if controller is None:
        connection.send_error(msg["id"], "not_configured", "Solar Spender is not configured")
        return
    connection.send_result(msg["id"], controller.status())


@websocket_api.websocket_command({vol.Required("type"): "solar_spender/config/get"})
@websocket_api.async_response
async def websocket_get_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the current configuration to an administrator."""
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Administrator access is required")
        return
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Solar Spender is not configured")
        return
    connection.send_result(msg["id"], {**DEFAULT_OPTIONS, **entry.options})


@websocket_api.websocket_command(
    {vol.Required("type"): "solar_spender/config/update", vol.Required("options"): dict}
)
@websocket_api.async_response
async def websocket_update_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Validate and persist complete panel configuration."""
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Administrator access is required")
        return
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Solar Spender is not configured")
        return
    options = {**DEFAULT_OPTIONS, **msg["options"]}
    try:
        config = SolarSpenderConfig.from_options(options)
        _validate_climate_capabilities(hass, config)
    except ConfigurationError as err:
        connection.send_error(msg["id"], "invalid_config", str(err))
        return
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"reloading": True})


def _entry(hass: HomeAssistant) -> ConfigEntry | None:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def _validate_climate_capabilities(
    hass: HomeAssistant, config: SolarSpenderConfig
) -> None:
    """Reject climate profiles which the selected entity cannot safely apply."""
    for load in config.loads:
        state = hass.states.get(load.entity_id)
        if state is None:
            raise ConfigurationError(f"{load.entity_id} does not exist")
        hvac_modes = set(state.attributes.get("hvac_modes", []))
        if load.hvac_mode is not None and load.hvac_mode not in hvac_modes:
            raise ConfigurationError(
                f"{load.entity_id} does not support HVAC mode {load.hvac_mode}"
            )
        if load.fan_mode is not None and load.fan_mode not in set(
            state.attributes.get("fan_modes", [])
        ):
            raise ConfigurationError(
                f"{load.entity_id} does not support fan mode {load.fan_mode}"
            )
        if load.temperature is not None:
            min_temp = state.attributes.get("min_temp")
            max_temp = state.attributes.get("max_temp")
            if min_temp is not None and load.temperature < float(min_temp):
                raise ConfigurationError(f"{load.entity_id} target is below its minimum")
            if max_temp is not None and load.temperature > float(max_temp):
                raise ConfigurationError(f"{load.entity_id} target is above its maximum")
            step = state.attributes.get("target_temp_step")
            if min_temp is not None and step is not None:
                increments = (load.temperature - float(min_temp)) / float(step)
                if abs(increments - round(increments)) > 0.000001:
                    raise ConfigurationError(
                        f"{load.entity_id} target does not match its temperature step"
                    )
