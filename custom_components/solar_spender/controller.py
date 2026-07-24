"""Event-driven Solar Spender controller."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
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
)
from .models import LoadConfig, SolarSpenderConfig

_LOGGER = logging.getLogger(__name__)
_INVALID_STATES = {STATE_UNKNOWN, STATE_UNAVAILABLE}


@dataclass(slots=True)
class Lease:
    """A climate load Solar Spender is permitted to release."""

    load: LoadConfig
    activated_at: datetime
    profile: dict[str, Any]


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
        self.headroom_w: float | None = None
        self._leases: dict[str, Lease] = {}
        self._last_off: dict[str, datetime] = {}
        self._pending_activation: tuple[LoadConfig, dict[str, Any]] | None = None
        self._settling_until: datetime | None = None
        self._unsubscribers: list[callback] = []
        self._scheduled: callback | None = None
        self._reconciling = False
        self._event_history: list[dict[str, str]] = []

    async def async_start(self) -> None:
        """Subscribe to all configured entities and evaluate initial state."""
        entity_ids = self._watched_entities()
        if entity_ids:
            self._unsubscribers.append(
                async_track_state_change_event(self.hass, entity_ids, self._async_state_changed)
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

    @callback
    def _async_state_changed(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        self.hass.async_create_task(self.async_reconcile(f"state changed: {entity_id}"))

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
            self.surplus_available = state is not None and state.state.lower() in {"on", "true", "1"}
            self.reason = "binary headroom on" if self.surplus_available else "binary headroom off"
            return

        if config.source_type == SOURCE_GRID:
            watts = self._power_value(config.grid_entity_id)
            if watts is None:
                self._clear_source("grid-flow sensor unavailable")
                return
            export_w = watts if config.grid_export_positive else -watts
            self.headroom_w = export_w - config.export_reserve_w
        else:
            production_w = self._power_value(config.production_entity_id)
            consumption_w = self._power_value(config.consumption_entity_id)
            if production_w is None or consumption_w is None:
                self._clear_source("production or consumption sensor unavailable")
                return
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
            self._set_state(STATE_SPENDING if self._leases else STATE_MONITORING, "no eligible load")
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
            if not load.enabled or load.entity_id in self._leases:
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
        self._pending_activation = (load, profile)
        self._record(f"Awaiting activation confirmation for {load.entity_id}")
        self._schedule_reconcile(self.config.settling_seconds)

    def _confirm_pending_activation(self) -> None:
        """Create a lease only after Home Assistant observes a running climate entity."""
        if self._pending_activation is None:
            return
        load, profile = self._pending_activation
        self._pending_activation = None
        state = self.hass.states.get(load.entity_id)
        if state is None or state.state in _INVALID_STATES or state.state == STATE_OFF:
            self._last_off[load.entity_id] = datetime.now().astimezone()
            self._record(f"Activation was not confirmed for {load.entity_id}")
            return
        self._leases[load.entity_id] = Lease(load, datetime.now().astimezone(), profile)
        self._record(f"Activated {load.entity_id}")

    async def _async_shed_one(self, reason: str) -> None:
        now = datetime.now().astimezone()
        eligible = [
            lease
            for lease in self._leases.values()
            if now >= lease.activated_at + timedelta(seconds=lease.load.min_on_seconds)
        ]
        if not eligible:
            self._set_state(STATE_SHEDDING, "waiting for minimum-on deadline")
            deadlines = [
                lease.activated_at + timedelta(seconds=lease.load.min_on_seconds)
                for lease in self._leases.values()
            ]
            if deadlines:
                self._schedule_reconcile(max(0, (min(deadlines) - now).total_seconds()))
            return
        lease = max(eligible, key=lambda item: (item.load.priority, -item.load.utility))
        self._set_state(STATE_SHEDDING, f"releasing {lease.load.entity_id}: {reason}")
        await self.hass.services.async_call(
            "climate", "turn_off", {"entity_id": lease.load.entity_id}, blocking=True
        )
        self._leases.pop(lease.load.entity_id, None)
        self._last_off[lease.load.entity_id] = now
        self._record(f"Released {lease.load.entity_id}: {reason}")
        self._schedule_reconcile(self.config.settling_seconds)

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
        return {
            "entry_id": self.entry_id,
            "state": self.state,
            "reason": self.reason,
            "enabled": self.config.enabled,
            "surplus_available": self.surplus_available,
            "headroom_w": self.headroom_w,
            "battery_allowed": self.battery_allowed,
            "owned_loads": [
                {"entity_id": lease.load.entity_id, "activated_at": lease.activated_at.isoformat()}
                for lease in self._leases.values()
            ],
            "pending_activation": self._pending_activation[0].entity_id
            if self._pending_activation is not None
            else None,
            "loads": [
                {
                    "entity_id": load.entity_id,
                    "enabled": load.enabled,
                    "owned": load.entity_id in self._leases,
                    "state": self.hass.states.get(load.entity_id).state
                    if self.hass.states.get(load.entity_id)
                    else None,
                }
                for load in self.config.loads
            ],
            "history": self._event_history,
        }
