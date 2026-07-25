"""Solar Spender custom integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DATA_CONTROLLER,
    DATA_PANEL_REGISTERED,
    DEFAULT_OPTIONS,
    DOMAIN,
    PANEL_COMPONENT,
    PANEL_MODULE_URL,
    PANEL_PATH,
    PANEL_URL,
    PLATFORMS,
    RUNTIME_OPTIONS_UPDATED,
)
from .controller import SolarSpenderController
from .migration import version_4_options, version_5_options, version_6_options
from .models import SolarSpenderConfig
from .websocket_api import async_register_websocket_api

type SolarSpenderConfigEntry = ConfigEntry[dict[str, object]]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up process-wide panel assets and WebSocket commands."""
    hass.data.setdefault(DOMAIN, {})
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                PANEL_URL,
                str(Path(__file__).parent / "frontend" / "solar-spender-panel.js"),
                cache_headers=False,
            )
        ]
    )
    async_register_websocket_api(hass)
    _async_register_panel(hass)
    return True


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: SolarSpenderConfigEntry,
) -> bool:
    """Migrate saved options without changing the user's AC order."""
    if entry.version < 6:
        options = dict(entry.options)
        if entry.version < 4:
            options = version_4_options(options)
        if entry.version < 5:
            options = version_5_options(options)
            entity_registry = er.async_get(hass)
            if entity_id := entity_registry.async_get_entity_id(
                "number",
                DOMAIN,
                f"{entry.entry_id}_feedback_sample_interval_minutes",
            ):
                entity_registry.async_remove(entity_id)
        options = version_6_options(options)
        hass.config_entries.async_update_entry(
            entry,
            options=options,
            version=6,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SolarSpenderConfigEntry) -> bool:
    """Set up Solar Spender from a config entry."""
    controller = SolarSpenderController(
        hass,
        SolarSpenderConfig.from_options(entry.options),
        entry.entry_id,
    )
    hass.data[DOMAIN][entry.entry_id] = controller
    await controller.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolarSpenderConfigEntry) -> bool:
    """Unload Solar Spender without changing user-owned equipment state."""
    controller: SolarSpenderController | None = hass.data[DOMAIN].pop(entry.entry_id, None)
    if controller is not None:
        await controller.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: SolarSpenderConfigEntry) -> None:
    """Reload safely after a validated options update."""
    controller: SolarSpenderController | None = hass.data[DOMAIN].get(
        entry.entry_id
    )
    config = SolarSpenderConfig.from_options(entry.options)
    if controller is not None and controller.supports_runtime_config(config):
        await controller.async_apply_runtime_config(config)
        async_dispatcher_send(
            hass,
            f"{RUNTIME_OPTIONS_UPDATED}_{entry.entry_id}",
        )
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_update_runtime_option(
    hass: HomeAssistant,
    entry: SolarSpenderConfigEntry,
    key: str,
    value: object,
) -> None:
    """Persist one entity-backed option without discarding controller ownership."""
    options = {**DEFAULT_OPTIONS, **entry.options, key: value}
    try:
        config = SolarSpenderConfig.from_options(options)
    except ValueError as err:
        raise HomeAssistantError(str(err)) from err
    controller: SolarSpenderController = hass.data[DOMAIN][entry.entry_id]
    await controller.async_apply_runtime_config(config)
    hass.config_entries.async_update_entry(entry, options=options)
    async_dispatcher_send(
        hass,
        f"{RUNTIME_OPTIONS_UPDATED}_{entry.entry_id}",
    )


def _async_register_panel(hass: HomeAssistant) -> None:
    """Register the documented custom-panel bridge once per Home Assistant."""
    if hass.data[DOMAIN].get(DATA_PANEL_REGISTERED):
        return
    # ``custom`` is Home Assistant's public panel bridge for a bundled module.
    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title="Solar Spender",
        sidebar_icon="mdi:solar-power",
        frontend_url_path=PANEL_PATH,
        config={
            "_panel_custom": {
                "name": PANEL_COMPONENT,
                "embed_iframe": False,
                "trust_external": False,
                "module_url": PANEL_MODULE_URL,
                "js_url": PANEL_MODULE_URL,
            }
        },
        require_admin=True,
    )
    hass.data[DOMAIN][DATA_PANEL_REGISTERED] = True
