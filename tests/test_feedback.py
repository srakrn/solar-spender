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


class BinaryDebounceTests(unittest.TestCase):
    """Verify entry and exit require continuous stable binary states."""

    def test_on_delay_blocks_activation_until_deadline(self) -> None:
        now = datetime(2026, 7, 24, 12, 4, tzinfo=UTC)
        decision = FEEDBACK.debounce_binary_source(
            surplus_available=False,
            raw_on=True,
            raw_changed_at=now - timedelta(minutes=4),
            now=now,
            on_delay_minutes=5,
            off_delay_minutes=2,
        )

        self.assertFalse(decision.surplus_available)
        self.assertEqual(decision.pending_until, now + timedelta(minutes=1))

        completed = FEEDBACK.debounce_binary_source(
            surplus_available=False,
            raw_on=True,
            raw_changed_at=now - timedelta(minutes=5),
            now=now,
            on_delay_minutes=5,
            off_delay_minutes=2,
        )
        self.assertTrue(completed.surplus_available)
        self.assertIsNone(completed.pending_until)

    def test_off_delay_keeps_surplus_latched_until_deadline(self) -> None:
        now = datetime(2026, 7, 24, 12, 1, tzinfo=UTC)
        decision = FEEDBACK.debounce_binary_source(
            surplus_available=True,
            raw_on=False,
            raw_changed_at=now - timedelta(minutes=1),
            now=now,
            on_delay_minutes=5,
            off_delay_minutes=3,
        )

        self.assertTrue(decision.surplus_available)
        self.assertEqual(decision.pending_until, now + timedelta(minutes=2))

    def test_bounce_uses_latest_raw_transition_time(self) -> None:
        now = datetime(2026, 7, 24, 12, 10, tzinfo=UTC)
        decision = FEEDBACK.debounce_binary_source(
            surplus_available=False,
            raw_on=True,
            raw_changed_at=now - timedelta(seconds=10),
            now=now,
            on_delay_minutes=5,
            off_delay_minutes=3,
        )

        self.assertEqual(
            decision.pending_until,
            now + timedelta(minutes=4, seconds=50),
        )


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
