# Solar Spender — Product and Implementation Plan

Status: sampled-feedback baseline implemented
Initial scope: Home Assistant custom integration with a sidebar panel and
`climate` loads

## 1. Product decision

Build Solar Spender as an event-driven Home Assistant backend plus a bundled
custom frontend panel. The Python integration continues operating when no
browser is open. The panel is a control and explanation surface, not the
automation engine.

The first release will support:

- one installation/config entry;
- binary headroom, grid-flow, production/consumption, and curtailed-production
  source strategies;
- a configurable export reserve for systems that permit export;
- an optional battery gate;
- multiple prioritized ACs with mode and/or target-temperature profiles;
- staged activation with re-evaluation after every load;
- gradual release governed by each AC's minimum-on time;
- per-AC minimum-off time before reactivation;
- explicit ownership so unrelated HVAC use is never shut down;
- live status and configuration in a left-sidebar panel.

## 2. Operating model

All live control calculations use power measurements normalized to watts.
Accumulated-energy statistics are outside the initial scope.

Solar Spender supports two solar-system topologies:

### Export-visible systems

When export is allowed, a signed grid-flow sensor is the preferred source. Solar
Spender maintains a configurable export reserve instead of necessarily reducing
export to zero:

```text
spendable_power_w = export_power_w - export_reserve_w
```

For example, with 2,000 W export and a 300 W reserve, at most 1,700 W is treated
as spendable. Entry and exit hysteresis apply around the reserve. A production
power minus whole-home consumption power calculation can provide the same model
when those sensors cover the same electrical boundary.

The reserve absorbs sensor latency and normal household fluctuations. Setting it
to zero requests maximum self-consumption; a positive value deliberately leaves
some export available.

### Curtailed or zero-export systems

When production is curtailed to demand, measured production follows consumption
and unused PV capacity is hidden. A single measurement cannot reveal that
ceiling. Solar Spender therefore uses an inverter headroom signal, a conservative
external estimate, or a controlled one-load probe.

For a probe, Solar Spender starts one AC, allows for compressor startup and
sensor settling, then checks whether production rose with consumption without
exceeding configured grid-import or battery-discharge limits. It keeps a
supported load and rolls back an unsupported load within equipment-safety
constraints.

A probe may temporarily use grid or battery power. It is allowed only when the
configured fallback power and energy budget can cover the settling period and
minimum-on time. A strict zero-import, zero-discharge configuration requires a
direct headroom signal or conservative external estimate instead.

A battery can also provide the opportunity heuristic. By default, probing is
eligible only when SOC is at/above a configurable “full” threshold and the
battery is neither charging nor discharging. Below-full SOC, charging, and
discharging all block probes. If the battery begins discharging during a probe,
the probe is unsupported and is rolled back safely.

### Load selection

Activation is staged:

1. observe that the source has crossed its entry condition;
2. rank eligible ACs using priority, utility, and estimated marginal draw;
3. start one AC;
4. wait for startup and measurement settling;
5. retain or release it from fresh feedback;
6. re-rank before considering another AC.

Every activation and release uses a configurable majority confirmation window.
The default is three fresh source snapshots at least five minutes apart, after a
five-minute first-check delay. Activation also waits for that load's minimum-on
deadline. A successful confirmation is followed by a five-minute pause before
another AC may change. Each vote requires every configured feedback entity to
report after its sampling boundary; cached readings never vote.

The current solar opportunity remembers supported and unsupported AC
combinations. A failed combination is blocked until the opportunity ends, while
the same AC may still be tried in a smaller combination. A larger failed
combination provides a temporary upper bound when every involved AC has an
expected-draw estimate. These bounds reset when fresh feedback shows no surplus
and no load remains owned.

Five ACs have only 32 possible on/off combinations, but available PV and
inverter-AC draw both vary. The initial controller therefore uses a greedy
feedback loop. Subset optimization becomes useful only after available power and
per-load estimates have adequate confidence.

