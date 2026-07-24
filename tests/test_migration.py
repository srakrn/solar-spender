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


class UtilityMigrationTests(unittest.TestCase):
    """The removed preference field must not survive a save or upgrade."""

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
