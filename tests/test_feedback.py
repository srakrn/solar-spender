"""Regression tests for fresh-report barriers without requiring Home Assistant."""

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
    / "feedback.py"
)
SPEC = importlib.util.spec_from_file_location("solar_spender_feedback", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
FEEDBACK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FEEDBACK
SPEC.loader.exec_module(FEEDBACK)


class FeedbackBarrierTests(unittest.TestCase):
    """Verify cached measurements never satisfy a post-action barrier."""

    def test_requires_every_entity_to_report_after_settling(self) -> None:
        not_before = datetime(2026, 7, 24, 12, 2, tzinfo=UTC)
        barrier = FEEDBACK.FeedbackBarrier(
            action="activation",
            load_entity_id="climate.second_ac",
            not_before=not_before,
            required_entities=frozenset(
                {"binary_sensor.headroom", "sensor.battery_soc"}
            ),
        )
        reports = {
            "binary_sensor.headroom": not_before - timedelta(minutes=3),
            "sensor.battery_soc": not_before - timedelta(minutes=1),
        }

        self.assertFalse(barrier.is_ready(reports))
        reports["binary_sensor.headroom"] = not_before + timedelta(minutes=3)
        self.assertEqual(
            barrier.pending_entities(reports),
            frozenset({"sensor.battery_soc"}),
        )
        reports["sensor.battery_soc"] = not_before
        self.assertTrue(barrier.is_ready(reports))


class CycleMemoryTests(unittest.TestCase):
    """Verify unsupported loads cannot churn within one solar opportunity."""

    def test_block_survives_release_while_surplus_remains(self) -> None:
        memory = FEEDBACK.CycleMemory()
        memory.mark_unsupported("climate.second_ac")

        self.assertFalse(
            memory.reset_if_cycle_ended(
                owned_loads=0,
                activation_pending=False,
                surplus_available=True,
                feedback_waiting=False,
            )
        )
        self.assertIn("climate.second_ac", memory.blocked_loads)

    def test_block_clears_after_fresh_feedback_ends_cycle(self) -> None:
        memory = FEEDBACK.CycleMemory({"climate.second_ac"})

        self.assertTrue(
            memory.reset_if_cycle_ended(
                owned_loads=0,
                activation_pending=False,
                surplus_available=False,
                feedback_waiting=False,
            )
        )
        self.assertFalse(memory.blocked_loads)


if __name__ == "__main__":
    unittest.main()
