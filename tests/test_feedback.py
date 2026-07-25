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
    """Verify distinct fresh votes produce a strict-majority decision."""

    def test_three_fresh_votes_require_two_yes_results(self) -> None:
        now = datetime(2026, 7, 24, 12, 5, tzinfo=UTC)
        assessment = FEEDBACK.FeedbackAssessment(
            action="activation",
            load_entity_id="climate.ac_a",
            next_not_before=now,
            deadline=now + timedelta(minutes=15),
            required_entities=frozenset({"binary_sensor.headroom"}),
            sample_count=3,
        )

        reports = {"binary_sensor.headroom": now}
        assessment.record_vote(supported=True, reports=reports)
        self.assertFalse(assessment.complete)
        reports["binary_sensor.headroom"] = now + timedelta(seconds=7)
        assessment.record_vote(
            supported=False,
            reports=reports,
        )
        reports["binary_sensor.headroom"] = now + timedelta(minutes=11)
        assessment.record_vote(
            supported=True,
            reports=reports,
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
            deadline=now + timedelta(minutes=15),
            required_entities=frozenset({"sensor.grid"}),
            sample_count=3,
        )
        assessment.record_vote(
            supported=True,
            reports={"sensor.grid": now},
        )

        self.assertEqual(
            assessment.pending_entities({"sensor.grid": now}),
            frozenset({"sensor.grid"}),
        )

    def test_all_entities_must_report_again_for_each_vote(self) -> None:
        now = datetime(2026, 7, 24, 12, 5, tzinfo=UTC)
        assessment = FEEDBACK.FeedbackAssessment(
            action="activation",
            load_entity_id="climate.ac_a",
            next_not_before=now,
            deadline=now + timedelta(minutes=15),
            required_entities=frozenset({"sensor.solar", "sensor.home"}),
            sample_count=3,
        )
        reports = {"sensor.solar": now, "sensor.home": now}
        assessment.record_vote(supported=True, reports=reports)
        reports["sensor.solar"] = now + timedelta(seconds=1)

        self.assertEqual(
            assessment.pending_entities(reports),
            frozenset({"sensor.home"}),
        )

    def test_incomplete_assessment_times_out_at_deadline(self) -> None:
        now = datetime(2026, 7, 24, 12, 5, tzinfo=UTC)
        deadline = now + timedelta(minutes=15)
        assessment = FEEDBACK.FeedbackAssessment(
            action="activation",
            load_entity_id="climate.ac_a",
            next_not_before=now,
            deadline=deadline,
            required_entities=frozenset({"sensor.grid"}),
            sample_count=3,
        )

        self.assertFalse(assessment.timed_out(deadline - timedelta(seconds=1)))
        self.assertTrue(assessment.timed_out(deadline))


class InputFreshnessTests(unittest.TestCase):
    """Verify maximum-age boundaries fail closed."""

    def test_report_is_fresh_through_maximum_age_boundary(self) -> None:
        now = datetime(2026, 7, 24, 12, 15, tzinfo=UTC)

        self.assertTrue(
            FEEDBACK.input_is_fresh(
                now - timedelta(minutes=15),
                now=now,
                maximum_age=timedelta(minutes=15),
            )
        )

    def test_missing_or_old_report_is_stale(self) -> None:
        now = datetime(2026, 7, 24, 12, 15, tzinfo=UTC)

        self.assertFalse(
            FEEDBACK.input_is_fresh(
                None,
                now=now,
                maximum_age=timedelta(minutes=15),
            )
        )
        self.assertFalse(
            FEEDBACK.input_is_fresh(
                now - timedelta(minutes=15, seconds=1),
                now=now,
                maximum_age=timedelta(minutes=15),
            )
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
