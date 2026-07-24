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


class FeedbackAssessmentTests(unittest.TestCase):
    """Verify spaced fresh votes produce a strict-majority decision."""

    def test_three_fresh_votes_require_two_yes_results(self) -> None:
        now = datetime(2026, 7, 24, 12, 5, tzinfo=UTC)
        assessment = FEEDBACK.FeedbackAssessment(
            action="activation",
            load_entity_id="climate.ac_a",
            next_not_before=now,
            required_entities=frozenset({"binary_sensor.headroom"}),
            sample_count=3,
            sample_interval=timedelta(minutes=5),
        )

        assessment.record_vote(supported=True, accepted_at=now)
        self.assertFalse(assessment.complete)
        self.assertEqual(
            assessment.next_not_before,
            now + timedelta(minutes=5),
        )
        assessment.record_vote(
            supported=False,
            accepted_at=now + timedelta(minutes=5),
        )
        assessment.record_vote(
            supported=True,
            accepted_at=now + timedelta(minutes=10),
        )

        self.assertTrue(assessment.complete)
        self.assertTrue(assessment.supported)
        self.assertEqual(assessment.required_yes_votes, 2)

    def test_cached_report_cannot_satisfy_next_vote(self) -> None:
        now = datetime(2026, 7, 24, 12, 5, tzinfo=UTC)
        assessment = FEEDBACK.FeedbackAssessment(
            action="release",
            load_entity_id="climate.ac_a",
            next_not_before=now,
            required_entities=frozenset({"sensor.grid"}),
            sample_count=3,
            sample_interval=timedelta(minutes=5),
        )
        assessment.record_vote(supported=True, accepted_at=now)

        self.assertEqual(
            assessment.pending_entities({"sensor.grid": now}),
            frozenset({"sensor.grid"}),
        )


class EventHistoryTests(unittest.TestCase):
    """Verify repeated reconciliation reasons do not bury useful history."""

    def test_consecutive_duplicate_updates_timestamp_without_adding_row(self) -> None:
        first = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
        second = first + timedelta(minutes=5)
        history = FEEDBACK.append_bounded_event(
            [],
            message="disabled",
            at=first,
        )

        history = FEEDBACK.append_bounded_event(
            history,
            message="disabled",
            at=second,
        )

        self.assertEqual(
            history,
            [{"at": second.isoformat(), "message": "disabled"}],
        )

    def test_nonconsecutive_duplicate_remains_visible(self) -> None:
        now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
        history: list[dict[str, str]] = []
        for index, message in enumerate(("disabled", "enabled", "disabled")):
            history = FEEDBACK.append_bounded_event(
                history,
                message=message,
                at=now + timedelta(minutes=index),
            )

        self.assertEqual(
            [event["message"] for event in history],
            ["disabled", "enabled", "disabled"],
        )


class LearnedDrawEstimateTests(unittest.TestCase):
    """Verify learned draw hints remain conservative and expire."""

    def test_requires_three_samples_and_never_undercuts_configured_draw(self) -> None:
        now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
        estimate = FEEDBACK.LearnedDrawEstimate(80, 2, now)
        self.assertEqual(
            estimate.conservative_value(configured_w=100, now=now),
            100,
        )

        estimate = estimate.update(160, updated_at=now)
        self.assertGreaterEqual(
            estimate.conservative_value(configured_w=100, now=now),
            100,
        )

    def test_expired_hint_falls_back_to_configured_draw(self) -> None:
        now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
        estimate = FEEDBACK.LearnedDrawEstimate(
            150,
            4,
            now - timedelta(days=31),
        )

        self.assertEqual(
            estimate.conservative_value(configured_w=100, now=now),
            100,
        )


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

    def test_failed_combination_creates_temporary_upper_bound(self) -> None:
        memory = FEEDBACK.CycleMemory()
        draws = {"climate.a": 100.0, "climate.b": 50.0}
        memory.record_combination(
            frozenset({"climate.a"}),
            supported=True,
            expected_draws_w=draws,
        )
        memory.record_combination(
            frozenset({"climate.a", "climate.b"}),
            supported=False,
            expected_draws_w=draws,
        )

        self.assertEqual(memory.lower_supported_w, 100)
        self.assertEqual(memory.upper_unsupported_w, 150)
        self.assertTrue(memory.fits_upper_bound(50))
        self.assertFalse(memory.fits_upper_bound(150))
        self.assertTrue(
            memory.combination_is_blocked(
                frozenset({"climate.a", "climate.b"})
            )
        )
        self.assertFalse(
            memory.combination_is_blocked(frozenset({"climate.b"}))
        )

    def test_newer_failure_invalidates_an_old_supported_bound(self) -> None:
        memory = FEEDBACK.CycleMemory()
        draws = {"climate.a": 100.0}
        memory.record_combination(
            frozenset({"climate.a"}),
            supported=True,
            expected_draws_w=draws,
        )
        memory.record_combination(
            frozenset({"climate.a"}),
            supported=False,
            expected_draws_w=draws,
        )

        self.assertIsNone(memory.lower_supported_w)
        self.assertEqual(memory.upper_unsupported_w, 100)


if __name__ == "__main__":
    unittest.main()
