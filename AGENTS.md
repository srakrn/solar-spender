# Solar Spender — Agent Guide

This repository contains Solar Spender, a Home Assistant custom integration that
uses otherwise-exported solar power for flexible household loads. The first
supported load type is `climate` (air conditioners).

This file is the working contract for coding agents. Read `PLAN.md` before
changing behavior.

## Product intent

Solar Spender observes a configured surplus signal, waits for that signal to be
available, and then applies a configured operating profile to eligible ACs.
When surplus disappears, it releases owned ACs one at a time as their minimum-on
constraints permit, re-evaluating after every confirmed state change. Per-load
minimum-off time controls when each AC can be started again.

The integration must optimize self-consumption without silently taking ownership
of unrelated HVAC use. Comfort, user intent, equipment safety, predictable
recovery after restart, and respecting the configured grid-flow target take
precedence over consuming every possible watt.

## Non-goals for the first release

- Forecast-based optimization, tariffs, and weather prediction.
- Modulating a target temperature continuously to match watt-level surplus.
- Generic switches, EV chargers, water heaters, batteries, or load groups.
- Direct control of inverters or battery charge/discharge behavior.
- YAML configuration.
- Accumulated-energy/statistics inputs.

Keep extension points generic enough for more load adapters later, but do not
build those features pre-emptively.

## Architecture

Solar Spender has two cooperating parts:

1. An async Python custom integration in
   `custom_components/solar_spender/`.
2. A frontend panel, authored in `frontend/`, registered in the Home Assistant
   sidebar by the integration.

The backend is authoritative. It owns configuration validation, entity
subscriptions, timers, the state machine, persistence, service calls, and
authorization checks. The panel renders backend state and sends validated
commands over Home Assistant's authenticated WebSocket connection. Business
logic must not live only in the browser; closing the panel must have no effect
on operation.

Use one config entry and declare `single_config_entry` in `manifest.json`. The
config flow bootstraps the integration. The panel is the primary day-to-day
configuration and status surface; Settings → Integrations must still provide a
valid setup/options experience and recovery path.

Use supported Home Assistant APIs for the minimum supported HA version. Do not
import private frontend modules or copy Home Assistant internals. Bundle
frontend dependencies; never load JavaScript or CSS from a CDN at runtime.

### Frontend implementation constraint

Use Home Assistant's built-in selector/form interface for entity selection and
standard Home Assistant configuration controls. Use bundled Bootstrap only for
layout, status presentation, and interactions that Home Assistant does not
provide. Do not invent a component library or custom web components, use Lit,
or rely on private Home Assistant frontend internals. Home Assistant requires a
panel registration boundary; keep it as a thin host that renders ordinary HTML.
Bundle any Bootstrap assets; never load JavaScript or CSS from a CDN at runtime.

Every configurable field needs concise contextual help. Every entity field must
use a Home Assistant entity selector constrained to its valid domain(s), never a
free-text entity ID input or JSON configuration blob.

Expected layout:

```text
custom_components/solar_spender/
  __init__.py
  config_flow.py
  const.py
  controller.py
  manifest.json
  models.py
  storage.py
  strings.json
  translations/en.json
  websocket_api.py
  frontend/solar-spender-panel.js
frontend/
  src/
  package.json
  ...
tests/
  components/solar_spender/
```

Names may change when a Home Assistant convention requires it. Keep state
machine logic isolated from UI and Home Assistant service-call plumbing so it
can be tested deterministically.

## Domain language

Use these terms consistently:

- **raw source value**: the latest validated value from the configured source.
- **surplus available**: the controller's hysteresis-latched source decision.
- **source**: signed grid flow, production/consumption power, or
  curtailed-production feedback.
- **power headroom**: production power minus consumption power, in watts.
- **export reserve**: grid export intentionally left unused by Solar Spender.
- **curtailed production**: production limited to current demand, so unused PV
  potential is hidden and measured production follows consumption.
- **probe**: a staged load activation used to discover whether curtailed
  production can support more demand. It is reversible only within the load's
  equipment-safety constraints.
- **supported probe**: after settling, the added demand is supplied without
  exceeding configured grid-import or battery-discharge limits.
- **battery gate**: optional condition that can prevent new activation.
- **waste headroom**: a valid, latched source opportunity after accounting for
  the configured battery's first claim. Charging, discharging, unknown battery
  direction, or an unsatisfied battery gate means no waste headroom.
- **probe-ready battery**: SOC is at/above the configured full threshold and
  the battery is neither charging nor discharging.
