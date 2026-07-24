export function shouldLoadPanel(loaded, loading) {
  return !loaded && !loading;
}

export function applySelectorValue(options, key, value) {
  return {
    ...options,
    [key]: value || "",
  };
}

export function relevantPowerEntityIds(states) {
  return Object.values(states || {})
    .filter((state) => {
      const unit = state.attributes?.unit_of_measurement;
      return (
        state.entity_id?.startsWith("sensor.") &&
        state.attributes?.device_class === "power" &&
        state.attributes?.state_class === "measurement" &&
        (unit === "W" || unit === "kW")
      );
    })
    .map((state) => state.entity_id)
    .sort();
}

export function relevantBatterySocEntityIds(states) {
  return Object.values(states || {})
    .filter(
      (state) =>
        state.entity_id?.startsWith("sensor.") &&
        state.attributes?.device_class === "battery" &&
        state.attributes?.state_class === "measurement" &&
        state.attributes?.unit_of_measurement === "%",
    )
    .map((state) => state.entity_id)
    .sort();
}

export function relevantBatteryStatusEntityIds(states, configuredValues = []) {
  const statusValues = new Set([
    "charging",
    "discharging",
    "idle",
    "full",
    "standby",
    "not_charging",
    ...configuredValues.map((value) => String(value).toLowerCase()),
  ]);

  return Object.values(states || {})
    .filter((state) => {
      if (
        state.entity_id?.startsWith("binary_sensor.") &&
        state.attributes?.device_class === "battery_charging"
      ) {
        return true;
      }
      if (!state.entity_id?.startsWith("sensor.")) {
        return false;
      }
      const options = state.attributes?.options || [];
      return (
        statusValues.has(String(state.state).toLowerCase()) ||
        options.some((option) => statusValues.has(String(option).toLowerCase()))
      );
    })
    .map((state) => state.entity_id)
    .sort();
}
