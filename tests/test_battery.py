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


class WasteHeadroomTests(unittest.TestCase):
    """Battery charging consumes solar before it becomes waste headroom."""

    def test_charging_battery_removes_waste_headroom(self) -> None:
        self.assertFalse(
            BATTERY.waste_headroom_available(
                source_valid=True,
                surplus_available=True,
                battery_configured=True,
                battery_allowed=True,
                battery_direction="charging",
            )
        )

    def test_full_idle_battery_allows_waste_headroom(self) -> None:
        self.assertTrue(
            BATTERY.waste_headroom_available(
                source_valid=True,
                surplus_available=True,
                battery_configured=True,
                battery_allowed=True,
                battery_direction="idle",
            )
        )

    def test_unconfigured_battery_does_not_hide_source_headroom(self) -> None:
        self.assertTrue(
            BATTERY.waste_headroom_available(
                source_valid=True,
                surplus_available=True,
                battery_configured=False,
                battery_allowed=True,
                battery_direction="not_configured",
            )
        )
