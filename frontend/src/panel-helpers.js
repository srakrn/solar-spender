export function shouldLoadPanel(loaded, loading) {
  return !loaded && !loading;
}

export function applySelectorValue(options, key, value) {
  return {
    ...options,
    [key]: value || "",
  };
}

export function reflectSelectorValue(selector, value) {
  selector.value = value;
}

export function sourceConfigurationVisibility(sourceType) {
  return {
    binary: sourceType === "binary",
    grid: sourceType === "grid_flow",
    production:
      sourceType === "production_consumption" ||
      sourceType === "curtailed_production",
    curtailed: sourceType === "curtailed_production",
  };
}

export function batteryConfigurationVisibility(policy) {
  return {
    status: policy !== "disabled",
    soc:
      policy === "charging_or_soc" ||
      policy === "full_idle_for_probe",
    threshold:
      policy === "charging_or_soc" ||
      policy === "full_idle_for_probe",
  };
}

export function statusPresentations(status, options) {
  const enabled = Boolean(status?.enabled);
  const stateLabels = {
    blocked_battery: "Blocked by battery",
    disabled: "Disabled",
    monitoring: "Monitoring",
    probing: "Probing",
    shedding: "Releasing loads",
    spending: "Spending solar",
    waiting_feedback: "Waiting for feedback",
  };
  const batteryConfigured = options?.battery_policy !== "disabled";

  return {
    controller: enabled
      ? {
          value: stateLabels[status?.state] || "Starting",
          detail: status?.reason || "Evaluating the configured source.",
        }
      : {
          value: "Disabled",
          detail: "Automation is paused. Solar Spender will not start or release ACs.",
        },
    surplus: enabled
      ? {
          value: status?.surplus_available ? "Available" : "Unavailable",
          detail: null,
        }
      : {
          value: "Inactive",
          detail: "Enable Solar Spender to evaluate this source for load control.",
        },
    battery: !batteryConfigured
      ? {
          value: "Not configured",
          detail: "No battery condition is applied to new activations.",
        }
      : !enabled
        ? {
            value: "Inactive",
            detail: "The configured battery condition is evaluated only while Solar Spender is enabled.",
          }
        : {
            value: status?.battery_allowed ? "Open" : "Blocking",
            detail: status?.battery_allowed
              ? "The configured battery condition permits a new activation."
              : "New activations are paused by the battery condition.",
          },
    feedback: !enabled
      ? {
          value: "Idle",
          detail: "Fresh feedback is required only after Solar Spender changes a load.",
        }
      : status?.feedback?.waiting
        ? {
            value: "Waiting for fresh data",
            detail: (status.feedback.pending_entities || []).join(", "),
          }
        : {
            value: "Ready",
            detail: "No load change is waiting for source confirmation.",
          },
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