### Battery, equipment, and ownership

When surplus is directly observable, the battery policy may allow activation
while charging or while SOC is at/above a configured threshold. For curtailed
probing, the default is the stricter full-and-idle heuristic described above.
Per-load minimum-on and minimum-off durations, plus source hysteresis, protect
against rapid cycling.

Solar Spender skips an AC that was already running. Only confirmed activations
create ownership. If a user changes a commanded field, ownership is
relinquished, and Solar Spender will not later turn that AC off.

The initial AC profiles support normal climate modes including `dry` when the
entity advertises it. Optional humidity bounds can later improve load utility
and prevent unnecessary dehumidification.

## 3. Configuration model

### General

| Field | Initial default | Notes |
|---|---:|---|
| Enabled | `false` after first setup | Prevent surprise activation |
| Feedback strategy | observable surplus | Or explicit curtailed probing |
| Export reserve | 0 W | Grid-flow/observable-surplus modes |
| First check delay | 5 min | Earliest report eligible after a load changes |
| Confirmation checks | 3 | Strict majority of fresh reports |
| Time between checks | 5 min | Minimum spacing; stale values never vote |
| Wait before next AC | 5 min | Quiet period after successful confirmation |

Defaults are provisional and must be presented for user confirmation.

The runtime timing controls are also exposed under one Solar Spender device as
Home Assistant entities: an Automation switch and number entities for first
check delay, confirmation checks, check spacing, and the delay before the next
AC. Source wiring and per-AC profiles remain panel configuration.

### Source: binary mode

- `entity_id`: binary-like entity;
- `on` = raw surplus;
- continuous-on debounce in minutes before the surplus latch opens;
- continuous-off debounce in minutes before the surplus latch closes;
- all other/unavailable states = no surplus.

Any bounce resets the relevant deadline. While `off` is pending, Solar Spender
keeps owned loads running but does not activate another load. Invalid,
`unknown`, and `unavailable` values clear the latch immediately. This mode still
trusts upstream thresholding. The status view must say that Solar Spender cannot
estimate how many loads the surplus can support.

### Source: grid-flow mode

- signed grid import/export power entity;
- explicit sign convention, verified in the setup preview;
- export reserve in watts;
- entry margin above the reserve;
- lower exit margin above or at the reserve;
- maximum sample age.

Normalize the sensor to watts and then to the internal convention
`export > 0`, `import < 0`. Spendable power is export minus the configured
reserve. Loads are added only while measured export remains sufficiently above
the entry margin; loads are gradually released when spendable power falls to the
lower exit margin. Require `entry_margin_w > exit_margin_w >= 0`.

The panel must show raw sensor power, normalized grid flow, reserved export, and
spendable power separately so a reversed sign or unsuitable sensor is obvious.

### Source: production/consumption mode

- production power entity;
- whole-home consumption power entity;
- entry threshold in watts;
- lower exit threshold in watts;
- optional future maximum sample age.

Normalize supported power units to watts. In **observable-surplus** strategy,
raw surplus is true at or above the entry threshold while idle. While spending,
preserve it until headroom falls below the exit threshold. Choose threshold
defaults only after tests with real sensor behavior; do not hide them.

In **curtailed-production** strategy, do not present
`production - consumption` as available headroom. Configuration additionally
contains:

- an opportunity condition: a binary headroom signal, or minimum PV production
  while the battery is full and idle;
- optional signed grid-flow power, strongly recommended;
- optional battery charge/discharge power;
- maximum tolerated grid import and battery discharge during a probe;
- maximum fallback energy per probe;
- startup allowance and measurement settling duration;
- probe retry backoff.

Without grid-flow or battery-power feedback, a probe is supportable only if the
production and whole-home consumption sensors cover the same electrical boundary
and their deficit is trustworthy. If the controller cannot distinguish added
solar production from imported or battery power, it must report
“unobservable” and stop probing rather than guess.

### Battery gate

