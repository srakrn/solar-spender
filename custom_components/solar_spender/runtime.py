"""Pure helpers for conservative runtime-state recovery."""

from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping
from typing import Any


def parse_aware_datetime(value: object) -> datetime | None:
    """Parse one timezone-aware persisted timestamp."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def climate_matches_commanded_profile(
    state: str,
    attributes: Mapping[str, Any],
    commanded_profile: dict[str, Any],
) -> bool:
    """Return whether a climate state still matches every persisted command."""
    commanded_mode = commanded_profile.get("hvac_mode")
    if commanded_mode is not None and state != commanded_mode:
        return False
    commanded_temperature = commanded_profile.get("temperature")
    if commanded_temperature is not None:
        actual_temperature = attributes.get("temperature")
        if actual_temperature is None:
            return False
        try:
            if abs(float(actual_temperature) - float(commanded_temperature)) > 0.1:
                return False
        except (TypeError, ValueError):
            return False
    commanded_fan_mode = commanded_profile.get("fan_mode")
    return (
        commanded_fan_mode is None
        or attributes.get("fan_mode") == commanded_fan_mode
    )


def load_definition_matches_profile(
    *,
    hvac_mode: str | None,
    temperature: float | None,
    fan_mode: str | None,
    commanded_profile: dict[str, Any],
) -> bool:
    """Reject a persisted lease when its configured command changed."""
    expected = {
        key: value
        for key, value in {
            "hvac_mode": hvac_mode,
            "temperature": temperature,
            "fan_mode": fan_mode,
        }.items()
        if value is not None
    }
    return expected == commanded_profile