- **load**: one configured AC plus its desired profile and priority.
- **owned load**: a load that Solar Spender changed from off to on during the
  current control lease and may therefore release.
- **release**: stop controlling an owned load, normally by calling
  `climate.turn_off`.
- **cycle**: activation through release of the last owned load.

Live decisions use power measurements (`W` or `kW`) normalized to watts.

## Input contract

Exactly one source mode is configured.

### Grid-flow source

- Require a power sensor plus an explicit, previewed sign convention.
- Normalize internally to `export > 0` and `import < 0`.
- Calculate `spendable_power_w = export_power_w - export_reserve_w`.
- Apply entry and exit margins above the configured reserve, with
  `entry_margin_w > exit_margin_w >= 0`.
- Treat stale, invalid, `unknown`, and `unavailable` values as no surplus.
- Expose raw power, normalized flow, reserve, and spendable power separately in
  status.

### Production/consumption source

- Require production and total-consumption sensors with power device class and
  compatible power units.
- Normalize both to watts before subtraction.
- In observable-surplus mode,
  `production_w - consumption_w >= enter_threshold_w` means raw surplus.
- Use a lower `exit_threshold_w` while surplus is latched to provide hysteresis.
- Reject non-numeric, non-finite, `unknown`, or `unavailable` values safely.
- Do not add or subtract imported/exported grid power unless the selected sensor
  semantics are explicit and covered by tests.

Some zero-export systems curtail production to match demand. In that case,
`production_w - consumption_w` near zero does not prove that no PV potential
remains. Never label that value “available headroom.” Support it only through an
explicit curtailed-production strategy:

- require minimum production, a probe-ready battery, and a low
  `consumption_w - production_w` deficit as the opportunity condition;
- enter when deficit is at/below a configured entry maximum and exit when it
  reaches a larger configured exit maximum;
- activate at most one candidate load as a probe;
- wait for both an AC startup allowance and a measurement settling window;
- retain the load only when production rose to cover its observed marginal
  demand without prohibited grid import or battery discharge;
- otherwise release that probe, respect minimum-on/off constraints, and do not
  immediately retry it;
- stop probing when observability is insufficient to distinguish solar support
  from grid or battery support.

Prefer an explicit grid-flow or battery-power feedback sensor for this strategy.
Production and consumption alone can show a deficit if their semantics cover
the same boundary, but they cannot reveal the remaining hidden ceiling before a
probe.

For zero-export deficit hysteresis require
`0 <= entry_deficit_w < exit_deficit_w`. A low deficit is permission to probe,
not proof of spare power. Keep minimum production as a separate guard so
nighttime zero production/consumption cannot qualify.

A probe is not free: the grid or battery may need to cover it until measurement
settles and minimum-on time permits release. Do not probe unless the configured
fallback power/energy budget permits that worst case. If the user permits no
fallback at all, do not probe hidden capacity.

Support for accumulated-energy sensors requires aligned interval statistics,
reset handling, and stale-sample handling. It is a later feature, not a shortcut
in the first release.

### Battery gate

Battery gating is optional. Supported policy should distinguish:

- `require_charging`: allow activation only while charging.
- `charging_or_soc`: allow while charging or while SOC is at/above a configured
  threshold.
- `full_idle_for_probe`: for curtailed-production probes, require SOC at/above a
  configurable full threshold and require the battery to be neither charging
  nor discharging.

Battery direction may come from a charging/status entity or a measurement power
sensor. For power sensors, require an explicit charging sign convention and a
non-negative symmetric idle threshold; normalize internally so charging is
positive, discharging is negative, and values within the threshold are idle.

Use `charging_or_soc` when surplus is directly observable and spending alongside
battery charging is intended. Use `full_idle_for_probe` as the default battery
heuristic for curtailed production: below-full SOC leaves solar for the battery,
charging indicates the battery still has first claim, and discharging indicates
current solar is insufficient for current demand.

Invalid or unavailable battery inputs fail closed for new activation. They do
not abruptly turn off running ACs unless the configured surplus source also
fails; shedding then follows the normal cadence.

Confirmed battery discharge is different from an unavailable battery input: it
proves current demand is no longer solar-supported. Mark an activation under
confirmation as unsupported, or shed existing owned loads one at a time using
the battery discharge magnitude when available. A closed battery gate must
never prevent simultaneous source-loss shedding.

## Control invariants

These are safety requirements, not implementation suggestions:

- Never turn off a load that Solar Spender does not own.
- By default, skip an AC that was already running when activation began. Do not
  alter its mode or target temperature.
