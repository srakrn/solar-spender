# Solar Spender

Solar Spender is a Home Assistant custom integration for using spare solar power
with climate loads such as AC-based dehumidification and pre-cooling.

It supports binary headroom, signed grid-flow, production/consumption, and
curtailed-production probe sources. Loads are activated one at a time and only
loads Solar Spender has activated are automatically released.

## Development

Build the bundled Bootstrap panel after changing frontend source:

```sh
cd frontend
npm install
npm run build
```

Copy `custom_components/solar_spender` into Home Assistant's
`custom_components` directory, restart Home Assistant, add **Solar Spender**
from Settings → Devices & services, and configure it from the sidebar panel.

The initial config entry is disabled. It will not command climate entities until
valid source and load options are saved and automation is enabled.

## Current safety model

- Numeric sources use entry/exit hysteresis.
- Each climate load has independent minimum-on and minimum-off durations.
- Solar Spender starts one load, confirms it, waits for measurement settling,
  then re-evaluates.
- An AC already on is not adopted or released.
- A manual mode or setpoint change relinquishes Solar Spender ownership.
- Curtailed-production probes require a configured full-and-idle battery gate.
