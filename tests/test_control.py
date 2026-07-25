"""Regression tests for high-level owned-load shedding decisions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "solar_spender"
    / "control.py"
)
SPEC = importlib.util.spec_from_file_location("solar_spender_control", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
CONTROL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CONTROL
SPEC.loader.exec_module(CONTROL)


class OwnedLoadSheddingTests(unittest.TestCase):
    """Source loss and battery discharge override a closed activation gate."""

    def test_source_loss_sheds_even_when_battery_gate_is_closed(self) -> None:
        reason = CONTROL.owned_load_shed_reason(
            has_owned_loads=True,
            source_available=False,
            battery_allowed=False,
            battery_direction="unknown",
            probing=False,
        )

        self.assertEqual(reason, "not enough spare solar")

    def test_battery_discharge_sheds_even_if_source_is_still_latched(self) -> None:
        reason = CONTROL.owned_load_shed_reason(
            has_owned_loads=True,
            source_available=True,
            battery_allowed=False,
            battery_direction="discharging",
            probing=False,
        )

        self.assertEqual(reason, "battery is discharging")

    def test_idle_gate_closure_does_not_abruptly_shed_running_load(self) -> None:
        reason = CONTROL.owned_load_shed_reason(
            has_owned_loads=True,
            source_available=True,
            battery_allowed=False,
            battery_direction="idle",
            probing=False,
        )

        self.assertIsNone(reason)

    def test_unknown_battery_during_probe_rolls_probe_back(self) -> None:
        reason = CONTROL.owned_load_shed_reason(
            has_owned_loads=True,
            source_available=True,
            battery_allowed=False,
            battery_direction="unknown",
            probing=True,
        )

        self.assertEqual(reason, "battery blocked the test")

    def test_battery_discharge_magnitude_can_size_release(self) -> None:
        self.assertEqual(
            CONTROL.effective_release_shortfall_w(
                source_shortfall_w=100,
                battery_direction="discharging",
                charging_positive_battery_w=-450,
            ),
            450,
        )

    def test_overlapping_shortfalls_are_not_added_together(self) -> None:
        self.assertEqual(
            CONTROL.effective_release_shortfall_w(
                source_shortfall_w=300,
                battery_direction="discharging",
                charging_positive_battery_w=-450,
            ),
            450,
        )
