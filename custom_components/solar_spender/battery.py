"""Pure battery-flow interpretation."""

from __future__ import annotations

from dataclasses import dataclass

BATTERY_CHARGING = "charging"
BATTERY_DISCHARGING = "discharging"
BATTERY_IDLE = "idle"


@dataclass(frozen=True, slots=True)
class BatteryPowerDirection:
    """Normalized battery power and its thresholded direction."""

    direction: str
    charging_positive_w: float


def direction_from_power(
    power_w: float,
    *,
    charging_positive: bool,
    threshold_w: float,
) -> BatteryPowerDirection:
    """Interpret battery power using a symmetric idle deadband."""
    normalized_w = power_w if charging_positive else -power_w
    if normalized_w > threshold_w:
        direction = BATTERY_CHARGING
    elif normalized_w < -threshold_w:
        direction = BATTERY_DISCHARGING
    else:
        direction = BATTERY_IDLE
    return BatteryPowerDirection(direction, normalized_w)