- Before changing an eligible off AC, snapshot only the controllable fields that
  Solar Spender will change: target temperature and fan mode. On an automatic
  release, turn the AC off and restore that snapshot best-effort.
- A manual user change to an owned AC relinquishes ownership when the change
  conflicts with the commanded profile or turns it off. Do not “fight” the
  user.
- Compare only fields Solar Spender commanded, such as HVAC mode, target
  temperature, and fan mode. Natural telemetry changes such as
  `current_temperature`, humidity, and `hvac_action` are not manual overrides.
- Never call services for `unknown` or `unavailable` entities.
- Re-check source, gate, eligibility, minimum off-time, and current state just
  before every activation service call.
- Re-check ownership and minimum on-time just before every release service call.
- One failed load must not stop reconciliation of other loads.
- Use deterministic priority order for activation. For shedding with a reliable
  measured shortfall and per-load draw, release the smallest eligible owned load
  that covers the shortfall; if none covers it, release the largest contributor.
  Use lowest user priority and stable configuration order as tie-breakers or as
  the fallback when power information is unavailable.
- Activate one load at a time, confirm its resulting state, and wait for fresh
  post-settling feedback before activating another.
- In curtailed-production mode, never activate a second probe until the first
  has settled and been classified as supported or rolled back.
- In `full_idle_for_probe` mode, re-check SOC and battery direction immediately
  before activation. Battery discharge during the probe makes it unsupported.
- A settling timer never authorizes another load change by itself. After every
  activation or release, wait until every configured source and battery
  feedback entity has reported again after the settling floor. Track unchanged
  reports through Home Assistant's entity-filtered `state_reported` event.
- If fresh feedback shows that a newly activated load exhausted surplus,
  release that specific load when minimum-on permits and block it for the
  remainder of the current spending cycle. Do not treat headroom returning
  because that load was removed as a new opportunity. Clear the block only
  after fresh feedback observes no surplus while no load is owned.
- For observable numeric sources, enter surplus only at/above the entry
  threshold and remain latched until at/below the exit threshold. Require
  `entry_threshold > exit_threshold`.
- For zero-export opportunity deficits, invert that ordering: enter at/below the
  lower maximum deficit, remain latched below the higher exit deficit, and
  require `entry_deficit < exit_deficit`.
- Invalid source data clears the surplus latch.
- When surplus is lost, use the measured production deficit or grid shortfall
  and live per-load power where available to select one eligible owned load.
  Confirm its release, then wait for fresh post-settling feedback before
  re-evaluating.
- If every owned load is still inside minimum-on time, schedule reconciliation
  for the earliest eligibility deadline.
- If surplus returns before release, cancel the pending reconciliation.
- After the last owned load is released, return directly to monitoring.
  Per-load minimum-off deadlines prevent immediate reactivation.
- Disabling or unloading the integration cancels listeners and timers. Disabling
  automation must not unexpectedly turn off loads; expose a separate,
  confirmable “release owned loads” action.
- Never restore a snapshot after ownership has been relinquished or after an
  entity is unavailable. Restoration failure is visible in runtime status but
  does not block release of other owned loads.

Do not infer success from a service call returning. Observe the resulting entity
state, use a bounded confirmation timeout, and report failures in runtime
status.

## State machine and time

The behavioral states are:

`DISABLED`, `MONITORING`, `SPENDING`, `SHEDDING`, `PROBING`,
`WAITING_FEEDBACK`, and `BLOCKED_BATTERY`.

Represent transitions in one controller and document every transition in tests.
Avoid scattered callbacks that each mutate timers and ownership.

Use Home Assistant event helpers and monotonic time for live durations. Persist
wall-clock deadlines and enough lease metadata to recover conservatively after
a Home Assistant restart. Never serialize an asyncio task or assume a timer
survives reload.

On restart:

- Do not claim ownership merely because a configured AC is on.
- Restore only leases that were persisted after a confirmed Solar Spender
  activation and whose observed state still matches the commanded profile.
- If ownership is ambiguous, relinquish it and notify the user rather than
  risking an unrelated shutdown.
- Recompute raw input from current entity states and rebuild future timers.

## Configuration and persistence

Version the persisted schema. Treat config-entry options (or a small,
versioned integration store if needed for the load collection) as the single
source of truth. All writes pass through backend validation and are atomic.

Required configuration:

