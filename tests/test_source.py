"""Regression tests for source-specific hysteresis."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components" / "solar_spender" / "source.py"
)
SPEC = importlib.util.spec_from_file_location("solar_spender_source", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
SOURCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SOURCE
SPEC.loader.exec_module(SOURCE)


class ObservableSurplusTests(unittest.TestCase):
    """Higher production-minus-consumption values are better."""

    def test_uses_higher_entry_and_lower_exit_thresholds(self) -> None:
        self.assertTrue(
            SOURCE.observable_surplus_available(
                was_available=False,
                headroom_w=300,
                entry_threshold_w=300,
                exit_threshold_w=100,
            )
        )
        self.assertTrue(
            SOURCE.observable_surplus_available(
                was_available=True,
                headroom_w=200,
                entry_threshold_w=300,
                exit_threshold_w=100,
            )
        )
        self.assertFalse(
            SOURCE.observable_surplus_available(
                was_available=True,
                headroom_w=100,
                entry_threshold_w=300,
                exit_threshold_w=100,
            )
        )


class ZeroExportOpportunityTests(unittest.TestCase):
    """Lower consumption-minus-production deficits are better."""

    def test_small_deficit_enters_and_larger_exit_prevents_chatter(self) -> None:
        entered = SOURCE.zero_export_opportunity_available(
            was_available=False,
            production_w=1000,
            consumption_w=1080,
            minimum_production_w=300,
            entry_deficit_w=100,
            exit_deficit_w=300,
        )
        retained = SOURCE.zero_export_opportunity_available(
            was_available=True,
            production_w=1000,
            consumption_w=1200,
            minimum_production_w=300,
            entry_deficit_w=100,
            exit_deficit_w=300,
        )
        exited = SOURCE.zero_export_opportunity_available(
            was_available=True,
            production_w=1000,
            consumption_w=1300,
            minimum_production_w=300,
            entry_deficit_w=100,
            exit_deficit_w=300,
        )

        self.assertTrue(entered.available)
        self.assertEqual(entered.deficit_w, 80)
        self.assertTrue(retained.available)
        self.assertFalse(exited.available)

    def test_minimum_production_rejects_nighttime_zero_deficit(self) -> None:
        decision = SOURCE.zero_export_opportunity_available(
            was_available=False,
            production_w=0,
            consumption_w=0,
            minimum_production_w=300,
            entry_deficit_w=100,
            exit_deficit_w=300,
        )

        self.assertFalse(decision.available)
