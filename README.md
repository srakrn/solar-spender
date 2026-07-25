# Solar Spender

Solar Spender is a Home Assistant custom integration for using spare solar power
with climate loads such as AC-based dehumidification and pre-cooling.

It supports signed grid-flow, production/consumption, and cautious zero-export
solar sources. Loads are activated one at a time and only loads Solar Spender
has activated are automatically released.

Solar Spender requires Home Assistant 2025.5 or newer for fresh same-value
sensor-report tracking.

The integration creates a Solar Spender device with an Automation switch, a
read-only Waste headroom binary sensor, and number entities for its global
confirmation timing. The headroom sensor continues evaluating while automation
is disabled. These entities can be used from dashboards and automations; source
wiring and AC profiles remain in the sidebar panel.

The sidebar also has 5, 15, and 30 minute pause controls. A timed pause freezes
source, battery, feedback, and ownership reactions while leaving every AC
untouched. When the pause ends, current inputs are evaluated again; if Solar
Spender was confirming a load change, that interrupted confirmation restarts
using only fresh post-pause reports.

## Development

Build the bundled Bootstrap panel after changing frontend source:

```sh
cd frontend
npm install
npm run build
```

Run the currently scaffolded regression checks:

```sh
python3 -m unittest discover -s tests -v
cd frontend
npm test
npm run build
```

Copy `custom_components/solar_spender` into Home Assistant's
`custom_components` directory, restart Home Assistant, add **Solar Spender**
from Settings → Devices & services, and configure it from the sidebar panel.

The initial config entry is disabled. It will not command climate entities until
valid source and load options are saved and automation is enabled.

## Upgrading to 0.9.0

Schema v6 adds measured grid/battery fallback accounting for zero-export
probes. Existing zero-export configurations are disabled during migration so
the grid-import allowance, fallback-energy budget, battery power sensor, and
conservative usual power for each enabled AC can be reviewed before automation
is turned on again.

Optional per-AC power sensors now have an “Assume zero after” setting, defaulting
to 15 minutes. This supports power sensors derived from cumulative Wh counters:
when a valid numeric reading stops reporting beyond that interval, Solar
Spender treats current draw as 0 W without treating stale source or battery
feedback as valid.

## Upgrading to 0.8.0

Schema v5 removes the fixed “wait between checks” setting. Existing entries
receive a 15-minute confirmation timeout and 15-minute maximum input age. The
retired number entity is removed; the two replacement number entities and all
other timing controls are editable from both the Solar Spender device and the
integration's Configure dialog.

## Current safety model

- Numeric sources use entry/exit hysteresis.
- Observable sources treat larger `production - consumption` headroom as
  better. Zero-export testing instead enters on a low
  `consumption - production` deficit and exits at a larger deficit; this is only
  permission to test one AC, not proof of spare capacity.
- Each climate load has independent minimum-on and minimum-off durations.
- Solar Spender changes one load at a time and confirms the result from a
  configurable majority of distinct fresh source reports.
- Cached inverter values never authorize another activation. Same-value reports
  are tracked through Home Assistant's filtered `state_reported` event.
- Confirmation fails closed if enough reports do not arrive before its
  configurable timeout. Source and configured battery inputs also have a
  configurable maximum age.
- A load rejected by fresh feedback is released safely and blocked until that
  solar opportunity genuinely ends.
- Each AC may use a conservative watt estimate, an optional live W/kW power
  sensor, or both. When a measured deficit requires shedding, Solar Spender
  prefers the smallest eligible AC that covers the gap and then re-measures.
- Supported and failed AC combinations form a temporary capacity range for the
  current solar opportunity. Stable marginal-draw observations are retained as
  conservative, expiring feasibility hints.
- An AC already on is not adopted or released.
- A manual mode or setpoint change relinquishes Solar Spender ownership.
- Zero-export solar testing requires a configured full-and-idle battery gate.
- Zero-export tests require fresh signed grid and battery power. Background grid
  import is allowed only up to its configured ceiling, and measured excess grid
  plus battery energy is bounded per probe.
- Battery direction can use a status entity or a signed W/kW power sensor with
  a configurable idle threshold.
- Waste headroom is off while a configured battery is charging, discharging, or
  not yet eligible; charging the battery is useful solar consumption, not waste.
- Confirmed battery discharge means owned AC demand is no longer free. Solar
  Spender sheds one AC at a time, using discharge watts to size the release when
  available. A closed battery gate cannot mask simultaneous solar-source loss.
- Confirmed ownership leases, timing deadlines, cycle blocks, and recent
  decisions survive restart. A lease is recovered only if the AC still matches
  Solar Spender's exact commanded profile; ambiguous ACs are left untouched.
- A valid per-AC power reading derived from cumulative energy becomes an
  explicit 0 W after its configured silence timeout; source and battery
  readings never receive that exception.
- A timed pause survives restart, ignores short household peaks, never starts or
  releases an AC, and discards any confirmation sequence interrupted by the
  pause.
