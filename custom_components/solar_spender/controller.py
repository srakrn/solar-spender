"""Event-driven Solar Spender controller."""

from __future__ import annotations

from asyncio import sleep
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

from .battery import direction_from_power, waste_headroom_available
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
    STATE_PAUSED,
    STATE_PROBING,
    STATE_SHEDDING,
    STATE_SPENDING,
    STATE_WAITING_FEEDBACK,
)
from .control import effective_release_shortfall_w, owned_load_shed_reason
from .feedback import (
    CycleMemory,
    FeedbackAssessment,
    LearnedDrawEstimate,
    LoadPowerReading,
    accumulate_fallback_energy_wh,
    append_bounded_event,
    effective_load_draw_w,
    input_is_fresh,
    load_power_reading,
    probe_fallback_power_w,
    worst_case_probe_energy_wh,
)
from .models import LoadConfig, SolarSpenderConfig
from .selection import best_to_release_for_shortfall, first_to_activate
from .runtime import (
    climate_matches_commanded_profile,
    load_definition_matches_profile,
    parse_aware_datetime,
    pause_remaining_seconds,
)
from .source import (
    observable_surplus_available,
    zero_export_opportunity_available,
)
from .storage import LearningStore, RuntimeStore

_INVALID_STATES = {STATE_UNKNOWN, STATE_UNAVAILABLE}


@dataclass(slots=True)
class Lease:
    """A climate load Solar Spender is permitted to release."""

    load: LoadConfig
    activated_at: datetime
    commanded_profile: dict[str, Any]
    previous_profile: dict[str, Any]


