"""Pure source hysteresis calculations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ZeroExportDecision:
    """A zero-export opportunity decision and its observable deficit."""

    available: bool
    deficit_w: float


def observable_surplus_available(
    *,
    was_available: bool,
    headroom_w: float,
    entry_threshold_w: float,
    exit_threshold_w: float,
) -> bool:
    """Latch observable headroom between a higher entry and lower exit."""
    if was_available:
        return headroom_w > exit_threshold_w
    return headroom_w >= entry_threshold_w


def zero_export_opportunity_available(
    *,
    was_available: bool,
    production_w: float,
    consumption_w: float,
    minimum_production_w: float,
    entry_deficit_w: float,
    exit_deficit_w: float,
) -> ZeroExportDecision:
    """Latch a test opportunity between a lower entry and higher exit deficit."""
    deficit_w = consumption_w - production_w
    if production_w < minimum_production_w:
        return ZeroExportDecision(False, deficit_w)
    if was_available:
        return ZeroExportDecision(deficit_w < exit_deficit_w, deficit_w)
    return ZeroExportDecision(deficit_w <= entry_deficit_w, deficit_w)
