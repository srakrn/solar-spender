"""Pure high-level control decisions."""

from __future__ import annotations


def owned_load_shed_reason(
    *,
    has_owned_loads: bool,
    source_available: bool,
    battery_allowed: bool,
    battery_direction: str,
    probing: bool,
) -> str | None:
    """Return why an owned load must be shed, if any."""
    if not has_owned_loads:
        return None
    if not source_available:
        return "surplus unavailable"
    if battery_direction == "discharging":
        return "battery discharging; owned load is no longer solar-supported"
    if probing and not battery_allowed:
        return "battery gate closed during probe"
    return None


def effective_release_shortfall_w(
    *,
    source_shortfall_w: float,
    battery_direction: str,
    charging_positive_battery_w: float | None,
) -> float:
    """Use the stronger of overlapping source and battery shortfall signals."""
    battery_shortfall_w = (
        max(0.0, -(charging_positive_battery_w or 0.0))
        if battery_direction == "discharging"
        else 0.0
    )
    return max(0.0, source_shortfall_w, battery_shortfall_w)
