"""Regression tests for configuration migrations."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "solar_spender"
    / "migration.py"
)
SPEC = importlib.util.spec_from_file_location("solar_spender_migration", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MIGRATION
SPEC.loader.exec_module(MIGRATION)


class ConfigurationMigrationTests(unittest.TestCase):
    """Retired configuration must migrate conservatively."""

    def test_removes_utility_without_changing_load_order_or_other_fields(self) -> None:
        options = {
            "enabled": True,
            "loads": [
                {"entity_id": "climate.a", "priority": 20, "utility": 1},
                {"entity_id": "climate.b", "priority": 10, "utility": 99},
            ],
        }

        migrated = MIGRATION.without_legacy_utility(options)

        self.assertEqual(
            migrated["loads"],
            [
                {"entity_id": "climate.a", "priority": 20},
                {"entity_id": "climate.b", "priority": 10},
            ],
        )
        self.assertTrue(migrated["enabled"])
        self.assertIn("utility", options["loads"][0])

    def test_removed_binary_source_is_disabled_and_requires_reconfiguration(
        self,
    ) -> None:
        options = {
            "enabled": True,
            "source_type": "binary",
            "binary_entity_id": "binary_sensor.old_headroom",
            "binary_on_delay_minutes": 5,
            "binary_off_delay_minutes": 2,
            "loads": [],
        }

        migrated = MIGRATION.current_options(options)

        self.assertFalse(migrated["enabled"])
        self.assertEqual(migrated["source_type"], "production_consumption")
        self.assertNotIn("binary_entity_id", migrated)
        self.assertNotIn("binary_on_delay_minutes", migrated)
        self.assertNotIn("binary_off_delay_minutes", migrated)

    def test_existing_status_battery_keeps_status_direction(self) -> None:
        migrated = MIGRATION.current_options(
            {
                "battery_status_entity_id": "sensor.battery_mode",
                "loads": [],
            }
        )

        self.assertEqual(migrated["battery_direction_source"], "status")

    def test_zero_export_thresholds_migrate_to_deficit_hysteresis(self) -> None:
        migrated = MIGRATION.version_4_options(
            {
                "source_type": "curtailed_production",
                "entry_threshold_w": 300,
                "exit_threshold_w": 100,
                "loads": [],
            }
        )

        self.assertEqual(migrated["minimum_production_w"], 300)
        self.assertEqual(migrated["entry_threshold_w"], 100)
        self.assertEqual(migrated["exit_threshold_w"], 300)

    def test_fixed_feedback_spacing_migrates_to_timeout_and_age_limits(self) -> None:
        migrated = MIGRATION.version_5_options(
            {
                "feedback_sample_interval_minutes": 5,
                "feedback_sample_count": 3,
                "loads": [],
            }
        )

        self.assertNotIn("feedback_sample_interval_minutes", migrated)
        self.assertEqual(migrated["feedback_timeout_minutes"], 15)
        self.assertEqual(migrated["input_max_age_minutes"], 15)
        self.assertEqual(migrated["feedback_sample_count"], 3)
