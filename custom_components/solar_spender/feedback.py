"""Fresh-feedback barriers and conservative per-cycle retry memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


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


@dataclass(frozen=True, slots=True)
class FeedbackBarrier:
    """Require every feedback entity to report after an action has settled."""

    action: str
    load_entity_id: str
    not_before: datetime
    required_entities: frozenset[str]

    def pending_entities(self, reports: dict[str, datetime]) -> frozenset[str]:
        """Return entities which have not produced an acceptable fresh report."""
        return frozenset(
            entity_id
            for entity_id in self.required_entities
            if reports.get(entity_id) is None
            or reports[entity_id] < self.not_before
        )

    def is_ready(self, reports: dict[str, datetime]) -> bool:
        """Return whether all required reports crossed this action's barrier."""
        return not self.pending_entities(reports)


@dataclass(frozen=True, slots=True)
class BinaryDebounceDecision:
    """A latched binary-source decision and any pending transition deadline."""

    surplus_available: bool
    pending_until: datetime | None


def debounce_binary_source(
    *,
    surplus_available: bool,
    raw_on: bool,
    raw_changed_at: datetime,
    now: datetime,
    on_delay_minutes: float,
    off_delay_minutes: float,
) -> BinaryDebounceDecision:
    """Apply continuous entry/exit delays to one valid binary source state."""
    if raw_on == surplus_available:
        return BinaryDebounceDecision(raw_on, None)
    delay_minutes = on_delay_minutes if raw_on else off_delay_minutes
    pending_until = raw_changed_at + timedelta(minutes=delay_minutes)
    if now >= pending_until:
        return BinaryDebounceDecision(raw_on, None)
    return BinaryDebounceDecision(surplus_available, pending_until)


@dataclass(slots=True)
class CycleMemory:
    """Remember loads proven unsafe until the current spending cycle ends."""

    blocked_loads: set[str] = field(default_factory=set)

    def mark_unsupported(self, entity_id: str) -> None:
        """Block an unsupported load for the remainder of this cycle."""
        self.blocked_loads.add(entity_id)

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
            or not self.blocked_loads
        ):
            return False
        self.blocked_loads.clear()
        return True
