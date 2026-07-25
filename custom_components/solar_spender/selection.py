"""Deterministic, user-controlled load ordering."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from math import isfinite
from typing import TypeVar


_T = TypeVar("_T")


def first_to_activate(
    candidates: Sequence[_T],
    priority: Callable[[_T], int],
) -> _T | None:
    """Choose the lowest priority number, preserving list order for ties."""
    return min(candidates, key=priority, default=None)


def first_to_release(
    candidates: Sequence[_T],
    priority: Callable[[_T], int],
) -> _T | None:
    """Choose the highest priority number, preserving list order for ties."""
    return max(candidates, key=priority, default=None)


def best_to_release_for_shortfall(
    candidates: Sequence[_T],
    *,
    draw_w: Callable[[_T], float | None],
    shortfall_w: float,
    priority: Callable[[_T], int],
) -> _T | None:
    """Choose one load that closes a measured shortfall with least overshoot."""
    measured = [
        (candidate, value)
        for candidate in candidates
        if (value := draw_w(candidate)) is not None
        and isfinite(value)
        and value > 0
    ]
    if shortfall_w > 0 and measured:
        covering = [
            (candidate, value)
            for candidate, value in measured
            if value >= shortfall_w
        ]
        if covering:
            return min(
                covering,
                key=lambda item: (item[1], -priority(item[0])),
            )[0]
        return max(
            measured,
            key=lambda item: (item[1], priority(item[0])),
        )[0]
    return first_to_release(candidates, priority)
