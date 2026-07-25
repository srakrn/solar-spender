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
        "feedback_sample_interval_minutes",
    ):
        normalized.pop(key, None)
    if "battery_direction_source" not in normalized:
        normalized["battery_direction_source"] = (
            "status" if normalized.get("battery_status_entity_id") else "power"
        )
    return normalized


def version_4_options(options: dict[str, Any]) -> dict[str, Any]:
    """Separate zero-export minimum production from deficit hysteresis."""
    normalized = current_options(options)
    if normalized.get("source_type") == "curtailed_production":
        old_entry = float(normalized.get("entry_threshold_w", 300))
        normalized.setdefault("minimum_production_w", max(old_entry, 0))
        # The old fields represented production thresholds, not deficits, so
        # they cannot be reinterpreted safely. Start from conservative,
        # documented deficit defaults and preserve only the production guard.
        normalized["entry_threshold_w"] = 100
        normalized["exit_threshold_w"] = 300
    else:
        normalized.setdefault("minimum_production_w", 300)
    return normalized


def version_5_options(options: dict[str, Any]) -> dict[str, Any]:
    """Replace fixed report spacing with timeout and input-age limits."""
    normalized = current_options(options)
    normalized.pop("feedback_sample_interval_minutes", None)
    normalized.setdefault("feedback_timeout_minutes", 15.0)
    normalized.setdefault("input_max_age_minutes", 15.0)
    return normalized