- disabled;
- `require_charging`, with a charging-status entity and configured charging
  values;
- `charging_or_soc`, adding an SOC sensor and threshold;
- `full_idle_for_probe`, requiring an SOC sensor, full threshold, and normalized
  charging/discharging status or power-flow entity.

The panel should preview the parsed charging state and gate result before the
user enables automation. In `full_idle_for_probe`, the preview must separately
show SOC qualification, charging state, discharging state, and the resulting
probe eligibility.

### AC load

Each entry contains:

- climate entity ID;
- display label (optional);
- priority and stable list order;
- desired HVAC mode (optional);
- desired target temperature (optional);
- desired fan mode (optional);
- optional expected power and utility weight;
- minimum-on duration;
- minimum-off duration;
- enabled flag.

At least one of HVAC mode or temperature is required. Derive mode and fan-mode
choices from the selected entity's `hvac_modes` and `fan_modes`; validate them
again in the backend. Constrain temperature to the entity's min/max/step and
validate command support against supported features.

A `dry` profile with no target temperature is explicitly valid: Solar Spender
sets only the selected HVAC mode and leaves the climate entity's existing target
temperature unchanged.

### Choosing the next AC

Start with a deterministic greedy controller, not a combinatorial optimizer:

1. Filter to eligible loads using ownership, minimum-off time, availability, and
   configured comfort/humidity constraints.
2. Estimate each AC's marginal power from its configured expected power and
   successful past observations.
3. Rank candidates by user priority/utility, then prefer a smaller estimated
   draw when candidates have equal utility.
4. In observable-surplus or grid-flow mode, select only a candidate whose
   conservative draw estimate fits the spendable-power budget.
5. Activate one candidate.
6. Measure the before/after production, consumption, grid flow, and battery flow
   over a settling window.
7. Keep it if supported; otherwise release it safely and add a retry backoff.
8. Re-rank before every subsequent activation.

Learning must attach uncertainty and reject samples when unrelated household
load changed materially during the observation window. Inverter AC draw varies
with room conditions, so recent observations should outweigh old ones without
being treated as a guaranteed rating.

Once measurement quality is proven, an optional selector may evaluate all
subsets using conservative upper estimates:

```text
maximize sum(utility_i)
subject to estimated_total_draw <= conservative_available_budget
```

With five ACs, exhaustive subset evaluation is trivial. It still cannot solve
the hidden-capacity problem; the available budget must come from direct
headroom, forecast, or probes. The greedy feedback loop is the v1 behavior.

## 4. State machine

```text
DISABLED
  └─ enable ───────────────> MONITORING

MONITORING
  ├─ surplus + eligible load ─> SPENDING
  ├─ curtailed opportunity ───> PROBING
  └─ battery gate closed ─────> BLOCKED_BATTERY

SPENDING
  ├─ source false/invalid ────> SHEDDING
  ├─ next curtailed candidate ─> PROBING
  └─ after each load change ───> WAITING_FEEDBACK

PROBING
  └─ after activation ─────────> WAITING_FEEDBACK

WAITING_FEEDBACK
  ├─ all sources report fresh + supported ─> SPENDING
  ├─ fresh feedback unsupported ───────────> release candidate safely
  └─ reports still cached/stale ───────────> remain waiting

SHEDDING
  ├─ source recovers ────────> SPENDING
  ├─ eligible owned load ────> release one, settle, then re-evaluate
  ├─ all inside minimum-on ──> wait for earliest eligibility deadline
  └─ no owned loads ─────────> MONITORING

BLOCKED_BATTERY
  └─ gate opens ─────────────> MONITORING (immediate re-evaluation)

Any active state
  └─ disable ──────────────> DISABLED
```

Clarifications:

- A battery gate blocks new starts. If it closes while loads are owned, the
  source still determines gradual shedding; this avoids abrupt comfort changes.
- Numeric sources use hysteresis rather than a time debounce: the surplus latch
  opens at the entry threshold and closes at the lower exit threshold.
