"""Constants for Solar Spender."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "solar_spender"
PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
]

PANEL_VERSION: Final = "0.7.0"
PANEL_URL: Final = "/solar_spender/solar-spender-panel.js"
PANEL_MODULE_URL: Final = f"{PANEL_URL}?v={PANEL_VERSION}"
PANEL_COMPONENT: Final = "solar-spender-panel"
PANEL_PATH: Final = "solar-spender"

DATA_CONTROLLER: Final = "controller"
DATA_PANEL_REGISTERED: Final = "panel_registered"
RUNTIME_OPTIONS_UPDATED: Final = "solar_spender_runtime_options_updated"
CONTROLLER_STATUS_UPDATED: Final = "solar_spender_controller_status_updated"

CONF_ENABLED: Final = "enabled"
CONF_SOURCE_TYPE: Final = "source_type"
CONF_GRID_ENTITY_ID: Final = "grid_entity_id"
CONF_GRID_EXPORT_POSITIVE: Final = "grid_export_positive"
CONF_PRODUCTION_ENTITY_ID: Final = "production_entity_id"
CONF_CONSUMPTION_ENTITY_ID: Final = "consumption_entity_id"
CONF_ENTRY_THRESHOLD_W: Final = "entry_threshold_w"
CONF_EXIT_THRESHOLD_W: Final = "exit_threshold_w"
CONF_MINIMUM_PRODUCTION_W: Final = "minimum_production_w"
CONF_EXPORT_RESERVE_W: Final = "export_reserve_w"
CONF_SETTLING_SECONDS: Final = "settling_seconds"
CONF_FEEDBACK_SAMPLE_COUNT: Final = "feedback_sample_count"
CONF_FEEDBACK_SAMPLE_INTERVAL_MINUTES: Final = "feedback_sample_interval_minutes"
CONF_NEXT_LOAD_DELAY_MINUTES: Final = "next_load_delay_minutes"
CONF_LOADS: Final = "loads"
CONF_BATTERY_POLICY: Final = "battery_policy"
CONF_BATTERY_SOC_ENTITY_ID: Final = "battery_soc_entity_id"
CONF_BATTERY_STATUS_ENTITY_ID: Final = "battery_status_entity_id"
CONF_BATTERY_POWER_ENTITY_ID: Final = "battery_power_entity_id"
CONF_BATTERY_DIRECTION_SOURCE: Final = "battery_direction_source"
CONF_BATTERY_POWER_CHARGING_POSITIVE: Final = "battery_power_charging_positive"
CONF_BATTERY_POWER_THRESHOLD_W: Final = "battery_power_threshold_w"
CONF_BATTERY_FULL_THRESHOLD: Final = "battery_full_threshold"
CONF_CHARGING_STATES: Final = "charging_states"
CONF_DISCHARGING_STATES: Final = "discharging_states"

SOURCE_GRID: Final = "grid_flow"
SOURCE_PRODUCTION: Final = "production_consumption"
SOURCE_CURTAILED: Final = "curtailed_production"
SOURCE_TYPES: Final = {
    SOURCE_GRID,
    SOURCE_PRODUCTION,
    SOURCE_CURTAILED,
}

BATTERY_DISABLED: Final = "disabled"
BATTERY_REQUIRE_CHARGING: Final = "require_charging"
BATTERY_CHARGING_OR_SOC: Final = "charging_or_soc"
BATTERY_FULL_IDLE_FOR_PROBE: Final = "full_idle_for_probe"
BATTERY_POLICIES: Final = {
    BATTERY_DISABLED,
    BATTERY_REQUIRE_CHARGING,
    BATTERY_CHARGING_OR_SOC,
    BATTERY_FULL_IDLE_FOR_PROBE,
}

BATTERY_DIRECTION_STATUS: Final = "status"
BATTERY_DIRECTION_POWER: Final = "power"
BATTERY_DIRECTION_SOURCES: Final = {
    BATTERY_DIRECTION_STATUS,
    BATTERY_DIRECTION_POWER,
}

DEFAULT_OPTIONS: Final = {
    CONF_ENABLED: False,
    CONF_SOURCE_TYPE: SOURCE_PRODUCTION,
    CONF_GRID_ENTITY_ID: "",
    CONF_GRID_EXPORT_POSITIVE: True,
    CONF_PRODUCTION_ENTITY_ID: "",
    CONF_CONSUMPTION_ENTITY_ID: "",
    CONF_ENTRY_THRESHOLD_W: 300.0,
    CONF_EXIT_THRESHOLD_W: 100.0,
    CONF_MINIMUM_PRODUCTION_W: 300.0,
    CONF_EXPORT_RESERVE_W: 0.0,
    CONF_SETTLING_SECONDS: 300,
    CONF_FEEDBACK_SAMPLE_COUNT: 3,
    CONF_FEEDBACK_SAMPLE_INTERVAL_MINUTES: 5.0,
    CONF_NEXT_LOAD_DELAY_MINUTES: 5.0,
    CONF_LOADS: [],
    CONF_BATTERY_POLICY: BATTERY_DISABLED,
    CONF_BATTERY_SOC_ENTITY_ID: "",
    CONF_BATTERY_STATUS_ENTITY_ID: "",
    CONF_BATTERY_POWER_ENTITY_ID: "",
    CONF_BATTERY_DIRECTION_SOURCE: BATTERY_DIRECTION_POWER,
    CONF_BATTERY_POWER_CHARGING_POSITIVE: True,
    CONF_BATTERY_POWER_THRESHOLD_W: 50.0,
    CONF_BATTERY_FULL_THRESHOLD: 98.0,
    CONF_CHARGING_STATES: ["charging"],
    CONF_DISCHARGING_STATES: ["discharging"],
}

STATE_DISABLED: Final = "disabled"
STATE_PAUSED: Final = "paused"
STATE_MONITORING: Final = "monitoring"
STATE_SPENDING: Final = "spending"
STATE_SHEDDING: Final = "shedding"
STATE_PROBING: Final = "probing"
STATE_BLOCKED_BATTERY: Final = "blocked_battery"
STATE_WAITING_FEEDBACK: Final = "waiting_feedback"
