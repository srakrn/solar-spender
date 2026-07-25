"""Read-only Solar Spender headroom entity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONTROLLER_STATUS_UPDATED, DOMAIN
from .controller import SolarSpenderController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Expose the source decision independently of automation state."""
    controller: SolarSpenderController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SolarSpenderHeadroomBinarySensor(entry, controller)])


class SolarSpenderHeadroomBinarySensor(BinarySensorEntity):
    """Whether qualified solar would otherwise remain unused."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:solar-power-variant"
    _attr_should_poll = False
    _attr_translation_key = "headroom"

    def __init__(
        self,
        entry: ConfigEntry,
        controller: SolarSpenderController,
    ) -> None:
        self._entry = entry
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_headroom"

    @property
    def device_info(self) -> DeviceInfo:
        """Group the sensor with Solar Spender controls."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Solar Spender",
            manufacturer="Solar Spender",
        )

    @property
    def is_on(self) -> bool:
        """Return the waste-headroom decision, including configured battery claim."""
        return self._controller.waste_headroom_available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose enough context to diagnose the binary decision."""
        return {
            "source_valid": self._controller.source_valid,
            "source_type": self._controller.config.source_type,
            "source_surplus_available": self._controller.surplus_available,
            "waste_headroom_available": (
                self._controller.waste_headroom_available
            ),
            "headroom_w": self._controller.headroom_w,
            "opportunity_power_w": self._controller.opportunity_power_w,
            "source_deficit_w": self._controller.source_deficit_w,
            "battery_policy": self._controller.config.battery_policy,
            "battery_allowed": self._controller.battery_allowed,
            "battery_direction": self._controller.battery_direction,
            "automation_enabled": self._controller.config.enabled,
        }

    async def async_added_to_hass(self) -> None:
        """Refresh after every controller input evaluation."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{CONTROLLER_STATUS_UPDATED}_{self._entry.entry_id}",
                self.async_write_ha_state,
            )
        )
