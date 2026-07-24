"""Deterministic, user-controlled load ordering."""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
