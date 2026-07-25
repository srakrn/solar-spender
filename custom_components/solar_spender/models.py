"""Validated Solar Spender configuration models."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.helpers import config_validation as cv

from .const import (
    BATTERY_CHARGING_OR_SOC,
    BATTERY_DISABLED,
    BATTERY_DIRECTION_POWER,
    BATTERY_DIRECTION_SOURCES,
    BATTERY_DIRECTION_STATUS,
    BATTERY_FULL_IDLE_FOR_PROBE,
    BATTERY_POLICIES,
    BATTERY_REQUIRE_CHARGING,
    CONF_BATTERY_DIRECTION_SOURCE,
    CONF_BATTERY_FULL_THRESHOLD,
    CONF_BATTERY_POLICY,
    CONF_BATTERY_POWER_CHARGING_POSITIVE,
    CONF_BATTERY_POWER_ENTITY_ID,
    CONF_BATTERY_POWER_THRESHOLD_W,
    CONF_BATTERY_SOC_ENTITY_ID,
    CONF_BATTERY_STATUS_ENTITY_ID,
    CONF_CHARGING_STATES,
    CONF_CONSUMPTION_ENTITY_ID,
    CONF_DISCHARGING_STATES,
    CONF_ENABLED,
    CONF_ENTRY_THRESHOLD_W,
    CONF_EXIT_THRESHOLD_W,
    CONF_EXPORT_RESERVE_W,
    CONF_FEEDBACK_SAMPLE_COUNT,
    CONF_FEEDBACK_TIMEOUT_MINUTES,
    CONF_GRID_ENTITY_ID,
    CONF_GRID_EXPORT_POSITIVE,
    CONF_INPUT_MAX_AGE_MINUTES,
    CONF_LOADS,
    CONF_MINIMUM_PRODUCTION_W,
    CONF_NEXT_LOAD_DELAY_MINUTES,
    CONF_PRODUCTION_ENTITY_ID,
    CONF_SETTLING_SECONDS,
    CONF_SOURCE_TYPE,
    DEFAULT_OPTIONS,
    SOURCE_TYPES,
)


class ConfigurationError(ValueError):
    """Raised when the Solar Spender configuration is invalid."""


@dataclass(frozen=True, slots=True)
class LoadConfig:
    """A configured climate load."""

    entity_id: str
    priority: int
    hvac_mode: str | None
    temperature: float | None
    fan_mode: str | None
    expected_power_w: float | None
    power_entity_id: str
    min_on_seconds: int
    min_off_seconds: int
    enabled: bool

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> LoadConfig:
        """Validate and create a load definition."""
        entity_id = cv.entity_id(value.get("entity_id", ""))
        if not entity_id.startswith("climate."):
            raise ConfigurationError("Choose an AC entity.")
        hvac_mode = value.get("hvac_mode") or None
        temperature = value.get(ATTR_TEMPERATURE)
        fan_mode = value.get("fan_mode") or None
        if hvac_mode is None and temperature is None:
            raise ConfigurationError("Choose a mode or temperature for each AC.")
        if temperature is not None:
            temperature = float(temperature)
        expected_power_w = value.get("expected_power_w")
        if expected_power_w is not None:
            expected_power_w = float(expected_power_w)
            if not isfinite(expected_power_w) or expected_power_w <= 0:
                raise ConfigurationError(
                    "Usual AC power must be greater than zero."
                )
        power_entity_id = str(value.get("power_entity_id") or "")
        if power_entity_id:
            power_entity_id = cv.entity_id(power_entity_id)
            if not power_entity_id.startswith("sensor."):
                raise ConfigurationError(
                    "Choose a sensor for AC power."
                )
        min_on_seconds = int(value.get("min_on_seconds", 300))
        min_off_seconds = int(value.get("min_off_seconds", 900))
        if min_on_seconds < 0 or min_off_seconds < 0:
            raise ConfigurationError("AC on and off times cannot be negative.")
        return cls(
            entity_id=entity_id,
            priority=int(value.get("priority", 100)),
            hvac_mode=hvac_mode,
            temperature=temperature,
            fan_mode=fan_mode,
            expected_power_w=expected_power_w,
            power_entity_id=power_entity_id,
            min_on_seconds=min_on_seconds,
            min_off_seconds=min_off_seconds,
            enabled=bool(value.get("enabled", True)),
        )


@dataclass(frozen=True, slots=True)
class SolarSpenderConfig:
    """The complete, normalized options for one config entry."""

    enabled: bool
    source_type: str
    grid_entity_id: str
    grid_export_positive: bool
    production_entity_id: str
    consumption_entity_id: str
    entry_threshold_w: float
    exit_threshold_w: float
    minimum_production_w: float
    export_reserve_w: float
    settling_seconds: int
    feedback_sample_count: int
    feedback_timeout_minutes: float
    input_max_age_minutes: float
    next_load_delay_minutes: float
    loads: tuple[LoadConfig, ...]
    battery_policy: str
    battery_soc_entity_id: str
    battery_status_entity_id: str
    battery_power_entity_id: str
    battery_direction_source: str
    battery_power_charging_positive: bool
    battery_power_threshold_w: float
    battery_full_threshold: float
    charging_states: frozenset[str]
    discharging_states: frozenset[str]

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> SolarSpenderConfig:
        """Validate and normalize config-entry options."""
        merged = {**DEFAULT_OPTIONS, **options}
        source_type = str(merged[CONF_SOURCE_TYPE])
        if source_type not in SOURCE_TYPES:
            raise ConfigurationError("Choose a valid solar sensor type.")
        entry = float(merged[CONF_ENTRY_THRESHOLD_W])
        exit_ = float(merged[CONF_EXIT_THRESHOLD_W])
        minimum_production = float(merged[CONF_MINIMUM_PRODUCTION_W])
        export_reserve = float(merged[CONF_EXPORT_RESERVE_W])
        if source_type == "curtailed_production":
            if entry >= exit_:
                raise ConfigurationError(
                    "For zero export, “Start test below” must be lower than "
                    "“Stop test above.”"
                )
        elif entry <= exit_:
            raise ConfigurationError(
                "“Start above” must be higher than “Stop below.”"
            )
        if (
            not isfinite(entry)
            or not isfinite(exit_)
            or not isfinite(minimum_production)
            or not isfinite(export_reserve)
            or entry < 0
            or exit_ < 0
            or minimum_production < 0
            or export_reserve < 0
        ):
            raise ConfigurationError("Solar power values cannot be negative.")
        settling = int(merged[CONF_SETTLING_SECONDS])
        if settling < 0:
            raise ConfigurationError("The first check wait cannot be negative.")
        sample_count = int(merged[CONF_FEEDBACK_SAMPLE_COUNT])
        if sample_count < 1 or sample_count > 9 or sample_count % 2 == 0:
            raise ConfigurationError(
                "Number of checks must be 1, 3, 5, 7, or 9."
            )
        feedback_timeout = float(merged[CONF_FEEDBACK_TIMEOUT_MINUTES])
        input_max_age = float(merged[CONF_INPUT_MAX_AGE_MINUTES])
        next_load_delay = float(merged[CONF_NEXT_LOAD_DELAY_MINUTES])
        if (
            not isfinite(feedback_timeout)
            or feedback_timeout < 1
            or not isfinite(input_max_age)
            or input_max_age < 1
            or not isfinite(next_load_delay)
            or next_load_delay < 0
        ):
            raise ConfigurationError(
                "Check timeout and maximum input age must be at least one "
                "minute. The wait before the next AC cannot be negative."
            )
        loads = tuple(LoadConfig.from_dict(item) for item in merged[CONF_LOADS])
        entity_ids = [load.entity_id for load in loads]
        if len(entity_ids) != len(set(entity_ids)):
            raise ConfigurationError("Each AC can be added only once.")
        battery_policy = str(merged[CONF_BATTERY_POLICY])
        if battery_policy not in BATTERY_POLICIES:
            raise ConfigurationError("Choose a valid battery rule.")
        battery_direction_source = str(merged[CONF_BATTERY_DIRECTION_SOURCE])
        if battery_direction_source not in BATTERY_DIRECTION_SOURCES:
            raise ConfigurationError("Choose how to read the battery.")
        battery_power_threshold_w = float(merged[CONF_BATTERY_POWER_THRESHOLD_W])
        battery_full_threshold = float(merged[CONF_BATTERY_FULL_THRESHOLD])
        if (
            not isfinite(battery_power_threshold_w)
            or battery_power_threshold_w < 0
        ):
            raise ConfigurationError(
                "Battery idle range cannot be negative."
            )
        if (
            not isfinite(battery_full_threshold)
            or battery_full_threshold < 0
            or battery_full_threshold > 100
        ):
            raise ConfigurationError("Battery level must be from 0% to 100%.")
        config = cls(
            enabled=bool(merged[CONF_ENABLED]),
            source_type=source_type,
            grid_entity_id=str(merged[CONF_GRID_ENTITY_ID]),
            grid_export_positive=bool(merged[CONF_GRID_EXPORT_POSITIVE]),
            production_entity_id=str(merged[CONF_PRODUCTION_ENTITY_ID]),
            consumption_entity_id=str(merged[CONF_CONSUMPTION_ENTITY_ID]),
            entry_threshold_w=entry,
            exit_threshold_w=exit_,
            minimum_production_w=minimum_production,
            export_reserve_w=export_reserve,
            settling_seconds=settling,
            feedback_sample_count=sample_count,
            feedback_timeout_minutes=feedback_timeout,
            input_max_age_minutes=input_max_age,
            next_load_delay_minutes=next_load_delay,
            loads=loads,
            battery_policy=battery_policy,
            battery_soc_entity_id=str(merged[CONF_BATTERY_SOC_ENTITY_ID]),
            battery_status_entity_id=str(merged[CONF_BATTERY_STATUS_ENTITY_ID]),
            battery_power_entity_id=str(merged[CONF_BATTERY_POWER_ENTITY_ID]),
            battery_direction_source=battery_direction_source,
            battery_power_charging_positive=bool(
                merged[CONF_BATTERY_POWER_CHARGING_POSITIVE]
            ),
            battery_power_threshold_w=battery_power_threshold_w,
            battery_full_threshold=battery_full_threshold,
            charging_states=frozenset(
                str(state).lower() for state in merged[CONF_CHARGING_STATES]
            ),
            discharging_states=frozenset(
                str(state).lower() for state in merged[CONF_DISCHARGING_STATES]
            ),
        )
        config._validate_source_entities()
        return config

    def _validate_source_entities(self) -> None:
        if not self.enabled:
            return
        if self.source_type == "grid_flow" and not self.grid_entity_id:
            raise ConfigurationError("Choose a grid power sensor.")
        if self.source_type in {"production_consumption", "curtailed_production"}:
            if not self.production_entity_id or not self.consumption_entity_id:
                raise ConfigurationError(
                    "Choose both a solar power sensor and a home power sensor."
                )
        if self.source_type == "curtailed_production":
            if self.battery_policy != BATTERY_FULL_IDLE_FOR_PROBE:
                raise ConfigurationError(
                    "Zero-export systems require the “Full and idle” battery rule."
                )
        if self.battery_policy == BATTERY_DISABLED:
            return
        if self.battery_policy in {
            BATTERY_CHARGING_OR_SOC,
            BATTERY_FULL_IDLE_FOR_PROBE,
        } and not self.battery_soc_entity_id:
            raise ConfigurationError(
                "Choose a battery level sensor."
            )
        if self.battery_policy in {
            BATTERY_REQUIRE_CHARGING,
            BATTERY_CHARGING_OR_SOC,
            BATTERY_FULL_IDLE_FOR_PROBE,
        }:
            if (
                self.battery_direction_source == BATTERY_DIRECTION_STATUS
                and not self.battery_status_entity_id
            ):
                raise ConfigurationError(
                    "Choose a battery status entity."
                )
            if (
                self.battery_direction_source == BATTERY_DIRECTION_POWER
                and not self.battery_power_entity_id
            ):
                raise ConfigurationError(
                    "Choose a battery power sensor."
                )
