"""Solar Spender custom integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DATA_CONTROLLER,
    DATA_PANEL_REGISTERED,
    DOMAIN,
    PANEL_COMPONENT,
    PANEL_PATH,
    PANEL_URL,
)
from .controller import SolarSpenderController
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


async def async_setup_entry(hass: HomeAssistant, entry: SolarSpenderConfigEntry) -> bool:
    """Set up Solar Spender from a config entry."""
    controller = SolarSpenderController(hass, SolarSpenderConfig.from_options(entry.options), entry.entry_id)
    hass.data[DOMAIN][entry.entry_id] = controller
    await controller.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolarSpenderConfigEntry) -> bool:
    """Unload Solar Spender without changing user-owned equipment state."""
    controller: SolarSpenderController | None = hass.data[DOMAIN].pop(entry.entry_id, None)
    if controller is not None:
        await controller.async_stop()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: SolarSpenderConfigEntry) -> None:
    """Reload safely after a validated options update."""
    await hass.config_entries.async_reload(entry.entry_id)


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
                "module_url": PANEL_URL,
                "js_url": PANEL_URL,
            }
        },
        require_admin=True,
    )
    hass.data[DOMAIN][DATA_PANEL_REGISTERED] = True