- Binary sources use independent continuous-on and continuous-off debounce
  deadlines. A pending binary transition blocks further load changes; a bounce
  restarts the applicable deadline.
- A timer never authorizes another load change by itself. After every activation
  or release, each configured source and battery-feedback entity must produce a
  new Home Assistant report after each sampling boundary. The strict majority
  of the configured checks decides the result. Unchanged values count only
  through Home Assistant's filtered `state_reported` event.
- If fresh post-activation feedback loses surplus, release the just-added load
  and block that load for the rest of the current opportunity. Removing it and
  seeing the same binary headroom signal return does not re-arm it. Clear the
  block only after fresh feedback observes no surplus while no load is owned.
- On loss, release the lowest-priority owned load as soon as its minimum-on time
  allows. Confirm the change and wait for fresh post-settling feedback before
  another.
- If surplus returns while waiting for minimum-on eligibility, cancel shedding.
- A load still inside minimum-on or minimum-off time remains ineligible and the
  controller reports its deadline.
- A probe is allowed only when its worst-case fallback budget covers the
  configured settling and minimum-on constraints.
- Once all owned loads are released, return to monitoring; each released AC
  remains protected by its own minimum-off time.

## 5. Ownership and command semantics

Before activation:

1. Read the latest state.
2. Skip unavailable, already-running, disabled, or minimum-off constrained ACs.
3. Re-check surplus and battery gate.
4. Call the smallest valid service sequence to apply mode and temperature.
5. Wait for a bounded state confirmation.
6. Persist a lease only after confirmed success.

An ownership lease records entity ID, command/profile fingerprint, activation
time, last observed matching state, controller generation, and a pre-activation
snapshot of the controllable fields Solar Spender changed. It does not grant
permission to overwrite future user intent.

While owned, an observed off state or a material profile change not initiated by
the controller relinquishes the lease. Compare only configured command fields:
for example, a different `hvac_mode` or target temperature is a manual override;
normal changes to `hvac_action`, current temperature, or reported humidity are
not. The controller must not immediately reapply relinquished settings.

On release, call `climate.turn_off` only if the lease is still valid and
minimum-on time has elapsed. Then restore the pre-activation target temperature
and fan mode best-effort, confirm the resulting off state, and remove the lease.
Do not restore after a manual override or ambiguous ownership. Failures remain
visible and are retried only at a bounded cadence.

## 6. Backend interfaces

Define authenticated, admin-only WebSocket commands for configuration writes and
manual control. Read-only status may be available to non-admin authenticated
users.

Proposed command surface:

- `solar_spender/config/get`
- `solar_spender/config/update`
- `solar_spender/status/get`
- `solar_spender/status/subscribe`
- `solar_spender/history/get`
- `solar_spender/control/set_enabled`
- `solar_spender/control/release_owned`

Every command has a versioned schema and structured errors. Configuration update
uses optimistic revision checking to prevent two open browsers from silently
overwriting each other.

Status includes:

- state-machine state and reason;
- raw source value and hysteresis-latched surplus decision;
- normalized input values and computed headroom;
- battery gate result and reason;
- feedback-barrier action, earliest acceptable report time, pending entities,
  and their last report times;
- loads blocked as unsupported for the current spending cycle;
- active deadlines/countdowns;
- configured load eligibility and ownership;
- last command/result per load;
- recent bounded decision events;
- configuration revision.

Do not store an unbounded event log. Home Assistant's own logbook/history can be
integrated later if useful.

## 7. Frontend shape

The sidebar item is named **Solar Spender** with an appropriate solar/power icon.
The panel has three responsive sections:

1. **Now** — controller state, headroom, battery, next action, countdown, enabled
   toggle, and owned-load summary.
2. **Loads** — ordered AC cards, desired profiles, eligibility, ownership, and
   per-load errors.
3. **Settings** — source thresholds, battery policy, per-load minimum-on/off
   times, action confirmation/settling, feedback/probing strategy, validation
   preview, and save.

