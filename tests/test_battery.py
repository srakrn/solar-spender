"""Regression tests for battery power direction."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components" / "solar_spender" / "battery.py"
)
SPEC = importlib.util.spec_from_file_location("solar_spender_battery", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BATTERY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BATTERY
SPEC.loader.exec_module(BATTERY)


class BatteryPowerDirectionTests(unittest.TestCase):
    """Power sign and threshold produce charging, idle, or discharging."""

    def test_positive_can_mean_charging(self) -> None:
        result = BATTERY.direction_from_power(
            250,
            charging_positive=True,
            threshold_w=50,
        )

        self.assertEqual(result.direction, "charging")
        self.assertEqual(result.charging_positive_w, 250)

    def test_negative_can_mean_charging(self) -> None:
        result = BATTERY.direction_from_power(
            -250,
            charging_positive=False,
            threshold_w=50,
        )

        self.assertEqual(result.direction, "charging")
        self.assertEqual(result.charging_positive_w, 250)

    def test_threshold_is_an_idle_deadband(self) -> None:
        for power_w in (-50, 0, 50):
            with self.subTest(power_w=power_w):
                result = BATTERY.direction_from_power(
                    power_w,
                    charging_positive=True,
                    threshold_w=50,
                )
                self.assertEqual(result.direction, "idle")

    def test_opposite_flow_is_discharging(self) -> None:
        result = BATTERY.direction_from_power(
            -51,
            charging_positive=True,
            threshold_w=50,
        )

        self.assertEqual(result.direction, "discharging")