@dataclass(slots=True)
class ProbeRuntime:
    """Persisted fallback accounting for one zero-export activation."""

    load_entity_id: str
    started_at: datetime
    accumulated_energy_wh: float
    last_sample_at: datetime
    last_fallback_power_w: float


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
        self.grid_import_w: float | None = None
        self.reason = "off" if not config.enabled else "waiting for solar sensors"
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
        self._feedback_timeout_scheduled: Callable[[], None] | None = None
        self._input_expiry_scheduled: Callable[[], None] | None = None
        self._load_power_expiry_scheduled: Callable[[], None] | None = None
        self._probe_budget_expiry_scheduled: Callable[[], None] | None = None
        self._probe_runtime: ProbeRuntime | None = None
        self._reconciling = False
        self._event_history: list[dict[str, str]] = []
        self._learning_store = LearningStore(hass, entry_id)
        self._learned_draws: dict[str, LearnedDrawEstimate] = {}
        self._runtime_store = RuntimeStore(hass, entry_id)
        self._restored_lease_count = 0
        self._discarded_lease_count = 0
        self._paused_until: datetime | None = None
        self._paused_feedback_assessment: FeedbackAssessment | None = None
        self._resume_requires_recovery = False

    async def async_start(self) -> None:
        """Subscribe to all configured entities and evaluate initial state."""
        self._learned_draws = await self._learning_store.async_load()
        self._restore_runtime_state(await self._runtime_store.async_load())
        entity_ids = self._watched_entities()
        for entity_id in self._feedback_entity_ids:
            if (state := self.hass.states.get(entity_id)) is not None:
                self._feedback_reports[entity_id] = state.last_reported
        if entity_ids:
            self._unsubscribers.append(
                async_track_state_change_event(self.hass, entity_ids, self._async_state_changed)
            )
        if self._feedback_entity_ids or self._load_power_entity_ids:
            self._unsubscribers.append(
                self.hass.bus.async_listen(
                    EVENT_STATE_REPORTED,
                    self._async_state_reported,
                    event_filter=self._is_feedback_report,
                )
            )
        if (
            self._leases
            and self._probe_runtime is None
            and not self._pause_is_active()
        ):
            self._begin_recovery_feedback("restored leases")
            self._record(
                f"Restored {len(self._leases)} owned AC(s). "
                "Checking solar again."
            )
        if self._pause_is_active():
            assert self._paused_until is not None
            self._schedule_reconcile(
                max(
                    0,
                    (
                        self._paused_until - datetime.now().astimezone()
                    ).total_seconds(),
                )
            )
        self._schedule_input_expiry()
        self._schedule_load_power_expiry()
        self._schedule_probe_budget_expiry()
        await self.async_reconcile("started")

    async def async_stop(self) -> None:
        """Stop all subscriptions and pending callbacks without altering loads."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._scheduled is not None:
            self._scheduled()
            self._scheduled = None
        if self._feedback_timeout_scheduled is not None:
            self._feedback_timeout_scheduled()
            self._feedback_timeout_scheduled = None
        if self._input_expiry_scheduled is not None:
            self._input_expiry_scheduled()
            self._input_expiry_scheduled = None
        if self._load_power_expiry_scheduled is not None:
            self._load_power_expiry_scheduled()
            self._load_power_expiry_scheduled = None
        if self._probe_budget_expiry_scheduled is not None:
            self._probe_budget_expiry_scheduled()
            self._probe_budget_expiry_scheduled = None
        await self._runtime_store.async_save(self._runtime_payload())

    async def async_apply_runtime_config(self, config: SolarSpenderConfig) -> None:
        """Apply options exposed as entities without dropping active leases."""
        self.config = config
        self._schedule_input_expiry()
        self._schedule_load_power_expiry()
        self._schedule_probe_budget_expiry()
        if self._feedback_assessment is not None:
            assessment = self._feedback_assessment
            assessment.deadline = assessment.next_not_before + timedelta(
                minutes=config.feedback_timeout_minutes
            )
            self._set_feedback_assessment(assessment)
        if not config.enabled:
            if self._probe_runtime is not None:
                self._pending_unsupported_release = (
                    self._probe_runtime.load_entity_id
                )
            self._paused_until = None
            self._paused_feedback_assessment = None
            self._resume_requires_recovery = False
            self._clear_feedback_assessment()
            self._next_load_not_before = None
            if self._scheduled is not None:
                self._scheduled()
                self._scheduled = None
        await self.async_reconcile("runtime setting changed")

    async def async_set_pause(self, minutes: int) -> None:
        """Pause all controller reactions temporarily, or resume with zero."""
        if minutes < 0 or minutes > 1440:
            raise HomeAssistantError("Pause must be from 0 to 1440 minutes.")
        if minutes and not self.config.enabled:
            raise HomeAssistantError("Turn on Solar Spender before pausing it.")

        # A climate change is atomic from the controller's perspective. If one
        # is already in flight, start the pause immediately after it completes.
        while self._reconciling:
            await sleep(0)

        now = datetime.now().astimezone()
        if self._scheduled is not None:
            self._scheduled()
            self._scheduled = None

        if minutes:
            self._confirm_pending_activation()
            if self._feedback_assessment is not None:
                self._paused_feedback_assessment = self._feedback_assessment
            self._clear_feedback_assessment()
            self._paused_until = now + timedelta(minutes=minutes)
            self._set_state(
                STATE_PAUSED,
                f"paused until {self._paused_until.isoformat(timespec='seconds')}",
            )
            self._schedule_reconcile(minutes * 60)
        else:
            was_paused = self._paused_until is not None
            self._paused_until = None
            if was_paused:
                self._resume_after_pause(now, "Pause ended")

        await self._runtime_store.async_save(self._runtime_payload())
        await self.async_reconcile("pause changed")

    def supports_runtime_config(self, config: SolarSpenderConfig) -> bool:
        """Return whether config can change without replacing subscriptions."""
        return replace(
            config,
            enabled=self.config.enabled,
            settling_seconds=self.config.settling_seconds,
            feedback_sample_count=self.config.feedback_sample_count,
            feedback_timeout_minutes=self.config.feedback_timeout_minutes,
            input_max_age_minutes=self.config.input_max_age_minutes,
            next_load_delay_minutes=self.config.next_load_delay_minutes,
        ) == self.config

    @callback
    def _async_state_changed(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        if entity_id in self._feedback_entity_ids:
            new_state = event.data.get("new_state")
            reported_at = (
                new_state.last_reported
                if new_state is not None
                else datetime.now().astimezone()
            )
            self._feedback_reports[entity_id] = reported_at
            self._schedule_input_expiry()
        if entity_id in self._load_power_entity_ids:
            self._schedule_load_power_expiry()
        self.hass.async_create_task(self.async_reconcile(f"state changed: {entity_id}"))

    @callback
    def _is_feedback_report(self, event_data: dict[str, Any]) -> bool:
        """Limit the high-volume state_reported event to configured feedback."""
        return event_data.get("entity_id") in (
            self._feedback_entity_ids | self._load_power_entity_ids
        )

    @callback
    def _async_state_reported(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        if entity_id in self._feedback_entity_ids:
            self._feedback_reports[entity_id] = event.data["last_reported"]
            self._schedule_input_expiry()
        if entity_id in self._load_power_entity_ids:
            self._schedule_load_power_expiry()
        self.hass.async_create_task(self.async_reconcile(f"state reported: {entity_id}"))

    @property
    def _load_power_entity_ids(self) -> frozenset[str]:
        """Return optional per-load power entities with silence semantics."""
        return frozenset(
            load.power_entity_id
            for load in self.config.loads
            if load.power_entity_id
        )

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
        values = self._source_entities() | self._battery_entities()
        if self.config.source_type == SOURCE_CURTAILED:
            values |= frozenset({self.config.grid_entity_id})
        return frozenset(value for value in values if value)

    def _source_entities(self) -> frozenset[str]:
        """Return source entities whose age determines source validity."""
        config = self.config
        if config.source_type == SOURCE_GRID:
            values = {config.grid_entity_id}
        else:
            values = {config.production_entity_id, config.consumption_entity_id}
        return frozenset(value for value in values if value)

    def _battery_entities(self) -> frozenset[str]:
        """Return configured battery inputs that must remain fresh."""
        config = self.config
        values: set[str] = set()
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
            now = datetime.now().astimezone()
            if self._pause_is_active(now):
                assert self._paused_until is not None
                self._set_state(
                    STATE_PAUSED,
                    f"paused until {self._paused_until.isoformat(timespec='seconds')}",
                )
                return
            if self._paused_until is not None:
                self._paused_until = None
                self._resume_after_pause(now, "Pause expired")
            self._update_inputs()
            self._relinquish_manual_overrides()
            self._confirm_pending_activation()
            self._update_probe_accounting(now)
            if not self.config.enabled:
                self._set_state(STATE_DISABLED, "disabled")
                return
            if (
                self._probe_runtime is not None
                and self._probe_runtime.accumulated_energy_wh
                >= self.config.probe_max_fallback_energy_wh
            ):
                entity_id = self._probe_runtime.load_entity_id
                if (
                    self._feedback_assessment is not None
                    and self._feedback_assessment.action == "activation"
                    and self._feedback_assessment.load_entity_id == entity_id
                ):
                    self._take_feedback_assessment()
                self._mark_unsupported_activation(entity_id, global_block=True)
                await self._async_shed_one(
                    "the probe used its fallback energy budget",
                    target_entity_id=entity_id,
                )
                return
            if (
                self._feedback_assessment is not None
                and self._feedback_assessment.timed_out(now)
            ):
                await self._async_feedback_timed_out()
                return
            if self._feedback_assessment is not None and not self.source_valid:
                assessment = self._take_feedback_assessment()
                assert assessment is not None
                self._record("Check failed because a solar sensor became unavailable.")
                if assessment.action == "activation":
                    self._mark_unsupported_activation(
                        assessment.load_entity_id,
                        global_block=True,
                    )
                    await self._async_shed_one(
                        "a solar sensor became unavailable during the check",
                        target_entity_id=assessment.load_entity_id,
                    )
                elif self._leases:
                    await self._async_shed_one(
                        "a solar sensor became unavailable during checks"
                    )
                return
            if (
                self._feedback_assessment is not None
                and self._feedback_assessment.action == "activation"
                and (
                    self.battery_direction == "discharging"
                    or (
                        self.config.source_type == SOURCE_CURTAILED
                        and not self.battery_allowed
                    )
                )
            ):
                assessment = self._take_feedback_assessment()
                assert assessment is not None
                self._mark_unsupported_activation(
                    assessment.load_entity_id,
                    global_block=True,
                )
                await self._async_shed_one(
                    "the battery could not support the new AC",
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
                assessment = self._take_feedback_assessment()
                assert assessment is not None
                self._mark_unsupported_activation(
                    assessment.load_entity_id,
                    global_block=True,
                )
                await self._async_shed_one(
                    "the new AC used too much grid power",
                    target_entity_id=assessment.load_entity_id,
                )
                return
            if self._feedback_assessment is not None:
                assessment = self._feedback_assessment
                pending_reports = assessment.pending_entities(self._feedback_reports)
                if pending_reports:
                    self._set_state(
                        STATE_WAITING_FEEDBACK,
                        f"check {len(assessment.votes)}/"
                        f"{assessment.sample_count}; waiting for "
                        + ", ".join(sorted(pending_reports)),
                    )
                    return
                assessment.record_vote(
                    supported=self._feedback_vote_supported(),
                    reports=self._feedback_reports,
                    measurement_w=self._power_value(
                        self.config.consumption_entity_id
                    )
                    if self.config.consumption_entity_id
                    else None,
                )
                self._record(
                    f"Check {len(assessment.votes)}/"
                    f"{assessment.sample_count} for {assessment.load_entity_id}: "
                    f"{'pass' if assessment.votes[-1] else 'fail'}"
                )
                if not assessment.complete:
                    self._set_state(
                        STATE_WAITING_FEEDBACK,
                        f"check {len(assessment.votes)}/"
                        f"{assessment.sample_count}",
                    )
                    return
                self._clear_feedback_assessment()
                if assessment.action == "activation":
                    if assessment.supported:
                        self._record_combination(True)
                        await self._async_learn_draw(assessment)
                        self._clear_probe_runtime()
                else:
                    self._record_combination(True)
                if assessment.action == "activation" and not assessment.supported:
                    self._mark_unsupported_activation(assessment.load_entity_id)
                    await self._async_shed_one(
                        "most checks failed",
                        target_entity_id=assessment.load_entity_id,
                    )
                    return
                if assessment.action == "recovery" and not assessment.supported:
                    await self._async_shed_one(
                        "not enough spare solar after restart"
                    )
                    return
                if not assessment.supported:
                    self._set_state(
                        STATE_SPENDING if self._leases else STATE_MONITORING,
                        "most checks failed",
                    )
                    return
                self._next_load_not_before = (
                    datetime.now().astimezone()
                    + timedelta(minutes=self.config.next_load_delay_minutes)
                )
                self._schedule_reconcile(self.config.next_load_delay_minutes * 60)
                self._set_state(
                    STATE_SPENDING if self._leases else STATE_MONITORING,
                    "checks passed; waiting before the next AC",
                )
                return
            if self._pending_unsupported_release is not None:
                if self._pending_unsupported_release not in self._leases:
                    self._pending_unsupported_release = None
                else:
                    await self._async_shed_one(
                        "turning off the AC that failed its checks",
                        target_entity_id=self._pending_unsupported_release,
                    )
                    return
            if self._cycle_memory.reset_if_cycle_ended(
                len(self._leases),
                self._pending_activation is not None,
                self.surplus_available,
                self._feedback_assessment is not None,
            ):
                self._record("Reset AC blocks because spare solar ended.")
            shed_reason = owned_load_shed_reason(
                has_owned_loads=bool(self._leases),
                source_available=self.surplus_available,
                battery_allowed=self.battery_allowed,
                battery_direction=self.battery_direction,
                probing=self.state == STATE_PROBING,
            )
            if shed_reason is not None:
                await self._async_shed_one(
                    shed_reason,
                    block_for_cycle=True,
                )
                return
            if not self.surplus_available:
                self._set_state(STATE_MONITORING, self.reason)
                return
            if not self.battery_allowed:
                self._set_state(STATE_BLOCKED_BATTERY, self.reason)
                return
            if (
                self._next_load_not_before is not None
                and datetime.now().astimezone() < self._next_load_not_before
            ):
                self._set_state(
                    STATE_SPENDING if self._leases else STATE_MONITORING,
                    "waiting before the next AC",
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
        self._update_probe_grid_import()
        if (
            self.config.source_type == SOURCE_CURTAILED
            and self.grid_import_w is None
        ):
            self.battery_allowed = False
            battery_reason = "grid power sensor is unavailable or stale"
        if not self.battery_allowed:
            self.reason = battery_reason

    def _update_probe_grid_import(self) -> None:
        """Normalize optional/required grid feedback to import-positive watts."""
        self.grid_import_w = None
        if self.config.source_type != SOURCE_CURTAILED:
            return
        watts = self._power_value(
            self.config.grid_entity_id,
            require_fresh=True,
        )
        if watts is None:
            return
        export_w = watts if self.config.grid_export_positive else -watts
        self.grid_import_w = max(0.0, -export_w)

    def _battery_discharge_w(self) -> float | None:
        """Return normalized battery discharge power when numerically known."""
        if self.battery_power_w is None:
            return None
        return max(0.0, -self.battery_power_w)

    def _current_probe_fallback_power_w(self) -> float | None:
        """Return observable probe fallback power above configured allowances."""
        battery_discharge_w = self._battery_discharge_w()
        if self.grid_import_w is None or battery_discharge_w is None:
            return None
        return probe_fallback_power_w(
            grid_import_w=self.grid_import_w,
            grid_import_allowance_w=self.config.probe_grid_import_allowance_w,
            battery_discharge_w=battery_discharge_w,
            battery_idle_threshold_w=self.config.battery_power_threshold_w,
        )

    def _feedback_vote_supported(self) -> bool:
        """Apply source and settled probe evidence to one feedback vote."""
        if self.config.source_type != SOURCE_CURTAILED:
            return self.surplus_available
        battery_discharge_w = self._battery_discharge_w()
        return (
            self.surplus_available
            and self.grid_import_w is not None
            and self.grid_import_w
            <= self.config.probe_grid_import_allowance_w
            and battery_discharge_w is not None
            and battery_discharge_w
            <= self.config.battery_power_threshold_w
        )

    def _update_source(self) -> None:
        config = self.config
        if config.source_type == SOURCE_GRID:
            watts = self._power_value(config.grid_entity_id)
            self.raw_source_value = watts
            if watts is None or not self._input_is_fresh(config.grid_entity_id):
                self._clear_source(
                    "grid power sensor stale"
                    if watts is not None
                    else "grid power sensor unavailable"
                )
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
            if (
                production_w is None
                or consumption_w is None
                or not self._input_is_fresh(config.production_entity_id)
                or not self._input_is_fresh(config.consumption_entity_id)
            ):
                self._clear_source(
                    "solar or home power sensor stale"
                    if production_w is not None and consumption_w is not None
                    else "solar or home power sensor unavailable"
                )
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
                    "ready to test one AC"
                    if opportunity.available
                    else "not ready to test an AC"
                )
                return

        self.surplus_available = observable_surplus_available(
            was_available=self.surplus_available,
            headroom_w=decision_w,
            entry_threshold_w=config.entry_threshold_w,
            exit_threshold_w=config.exit_threshold_w,
        )
        self.reason = (
            f"spare solar {'available' if self.surplus_available else 'too low'}"
        )

    def _clear_source(self, reason: str) -> None:
        self.source_valid = False
        self.surplus_available = False
        self.headroom_w = None
        self.opportunity_power_w = None
        self.source_deficit_w = None
        self.reason = reason

    def _power_value(
        self,
        entity_id: str,
        *,
        require_fresh: bool = False,
    ) -> float | None:
        state = self.hass.states.get(entity_id)
        if (
            state is None
            or state.state in _INVALID_STATES
            or (require_fresh and not self._input_is_fresh(entity_id))
        ):
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
            return True, "battery ignored"
        status = self._battery_direction()
        charging = status == "charging"
        discharging = status == "discharging"
        soc = self._numeric_state(
            config.battery_soc_entity_id,
            require_fresh=True,
        )
        if config.battery_policy == BATTERY_REQUIRE_CHARGING:
            return charging, "battery is not charging"
        if config.battery_policy == BATTERY_CHARGING_OR_SOC:
            return (
                charging
                or (soc is not None and soc >= config.battery_full_threshold),
                "battery does not pass the rule",
            )
        if config.battery_policy == BATTERY_FULL_IDLE_FOR_PROBE:
            allowed = (
                soc is not None
                and soc >= config.battery_full_threshold
                and not charging
                and not discharging
                and bool(status)
            )
            return allowed, "battery must be full and idle"
        return False, "battery rule is invalid"

    def _battery_direction(self) -> str:
        if self.config.battery_direction_source == BATTERY_DIRECTION_POWER:
            power_w = self._power_value(
                self.config.battery_power_entity_id,
                require_fresh=True,
            )
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
        if (
            state is None
            or state.state in _INVALID_STATES
            or not self._input_is_fresh(self.config.battery_status_entity_id)
        ):
            self.battery_direction = "unknown"
            self.battery_power_w = None
            return ""
        if (
            state.entity_id.startswith("binary_sensor.")
            and state.attributes.get("device_class") == "battery_charging"
        ):
            direction = "charging" if state.state == "on" else "idle"
        else:
            raw_direction = state.state.lower()
            if raw_direction in self.config.charging_states:
                direction = "charging"
            elif raw_direction in self.config.discharging_states:
                direction = "discharging"
            else:
                direction = raw_direction
        self.battery_direction = direction
        self.battery_power_w = None
        return direction

    @property
    def waste_headroom_available(self) -> bool:
        """Return whether qualified solar would otherwise remain unused."""
        return waste_headroom_available(
            source_valid=self.source_valid,
            surplus_available=self.surplus_available,
            battery_configured=self.config.battery_policy != BATTERY_DISABLED,
            battery_allowed=self.battery_allowed,
            battery_direction=self.battery_direction,
        )

    def _numeric_state(
        self,
        entity_id: str,
        *,
        require_fresh: bool = False,
    ) -> float | None:
        state = self.hass.states.get(entity_id)
        if (
            state is None
            or state.state in _INVALID_STATES
            or (require_fresh and not self._input_is_fresh(entity_id))
        ):
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
                "remaining ACs are blocked for now"
                if (
                    self._cycle_memory.blocked_loads
                    or self._cycle_memory.unsupported_combinations
                )
                else "no AC is ready"
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
                "not enough spare solar for another AC",
            )
            return
        self._set_state(
            STATE_PROBING if self.config.source_type == SOURCE_CURTAILED else STATE_SPENDING,
            f"turning on {candidate.entity_id}: {reason}",
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
            if (
                self.config.source_type == SOURCE_CURTAILED
                and not self._probe_preflight_fits(load)
            ):
                continue
            candidates.append(load)
        # ``candidates`` retains configuration order, so Python's stable
        # minimum gives equally prioritized loads a predictable tie-break.
        return first_to_activate(candidates, lambda load: load.priority)

    def _probe_preflight_fits(self, load: LoadConfig) -> bool:
        """Require the configured budget to cover a fail-closed probe."""
        required_wh = self._probe_preflight_energy_wh(load)
        return (
            required_wh is not None
            and required_wh <= self.config.probe_max_fallback_energy_wh
        )

    def _probe_preflight_energy_wh(
        self,
        load: LoadConfig,
    ) -> float | None:
        """Return the conservative fallback energy required by one load."""
        if load.expected_power_w is None:
            return None
        return worst_case_probe_energy_wh(
            expected_power_w=load.expected_power_w,
            settling_seconds=self.config.settling_seconds,
            minimum_on_seconds=load.min_on_seconds,
            feedback_timeout_seconds=self.config.feedback_timeout_minutes * 60,
        )

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
        if self.config.source_type == SOURCE_CURTAILED:
            fallback_power_w = self._current_probe_fallback_power_w() or 0.0
            self._probe_runtime = ProbeRuntime(
                load_entity_id=load.entity_id,
                started_at=action_completed_at,
                accumulated_energy_wh=0.0,
                last_sample_at=action_completed_at,
                last_fallback_power_w=fallback_power_w,
            )
            self._schedule_probe_budget_expiry()
        self._begin_feedback_assessment(
            "activation",
            load.entity_id,
            action_completed_at,
            minimum_on_seconds=load.min_on_seconds,
            baseline_consumption_w=baseline_consumption_w,
        )
        self._record(f"Turned on {load.entity_id}. Waiting to check it.")
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
                self._clear_feedback_assessment()
            self._clear_probe_runtime(load.entity_id)
            self._record(f"{load.entity_id} did not turn on.")
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
        self._record(f"{load.entity_id} is now owned.")

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
                    f"Left {target_entity_id} alone because it is no longer owned."
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
            self._set_state(STATE_SHEDDING, "waiting for the minimum on time")
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
                    f"Need to remove {round(shortfall_w)} W. Chose "
                    f"{lease.load.entity_id}, which uses "
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
        self._set_state(STATE_SHEDDING, f"turning off {lease.load.entity_id}: {reason}")
        try:
            await self.hass.services.async_call(
                "climate", "turn_off", {"entity_id": lease.load.entity_id}, blocking=True
            )
        except HomeAssistantError as err:
            self._record(f"Could not turn off {lease.load.entity_id}: {err}")
            self._schedule_reconcile(self.config.settling_seconds)
            return
        await self._async_restore_profile(lease)
        self._leases.pop(lease.load.entity_id, None)
        self._clear_probe_runtime(lease.load.entity_id)
        if self._pending_unsupported_release == lease.load.entity_id:
            self._pending_unsupported_release = None
        self._last_off[lease.load.entity_id] = now
        self._record(f"Turned off {lease.load.entity_id}: {reason}")
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
        self._set_feedback_assessment(
            FeedbackAssessment(
                action=action,
                load_entity_id=load_entity_id,
                next_not_before=not_before,
                deadline=not_before
                + timedelta(minutes=self.config.feedback_timeout_minutes),
                required_entities=self._feedback_entity_ids,
                sample_count=self.config.feedback_sample_count,
                baseline_consumption_w=baseline_consumption_w,
            ),
        )
        self._record(
            f"Waiting to check {load_entity_id}. First sensor reports must be after "
            f"{not_before.isoformat()}"
        )

    async def _async_feedback_timed_out(self) -> None:
        """Fail closed when distinct fresh reports do not arrive in time."""
        assessment = self._take_feedback_assessment()
        assert assessment is not None
        self._record(
            f"Checks timed out for {assessment.load_entity_id} after "
            f"{self.config.feedback_timeout_minutes:g} minutes."
        )
        if assessment.action == "activation":
            self._clear_source("fresh sensor checks timed out")
            self._mark_unsupported_activation(
                assessment.load_entity_id,
                global_block=True,
            )
            await self._async_shed_one(
                "fresh sensor checks timed out",
                target_entity_id=assessment.load_entity_id,
            )
        elif not self.source_valid and self._leases:
            await self._async_shed_one("fresh sensor checks timed out")
        elif not self.source_valid:
            self._set_state(STATE_MONITORING, "fresh sensor checks timed out")
        else:
            now = datetime.now().astimezone()
            self._set_feedback_assessment(
                FeedbackAssessment(
                    action="recovery",
                    load_entity_id=assessment.load_entity_id,
                    next_not_before=now,
                    deadline=now
                    + timedelta(minutes=self.config.feedback_timeout_minutes),
                    required_entities=self._feedback_entity_ids,
                    sample_count=self.config.feedback_sample_count,
                )
            )
            self._set_state(
                STATE_WAITING_FEEDBACK,
                "checks timed out; waiting for a new fresh sequence",
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
        return effective_load_draw_w(
            self._load_power_reading(load),
            conservative_estimate_w=self._expected_draws().get(
                load.entity_id
            ),
        )

    def _load_power_reading(
        self,
        load: LoadConfig,
        now: datetime | None = None,
    ) -> LoadPowerReading:
        """Resolve one optional live power sensor, including silent zero."""
        if not load.power_entity_id:
            return LoadPowerReading(None, False, None)
        state = self.hass.states.get(load.power_entity_id)
        value_w = self._power_value(load.power_entity_id)
        return load_power_reading(
            value_w,
            state.last_reported if state is not None else None,
            now=now or datetime.now().astimezone(),
            zero_after=timedelta(minutes=load.power_zero_after_minutes),
        )

    def _release_shortfall_w(self) -> float:
        """Return the currently observable watts that load removal should cover."""
        if self.config.source_type == SOURCE_CURTAILED:
            source_shortfall_w = max(0.0, self.source_deficit_w or 0.0)
        else:
            source_shortfall_w = max(0.0, -(self.headroom_w or 0.0))
        return effective_release_shortfall_w(
            source_shortfall_w=source_shortfall_w,
            battery_direction=self.battery_direction,
            charging_positive_battery_w=self.battery_power_w,
        )

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
            f"Blocked {'AC' if global_block else 'AC group'} "
            f"containing {entity_id} for now."
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
                f"Could not learn the power used by {assessment.load_entity_id}: "
                "home power changed too much."
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
            f"{assessment.load_entity_id} uses about "
            f"{round(estimate.estimate_w)} W based on {estimate.samples} check(s)."
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
            self._record(f"Restored the old settings for {entity_id}.")
        except HomeAssistantError as err:
            self._record(f"Could not restore all settings for {entity_id}: {err}")

    def _relinquish_manual_overrides(self) -> None:
        for entity_id, lease in list(self._leases.items()):
            state = self.hass.states.get(entity_id)
            if state is None or state.state in _INVALID_STATES or state.state == STATE_OFF:
                self._leases.pop(entity_id, None)
                self._clear_probe_runtime(entity_id)
                self._record(
                    f"{entity_id} is no longer owned because it was turned off."
                )
                continue
            if lease.load.hvac_mode is not None and state.state != lease.load.hvac_mode:
                self._leases.pop(entity_id, None)
                self._clear_probe_runtime(entity_id)
                self._record(
                    f"{entity_id} is no longer owned because its mode changed."
                )
                continue
            if lease.load.temperature is not None:
                actual = state.attributes.get(ATTR_TEMPERATURE)
                if actual is not None and abs(float(actual) - lease.load.temperature) > 0.1:
                    self._leases.pop(entity_id, None)
                    self._clear_probe_runtime(entity_id)
                    self._record(
                        f"{entity_id} is no longer owned because its temperature changed."
                    )
                    continue
            if (
                lease.load.fan_mode is not None
                and state.attributes.get("fan_mode") != lease.load.fan_mode
            ):
                self._leases.pop(entity_id, None)
                self._clear_probe_runtime(entity_id)
                self._record(
                    f"{entity_id} is no longer owned because its fan changed."
                )
        if (
            self._feedback_assessment is not None
            and self._feedback_assessment.action == "activation"
            and self._feedback_assessment.load_entity_id not in self._leases
            and self._pending_activation is None
        ):
            self._record(
                f"Stopped checking {self._feedback_assessment.load_entity_id}: "
                "it is no longer owned."
            )
            self._clear_feedback_assessment()
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

    def _set_feedback_assessment(self, assessment: FeedbackAssessment) -> None:
        """Install an assessment and arm its independent fail-closed timeout."""
        self._clear_feedback_assessment()
        self._feedback_assessment = assessment
        delay = max(
            0,
            (assessment.deadline - datetime.now().astimezone()).total_seconds(),
        )
        self._feedback_timeout_scheduled = async_call_later(
            self.hass,
            delay,
            self._async_feedback_timeout,
        )

    def _clear_feedback_assessment(self) -> None:
        """Discard the current assessment and its timeout callback."""
        self._feedback_assessment = None
        if self._feedback_timeout_scheduled is not None:
            self._feedback_timeout_scheduled()
            self._feedback_timeout_scheduled = None

    def _take_feedback_assessment(self) -> FeedbackAssessment | None:
        """Remove and return the current assessment."""
        assessment = self._feedback_assessment
        self._clear_feedback_assessment()
        return assessment

    @callback
    def _async_feedback_timeout(self, _now: datetime) -> None:
        self._feedback_timeout_scheduled = None
        self.hass.async_create_task(self.async_reconcile("feedback timeout"))

    def _input_is_fresh(
        self,
        entity_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Return whether an input has reported within the configured age."""
        reported_at = self._feedback_reports.get(entity_id)
        if reported_at is None:
            state = self.hass.states.get(entity_id)
            reported_at = state.last_reported if state is not None else None
        current = now or datetime.now().astimezone()
        return input_is_fresh(
            reported_at,
            now=current,
            maximum_age=timedelta(minutes=self.config.input_max_age_minutes),
        )

    def _stale_input_entities(
        self,
        now: datetime | None = None,
    ) -> frozenset[str]:
        """Return configured decision inputs older than the maximum age."""
        current = now or datetime.now().astimezone()
        return frozenset(
            entity_id
            for entity_id in self._feedback_entity_ids
            if not self._input_is_fresh(entity_id, current)
        )

    def _schedule_input_expiry(self) -> None:
        """Reconcile when the next currently fresh decision input expires."""
        if self._input_expiry_scheduled is not None:
            self._input_expiry_scheduled()
            self._input_expiry_scheduled = None
        now = datetime.now().astimezone()
        maximum_age = timedelta(minutes=self.config.input_max_age_minutes)
        deadlines = [
            reported_at + maximum_age
            for entity_id, reported_at in self._feedback_reports.items()
            if entity_id in self._feedback_entity_ids
            and reported_at + maximum_age > now
        ]
        if not deadlines:
            return
        delay = max(0, (min(deadlines) - now).total_seconds())
        self._input_expiry_scheduled = async_call_later(
            self.hass,
            delay,
            self._async_input_expired,
        )

    def _schedule_load_power_expiry(self) -> None:
        """Reconcile when a valid per-load derivative reading becomes zero."""
        if self._load_power_expiry_scheduled is not None:
            self._load_power_expiry_scheduled()
            self._load_power_expiry_scheduled = None
        now = datetime.now().astimezone()
        deadlines: list[datetime] = []
        for load in self.config.loads:
            if not load.power_entity_id:
                continue
            state = self.hass.states.get(load.power_entity_id)
            value_w = self._power_value(load.power_entity_id)
            if state is None or value_w is None or value_w < 0:
                continue
            deadline = state.last_reported + timedelta(
                minutes=load.power_zero_after_minutes
            ) + timedelta(microseconds=1)
            if deadline > now:
                deadlines.append(deadline)
        if not deadlines:
            return
        delay = max(0.0, (min(deadlines) - now).total_seconds())
        self._load_power_expiry_scheduled = async_call_later(
            self.hass,
            delay,
            self._async_load_power_expired,
        )

    @callback
    def _async_load_power_expired(self, _now: datetime) -> None:
        self._load_power_expiry_scheduled = None
        self._schedule_load_power_expiry()
        self.hass.async_create_task(
            self.async_reconcile("AC power assume-zero time expired")
        )

    def _update_probe_accounting(self, now: datetime) -> None:
        """Accumulate bounded fallback energy for the active probe."""
        runtime = self._probe_runtime
        if runtime is None:
            return
        current_power_w = self._current_probe_fallback_power_w()
        if current_power_w is None:
            self._schedule_probe_budget_expiry()
            return
        runtime.accumulated_energy_wh = accumulate_fallback_energy_wh(
            runtime.accumulated_energy_wh,
            previous_power_w=runtime.last_fallback_power_w,
            current_power_w=current_power_w,
            elapsed_seconds=max(
                0.0,
                (now - runtime.last_sample_at).total_seconds(),
            ),
        )
        runtime.last_sample_at = now
        runtime.last_fallback_power_w = current_power_w
        self._runtime_store.async_delay_save(self._runtime_payload)
        self._schedule_probe_budget_expiry()

    def _schedule_probe_budget_expiry(self) -> None:
        """Reconcile when steady fallback power would exhaust the budget."""
        if self._probe_budget_expiry_scheduled is not None:
            self._probe_budget_expiry_scheduled()
            self._probe_budget_expiry_scheduled = None
        runtime = self._probe_runtime
        if (
            runtime is None
            or not self.config.enabled
            or self.config.probe_max_fallback_energy_wh <= 0
            or runtime.last_fallback_power_w <= 0
        ):
            return
        remaining_wh = max(
            0.0,
            self.config.probe_max_fallback_energy_wh
            - runtime.accumulated_energy_wh,
        )
        delay = remaining_wh * 3600 / runtime.last_fallback_power_w
        self._probe_budget_expiry_scheduled = async_call_later(
            self.hass,
            max(0.0, delay),
            self._async_probe_budget_expired,
        )

    @callback
    def _async_probe_budget_expired(self, _now: datetime) -> None:
        self._probe_budget_expiry_scheduled = None
        self.hass.async_create_task(
            self.async_reconcile("probe fallback budget deadline")
        )

    def _clear_probe_runtime(self, entity_id: str | None = None) -> None:
        """Forget completed probe accounting without affecting other loads."""
        if (
            self._probe_runtime is None
            or (
                entity_id is not None
                and self._probe_runtime.load_entity_id != entity_id
            )
        ):
            return
        self._probe_runtime = None
        if self._probe_budget_expiry_scheduled is not None:
            self._probe_budget_expiry_scheduled()
            self._probe_budget_expiry_scheduled = None

    @callback
    def _async_input_expired(self, _now: datetime) -> None:
        self._input_expiry_scheduled = None
        self._schedule_input_expiry()
        self.hass.async_create_task(self.async_reconcile("input age expired"))

    def _pause_is_active(self, now: datetime | None = None) -> bool:
        """Return whether the persisted temporary pause is still active."""
        current = now or datetime.now().astimezone()
        return pause_remaining_seconds(self._paused_until, current) > 0

    def _begin_recovery_feedback(self, label: str) -> None:
        """Require fresh post-boundary reports before changing another load."""
        now = datetime.now().astimezone()
        not_before = now + timedelta(seconds=self.config.settling_seconds)
        self._set_feedback_assessment(
            FeedbackAssessment(
                action="recovery",
                load_entity_id=label,
                next_not_before=not_before,
                deadline=not_before
                + timedelta(minutes=self.config.feedback_timeout_minutes),
                required_entities=self._feedback_entity_ids,
                sample_count=self.config.feedback_sample_count,
            ),
        )

    def _resume_after_pause(self, now: datetime, message: str) -> None:
        """Restore only feedback work that still needs a post-pause decision."""
        assessment = self._paused_feedback_assessment
        self._paused_feedback_assessment = None
        if assessment is not None and (
            assessment.action != "activation"
            or assessment.load_entity_id in self._leases
        ):
            assessment.votes.clear()
            assessment.measurements_w.clear()
            assessment.consumed_reports.clear()
            assessment.next_not_before = now
            assessment.deadline = now + timedelta(
                minutes=self.config.feedback_timeout_minutes
            )
            self._set_feedback_assessment(assessment)
            self._record(f"{message}. Restarting the interrupted checks.")
        elif self._resume_requires_recovery and self._leases:
            self._begin_recovery_feedback("post-restart paused leases")
            self._record(f"{message}. Checking the solar sensors again.")
        else:
            self._record(f"{message}. Checking current sensor values.")
        self._resume_requires_recovery = False

    def _set_state(self, state: str, reason: str) -> None:
        if self.state == state and self.reason == reason:
            return
        self.state = state
        self.reason = reason
        self._record(reason)

    def _record(self, message: str) -> None:
        self._event_history = append_bounded_event(
            self._event_history,
            message=message,
            at=datetime.now().astimezone(),
        )
        self._runtime_store.async_delay_save(self._runtime_payload)

    def _runtime_payload(self) -> dict[str, Any]:
        """Serialize only state needed for safe continuation after restart."""
        return {
            "saved_at": datetime.now().astimezone().isoformat(),
            "leases": {
                entity_id: {
                    "activated_at": lease.activated_at.isoformat(),
                    "commanded_profile": lease.commanded_profile,
                    "previous_profile": lease.previous_profile,
                }
                for entity_id, lease in self._leases.items()
            },
            "last_off": {
                entity_id: value.isoformat()
                for entity_id, value in self._last_off.items()
            },
            "next_load_not_before": (
                self._next_load_not_before.isoformat()
                if self._next_load_not_before is not None
                else None
            ),
            "cycle_memory": {
                "blocked_loads": sorted(self._cycle_memory.blocked_loads),
                "supported_combinations": [
                    sorted(value)
                    for value in self._cycle_memory.supported_combinations
                ],
                "unsupported_combinations": [
                    sorted(value)
                    for value in self._cycle_memory.unsupported_combinations
                ],
                "lower_supported_w": self._cycle_memory.lower_supported_w,
                "upper_unsupported_w": self._cycle_memory.upper_unsupported_w,
            },
            "pending_unsupported_release": self._pending_unsupported_release,
            "probe_runtime": (
                {
                    "load_entity_id": self._probe_runtime.load_entity_id,
                    "started_at": self._probe_runtime.started_at.isoformat(),
                    "accumulated_energy_wh": (
                        self._probe_runtime.accumulated_energy_wh
                    ),
                    "last_sample_at": (
                        self._probe_runtime.last_sample_at.isoformat()
                    ),
                    "last_fallback_power_w": (
                        self._probe_runtime.last_fallback_power_w
                    ),
                }
                if self._probe_runtime is not None
                else None
            ),
            "paused_until": (
                self._paused_until.isoformat()
                if self._paused_until is not None
                else None
            ),
            "history": self._event_history,
        }

    def _restore_runtime_state(self, data: dict[str, Any]) -> None:
        """Restore only unambiguous leases and valid wall-clock deadlines."""
        configured_loads = {load.entity_id: load for load in self.config.loads}
        now = datetime.now().astimezone()
        history = data.get("history")
        if isinstance(history, list):
            self._event_history = [
                {"at": str(item["at"]), "message": str(item["message"])}
                for item in history[-30:]
                if isinstance(item, dict)
                and isinstance(item.get("at"), str)
                and isinstance(item.get("message"), str)
            ]
        last_off = data.get("last_off")
        if isinstance(last_off, dict):
            for entity_id, value in last_off.items():
                parsed = parse_aware_datetime(value)
                if (
                    isinstance(entity_id, str)
                    and entity_id in configured_loads
                    and parsed is not None
                ):
                    self._last_off[entity_id] = parsed
        next_load_not_before = parse_aware_datetime(
            data.get("next_load_not_before")
        )
        if next_load_not_before is not None and next_load_not_before > now:
            self._next_load_not_before = next_load_not_before
        paused_until = parse_aware_datetime(data.get("paused_until"))
        if paused_until is not None and paused_until > now and self.config.enabled:
            self._paused_until = paused_until
        leases = data.get("leases")
        persisted_lease_count = len(leases) if isinstance(leases, dict) else 0
        if isinstance(leases, dict):
            for entity_id, value in leases.items():
                if not isinstance(entity_id, str):
                    continue
                load = configured_loads.get(entity_id)
                state = self.hass.states.get(entity_id)
                if (
                    load is None
                    or not isinstance(value, dict)
                    or state is None
                    or state.state in _INVALID_STATES
                    or state.state == STATE_OFF
                ):
                    continue
                activated_at = parse_aware_datetime(value.get("activated_at"))
                commanded_profile = value.get("commanded_profile")
                previous_profile = value.get("previous_profile")
                if (
                    activated_at is None
                    or activated_at > now + timedelta(minutes=5)
                    or not isinstance(commanded_profile, dict)
                    or not isinstance(previous_profile, dict)
                    or not load_definition_matches_profile(
                        hvac_mode=load.hvac_mode,
                        temperature=load.temperature,
                        fan_mode=load.fan_mode,
                        commanded_profile=commanded_profile,
                    )
                    or not climate_matches_commanded_profile(
                        state.state,
                        state.attributes,
                        commanded_profile,
                    )
                ):
                    continue
                self._leases[entity_id] = Lease(
                    load=load,
                    activated_at=activated_at,
                    commanded_profile=commanded_profile,
                    previous_profile=previous_profile,
                )
        self._restored_lease_count = len(self._leases)
        self._resume_requires_recovery = (
            self._paused_until is not None and bool(self._leases)
        )
        self._discarded_lease_count = (
            persisted_lease_count - self._restored_lease_count
        )
        if self._discarded_lease_count:
            self._record(
                f"Could not trust {self._discarded_lease_count} saved AC state(s). "
                "Those ACs were left alone."
            )
        cycle = data.get("cycle_memory")
        if isinstance(cycle, dict):
            configured_ids = set(configured_loads)
            blocked_values = cycle.get("blocked_loads", [])
            if not isinstance(blocked_values, list):
                blocked_values = []
            supported_values = cycle.get("supported_combinations", [])
            if not isinstance(supported_values, list):
                supported_values = []
            unsupported_values = cycle.get("unsupported_combinations", [])
            if not isinstance(unsupported_values, list):
                unsupported_values = []
            self._cycle_memory.blocked_loads = {
                str(value)
                for value in blocked_values
                if str(value) in configured_ids
            }
            self._cycle_memory.supported_combinations = {
                frozenset(str(entity_id) for entity_id in value)
                for value in supported_values
                if isinstance(value, list)
                and all(str(entity_id) in configured_ids for entity_id in value)
            }
            self._cycle_memory.unsupported_combinations = {
                frozenset(str(entity_id) for entity_id in value)
                for value in unsupported_values
                if isinstance(value, list)
                and all(str(entity_id) in configured_ids for entity_id in value)
            }
            for attribute in ("lower_supported_w", "upper_unsupported_w"):
                value = cycle.get(attribute)
                if (
                    isinstance(value, (int, float))
                    and isfinite(value)
                    and value >= 0
                ):
                    setattr(self._cycle_memory, attribute, float(value))
        pending_release = data.get("pending_unsupported_release")
        if isinstance(pending_release, str) and pending_release in self._leases:
            self._pending_unsupported_release = pending_release
        probe_runtime = data.get("probe_runtime")
        if isinstance(probe_runtime, dict):
            load_entity_id = probe_runtime.get("load_entity_id")
            started_at = parse_aware_datetime(probe_runtime.get("started_at"))
            last_sample_at = parse_aware_datetime(
                probe_runtime.get("last_sample_at")
            )
            accumulated_energy_wh = probe_runtime.get(
                "accumulated_energy_wh"
            )
            last_fallback_power_w = probe_runtime.get(
                "last_fallback_power_w"
            )
            if (
                isinstance(load_entity_id, str)
                and load_entity_id in self._leases
                and started_at is not None
                and last_sample_at is not None
                and isinstance(accumulated_energy_wh, (int, float))
                and isfinite(accumulated_energy_wh)
                and accumulated_energy_wh >= 0
                and isinstance(last_fallback_power_w, (int, float))
                and isfinite(last_fallback_power_w)
                and last_fallback_power_w >= 0
            ):
                self._probe_runtime = ProbeRuntime(
                    load_entity_id=load_entity_id,
                    started_at=started_at,
                    accumulated_energy_wh=float(accumulated_energy_wh),
                    last_sample_at=last_sample_at,
                    last_fallback_power_w=float(last_fallback_power_w),
                )
                self._pending_unsupported_release = load_entity_id
                self._record(
                    f"Recovered an interrupted probe for {load_entity_id}; "
                    "it will be released safely."
                )

    def _load_status(self, load: LoadConfig) -> dict[str, Any]:
        """Explain ownership and current eligibility for one configured AC."""
        now = datetime.now().astimezone()
        power = self._load_power_reading(load, now)
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
            ownership_reason = "Solar Spender turned this AC on"
        elif (
            self._pending_activation is not None
            and self._pending_activation[0].entity_id == load.entity_id
        ):
            ownership_reason = "Solar Spender is turning this AC on"
        elif not load.enabled:
            ownership_reason = "Turned off in Solar Spender"
        elif state is None or state_value in _INVALID_STATES:
            ownership_reason = "AC is unavailable"
        elif state_value != STATE_OFF:
            ownership_reason = (
                "Already running or changed by someone else"
            )
        elif blocked_for_cycle:
            ownership_reason = "Blocked for now"
        elif (
            self.config.source_type == SOURCE_CURTAILED
            and not self._probe_preflight_fits(load)
        ):
            required_wh = self._probe_preflight_energy_wh(load)
            ownership_reason = (
                f"Fallback budget must cover {required_wh:.1f} Wh"
                if required_wh is not None
                else "Set usual power before zero-export testing"
            )
        else:
            last_off = self._last_off.get(load.entity_id)
            minimum_off_until = (
                last_off + timedelta(seconds=load.min_off_seconds)
                if last_off is not None
                else None
            )
            if (
                minimum_off_until is not None
                and now < minimum_off_until
            ):
                ownership_reason = (
                    "Waiting for its minimum off time"
                )
            else:
                can_be_owned = True
                ownership_reason = "Ready"
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
            "current_power_w": power.value_w,
            "current_power_assumed_zero": power.assumed_zero,
            "current_power_age_seconds": power.age_seconds,
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
        now = datetime.now().astimezone()
        feedback_assessment = self._feedback_assessment
        pending_feedback = (
            feedback_assessment.pending_entities(self._feedback_reports)
            if feedback_assessment is not None
            else frozenset()
        )
        probe_runtime = self._probe_runtime
        battery_discharge_w = self._battery_discharge_w()
        fallback_power_w = self._current_probe_fallback_power_w()
        fallback_energy_wh = (
            probe_runtime.accumulated_energy_wh
            if probe_runtime is not None
            else 0.0
        )
        return {
            "entry_id": self.entry_id,
            "state": self.state,
            "reason": self.reason,
            "enabled": self.config.enabled,
            "paused": self._pause_is_active(),
            "paused_until": (
                self._paused_until.isoformat()
                if self._pause_is_active()
                else None
            ),
            "pause_remaining_seconds": (
                pause_remaining_seconds(
                    self._paused_until,
                    datetime.now().astimezone(),
                )
                if self._pause_is_active() and self._paused_until is not None
                else 0
            ),
            "raw_source_value": self.raw_source_value,
            "source_valid": self.source_valid,
            "surplus_available": self.surplus_available,
            "waste_headroom_available": self.waste_headroom_available,
            "headroom_w": self.headroom_w,
            "opportunity_power_w": self.opportunity_power_w,
            "source_deficit_w": self.source_deficit_w,
            "stale_input_entities": sorted(self._stale_input_entities(now)),
            "input_report_ages_seconds": {
                entity_id: max(0, (now - reported_at).total_seconds())
                for entity_id, reported_at in self._feedback_reports.items()
                if entity_id in self._feedback_entity_ids
            },
            "battery_allowed": self.battery_allowed,
            "battery_direction": self.battery_direction,
            "battery_power_w": self.battery_power_w,
            "probe": {
                "active": probe_runtime is not None,
                "load_entity_id": (
                    probe_runtime.load_entity_id
                    if probe_runtime is not None
                    else None
                ),
                "grid_import_w": self.grid_import_w,
                "grid_import_allowance_w": (
                    self.config.probe_grid_import_allowance_w
                ),
                "battery_discharge_w": battery_discharge_w,
                "fallback_power_w": fallback_power_w,
                "fallback_energy_wh": fallback_energy_wh,
                "fallback_energy_limit_wh": (
                    self.config.probe_max_fallback_energy_wh
                ),
                "fallback_energy_remaining_wh": max(
                    0.0,
                    self.config.probe_max_fallback_energy_wh
                    - fallback_energy_wh,
                ),
            },
            "owned_loads": [
                {"entity_id": lease.load.entity_id, "activated_at": lease.activated_at.isoformat()}
                for lease in self._leases.values()
            ],
            "restored_lease_count": self._restored_lease_count,
            "discarded_lease_count": self._discarded_lease_count,
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
                "deadline": feedback_assessment.deadline.isoformat()
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
