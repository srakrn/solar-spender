# Solar Spender

Solar Spender is a Home Assistant custom integration for using spare solar power
with climate loads such as AC-based dehumidification and pre-cooling.

It supports binary headroom, signed grid-flow, production/consumption, and
curtailed-production probe sources. Loads are activated one at a time and only
loads Solar Spender has activated are automatically released.

Solar Spender requires Home Assistant 2025.5 or newer for fresh same-value
sensor-report tracking.

The integration creates a Solar Spender device with an Automation switch and
number entities for its global confirmation timing. These controls can be used
from dashboards and automations; source wiring and AC profiles remain in the
sidebar panel.

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
- Binary sources have independently configurable continuous-on and
  continuous-off debounce durations; a bounce resets the deadline.
- Each climate load has independent minimum-on and minimum-off durations.
- Solar Spender changes one load at a time and confirms the result from a
  configurable majority of fresh, spaced source reports.
- Cached inverter values never authorize another activation. Same-value reports
  are tracked through Home Assistant's filtered `state_reported` event.
- A load rejected by fresh feedback is released safely and blocked until that
  solar opportunity genuinely ends.
- Supported and failed AC combinations form a temporary capacity range for the
  current solar opportunity. Stable marginal-draw observations are retained as
  conservative, expiring ranking hints.
- An AC already on is not adopted or released.
- A manual mode or setpoint change relinquishes Solar Spender ownership.
- Curtailed-production probes require a configured full-and-idle battery gate.
