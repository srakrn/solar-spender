"""Regression tests for conservative restart lease recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "solar_spender"
    / "runtime.py"
)
SPEC = importlib.util.spec_from_file_location("solar_spender_runtime", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNTIME = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNTIME
SPEC.loader.exec_module(RUNTIME)


class RuntimeRecoveryTests(unittest.TestCase):
    """Only unambiguous, profile-matched leases may survive restart."""

    def test_timestamp_must_include_timezone(self) -> None:
        aware = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)

        self.assertEqual(
            RUNTIME.parse_aware_datetime(aware.isoformat()),
            aware,
        )
        self.assertIsNone(RUNTIME.parse_aware_datetime("2026-07-25T12:00:00"))
        self.assertIsNone(RUNTIME.parse_aware_datetime("not-a-timestamp"))

    def test_pause_remaining_time_expires_without_going_negative(self) -> None:
        now = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)

        self.assertEqual(
            RUNTIME.pause_remaining_seconds(now + timedelta(minutes=5), now),
            300,
        )
        self.assertEqual(
            RUNTIME.pause_remaining_seconds(now - timedelta(seconds=1), now),
            0,
        )
        self.assertEqual(RUNTIME.pause_remaining_seconds(None, now), 0)

    def test_current_climate_must_match_every_commanded_field(self) -> None:
        profile = {
            "hvac_mode": "dry",
            "temperature": 25,
            "fan_mode": "quiet",
        }

        self.assertTrue(
            RUNTIME.climate_matches_commanded_profile(
                "dry",
                {"temperature": 25, "fan_mode": "quiet"},
                profile,
            )
        )
        self.assertFalse(
            RUNTIME.climate_matches_commanded_profile(
                "cool",
                {"temperature": 25, "fan_mode": "quiet"},
                profile,
            )
        )
        self.assertFalse(
            RUNTIME.climate_matches_commanded_profile(
                "dry",
                {"temperature": 24, "fan_mode": "quiet"},
                profile,
            )
        )

    def test_changed_load_configuration_invalidates_old_lease(self) -> None:
        self.assertTrue(
            RUNTIME.load_definition_matches_profile(
                hvac_mode="dry",
                temperature=None,
                fan_mode=None,
                commanded_profile={"hvac_mode": "dry"},
            )
        )
        self.assertFalse(
            RUNTIME.load_definition_matches_profile(
                hvac_mode="cool",
                temperature=None,
                fan_mode=None,
                commanded_profile={"hvac_mode": "dry"},
            )
        )