Use a single-column mobile layout and a wider summary/detail layout on desktop.
Show causal language such as “AC Bedroom can stop in 02:14 (minimum-on)”,
“Surplus remains active until the 200 W exit threshold”, or “AC Bedroom kept on:
Solar Spender did not start it.” This explainability is a primary feature, not
polish.

## 8. Delivery milestones

### Milestone 0 — scaffold and developer loop

- Create HACS-compatible custom-integration structure and manifest.
- Select and document a minimum Home Assistant version.
- Add Python lint/type/test configuration and frontend toolchain.
- Add CI for backend and frontend.
- Add a development Home Assistant setup or documented container workflow.
- Add README installation and development instructions.

Exit criteria: a blank integration can be installed through the UI, unloaded,
and reloaded; a placeholder panel appears with no external network dependency.

### Milestone 1 — configuration and read-only evaluation

- Implement config flow and single-entry lifecycle.
- Implement versioned models and backend validation.
- Implement binary, grid-flow, and production/consumption evaluators, watt
  conversion, export reserve, thresholds, and hysteresis.
- Implement explicit observable-surplus and curtailed-production strategies.
- Implement battery policies.
- Subscribe to entity changes and expose live read-only status.
- Build the panel's Now and Settings views.

Exit criteria: real or test entities drive visible raw/latched/gated states,
but no climate service is called.

### Milestone 2 — deterministic controller

- Implement the explicit state machine and injected clock/scheduler boundaries.
- Implement entry/exit hysteresis, one-load-at-a-time activation, minimum-on
  constrained shedding, minimum-off constrained reactivation, and measurement
  settling.
- Implement one-at-a-time probes, settling classification, rollback, and retry
  backoff for curtailed production.
- Add cancellation/reconciliation logic for rapid state changes.
- Add exhaustive transition tests using simulated time.

Exit criteria: deterministic tests cover every transition and timer replacement;
the controller still makes no real service calls.

### Milestone 3 — climate adapter and ownership

- Validate climate capabilities and desired profiles.
- Add activation confirmation, ownership leases, manual-override detection,
  minimum-on/off constraints, and safe release.
- Learn conservative marginal-power observations and expose their confidence.
- Persist and conservatively recover leases.
- Add partial-failure reporting and bounded retries.
- Expose explicit release-owned-loads control.

Exit criteria: end-to-end tests prove that pre-existing/manual HVAC use is never
released and restart recovery cannot claim ambiguous loads.

### Milestone 4 — usable panel

- Complete load editor with priority ordering.
- Add countdowns, decision reasons, bounded event history, and per-load status.
- Add admin authorization and concurrent-edit revision handling.
- Complete accessibility, mobile behavior, translations, and error states.

Exit criteria: all normal configuration can be completed in the panel, invalid
config cannot be saved, and keyboard-only use passes the documented checklist.

### Milestone 5 — release hardening

- Test against the minimum and current stable Home Assistant versions.
- Exercise removal/rename/unavailability, reload, restart, DST/time jumps, and
  frontend cache updates.
- Add diagnostics with redaction.
- Complete HACS metadata, release packaging, documentation, and upgrade notes.
- Run a multi-day shadow-mode trial, then a limited one-AC trial before enabling
  multiple real loads.

Exit criteria: CI is green, install/upgrade/rollback are documented, and a real
trial shows no unintended shutdowns or rapid cycling.

## 9. Test scenarios that block release

The release is not acceptable until all of these are automated:

1. A numeric source below its entry threshold does not activate a load.
2. A source crossing the entry threshold latches surplus available.
3. A value between entry and exit thresholds preserves the prior latch state.
4. Crossing the exit threshold clears the surplus latch.
5. Invalid, stale, removed, `unknown`, `unavailable`, NaN, and infinity inputs
   clear the latch and fail safe.
