"""Regression tests for simple priority-only load ordering."""

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
    """Priority is the only preference, with stable list-order ties."""

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
