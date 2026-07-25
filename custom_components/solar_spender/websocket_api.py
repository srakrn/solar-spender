"""Authenticated WebSocket API used by the Solar Spender panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    BATTERY_CHARGING_OR_SOC,
    BATTERY_DIRECTION_POWER,
    BATTERY_DIRECTION_STATUS,
    BATTERY_DISABLED,
    BATTERY_FULL_IDLE_FOR_PROBE,
    DATA_CONTROLLER,
    DEFAULT_OPTIONS,
    DOMAIN,
    SOURCE_CURTAILED,
    SOURCE_GRID,
    SOURCE_PRODUCTION,
)
from .controller import SolarSpenderController
from .migration import current_options
from .models import ConfigurationError, SolarSpenderConfig


def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register commands once during component setup."""
    websocket_api.async_register_command(hass, websocket_get_status)
    websocket_api.async_register_command(hass, websocket_get_config)
    websocket_api.async_register_command(hass, websocket_update_config)
    websocket_api.async_register_command(hass, websocket_set_pause)


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
    connection.send_result(
        msg["id"],
        current_options({**DEFAULT_OPTIONS, **entry.options}),
    )


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
    options = current_options({**DEFAULT_OPTIONS, **msg["options"]})
    try:
        config = SolarSpenderConfig.from_options(options)
        _validate_configured_entities(hass, config)
        _validate_climate_capabilities(hass, config)
    except ConfigurationError as err:
        connection.send_error(msg["id"], "invalid_config", str(err))
        return
    controller = _controller(hass)
    reloading = (
        controller is None
        or not controller.supports_runtime_config(config)
    )
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"reloading": reloading})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "solar_spender/control/set_pause",
        vol.Required("minutes"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
    }
)
@websocket_api.async_response
async def websocket_set_pause(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Temporarily ignore all controller inputs, or resume with zero minutes."""
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Administrator access is required")
        return
    controller = _controller(hass)
    if controller is None:
        connection.send_error(msg["id"], "not_configured", "Solar Spender is not configured")
        return
    try:
        await controller.async_set_pause(msg["minutes"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "invalid_pause", str(err))
        return
    connection.send_result(msg["id"], controller.status())


def _entry(hass: HomeAssistant) -> ConfigEntry | None:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def _validate_configured_entities(
    hass: HomeAssistant, config: SolarSpenderConfig
) -> None:
    """Validate entity metadata independently of the frontend selectors."""
    if config.source_type == SOURCE_GRID and config.grid_entity_id:
        _validate_power_entity(hass, config.grid_entity_id)
    elif config.source_type in {SOURCE_PRODUCTION, SOURCE_CURTAILED}:
        if config.production_entity_id:
            _validate_power_entity(hass, config.production_entity_id)
        if config.consumption_entity_id:
            _validate_power_entity(hass, config.consumption_entity_id)

    for load in config.loads:
        if load.enabled and load.power_entity_id:
            _validate_power_entity(hass, load.power_entity_id)

    if config.battery_policy == BATTERY_DISABLED:
        return
    if (
        config.battery_direction_source == BATTERY_DIRECTION_STATUS
        and config.battery_status_entity_id
    ):
        _validate_battery_status_entity(hass, config)
    if (
        config.battery_direction_source == BATTERY_DIRECTION_POWER
        and config.battery_power_entity_id
    ):
        _validate_power_entity(hass, config.battery_power_entity_id)
    if (
        config.battery_policy
        in {BATTERY_CHARGING_OR_SOC, BATTERY_FULL_IDLE_FOR_PROBE}
        and config.battery_soc_entity_id
    ):
        _validate_battery_soc_entity(hass, config.battery_soc_entity_id)


def _require_entity(hass: HomeAssistant, entity_id: str) -> State:
    state = hass.states.get(entity_id)
    if state is None:
        raise ConfigurationError(f"{entity_id} does not exist")
    return state


def _validate_power_entity(hass: HomeAssistant, entity_id: str) -> None:
    state = _require_entity(hass, entity_id)
    attributes = state.attributes
    if (
        not entity_id.startswith("sensor.")
        or attributes.get("device_class") != "power"
        or attributes.get("state_class") != "measurement"
        or attributes.get("unit_of_measurement") not in {"W", "kW"}
    ):
        raise ConfigurationError(
            f"{entity_id} must be a measurement power sensor using W or kW"
        )


def _validate_battery_soc_entity(hass: HomeAssistant, entity_id: str) -> None:
    state = _require_entity(hass, entity_id)
    attributes = state.attributes
    if (
        not entity_id.startswith("sensor.")
        or attributes.get("device_class") != "battery"
        or attributes.get("state_class") != "measurement"
        or attributes.get("unit_of_measurement") != "%"
    ):
        raise ConfigurationError(
            f"{entity_id} must be a measurement battery sensor using %"
        )


def _validate_battery_status_entity(
    hass: HomeAssistant, config: SolarSpenderConfig
) -> None:
    state = _require_entity(hass, config.battery_status_entity_id)
    attributes = state.attributes
    if state.entity_id.startswith("binary_sensor."):
        if attributes.get("device_class") != "battery_charging":
            raise ConfigurationError(
                f"{state.entity_id} must use the battery_charging device class"
            )
        return
    known_states = {
        *config.charging_states,
        *config.discharging_states,
        "idle",
        "full",
        "standby",
        "not_charging",
    }
    options = {str(value).lower() for value in attributes.get("options", [])}
    if not state.entity_id.startswith("sensor.") or not (
        state.state.lower() in known_states or options.intersection(known_states)
    ):
        raise ConfigurationError(
            f"{state.entity_id} must report charging, discharging, or idle status"
        )


def _validate_climate_capabilities(
    hass: HomeAssistant, config: SolarSpenderConfig
) -> None:
    """Reject climate profiles which the selected entity cannot safely apply."""
    for load in config.loads:
        if not load.enabled:
            # Keep intentionally disabled definitions editable even if the
            # entity is temporarily unavailable, renamed, or removed.
            continue
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