6. Power sensors in `W` and `kW` yield the same normalized decision.
7. Both grid-flow sign conventions normalize to the same internal values.
8. No load starts unless export exceeds reserve plus the entry margin.
9. Loads shed when spendable power reaches the lower exit margin.
10. Candidate selection rejects an AC whose conservative estimate exceeds the
    observable spendable-power budget.
11. A full non-charging battery passes `charging_or_soc` but not
    `require_charging`.
12. `full_idle_for_probe` opens only for known full-and-idle battery state.
13. Below-full SOC, charging, discharging, or unavailable battery data blocks a
    curtailed-system probe.
14. Battery discharge beginning during a probe marks it unsupported and rolls
    it back safely.
15. Activation changes only one AC before confirmation and measurement
    settling.
16. The controller re-checks headroom before every AC.
17. Curtailed mode never equates near-zero net flow with zero hidden potential.
18. A supported probe is retained; an importing/discharging probe is rolled
    back.
19. An unobservable probe stops further activation and reports its reason.
20. Overlapping unrelated demand invalidates a marginal-power learning sample.
21. Loss of surplus releases the lowest-priority owned AC whose minimum-on time
    has elapsed.
22. If all owned ACs are inside minimum-on time, no release occurs and the
    earliest eligibility deadline is scheduled.
23. Surplus recovery while waiting cancels the pending release.
24. After the last release, the controller returns to monitoring.
25. A released AC remains ineligible until its own minimum-off deadline.
26. An AC already on before the cycle is neither modified nor released.
27. A manual command-field change relinquishes ownership, while a normal
    `hvac_action` change does not.
28. A `dry` profile without a target temperature never calls
    `climate.set_temperature`.
29. Automatic release restores the pre-activation temperature/fan profile;
    manual override prevents restoration.
30. Minimum-on/off deadlines survive integration reload and Home Assistant
    restart conservatively.
31. Two simultaneous configuration editors cannot lose updates silently.
32. Non-admin users cannot mutate configuration or issue control commands.

## 10. Later roadmap

Only consider these after v1 behavior is proven:

- cumulative-energy interval source;
- subset/knapsack selection after power-estimate confidence is established;
- generic load adapters for switches, water heaters, EVSEs, and dehumidifiers;
- restore-to-previous-state leases as an opt-in alternative to skip-if-on;
- forecast, tariff, battery reserve, and comfort schedules;
- economic export policy that compares feed-in compensation with the configured
  value of pre-cooling/dehumidification;
- variable-temperature or setpoint modulation for inverter ACs;
- per-area occupancy, humidity, and upper/lower comfort bounds;
- Energy Dashboard and logbook integration;
- shadow mode that recommends actions without making service calls.

## 11. Open decisions

Resolve these with prototypes or user testing, and record the result here:

- Minimum supported Home Assistant release and exact supported panel
  registration/static-path APIs.
- Whether config-entry options alone are ergonomic for the ordered load list or
  a small versioned Store is justified.
- Default watt thresholds and minimum-on/off durations; these should not be
  guessed without representative hardware data.
- Whether a closed battery gate during an active cycle should optionally start
  normal shedding.
- Which available sensors can distinguish PV support from grid import and
  battery discharge, and their sign conventions/update latency.
- Default full-SOC threshold and battery-state mappings for the target
  installation; both remain user-configurable.
- Whether temperature-only profiles may rely on an AC's current HVAC mode or
  must require an explicit mode for predictable activation.

## 12. Reference baseline

- Home Assistant integration structure:
  <https://developers.home-assistant.io/docs/creating_integration_file_structure/>
- Config flow:
  <https://developers.home-assistant.io/docs/config_entries_config_flow_handler/>
- Options flow:
  <https://developers.home-assistant.io/docs/core/integration/options_flow/>
- Custom panels:
  <https://developers.home-assistant.io/docs/frontend/custom-ui/creating-custom-panels/>
- Integration quality scale:
  <https://developers.home-assistant.io/docs/core/integration-quality-scale/>
