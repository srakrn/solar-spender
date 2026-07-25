"""Regression tests for deterministic activation and deficit-aware release."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "solar_spender"
    / "selection.py"
)
SPEC = importlib.util.spec_from_file_location("solar_spender_selection", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
SELECTION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SELECTION
SPEC.loader.exec_module(SELECTION)


class PrioritySelectionTests(unittest.TestCase):
    """Priority controls activation and breaks release-selection ties."""

    def test_activation_uses_lowest_priority_number(self) -> None:
        loads = [
            {"name": "first", "priority": 100, "draw": 50},
            {"name": "preferred", "priority": 10, "draw": 2000},
        ]

        selected = SELECTION.first_to_activate(
            loads,
            lambda load: load["priority"],
        )

        self.assertEqual(selected["name"], "preferred")

    def test_activation_preserves_configuration_order_for_ties(self) -> None:
        loads = [
            {"name": "first", "priority": 10},
            {"name": "second", "priority": 10},
        ]

        selected = SELECTION.first_to_activate(
            loads,
            lambda load: load["priority"],
        )

        self.assertEqual(selected["name"], "first")

    def test_release_uses_highest_priority_number(self) -> None:
        loads = [
            {"name": "keep", "priority": 10},
            {"name": "release", "priority": 100},
        ]

        selected = SELECTION.first_to_release(
            loads,
            lambda load: load["priority"],
        )

        self.assertEqual(selected["name"], "release")

    def test_release_uses_smallest_load_that_covers_shortfall(self) -> None:
        loads = [
            {"name": "large", "priority": 100, "draw": 900},
            {"name": "best", "priority": 10, "draw": 500},
            {"name": "small", "priority": 200, "draw": 200},
        ]

        selected = SELECTION.best_to_release_for_shortfall(
            loads,
            draw_w=lambda load: load["draw"],
            shortfall_w=450,
            priority=lambda load: load["priority"],
        )

        self.assertEqual(selected["name"], "best")

    def test_release_uses_largest_contributor_when_none_covers_shortfall(self) -> None:
        loads = [
            {"name": "small", "priority": 200, "draw": 200},
            {"name": "large", "priority": 10, "draw": 500},
        ]

        selected = SELECTION.best_to_release_for_shortfall(
            loads,
            draw_w=lambda load: load["draw"],
            shortfall_w=700,
            priority=lambda load: load["priority"],
        )

        self.assertEqual(selected["name"], "large")

    def test_release_falls_back_to_priority_without_a_measured_shortfall(self) -> None:
        loads = [
            {"name": "keep", "priority": 10, "draw": 500},
            {"name": "release", "priority": 100, "draw": None},
        ]

        selected = SELECTION.best_to_release_for_shortfall(
            loads,
            draw_w=lambda load: load["draw"],
            shortfall_w=0,
            priority=lambda load: load["priority"],
        )

        self.assertEqual(selected["name"], "release")
