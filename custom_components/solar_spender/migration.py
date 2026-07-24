"""Pure configuration migrations for Solar Spender."""

from __future__ import annotations

from typing import Any


def without_legacy_utility(options: dict[str, Any]) -> dict[str, Any]:
    """Copy options while removing the retired per-load utility weight."""
    normalized = dict(options)
    normalized["loads"] = [
        {key: value for key, value in load.items() if key != "utility"}
        for load in normalized.get("loads", [])
    ]
    return normalized


def current_options(options: dict[str, Any]) -> dict[str, Any]:
    """Normalize retired fields and fail-safe removed binary-source configs."""
    normalized = without_legacy_utility(options)
    if normalized.get("source_type") == "binary":
        normalized["source_type"] = "production_consumption"
        normalized["enabled"] = False
    for key in (
        "binary_entity_id",
        "binary_on_delay_minutes",
        "binary_off_delay_minutes",
    ):
        normalized.pop(key, None)
    if "battery_direction_source" not in normalized:
        normalized["battery_direction_source"] = (
            "status" if normalized.get("battery_status_entity_id") else "power"
        )
    return normalized
