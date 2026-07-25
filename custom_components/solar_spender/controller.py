"""Event-driven Solar Spender controller."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite
from statistics import median
from typing import Any

from homeassistant.const import (
    ATTR_TEMPERATURE,
    EVENT_STATE_REPORTED,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .battery import direction_from_power
from .const import (
    BATTERY_CHARGING_OR_SOC,
    BATTERY_DIRECTION_POWER,
    BATTERY_DISABLED,
    BATTERY_FULL_IDLE_FOR_PROBE,
    BATTERY_REQUIRE_CHARGING,
    CONTROLLER_STATUS_UPDATED,
    SOURCE_CURTAILED,
    SOURCE_GRID,
    STATE_BLOCKED_BATTERY,
    STATE_DISABLED,
    STATE_MONITORING,
    STATE_PROBING,
    STATE_SHEDDING,
    STATE_SPENDING,
    STATE_WAITING_FEEDBACK,
)
from .feedback import (
    CycleMemory,
    FeedbackAssessment,
    LearnedDrawEstimate,
    append_bounded_event,
)
from .models import LoadConfig, SolarSpenderConfig
from .selection import best_to_release_for_shortfall, first_to_activate
from .source import (
    observable_surplus_available,
    zero_export_opportunity_available,
)
from .storage import LearningStore

_INVALID_STATES = {STATE_UNKNOWN, STATE_UNAVAILABLE}


@dataclass(slots=True)
class Lease:
    """A climate load Solar Spender is permitted to release."""

    load: LoadConfig
    activated_at: datetime
    commanded_profile: dict[str, Any]
    previous_profile: dict[str, Any]


class SolarSpenderController:
    """Own subscriptions, hysteresis, climate leases, and reconciliation."""

    def __init__(
        self, hass: HomeAssistant, config: SolarSpenderConfig, entry_id: str
    ) -> None:
        self.hass = hass
        self.config = config
        self.entry_id = entry_id
        self.state = STATE_DISABLED if not config.enabled else STATE_MONITORING
        self.surplus_available = False
        self.battery_allowed = config.battery_policy == BATTERY_DISABLED
        self.battery_direction = "not_configured"
        self.battery_power_w: float | None = None
        self.reason = "disabled" if not config.enabled else "awaiting source"
        self.raw_source_value: str | float | None = None
        self.source_valid = False
        self.headroom_w: float | None = None
        self.opportunity_power_w: float | None = None
        self.source_deficit_w: float | None = None
        self._leases: dict[str, Lease] = {}
        self._last_off: dict[str, datetime] = {}
        self._pending_activation: tuple[LoadConfig, dict[str, Any], dict[str, Any]] | None = None
        self._feedback_entity_ids = self._feedback_entities()
        self._feedback_reports: dict[str, datetime] = {}
        self._feedback_assessment: FeedbackAssessment | None = None
        self._cycle_memory = CycleMemory()
        self._pending_unsupported_release: str | None = None
        self._next_load_not_before: datetime | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        self._scheduled: Callable[[], None] | None = None
        self._reconciling = False
        self._event_history: list[dict[str, str]] = []
        self._learning_store = LearningStore(hass, entry_id)
        self._learned_draws: dict[str, LearnedDrawEstimate] = {}

    async def async_start(self) -> None:
        """Subscribe to all configured entities and evaluate initial state."""
        self._learned_draws = await self._learning_store.async_load()
        entity_ids = self._watched_entities()
        for entity_id in self._feedback_entity_ids:
            if (state := self.hass.states.get(entity_id)) is not None:
                self._feedback_reports[entity_id] = state.last_reported
        if entity_ids:
            self._unsubscribers.append(
                async_track_state_change_event(self.hass, entity_ids, self._async_state_changed)
            )
        if self._feedback_entity_ids:
            self._unsubscribers.append(
                self.hass.bus.async_listen(
                    EVENT_STATE_REPORTED,
                    self._async_state_reported,
                    event_filter=self._is_feedback_report,
                )
            )
        await self.async_reconcile("started")

    async def async_stop(self) -> None:
        """Stop all subscriptions and pending callbacks without altering loads."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._scheduled is not None:
            self._scheduled()
            self._scheduled = None

    async def async_apply_runtime_config(self, config: SolarSpenderConfig) -> None:
        """Apply options exposed as entities without dropping active leases."""
        self.config = config
        if not config.enabled:
            self._feedback_assessment = None
            self._next_load_not_before = None
            if self._scheduled is not None:
                self._scheduled()
                self._scheduled = None
        await self.async_reconcile("runtime setting changed")

    def supports_runtime_config(self, config: SolarSpenderConfig) -> bool:
        """Return whether config can change without replacing subscriptions."""
        return replace(
            config,
            enabled=self.config.enabled,
            settling_seconds=self.config.settling_seconds,
            feedback_sample_count=self.config.feedback_sample_count,
            feedback_sample_interval_minutes=(
                self.config.feedback_sample_interval_minutes
            ),
            next_load_delay_minutes=self.config.next_load_delay_minutes,
        ) == self.config

    @callback
    def _async_state_changed(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        if entity_id in self._feedback_entity_ids:
            new_state = event.data.get("new_state")
            reported_at = (
                new_state.last_updated
                if new_state is not None
                else datetime.now().astimezone()
            )
            self._feedback_reports[entity_id] = reported_at
        self.hass.async_create_task(self.async_reconcile(f"state changed: {entity_id}"))

    @callback
    def _is_feedback_report(self, event_data: dict[str, Any]) -> bool:
        """Limit the high-volume state_reported event to configured feedback."""
        return event_data.get("entity_id") in self._feedback_entity_ids

    @callback
    def _async_state_reported(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        self._feedback_reports[entity_id] = event.data["last_reported"]
        self.hass.async_create_task(self.async_reconcile(f"state reported: {entity_id}"))

    def _watched_entities(self) -> set[str]:
        config = self.config
        values = {
            config.grid_entity_id,
            config.production_entity_id,
            config.consumption_entity_id,
            config.battery_soc_entity_id,
            config.battery_status_entity_id,
            config.battery_power_entity_id,
            *(load.entity_id for load in config.loads),
            *(load.power_entity_id for load in config.loads),
        }
        return {value for value in values if value}

    def _feedback_entities(self) -> frozenset[str]:
        """Return entities that must report freshly after every load change."""
        config = self.config
        if config.source_type == SOURCE_GRID:
            values = {config.grid_entity_id}
        else:
            values = {
                config.production_entity_id,
                config.consumption_entity_id,
            }
        if config.battery_policy != BATTERY_DISABLED:
            values.add(
                config.battery_power_entity_id
                if config.battery_direction_source == BATTERY_DIRECTION_POWER
                else config.battery_status_entity_id
            )
        if config.battery_policy in {
            BATTERY_CHARGING_OR_SOC,
            BATTERY_FULL_IDLE_FOR_PROBE,
        }:
            values.add(config.battery_soc_entity_id)
        return frozenset(value for value in values if value)

    async def async_reconcile(self, reason: str) -> None:
        """Apply one safe controller decision from the latest Home Assistant state."""
        if self._reconciling:
            return
        self._reconciling = True
        try:
            self._update_inputs()
            self._relinquish_manual_overrides()
            self._confirm_pending_activation()
            if not self.config.enabled:
                self._set_state(STATE_DISABLED, "disabled")
                return
            if self._feedback_assessment is not None and not self.source_valid:
                assessment = self._feedback_assessment
                self._feedback_assessment = None
                self._record("Feedback assessment failed: source became invalid")
                if assessment.action == "activation":
                    self._mark_unsupported_activation(
                        assessment.load_entity_id,
                        global_block=True,
                    )
                    await self._async_shed_one(
                        "source became invalid during confirmation",
                        target_entity_id=assessment.load_entity_id,
                    )
                return
            if (
                self._feedback_assessment is not None
                and self._feedback_assessment.action == "activation"
                and self.config.source_type == SOURCE_CURTAILED
                and not self.battery_allowed
            ):
                assessment = self._feedback_assessment
                self._feedback_assessment = None
                self._mark_unsupported_activation(
                    assessment.load_entity_id,
                    global_block=True,
                )
                await self._async_shed_one(
                    "battery did not support the probe",
                    target_entity_id=assessment.load_entity_id,
                )
                return
            if (
                self._feedback_assessment is not None
                and self._feedback_assessment.action == "activation"
                and self.config.source_type == SOURCE_GRID
                and self.headroom_w is not None
                and self.headroom_w < 0
            ):
                assessment = self._feedback_assessment
                self._feedback_assessment = None
                self._mark_unsupported_activation(
                    assessment.load_entity_id,
                    global_block=True,
                )
                await self._async_shed_one(
                    "grid import exceeded the configured export reserve",
                    target_entity_id=assessment.load_entity_id,
                )
                return
            if self._feedback_assessment is not None:
                assessment = self._feedback_assessment
                pending_reports = assessment.pending_entities(self._feedback_reports)
                if pending_reports:
                    self._set_state(
                        STATE_WAITING_FEEDBACK,
                        f"confirmation {len(assessment.votes)}/"
                        f"{assessment.sample_count}; waiting for "
                        + ", ".join(sorted(pending_reports)),
                    )
                    return
                assessment.record_vote(
                    supported=self.surplus_available,
                    accepted_at=datetime.now().astimezone(),
                    measurement_w=self._power_value(
                        self.config.consumption_entity_id
                    )
                    if self.config.consumption_entity_id
                    else None,
                )
                self._record(
                    f"Confirmation vote {len(assessment.votes)}/"
                    f"{assessment.sample_count} for {assessment.load_entity_id}: "
                    f"{'headroom' if assessment.votes[-1] else 'no headroom'}"
                )
                if not assessment.complete:
                    self._schedule_reconcile(
                        self.config.feedback_sample_interval_minutes * 60
                    )
                    self._set_state(
                        STATE_WAITING_FEEDBACK,
                        f"confirmation {len(assessment.votes)}/"
                        f"{assessment.sample_count}",
                    )
                    return
                self._feedback_assessment = None
                if assessment.action == "activation":
                    if assessment.supported:
                        self._record_combination(True)
                        await self._async_learn_draw(assessment)
                else:
                    self._record_combination(True)
                if assessment.action == "activation" and not assessment.supported:
                    self._mark_unsupported_activation(assessment.load_entity_id)
                    await self._async_shed_one(
                        "activation failed majority confirmation",
                        target_entity_id=assessment.load_entity_id,
                    )
                    return
                if not assessment.supported:
                    self._set_state(
                        STATE_SPENDING if self._leases else STATE_MONITORING,
                        "majority confirmation found no headroom",
                    )
                    return
                self._next_load_not_before = (
                    datetime.now().astimezone()
                    + timedelta(minutes=self.config.next_load_delay_minutes)
                )
                self._schedule_reconcile(self.config.next_load_delay_minutes * 60)
                self._set_state(
                    STATE_SPENDING if self._leases else STATE_MONITORING,
                    "majority confirmation passed; waiting before next AC",
                )
                return
            if self._pending_unsupported_release is not None:
                if self._pending_unsupported_release not in self._leases:
                    self._pending_unsupported_release = None
                else:
                    await self._async_shed_one(
                        "releasing unsupported activation",
                        target_entity_id=self._pending_unsupported_release,
                    )
                    return
            if self._cycle_memory.reset_if_cycle_ended(
                len(self._leases),
                self._pending_activation is not None,
                self.surplus_available,
                self._feedback_assessment is not None,
            ):
                self._record("Cleared unsupported-load memory after surplus ended")
            if not self.battery_allowed:
                if self.state == STATE_PROBING:
                    await self._async_shed_one("battery discharging during probe")
                elif not self._leases:
                    self._set_state(STATE_BLOCKED_BATTERY, self.reason)
                return
            if not self.surplus_available:
                if self._leases:
                    await self._async_shed_one(
                        "surplus unavailable",
                        block_for_cycle=True,
                    )
                else:
                    self._set_state(STATE_MONITORING, self.reason)
                return
            if (
                self._next_load_not_before is not None
                and datetime.now().astimezone() < self._next_load_not_before
            ):
                self._set_state(
                    STATE_SPENDING if self._leases else STATE_MONITORING,
                    "waiting before next AC",
                )
                return
            self._next_load_not_before = None
            await self._async_activate_one(reason)
        finally:
            async_dispatcher_send(
                self.hass,
                f"{CONTROLLER_STATUS_UPDATED}_{self.entry_id}",
            )
            self._reconciling = False

    def _update_inputs(self) -> None:
        self._update_source()
        self.battery_allowed, battery_reason = self._battery_allows_activation()
        if not self.battery_allowed:
            self.reason = battery_reason

    def _update_source(self) -> None:
        config = self.config
        if config.source_type == SOURCE_GRID:
            watts = self._power_value(config.grid_entity_id)
            self.raw_source_value = watts
            if watts is None:
                self._clear_source("grid-flow sensor unavailable")
                return
            self.source_valid = True
            export_w = watts if config.grid_export_positive else -watts
            decision_w = export_w - config.export_reserve_w
            self.headroom_w = decision_w
            self.opportunity_power_w = None
            self.source_deficit_w = None
        else:
            production_w = self._power_value(config.production_entity_id)
            consumption_w = self._power_value(config.consumption_entity_id)
            self.raw_source_value = production_w
            if production_w is None or consumption_w is None:
                self._clear_source("production or consumption sensor unavailable")
                return
            self.source_valid = True
            decision_w = production_w - consumption_w
            self.headroom_w = decision_w
            self.opportunity_power_w = None
            self.source_deficit_w = None
            if config.source_type == SOURCE_CURTAILED:
                # A small uncovered deficit permits a trial; it is not hidden headroom.
                opportunity = zero_export_opportunity_available(
                    was_available=self.surplus_available,
                    production_w=production_w,
                    consumption_w=consumption_w,
                    minimum_production_w=config.minimum_production_w,
                    entry_deficit_w=config.entry_threshold_w,
                    exit_deficit_w=config.exit_threshold_w,
                )
                self.headroom_w = None
                self.opportunity_power_w = production_w
                self.source_deficit_w = opportunity.deficit_w
                self.surplus_available = opportunity.available
                self.reason = (
                    "zero-export test opportunity available"
                    if opportunity.available
                    else "zero-export deficit or production outside limits"
                )
                return

        self.surplus_available = observable_surplus_available(
            was_available=self.surplus_available,
            headroom_w=decision_w,
            entry_threshold_w=config.entry_threshold_w,
            exit_threshold_w=config.exit_threshold_w,
        )
        self.reason = (
            f"source {'available' if self.surplus_available else 'below threshold'}"
        )

    def _clear_source(self, reason: str) -> None:
        self.source_valid = False
        self.surplus_available = False
        self.headroom_w = None
        self.opportunity_power_w = None
        self.source_deficit_w = None
        self.reason = reason

    def _power_value(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _INVALID_STATES:
            return None
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None
        if not isfinite(value):
            return None
        unit = str(state.attributes.get("unit_of_measurement", "W")).lower()
        if unit == "kw":
            value *= 1000
        elif unit == "mw":
            value *= 1_000_000
        elif unit != "w":
            return None
        return value

    def _battery_allows_activation(self) -> tuple[bool, str]:
        config = self.config
        if config.battery_policy == BATTERY_DISABLED:
            self.battery_direction = "not_configured"
            self.battery_power_w = None
            return True, "battery policy disabled"
        status = self._battery_direction()
        if config.battery_direction_source == BATTERY_DIRECTION_POWER:
            charging = status == "charging"
            discharging = status == "discharging"
        else:
            charging = status in config.charging_states
            discharging = status in config.discharging_states
        soc = self._numeric_state(config.battery_soc_entity_id)
        if config.battery_policy == BATTERY_REQUIRE_CHARGING:
            return charging, "battery is not charging"
        if config.battery_policy == BATTERY_CHARGING_OR_SOC:
            return (
                charging
                or (soc is not None and soc >= config.battery_full_threshold),
                "battery gate closed",
            )
        if config.battery_policy == BATTERY_FULL_IDLE_FOR_PROBE:
            allowed = (
                soc is not None
                and soc >= config.battery_full_threshold
                and not charging
                and not discharging
                and bool(status)
            )
            return allowed, "battery must be full and idle before probing"
        return False, "invalid battery policy"

    def _battery_direction(self) -> str:
        if self.config.battery_direction_source == BATTERY_DIRECTION_POWER:
            power_w = self._power_value(self.config.battery_power_entity_id)
            if power_w is None:
                self.battery_direction = "unknown"
                self.battery_power_w = None
                return ""
            decision = direction_from_power(
                power_w,
                charging_positive=self.config.battery_power_charging_positive,
                threshold_w=self.config.battery_power_threshold_w,
            )
            self.battery_direction = decision.direction
            self.battery_power_w = decision.charging_positive_w
            return decision.direction
        state = self.hass.states.get(self.config.battery_status_entity_id)
        if state is None or state.state in _INVALID_STATES:
            self.battery_direction = "unknown"
            self.battery_power_w = None
            return ""
        if (
            state.entity_id.startswith("binary_sensor.")
            and state.attributes.get("device_class") == "battery_charging"
        ):
            direction = "charging" if state.state == "on" else "idle"
        else:
            direction = state.state.lower()
        self.battery_direction = direction
        self.battery_power_w = None
        return direction

    def _numeric_state(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _INVALID_STATES:
            return None
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None
        return value if isfinite(value) else None

    async def _async_activate_one(self, reason: str) -> None:
        if self._pending_activation is not None:
            return
        candidate = self._next_eligible_load()
        if candidate is None:
            no_load_reason = (
                "remaining loads blocked for current solar opportunity"
                if (
                    self._cycle_memory.blocked_loads
                    or self._cycle_memory.unsupported_combinations
                )
                else "no eligible load"
            )
            self._set_state(
                STATE_SPENDING if self._leases else STATE_MONITORING,
                no_load_reason,
            )
            return
        effective_draw_w = self._expected_draws().get(candidate.entity_id)
        if self.config.source_type != SOURCE_CURTAILED and (
            effective_draw_w is not None
            and self.headroom_w is not None
            and effective_draw_w > self.headroom_w
        ):
            self._set_state(
                STATE_SPENDING if self._leases else STATE_MONITORING,
                "no load fits headroom",
            )
            return
        self._set_state(
            STATE_PROBING if self.config.source_type == SOURCE_CURTAILED else STATE_SPENDING,
            f"activating {candidate.entity_id}: {reason}",
        )
        await self._async_activate_load(candidate)

    def _next_eligible_load(self) -> LoadConfig | None:
        now = datetime.now().astimezone()
        candidates: list[LoadConfig] = []
        effective_draws = self._expected_draws()
        owned_draw_w = self._combination_draw_w(frozenset(self._leases))
        for load in self.config.loads:
            proposed_combination = frozenset(
                {*self._leases, load.entity_id}
            )
            if (
                not load.enabled
                or load.entity_id in self._leases
                or load.entity_id in self._cycle_memory.blocked_loads
                or self._cycle_memory.combination_is_blocked(
                    proposed_combination
                )
            ):
                continue
            state = self.hass.states.get(load.entity_id)
            if state is None or state.state in _INVALID_STATES or state.state != STATE_OFF:
                continue
            last_off = self._last_off.get(load.entity_id)
            if last_off is not None and now < last_off + timedelta(seconds=load.min_off_seconds):
                continue
            candidate_total_w = (
                owned_draw_w + effective_draws[load.entity_id]
                if owned_draw_w is not None
                and effective_draws[load.entity_id] is not None
                else None
            )
            if not self._cycle_memory.fits_upper_bound(candidate_total_w):
                continue
            candidates.append(load)
        # ``candidates`` retains configuration order, so Python's stable
        # minimum gives equally prioritized loads a predictable tie-break.
        return first_to_activate(candidates, lambda load: load.priority)

    async def _async_activate_load(self, load: LoadConfig) -> None:
        previous_profile = self._capture_profile(load.entity_id)
        baseline_consumption_w = (
            self._power_value(self.config.consumption_entity_id)
            if self.config.consumption_entity_id
            else None
        )
        profile: dict[str, Any] = {}
        await self.hass.services.async_call(
            "climate", "turn_on", {"entity_id": load.entity_id}, blocking=True
        )
        if load.hvac_mode is not None:
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": load.entity_id, "hvac_mode": load.hvac_mode},
                blocking=True,
            )
            profile["hvac_mode"] = load.hvac_mode
        if load.temperature is not None:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {
                    "entity_id": load.entity_id,
                    ATTR_TEMPERATURE: load.temperature,
                },
                blocking=True,
            )
            profile[ATTR_TEMPERATURE] = load.temperature
        if load.fan_mode is not None:
            await self.hass.services.async_call(
                "climate",
                "set_fan_mode",
                {"entity_id": load.entity_id, "fan_mode": load.fan_mode},
                blocking=True,
            )
            profile["fan_mode"] = load.fan_mode
        self._pending_activation = (load, profile, previous_profile)
        action_completed_at = datetime.now().astimezone()
        self._begin_feedback_assessment(
            "activation",
            load.entity_id,
            action_completed_at,
            minimum_on_seconds=load.min_on_seconds,
            baseline_consumption_w=baseline_consumption_w,
        )
        self._record(f"Awaiting activation confirmation for {load.entity_id}")
        self._schedule_reconcile(self.config.settling_seconds)

    def _confirm_pending_activation(self) -> None:
        """Create a lease only after Home Assistant observes a running climate entity."""
        if self._pending_activation is None:
            return
        load, profile, previous_profile = self._pending_activation
        self._pending_activation = None
        state = self.hass.states.get(load.entity_id)
        if state is None or state.state in _INVALID_STATES or state.state == STATE_OFF:
            self._last_off[load.entity_id] = datetime.now().astimezone()
            if (
                self._feedback_assessment is not None
                and self._feedback_assessment.action == "activation"
                and self._feedback_assessment.load_entity_id == load.entity_id
            ):
                self._feedback_assessment = None
            self._record(f"Activation was not confirmed for {load.entity_id}")
            return
        activated_at = datetime.now().astimezone()
        self._leases[load.entity_id] = Lease(
            load,
            activated_at,
            profile,
            previous_profile,
        )
        if (
            self._feedback_assessment is not None
            and self._feedback_assessment.action == "activation"
            and self._feedback_assessment.load_entity_id == load.entity_id
        ):
            minimum_on_deadline = activated_at + timedelta(
                seconds=load.min_on_seconds
            )
            self._feedback_assessment.next_not_before = max(
                self._feedback_assessment.next_not_before,
                minimum_on_deadline,
            )
        self._record(f"Activated {load.entity_id}")

    async def _async_shed_one(
        self,
        reason: str,
        target_entity_id: str | None = None,
        block_for_cycle: bool = False,
    ) -> None:
        now = datetime.now().astimezone()
        considered = list(self._leases.values())
        if target_entity_id is not None:
            target = self._leases.get(target_entity_id)
            if target is None:
                self._record(
                    f"Could not release {target_entity_id}: ownership was relinquished"
                )
                if self._pending_unsupported_release == target_entity_id:
                    self._pending_unsupported_release = None
                return
            considered = [target]
        eligible = [
            lease
            for lease in considered
            if now >= lease.activated_at + timedelta(seconds=lease.load.min_on_seconds)
        ]
        if not eligible:
            self._set_state(STATE_SHEDDING, "waiting for minimum-on deadline")
            deadlines = [
                lease.activated_at + timedelta(seconds=lease.load.min_on_seconds)
                for lease in considered
            ]
            if deadlines:
                self._schedule_reconcile(max(0, (min(deadlines) - now).total_seconds()))
            return
        if target_entity_id is not None:
            lease = eligible[0]
        else:
            shortfall_w = self._release_shortfall_w()
            lease = best_to_release_for_shortfall(
                eligible,
                draw_w=lambda item: self._current_release_draw_w(item.load),
                shortfall_w=shortfall_w,
                priority=lambda item: item.load.priority,
            )
            if lease is not None and shortfall_w > 0:
                draw_w = self._current_release_draw_w(lease.load)
                self._record(
                    f"Measured shortfall is {round(shortfall_w)} W; selected "
                    f"{lease.load.entity_id} at "
                    f"{round(draw_w) if draw_w is not None else 'unknown'} W"
                )
        if lease is None:
            return
        if block_for_cycle:
            self._cycle_memory.record_combination(
                frozenset(self._leases),
                supported=False,
                expected_draws_w=self._expected_draws(),
            )
        self._set_state(STATE_SHEDDING, f"releasing {lease.load.entity_id}: {reason}")
        try:
            await self.hass.services.async_call(
                "climate", "turn_off", {"entity_id": lease.load.entity_id}, blocking=True
            )
        except HomeAssistantError as err:
            self._record(f"Could not release {lease.load.entity_id}: {err}")
            self._schedule_reconcile(self.config.settling_seconds)
            return
        await self._async_restore_profile(lease)
        self._leases.pop(lease.load.entity_id, None)
        if self._pending_unsupported_release == lease.load.entity_id:
            self._pending_unsupported_release = None
        self._last_off[lease.load.entity_id] = now
        self._record(f"Released {lease.load.entity_id}: {reason}")
        self._begin_feedback_assessment(
            "release",
            lease.load.entity_id,
            datetime.now().astimezone(),
        )
        self._schedule_reconcile(self.config.settling_seconds)

    def _begin_feedback_assessment(
        self,
        action: str,
        load_entity_id: str,
        action_completed_at: datetime,
        minimum_on_seconds: int = 0,
        baseline_consumption_w: float | None = None,
    ) -> None:
        """Collect spaced fresh reports before permitting another load change."""
        not_before = action_completed_at + timedelta(
            seconds=max(self.config.settling_seconds, minimum_on_seconds)
        )
        self._feedback_assessment = FeedbackAssessment(
            action=action,
            load_entity_id=load_entity_id,
            next_not_before=not_before,
            required_entities=self._feedback_entity_ids,
            sample_count=self.config.feedback_sample_count,
            sample_interval=timedelta(
                minutes=self.config.feedback_sample_interval_minutes
            ),
            baseline_consumption_w=baseline_consumption_w,
        )
        self._record(
            f"Confirmation for {load_entity_id}; first reports must be newer than "
            f"{not_before.isoformat()}"
        )

    def _expected_draws(self) -> dict[str, float | None]:
        now = datetime.now().astimezone()
        return {
            load.entity_id: (
                self._learned_draws[load.entity_id].conservative_value(
                    configured_w=load.expected_power_w,
                    now=now,
                )
                if load.entity_id in self._learned_draws
                else load.expected_power_w
            )
            for load in self.config.loads
        }

    def _current_release_draw_w(self, load: LoadConfig) -> float | None:
        """Prefer a valid non-negative AC draw, then use its estimate."""
        if load.power_entity_id:
            live_w = self._power_value(load.power_entity_id)
            if live_w is not None and live_w >= 0:
                return live_w
        return self._expected_draws().get(load.entity_id)

    def _release_shortfall_w(self) -> float:
        """Return the currently observable watts that load removal should cover."""
        if self.config.source_type == SOURCE_CURTAILED:
            return max(0.0, self.source_deficit_w or 0.0)
        return max(0.0, -(self.headroom_w or 0.0))

    def _combination_draw_w(self, entity_ids: frozenset[str]) -> float | None:
        draws = self._expected_draws()
        values = [draws.get(entity_id) for entity_id in entity_ids]
        if any(value is None for value in values):
            return None
        return sum(float(value) for value in values if value is not None)

    def _record_combination(self, supported: bool) -> None:
        self._cycle_memory.record_combination(
            frozenset(self._leases),
            supported=supported,
            expected_draws_w=self._expected_draws(),
        )

    def _mark_unsupported_activation(
        self,
        entity_id: str,
        *,
        global_block: bool = False,
    ) -> None:
        self._record_combination(False)
        if global_block:
            self._cycle_memory.mark_unsupported(entity_id)
        self._pending_unsupported_release = entity_id
        self._record(
            f"Blocked {'load' if global_block else 'combination'} "
            f"containing {entity_id} for this solar opportunity"
        )

    async def _async_learn_draw(self, assessment: FeedbackAssessment) -> None:
        """Persist a marginal draw only when the post-action samples are stable."""
        baseline_w = assessment.baseline_consumption_w
        samples = assessment.measurements_w
        if baseline_w is None or len(samples) != assessment.sample_count:
            return
        post_w = float(median(samples))
        observed_w = post_w - baseline_w
        spread_w = max(samples) - min(samples)
        if observed_w <= 0 or spread_w > max(100.0, observed_w * 0.2):
            self._record(
                f"Skipped draw learning for {assessment.load_entity_id}: "
                "household load was not stable"
            )
            return
        now = datetime.now().astimezone()
        previous = self._learned_draws.get(assessment.load_entity_id)
        if previous is None:
            estimate = LearnedDrawEstimate(observed_w, 1, now)
        else:
            estimate = previous.update(observed_w, updated_at=now)
        self._learned_draws[assessment.load_entity_id] = estimate
        await self._learning_store.async_save(self._learned_draws)
        self._record(
            f"Learned {assessment.load_entity_id} draw near "
            f"{round(estimate.estimate_w)} W from {estimate.samples} sample(s)"
        )

    def _capture_profile(self, entity_id: str) -> dict[str, Any]:
        """Capture only fields Solar Spender may later change."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return {}
        return {
            "hvac_mode": state.state,
            ATTR_TEMPERATURE: state.attributes.get(ATTR_TEMPERATURE),
            "fan_mode": state.attributes.get("fan_mode"),
        }

    async def _async_restore_profile(self, lease: Lease) -> None:
        """Restore the pre-activation controllable profile after automatic release."""
        previous = lease.previous_profile
        entity_id = lease.load.entity_id
        try:
            if previous.get(ATTR_TEMPERATURE) is not None:
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {"entity_id": entity_id, ATTR_TEMPERATURE: previous[ATTR_TEMPERATURE]},
                    blocking=True,
                )
            if previous.get("fan_mode") is not None:
                await self.hass.services.async_call(
                    "climate",
                    "set_fan_mode",
                    {"entity_id": entity_id, "fan_mode": previous["fan_mode"]},
                    blocking=True,
                )
            # The previous state is normally off because activation skips ACs
            # already in use. `turn_off` above restores it without re-energizing.
            self._record(f"Restored profile for {entity_id}")
        except HomeAssistantError as err:
            self._record(f"Could not fully restore profile for {entity_id}: {err}")

    def _relinquish_manual_overrides(self) -> None:
        for entity_id, lease in list(self._leases.items()):
            state = self.hass.states.get(entity_id)
            if state is None or state.state in _INVALID_STATES or state.state == STATE_OFF:
                self._leases.pop(entity_id, None)
                self._record(f"Relinquished {entity_id}: turned off externally")
                continue
            if lease.load.hvac_mode is not None and state.state != lease.load.hvac_mode:
                self._leases.pop(entity_id, None)
                self._record(f"Relinquished {entity_id}: HVAC mode changed")
                continue
            if lease.load.temperature is not None:
                actual = state.attributes.get(ATTR_TEMPERATURE)
                if actual is not None and abs(float(actual) - lease.load.temperature) > 0.1:
                    self._leases.pop(entity_id, None)
                    self._record(f"Relinquished {entity_id}: temperature changed")
                    continue
            if (
                lease.load.fan_mode is not None
                and state.attributes.get("fan_mode") != lease.load.fan_mode
            ):
                self._leases.pop(entity_id, None)
                self._record(f"Relinquished {entity_id}: fan mode changed")
        if (
            self._feedback_assessment is not None
            and self._feedback_assessment.action == "activation"
            and self._feedback_assessment.load_entity_id not in self._leases
            and self._pending_activation is None
        ):
            self._record(
                f"Cancelled confirmation for "
                f"{self._feedback_assessment.load_entity_id}: no longer owned"
            )
            self._feedback_assessment = None
        if (
            self._pending_unsupported_release is not None
            and self._pending_unsupported_release not in self._leases
        ):
            self._pending_unsupported_release = None

    def _schedule_reconcile(self, seconds: float) -> None:
        if self._scheduled is not None:
            self._scheduled()
        self._scheduled = async_call_later(
            self.hass,
            seconds,
            self._async_scheduled_reconcile,
        )

    @callback
    def _async_scheduled_reconcile(self, _now: datetime) -> None:
        self._scheduled = None
        self.hass.async_create_task(self.async_reconcile("scheduled check"))

    def _set_state(self, state: str, reason: str) -> None:
        self.state = state
        self.reason = reason
        self._record(reason)

    def _record(self, message: str) -> None:
        self._event_history = append_bounded_event(
            self._event_history,
            message=message,
            at=datetime.now().astimezone(),
        )

    def _load_status(self, load: LoadConfig) -> dict[str, Any]:
        """Explain ownership and current eligibility for one configured AC."""
        state = self.hass.states.get(load.entity_id)
        state_value = state.state if state is not None else None
        owned = load.entity_id in self._leases
        blocked_for_cycle = (
            load.entity_id in self._cycle_memory.blocked_loads
            or self._cycle_memory.combination_is_blocked(
                frozenset({*self._leases, load.entity_id})
            )
        )
        can_be_owned = False
        if owned:
            ownership_reason = "Owned by Solar Spender"
        elif (
            self._pending_activation is not None
            and self._pending_activation[0].entity_id == load.entity_id
        ):
            ownership_reason = "Solar Spender is starting this AC"
        elif not load.enabled:
            ownership_reason = "Disabled in Solar Spender"
        elif state is None or state_value in _INVALID_STATES:
            ownership_reason = "Unavailable; Solar Spender cannot own it"
        elif state_value != STATE_OFF:
            ownership_reason = (
                "Already running or changed manually; Solar Spender does not own it"
            )
        elif blocked_for_cycle:
            ownership_reason = "Blocked for this solar opportunity"
        else:
            last_off = self._last_off.get(load.entity_id)
            minimum_off_until = (
                last_off + timedelta(seconds=load.min_off_seconds)
                if last_off is not None
                else None
            )
            if (
                minimum_off_until is not None
                and datetime.now().astimezone() < minimum_off_until
            ):
                ownership_reason = (
                    "Waiting for minimum-off time before it can be owned"
                )
            else:
                can_be_owned = True
                ownership_reason = "Available for Solar Spender to own"
        return {
            "entity_id": load.entity_id,
            "enabled": load.enabled,
            "owned": owned,
            "can_be_owned": can_be_owned,
            "ownership_reason": ownership_reason,
            "blocked_for_cycle": blocked_for_cycle,
            "effective_expected_power_w": self._expected_draws().get(
                load.entity_id
            ),
            "current_power_w": self._current_release_draw_w(load),
            "power_entity_id": load.power_entity_id,
            "learned_draw_samples": (
                self._learned_draws[load.entity_id].samples
                if load.entity_id in self._learned_draws
                else 0
            ),
            "state": state_value,
        }

    def status(self) -> dict[str, Any]:
        """Return a frontend-safe controller snapshot."""
        feedback_assessment = self._feedback_assessment
        pending_feedback = (
            feedback_assessment.pending_entities(self._feedback_reports)
            if feedback_assessment is not None
            else frozenset()
        )
        return {
            "entry_id": self.entry_id,
            "state": self.state,
            "reason": self.reason,
            "enabled": self.config.enabled,
            "raw_source_value": self.raw_source_value,
            "source_valid": self.source_valid,
            "surplus_available": self.surplus_available,
            "headroom_w": self.headroom_w,
            "opportunity_power_w": self.opportunity_power_w,
            "source_deficit_w": self.source_deficit_w,
            "battery_allowed": self.battery_allowed,
            "battery_direction": self.battery_direction,
            "battery_power_w": self.battery_power_w,
            "owned_loads": [
                {"entity_id": lease.load.entity_id, "activated_at": lease.activated_at.isoformat()}
                for lease in self._leases.values()
            ],
            "pending_activation": self._pending_activation[0].entity_id
            if self._pending_activation is not None
            else None,
            "feedback": {
                "waiting": feedback_assessment is not None,
                "action": feedback_assessment.action
                if feedback_assessment is not None
                else None,
                "load_entity_id": feedback_assessment.load_entity_id
                if feedback_assessment is not None
                else None,
                "not_before": feedback_assessment.next_not_before.isoformat()
                if feedback_assessment is not None
                else None,
                "votes": list(feedback_assessment.votes)
                if feedback_assessment is not None
                else [],
                "sample_count": feedback_assessment.sample_count
                if feedback_assessment is not None
                else self.config.feedback_sample_count,
                "required_yes_votes": feedback_assessment.required_yes_votes
                if feedback_assessment is not None
                else self.config.feedback_sample_count // 2 + 1,
                "pending_entities": sorted(pending_feedback),
                "last_reports": {
                    entity_id: reported_at.isoformat()
                    for entity_id, reported_at in self._feedback_reports.items()
                },
            },
            "blocked_loads": sorted(self._cycle_memory.blocked_loads),
            "learned_range": {
                "supported_at_least_w": self._cycle_memory.lower_supported_w,
                "unsupported_at_or_above_w": self._cycle_memory.upper_unsupported_w,
            },
            "pending_unsupported_release": self._pending_unsupported_release,
            "loads": [self._load_status(load) for load in self.config.loads],
            "history": self._event_history,
        }
