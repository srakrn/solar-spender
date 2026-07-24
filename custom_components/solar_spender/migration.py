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
