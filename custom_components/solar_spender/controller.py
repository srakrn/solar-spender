"""Event-driven Solar Spender controller."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    BATTERY_CHARGING_OR_SOC,
    BATTERY_DISABLED,
    BATTERY_FULL_IDLE_FOR_PROBE,
    BATTERY_REQUIRE_CHARGING,
    SOURCE_BINARY,
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
from .feedback import CycleMemory, FeedbackBarrier, debounce_binary_source
from .models import LoadConfig, SolarSpenderConfig

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
        self.reason = "disabled" if not config.enabled else "awaiting source"
        self.raw_source_value: str | float | None = None
        self.source_valid = False
        self.headroom_w: float | None = None
        self._leases: dict[str, Lease] = {}
        self._last_off: dict[str, datetime] = {}
        self._pending_activation: tuple[LoadConfig, dict[str, Any], dict[str, Any]] | None = None
        self._feedback_entity_ids = self._feedback_entities()
        self._feedback_reports: dict[str, datetime] = {}
        self._feedback_barrier: FeedbackBarrier | None = None
        self._cycle_memory = CycleMemory()
        self._pending_unsupported_release: str | None = None
        self._binary_debounce_until: datetime | None = None
        self._binary_debounce_scheduled: Callable[[], None] | None = None
        self._settling_until: datetime | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        self._scheduled: Callable[[], None] | None = None
        self._reconciling = False
        self._event_history: list[dict[str, str]] = []

    async def async_start(self) -> None:
        """Subscribe to all configured entities and evaluate initial state."""
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
        if self._binary_debounce_scheduled is not None:
            self._binary_debounce_scheduled()
            self._binary_debounce_scheduled = None

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
            config.binary_entity_id,
            config.grid_entity_id,
            config.production_entity_id,
            config.consumption_entity_id,
            config.battery_soc_entity_id,
            config.battery_status_entity_id,
            *(load.entity_id for load in config.loads),
        }
        return {value for value in values if value}

    def _feedback_entities(self) -> frozenset[str]:
        """Return entities that must report freshly after every load change."""
        config = self.config
        if config.source_type == SOURCE_BINARY:
            values = {config.binary_entity_id}
        elif config.source_type == SOURCE_GRID:
            values = {config.grid_entity_id}
        else:
            values = {
                config.production_entity_id,
                config.consumption_entity_id,
            }
        if config.battery_policy != BATTERY_DISABLED:
            values.add(config.battery_status_entity_id)
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
            if self._settling_until is not None and datetime.now().astimezone() < self._settling_until:
                self.reason = "waiting for measurement settling"
                return
            self._settling_until = None
            self._confirm_pending_activation()
            if not self.config.enabled:
                self._set_state(STATE_DISABLED, "disabled")
                return
            if self._feedback_barrier is not None and not self.source_valid:
                self._record(
                    "Cancelled feedback barrier because the source became invalid"
                )
                self._feedback_barrier = None
            if self._feedback_barrier is not None:
                barrier = self._feedback_barrier
                pending_reports = barrier.pending_entities(self._feedback_reports)
                if pending_reports:
                    self._set_state(
                        STATE_WAITING_FEEDBACK,
                        "waiting for fresh feedback: "
                        + ", ".join(sorted(pending_reports)),
                    )
                    return
                if self._binary_debounce_until is not None:
                    self._set_state(
                        STATE_WAITING_FEEDBACK,
                        self.reason,
                    )
                    return
                self._feedback_barrier = None
                self._record(
                    f"Fresh feedback confirmed after {barrier.action} of "
                    f"{barrier.load_entity_id}"
                )
                if barrier.action == "activation" and not self.surplus_available:
                    self._cycle_memory.mark_unsupported(barrier.load_entity_id)
                    self._pending_unsupported_release = barrier.load_entity_id
                    self._record(
                        f"Blocked {barrier.load_entity_id} for this cycle: "
                        "fresh feedback showed no surplus"
                    )
                    await self._async_shed_one(
                        "activation was not supported by fresh feedback",
                        target_entity_id=barrier.load_entity_id,
                    )
                    return
                if (
                    barrier.action == "activation"
                    and self.config.source_type == SOURCE_CURTAILED
                    and not self.battery_allowed
                ):
                    self._cycle_memory.mark_unsupported(barrier.load_entity_id)
                    self._pending_unsupported_release = barrier.load_entity_id
                    self._record(
                        f"Blocked {barrier.load_entity_id} for this cycle: "
                        "battery gate closed after activation"
                    )
                    await self._async_shed_one(
                        "battery did not support the probe",
                        target_entity_id=barrier.load_entity_id,
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
                self._feedback_barrier is not None,
            ):
                self._record("Cleared unsupported-load memory after surplus ended")
            if self._binary_debounce_until is not None:
                self._set_state(
                    STATE_SPENDING if self._leases else STATE_MONITORING,
                    self.reason,
                )
                return
            if not self.battery_allowed:
                if self.state == STATE_PROBING:
                    await self._async_shed_one("battery discharging during probe")
                elif not self._leases:
                    self._set_state(STATE_BLOCKED_BATTERY, self.reason)
                return
            if not self.surplus_available:
                if self._leases:
                    await self._async_shed_one("surplus unavailable")
                else:
                    self._set_state(STATE_MONITORING, self.reason)
                return
            await self._async_activate_one(reason)
        finally:
            self._reconciling = False

    def _update_inputs(self) -> None:
        self._update_source()
        self.battery_allowed, battery_reason = self._battery_allows_activation()
        if not self.battery_allowed:
            self.reason = battery_reason

    def _update_source(self) -> None:
        config = self.config
        if config.source_type == SOURCE_BINARY:
            state = self.hass.states.get(config.binary_entity_id)
            self.headroom_w = None
            self.raw_source_value = state.state if state is not None else None
            if (
                state is None
                or state.state in _INVALID_STATES
                or state.state.lower() not in {"on", "off", "true", "false", "1", "0"}
            ):
                self.source_valid = False
                self.surplus_available = False
                self._clear_binary_debounce()
                self.reason = "binary headroom unavailable or invalid"
                return
            self.source_valid = True
            raw_on = state.state.lower() in {"on", "true", "1"}
            now = datetime.now().astimezone()
            decision = debounce_binary_source(
                surplus_available=self.surplus_available,
                raw_on=raw_on,
                raw_changed_at=state.last_changed,
                now=now,
                on_delay_minutes=config.binary_on_delay_minutes,
                off_delay_minutes=config.binary_off_delay_minutes,
            )
            self.surplus_available = decision.surplus_available
            self._binary_debounce_until = decision.pending_until
            if decision.pending_until is not None:
                self._schedule_binary_debounce(decision.pending_until)
                transition = "entry" if raw_on else "exit"
                remaining = max(
                    0,
                    int((decision.pending_until - now).total_seconds()),
                )
                self.reason = (
                    f"binary headroom {'on' if raw_on else 'off'}; "
                    f"{transition} debounce {remaining}s remaining"
                )
            else:
                self._cancel_binary_debounce_timer()
                self.reason = (
                    "binary headroom on"
                    if self.surplus_available
                    else "binary headroom off"
                )
            return

        if config.source_type == SOURCE_GRID:
            watts = self._power_value(config.grid_entity_id)
            self.raw_source_value = watts
            if watts is None:
                self._clear_source("grid-flow sensor unavailable")
                return
            self.source_valid = True
            export_w = watts if config.grid_export_positive else -watts
            self.headroom_w = export_w - config.export_reserve_w
        else:
            production_w = self._power_value(config.production_entity_id)
            consumption_w = self._power_value(config.consumption_entity_id)
            self.raw_source_value = production_w
            if production_w is None or consumption_w is None:
                self._clear_source("production or consumption sensor unavailable")
                return
            self.source_valid = True
            self.headroom_w = production_w - consumption_w
            if config.source_type == SOURCE_CURTAILED:
                # In a curtailed system this is an opportunity signal, not hidden headroom.
                self.headroom_w = production_w

        if self.surplus_available:
            self.surplus_available = self.headroom_w > config.exit_threshold_w
        else:
            self.surplus_available = self.headroom_w >= config.entry_threshold_w
        self.reason = (
            f"source {'available' if self.surplus_available else 'below threshold'}"
        )

    def _clear_source(self, reason: str) -> None:
        self.source_valid = False
        self.surplus_available = False
        self.headroom_w = None
        self.reason = reason

    def _power_value(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _INVALID_STATES:
            return None
        try:
            value = float(state.state)
        except (TypeError, ValueError):
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
            return True, "battery policy disabled"
        status = self._battery_status()
        charging = status in config.charging_states
        discharging = status in config.discharging_states
        soc = self._numeric_state(config.battery_soc_entity_id)
        if config.battery_policy == BATTERY_REQUIRE_CHARGING:
            return charging, "battery is not charging"
        if config.battery_policy == BATTERY_CHARGING_OR_SOC:
            return charging or (soc is not None and soc >= config.battery_full_threshold), "battery gate closed"
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

    def _battery_status(self) -> str:
        state = self.hass.states.get(self.config.battery_status_entity_id)
        if state is None or state.state in _INVALID_STATES:
            return ""
        if (
            state.entity_id.startswith("binary_sensor.")
            and state.attributes.get("device_class") == "battery_charging"
        ):
            return "charging" if state.state == "on" else "idle"
        return state.state.lower()

    def _numeric_state(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _INVALID_STATES:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    async def _async_activate_one(self, reason: str) -> None:
        if self._pending_activation is not None:
            return
        candidate = self._next_eligible_load()
        if candidate is None:
            no_load_reason = (
                "remaining loads blocked for current cycle"
                if self._cycle_memory.blocked_loads
                else "no eligible load"
            )
            self._set_state(
                STATE_SPENDING if self._leases else STATE_MONITORING,
                no_load_reason,
            )
            return
        if self.config.source_type != SOURCE_CURTAILED and (
            candidate.expected_power_w is not None
            and self.headroom_w is not None
            and candidate.expected_power_w > self.headroom_w
        ):
            self._set_state(STATE_SPENDING if self._leases else STATE_MONITORING, "no load fits headroom")
            return
        self._set_state(
            STATE_PROBING if self.config.source_type == SOURCE_CURTAILED else STATE_SPENDING,
            f"activating {candidate.entity_id}: {reason}",
        )
        await self._async_activate_load(candidate)

    def _next_eligible_load(self) -> LoadConfig | None:
        now = datetime.now().astimezone()
        candidates: list[LoadConfig] = []
        for load in self.config.loads:
            if (
                not load.enabled
                or load.entity_id in self._leases
                or load.entity_id in self._cycle_memory.blocked_loads
            ):
                continue
            state = self.hass.states.get(load.entity_id)
            if state is None or state.state in _INVALID_STATES or state.state != STATE_OFF:
                continue
            last_off = self._last_off.get(load.entity_id)
            if last_off is not None and now < last_off + timedelta(seconds=load.min_off_seconds):
                continue
            candidates.append(load)
        if not candidates:
            return None
        return min(candidates, key=lambda load: (-load.utility, load.priority, load.expected_power_w or float("inf")))

    async def _async_activate_load(self, load: LoadConfig) -> None:
        previous_profile = self._capture_profile(load.entity_id)
        profile: dict[str, Any] = {}
        await self.hass.services.async_call(
            "climate", "turn_on", {"entity_id": load.entity_id}, blocking=True
        )
        if load.hvac_mode is not None:
            await self.hass.services.async_call(
                "climate", "set_hvac_mode", {"entity_id": load.entity_id, "hvac_mode": load.hvac_mode}, blocking=True
            )
            profile["hvac_mode"] = load.hvac_mode
        if load.temperature is not None:
            await self.hass.services.async_call(
                "climate", "set_temperature", {"entity_id": load.entity_id, ATTR_TEMPERATURE: load.temperature}, blocking=True
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
        self._begin_feedback_barrier(
            "activation",
            load.entity_id,
            action_completed_at,
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
                self._feedback_barrier is not None
                and self._feedback_barrier.action == "activation"
                and self._feedback_barrier.load_entity_id == load.entity_id
            ):
                self._feedback_barrier = None
            self._record(f"Activation was not confirmed for {load.entity_id}")
            return
        self._leases[load.entity_id] = Lease(
            load,
            datetime.now().astimezone(),
            profile,
            previous_profile,
        )
        self._record(f"Activated {load.entity_id}")

    async def _async_shed_one(
        self,
        reason: str,
        target_entity_id: str | None = None,
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
        lease = (
            eligible[0]
            if target_entity_id is not None
            else max(
                eligible,
                key=lambda item: (item.load.priority, -item.load.utility),
            )
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
        self._begin_feedback_barrier(
            "release",
            lease.load.entity_id,
            datetime.now().astimezone(),
        )
        self._schedule_reconcile(self.config.settling_seconds)

    def _begin_feedback_barrier(
        self,
        action: str,
        load_entity_id: str,
        action_completed_at: datetime,
    ) -> None:
        """Prevent another load change until post-settling reports arrive."""
        not_before = action_completed_at + timedelta(
            seconds=self.config.settling_seconds
        )
        self._feedback_barrier = FeedbackBarrier(
            action=action,
            load_entity_id=load_entity_id,
            not_before=not_before,
            required_entities=self._feedback_entity_ids,
        )
        self._record(
            f"Feedback barrier for {load_entity_id}; reports must be newer than "
            f"{not_before.isoformat()}"
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
            if lease.load.fan_mode is not None and state.attributes.get("fan_mode") != lease.load.fan_mode:
                self._leases.pop(entity_id, None)
                self._record(f"Relinquished {entity_id}: fan mode changed")
        if (
            self._feedback_barrier is not None
            and self._feedback_barrier.action == "activation"
            and self._feedback_barrier.load_entity_id not in self._leases
            and self._pending_activation is None
        ):
            self._record(
                f"Cancelled feedback barrier for "
                f"{self._feedback_barrier.load_entity_id}: no longer owned"
            )
            self._feedback_barrier = None
        if (
            self._pending_unsupported_release is not None
            and self._pending_unsupported_release not in self._leases
        ):
            self._pending_unsupported_release = None

    def _schedule_binary_debounce(self, deadline: datetime) -> None:
        """Reconcile when a continuous binary transition becomes eligible."""
        self._cancel_binary_debounce_timer()
        seconds = max(
            0,
            (deadline - datetime.now().astimezone()).total_seconds(),
        )
        self._binary_debounce_scheduled = async_call_later(
            self.hass,
            seconds,
            self._async_binary_debounce_complete,
        )

    def _cancel_binary_debounce_timer(self) -> None:
        if self._binary_debounce_scheduled is not None:
            self._binary_debounce_scheduled()
            self._binary_debounce_scheduled = None

    def _clear_binary_debounce(self) -> None:
        self._binary_debounce_until = None
        self._cancel_binary_debounce_timer()

    @callback
    def _async_binary_debounce_complete(self, _now: datetime) -> None:
        self._binary_debounce_scheduled = None
        self.hass.async_create_task(self.async_reconcile("binary debounce complete"))

    def _schedule_reconcile(self, seconds: float) -> None:
        if self._scheduled is not None:
            self._scheduled()
        self._settling_until = datetime.now().astimezone() + timedelta(seconds=seconds)
        self._scheduled = async_call_later(self.hass, seconds, self._async_scheduled_reconcile)

    @callback
    def _async_scheduled_reconcile(self, _now: datetime) -> None:
        self._scheduled = None
        self._settling_until = None
        self.hass.async_create_task(self.async_reconcile("settling complete"))

    def _set_state(self, state: str, reason: str) -> None:
        self.state = state
        self.reason = reason
        self._record(reason)

    def _record(self, message: str) -> None:
        self._event_history.append({"at": datetime.now().astimezone().isoformat(), "message": message})
        self._event_history = self._event_history[-30:]

    def status(self) -> dict[str, Any]:
        """Return a frontend-safe controller snapshot."""
        feedback_barrier = self._feedback_barrier
        pending_feedback = (
            feedback_barrier.pending_entities(self._feedback_reports)
            if feedback_barrier is not None
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
            "binary_debounce_until": self._binary_debounce_until.isoformat()
            if self._binary_debounce_until is not None
            else None,
            "battery_allowed": self.battery_allowed,
            "owned_loads": [
                {"entity_id": lease.load.entity_id, "activated_at": lease.activated_at.isoformat()}
                for lease in self._leases.values()
            ],
            "pending_activation": self._pending_activation[0].entity_id
            if self._pending_activation is not None
            else None,
            "feedback": {
                "waiting": feedback_barrier is not None,
                "action": feedback_barrier.action
                if feedback_barrier is not None
                else None,
                "load_entity_id": feedback_barrier.load_entity_id
                if feedback_barrier is not None
                else None,
                "not_before": feedback_barrier.not_before.isoformat()
                if feedback_barrier is not None
                else None,
                "pending_entities": sorted(pending_feedback),
                "last_reports": {
                    entity_id: reported_at.isoformat()
                    for entity_id, reported_at in self._feedback_reports.items()
                },
            },
            "blocked_loads": sorted(self._cycle_memory.blocked_loads),
            "pending_unsupported_release": self._pending_unsupported_release,
            "loads": [
                {
                    "entity_id": load.entity_id,
                    "enabled": load.enabled,
                    "owned": load.entity_id in self._leases,
                    "blocked_for_cycle": (
                        load.entity_id in self._cycle_memory.blocked_loads
                    ),
                    "state": self.hass.states.get(load.entity_id).state
                    if self.hass.states.get(load.entity_id)
                    else None,
                }
                for load in self.config.loads
            ],
            "history": self._event_history,
        }
