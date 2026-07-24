"""Fresh-feedback barriers and conservative per-cycle retry memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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
