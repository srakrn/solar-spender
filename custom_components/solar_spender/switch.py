"""Entity-backed Solar Spender automation control."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import async_update_runtime_option
from .const import (
    CONF_ENABLED,
    DEFAULT_OPTIONS,
    DOMAIN,
    RUNTIME_OPTIONS_UPDATED,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the Solar Spender automation switch."""
    async_add_entities([SolarSpenderAutomationSwitch(hass, entry)])


class SolarSpenderAutomationSwitch(SwitchEntity):
    """Pause or resume automatic load control without releasing owned ACs."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:solar-power"
    _attr_should_poll = False
    _attr_translation_key = "automation"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{CONF_ENABLED}"

    @property
    def device_info(self) -> DeviceInfo:
        """Group runtime controls under one Solar Spender device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Solar Spender",
            manufacturer="Solar Spender",
        )

    @property
    def is_on(self) -> bool:
        """Return whether automatic control is enabled."""
        return bool(
            self._entry.options.get(
                CONF_ENABLED,
                DEFAULT_OPTIONS[CONF_ENABLED],
            )
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable automatic control."""
        await async_update_runtime_option(
            self.hass,
            self._entry,
            CONF_ENABLED,
            True,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pause automatic control without releasing owned ACs."""
        await async_update_runtime_option(
            self.hass,
            self._entry,
            CONF_ENABLED,
            False,
        )

    async def async_added_to_hass(self) -> None:
        """Refresh when another Solar Spender control changes options."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{RUNTIME_OPTIONS_UPDATED}_{self._entry.entry_id}",
                self.async_write_ha_state,
            )
        )