- enabled flag;
- source mode and source entities;
- export reserve and valid entry/exit margins for grid-flow mode;
- entry and exit thresholds for production/consumption mode;
- optional curtailed-production probing policy, minimum production,
  entry/exit deficit thresholds, grid-import/battery-discharge limits, and
  settling duration;
- action-confirmation timeout and measurement-settling duration;
- optional battery policy, SOC/status/power entities, direction source, power
  sign and idle threshold, SOC threshold, and normalized charging/discharging
  state mappings;
- ordered AC definitions with entity ID, priority, optional HVAC mode, optional
  target temperature, optional fan mode, optional expected marginal power,
  optional live power entity, and minimum on/off durations. `dry` with no
  temperature is a valid profile.

Validate entity domain, supported HVAC and fan modes, temperature range/step,
units, duplicate IDs, non-negative durations, sensible threshold ordering, and
at least one commanded field per AC. Entity selectors improve UX but never
replace backend validation.

Do not log access tokens, full WebSocket payloads, or unrelated Home Assistant
state. Diagnostic output should redact anything not needed to debug this
integration.

## Panel requirements

The sidebar panel must be usable on phone and desktop and follow Home Assistant
theme variables. It needs:

- current state, raw source value, hysteresis-latched surplus, calculated watts,
  battery gate, and relevant per-load deadlines;
- a read-only Home Assistant binary sensor for waste headroom which continues
  evaluating while automation is disabled; when battery feedback is configured,
  source opportunity alone is insufficient while the battery is charging;
- enabled/pause control with clear semantics;
- source and battery configuration;
- feedback/probing strategy and measurement confidence;
- ordered AC list and profile editor;
- per-load enabled state, ownership eligibility/reason, current status, last
  command, and error;
- a concise event history for explaining decisions;
- validation errors before save;
- confirmation before releasing owned loads.

Accessibility is required: keyboard navigation, labels, focus states, sufficient
contrast, reduced-motion support, and status text that does not depend on color.
Do not depend on undocumented Home Assistant frontend components.

## Python and Home Assistant conventions

- Target the Python version required by the chosen minimum Home Assistant
  release.
- Keep all runtime I/O async and non-blocking.
- Add complete type annotations.
- Use config-entry lifecycle methods and register cleanup with
  `entry.async_on_unload`.
- Use entity state-change tracking rather than polling where inputs are already
  in Home Assistant.
- Register services and WebSocket commands once, not once per reload.
- Add translations for every user-facing backend string.
- Keep constants in `const.py`; use dataclasses/enums for validated runtime
  models rather than untyped dictionaries.
- Avoid a `DataUpdateCoordinator` unless there is actually a coordinated polling
  source. This integration is event-driven.

## Tests and quality gates

Use time-freezing and mocked Home Assistant service calls. Never make tests wait
for real timers.

Minimum backend coverage:

- config flow and validation;
- unit conversion and invalid sensor states;
- grid-flow sign normalization, export reserve, and entry/exit margins;
- entry/exit threshold hysteresis and invalid-source latch clearing;
- battery policies, including full/idle, below-full, charging, discharging, and
  unavailable combinations;
- activation/release order and cadence;
- cancellation of a pending release when surplus returns;
- minimum-on constrained shedding and minimum-off constrained reactivation;
- already-running ACs are skipped and never released;
- manual overrides relinquish ownership;
- automatic release restores the pre-activation temperature/fan profile, while
  a manual override prevents restoration;
- partial service failures and confirmation timeouts;
- curtailed-production probes that are supported, unsupported, and unobservable;
- learned marginal-power updates that reject overlapping household load changes;
- reload, unload, and restart lease recovery;
- stale, removed, renamed, unavailable, and restored entities;
- authorization and malformed WebSocket messages.

Minimum frontend coverage:

- configuration round-trip;
- accessible form labels and keyboard reordering;
- responsive status and load views;
- backend error rendering;
- unavailable entity rendering.

Before handing off a change, run the repository's format, lint, type-check,
backend test, and frontend test/build commands. Until tooling is scaffolded,
record the exact commands in `README.md` and CI at the same time they are added.
Do not claim a check passed if it was not run.

## Change discipline

- Keep commits and patches scoped to one behavioral change.
- Update `PLAN.md` when a milestone, decision, or scope changes.
- Add a regression test with every bug fix.
- Preserve user changes and inspect the worktree before editing.
- Explain migrations and breaking configuration changes in release notes.
- Do not add dependencies when the standard library or Home Assistant already
  provides the needed facility.
- Use `git` commands directly for commits and pushes; do not require or use the GitHub CLI (`gh`) for those operations.
- Use conventional commits.
