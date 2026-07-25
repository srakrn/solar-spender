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

## Current safety model

- Numeric sources use entry/exit hysteresis.
- Observable sources treat larger `production - consumption` headroom as
  better. Zero-export testing instead enters on a low
  `consumption - production` deficit and exits at a larger deficit; this is only
  permission to test one AC, not proof of spare capacity.
- Each climate load has independent minimum-on and minimum-off durations.
- Solar Spender changes one load at a time and confirms the result from a
  configurable majority of fresh, spaced source reports.
- Cached inverter values never authorize another activation. Same-value reports
  are tracked through Home Assistant's filtered `state_reported` event.
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
- Battery direction can use a status entity or a signed W/kW power sensor with
  a configurable idle threshold.
- Waste headroom is off while a configured battery is charging, discharging, or
  not yet eligible; charging the battery is useful solar consumption, not waste.
- Confirmed ownership leases, timing deadlines, cycle blocks, and recent
  decisions survive restart. A lease is recovered only if the AC still matches
  Solar Spender's exact commanded profile; ambiguous ACs are left untouched.
