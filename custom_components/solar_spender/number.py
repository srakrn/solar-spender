"""Entity-backed Solar Spender runtime timing controls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import async_update_runtime_option
from .const import (
    CONF_FEEDBACK_SAMPLE_COUNT,
    CONF_FEEDBACK_SAMPLE_INTERVAL_MINUTES,
    CONF_NEXT_LOAD_DELAY_MINUTES,
    CONF_SETTLING_SECONDS,
    DEFAULT_OPTIONS,
    DOMAIN,
    RUNTIME_OPTIONS_UPDATED,
)


@dataclass(frozen=True, slots=True)
class TimingDescription:
    """Describe one options-backed number entity."""

    key: str
    translation_key: str
    minimum: float
    maximum: float
    step: float
    to_native: Callable[[object], float]
    from_native: Callable[[float], object]


_TIMINGS = (
    TimingDescription(
        CONF_SETTLING_SECONDS,
        "first_check_delay",
        0,
        60,
        1,
        lambda value: float(value) / 60,
        lambda value: int(value * 60),
    ),
    TimingDescription(
        CONF_FEEDBACK_SAMPLE_COUNT,
        "confirmation_checks",
        1,
        9,
        2,
        float,
        lambda value: int(value),
    ),
    TimingDescription(
        CONF_FEEDBACK_SAMPLE_INTERVAL_MINUTES,
        "minutes_between_checks",
        1,
        60,
        1,
        float,
        float,
    ),
    TimingDescription(
        CONF_NEXT_LOAD_DELAY_MINUTES,
        "wait_before_next_ac",
        0,
        60,
        1,
        float,
        float,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the global timing controls."""
    async_add_entities(
        SolarSpenderTimingNumber(hass, entry, description)
        for description in _TIMINGS
    )


class SolarSpenderTimingNumber(NumberEntity):
    """A validated Solar Spender timing option."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: TimingDescription,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._description = description
        self._attr_translation_key = description.translation_key
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_native_min_value = description.minimum
        self._attr_native_max_value = description.maximum
        self._attr_native_step = description.step
        if description.key == CONF_FEEDBACK_SAMPLE_COUNT:
            self._attr_native_unit_of_measurement = None

    @property
    def device_info(self) -> DeviceInfo:
        """Group runtime controls under one Solar Spender device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Solar Spender",
            manufacturer="Solar Spender",
        )

    @property
    def native_value(self) -> float:
        """Return the current option in the entity's display unit."""
        value = self._entry.options.get(
            self._description.key,
            DEFAULT_OPTIONS[self._description.key],
        )
        return self._description.to_native(value)

    async def async_set_native_value(self, value: float) -> None:
        """Validate and persist a timing change."""
        await async_update_runtime_option(
            self.hass,
            self._entry,
            self._description.key,
            self._description.from_native(value),
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
