"""Fresh-feedback barriers and conservative per-cycle retry memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


def input_is_fresh(
    reported_at: datetime | None,
    *,
    now: datetime,
    maximum_age: timedelta,
) -> bool:
    """Return whether a decision input remains within its configured age."""
    return reported_at is not None and now - reported_at <= maximum_age


def append_bounded_event(
    history: list[dict[str, str]],
    *,
    message: str,
    at: datetime,
    limit: int = 30,
) -> list[dict[str, str]]:
    """Append one event while coalescing consecutive identical decisions."""
    event = {"at": at.isoformat(), "message": message}
    if history and history[-1]["message"] == message:
        return [*history[:-1], event]
    return [*history, event][-limit:]


@dataclass(slots=True)
class FeedbackAssessment:
    """Collect distinct fresh source votes after one controlled load change."""

    action: str
    load_entity_id: str
    next_not_before: datetime
    deadline: datetime
    required_entities: frozenset[str]
    sample_count: int
    votes: list[bool] = field(default_factory=list)
    measurements_w: list[float] = field(default_factory=list)
    consumed_reports: dict[str, datetime] = field(default_factory=dict)
    baseline_consumption_w: float | None = None

    def pending_entities(self, reports: dict[str, datetime]) -> frozenset[str]:
        """Return entities without a distinct eligible report for this vote."""
        return frozenset(
            entity_id
            for entity_id in self.required_entities
            if reports.get(entity_id) is None
            or reports[entity_id] < self.next_not_before
            or (
                entity_id in self.consumed_reports
                and reports[entity_id] <= self.consumed_reports[entity_id]
            )
        )

    def timed_out(self, now: datetime) -> bool:
        """Return whether the fail-closed confirmation deadline has passed."""
        return not self.complete and now >= self.deadline

    @property
    def complete(self) -> bool:
        """Return whether all configured votes have been collected."""
        return len(self.votes) >= self.sample_count

    @property
    def required_yes_votes(self) -> int:
        """Return the strict-majority threshold."""
        return self.sample_count // 2 + 1

    @property
    def supported(self) -> bool:
        """Return the final majority result."""
        return self.complete and sum(self.votes) >= self.required_yes_votes

    def record_vote(
        self,
        *,
        supported: bool,
        reports: dict[str, datetime],
        measurement_w: float | None = None,
    ) -> None:
        """Record one vote and consume the reports that made it eligible."""
        if self.complete:
            raise RuntimeError("feedback assessment is already complete")
        pending = self.pending_entities(reports)
        if pending:
            raise RuntimeError(
                "feedback vote is missing fresh reports from "
                + ", ".join(sorted(pending))
            )
        self.votes.append(supported)
        self.consumed_reports.update(
            {
                entity_id: reports[entity_id]
                for entity_id in self.required_entities
            }
        )
        if measurement_w is not None:
            self.measurements_w.append(measurement_w)


@dataclass(frozen=True, slots=True)
class LearnedDrawEstimate:
    """A decaying per-load marginal-power hint."""

    estimate_w: float
    samples: int
    updated_at: datetime

    def update(
        self,
        observed_w: float,
        *,
        updated_at: datetime,
        alpha: float = 0.25,
    ) -> LearnedDrawEstimate:
        """Return an exponentially weighted update."""
        return LearnedDrawEstimate(
            estimate_w=(1 - alpha) * self.estimate_w + alpha * observed_w,
            samples=self.samples + 1,
            updated_at=updated_at,
        )

    def conservative_value(
        self,
        *,
        configured_w: float | None,
        now: datetime,
        minimum_samples: int = 3,
        maximum_age: timedelta = timedelta(days=30),
    ) -> float | None:
        """Return a usable hint only while it has enough recent evidence."""
        if self.samples < minimum_samples or now - self.updated_at > maximum_age:
            return configured_w
        if configured_w is None:
            return self.estimate_w
        return max(configured_w, self.estimate_w)


@dataclass(slots=True)
class CycleMemory:
    """Remember loads proven unsafe until the current spending cycle ends."""

    blocked_loads: set[str] = field(default_factory=set)
    supported_combinations: set[frozenset[str]] = field(default_factory=set)
    unsupported_combinations: set[frozenset[str]] = field(default_factory=set)
    lower_supported_w: float | None = None
    upper_unsupported_w: float | None = None

    def mark_unsupported(self, entity_id: str) -> None:
        """Block an unsupported load for the remainder of this cycle."""
        self.blocked_loads.add(entity_id)

    def record_combination(
        self,
        entity_ids: frozenset[str],
        *,
        supported: bool,
        expected_draws_w: dict[str, float | None],
    ) -> None:
        """Remember a result and update its temporary wattage bracket."""
        if supported:
            self.supported_combinations.add(entity_ids)
        else:
            self.unsupported_combinations.add(entity_ids)
        draws = [expected_draws_w.get(entity_id) for entity_id in entity_ids]
        if not entity_ids or any(draw is None for draw in draws):
            return
        total_w = sum(float(draw) for draw in draws if draw is not None)
        if supported:
            if (
                self.upper_unsupported_w is not None
                and total_w >= self.upper_unsupported_w
            ):
                self.upper_unsupported_w = None
            self.lower_supported_w = max(self.lower_supported_w or 0, total_w)
        else:
            if self.lower_supported_w is not None and total_w <= self.lower_supported_w:
                self.lower_supported_w = None
            self.upper_unsupported_w = min(
                self.upper_unsupported_w or total_w,
                total_w,
            )

    def fits_upper_bound(self, total_draw_w: float | None) -> bool:
        """Return whether a candidate fits below the latest failed bound."""
        return (
            total_draw_w is None
            or self.upper_unsupported_w is None
            or total_draw_w < self.upper_unsupported_w
        )

    def combination_is_blocked(self, entity_ids: frozenset[str]) -> bool:
        """Return whether this exact combination already failed."""
        return entity_ids in self.unsupported_combinations

    def reset_if_cycle_ended(
        self,
        owned_loads: int,
        activation_pending: bool,
        surplus_available: bool,
        feedback_waiting: bool,
    ) -> bool:
        """Clear memory after fresh feedback proves the opportunity has ended."""
        if (
            owned_loads
            or activation_pending
            or surplus_available
            or feedback_waiting
            or not (
                self.blocked_loads
                or self.supported_combinations
                or self.unsupported_combinations
                or self.lower_supported_w is not None
                or self.upper_unsupported_w is not None
            )
        ):
            return False
        self.blocked_loads.clear()
        self.supported_combinations.clear()
        self.unsupported_combinations.clear()
        self.lower_supported_w = None
        self.upper_unsupported_w = None
        return True
