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

export function constrainNumberValue(value, min, max, previousValue) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return previousValue;
  if (numeric < min || numeric > max) return previousValue;
  return numeric;
}

export function sourceConfigurationVisibility(sourceType) {
  return {
    grid: sourceType === "grid_flow",
    production:
      sourceType === "production_consumption" ||
      sourceType === "curtailed_production",
    curtailed: sourceType === "curtailed_production",
  };
}

export function sourceModeDescription(sourceType) {
  return {
    grid_flow:
      "Best when your grid meter reports live export. Solar Spender uses only export above your reserve and margins.",
    production_consumption:
      "Use when production minus whole-home consumption is directly measurable spare power. Larger positive values are better, so entry is higher than exit.",
    curtailed_production:
      "For zero-export systems where unused capacity is hidden. A small consumption-minus-production deficit permits a cautious one-AC test; it does not prove excess power.",
  }[sourceType] || "Choose how Solar Spender detects spare solar power.";
}

export function batteryPolicyDescription(policy) {
  return {
    disabled:
      "Battery state does not block new AC starts.",
    require_charging:
      "Start another AC only while the battery is measurably charging.",
    charging_or_soc:
      "Start another AC while charging, or after battery state of charge reaches the configured threshold.",
    full_idle_for_probe:
      "Before testing hidden solar capacity, require a full battery with power flow inside the idle threshold.",
  }[policy] || "Choose how battery state affects new AC starts.";
}

export function batteryConfigurationVisibility(policy, directionSource = "power") {
  const enabled = policy !== "disabled";
  return {
    direction: enabled,
    status: enabled && directionSource === "status",
    power: enabled && directionSource === "power",
    soc:
      policy === "charging_or_soc" ||
      policy === "full_idle_for_probe",
    threshold:
      policy === "charging_or_soc" ||
      policy === "full_idle_for_probe",
  };
}

export function loadOwnershipPresentation(load) {
  if (load.owned) return { style: "success", label: "Owned" };
  if (!load.enabled) return { style: "secondary", label: "Disabled" };
  if (load.can_be_owned) return { style: "primary", label: "Can be owned" };
  if (load.blocked_for_cycle) {
    return { style: "warning", label: "Blocked this cycle" };
  }
  return { style: "secondary", label: "Not owned" };
}

export function statusPresentations(status, options) {
  const enabled = Boolean(status?.enabled);
  const stateLabels = {
    blocked_battery: "Blocked by battery",
    disabled: "Disabled",
    monitoring: "Monitoring",
    probing: "Testing one AC",
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
    surplus: status?.source_valid
      ? {
          value: status?.waste_headroom_available ? "Available" : "Unavailable",
          detail: status?.surplus_available
            && !status?.waste_headroom_available
            ? "The solar source qualifies, but the configured battery still has first claim on the power."
            : null,
        }
      : {
          value: "Unknown",
          detail: "The configured source is unavailable or incomplete.",
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
            detail: `${status?.battery_allowed
              ? "The configured battery condition permits a new activation."
              : "New activations are paused by the battery condition."}`
              + (status?.battery_direction
                ? ` Direction: ${status.battery_direction}.`
                : "")
              + (typeof status?.battery_power_w === "number"
                ? ` Normalized battery power: ${Math.round(status.battery_power_w)} W (positive is charging).`
                : ""),
          },
    feedback: !enabled
      ? {
          value: "Idle",
          detail: "Fresh feedback is required only after Solar Spender changes a load.",
        }
      : status?.feedback?.waiting
        ? {
            value: `Checking ${(status.feedback.votes || []).length + 1} of ${status.feedback.sample_count || 1}`,
            detail: `Yes ${(status.feedback.votes || []).filter(Boolean).length} · No ${(status.feedback.votes || []).filter((vote) => !vote).length}`
              + ((status.feedback.pending_entities || []).length
                ? ` · Waiting for ${(status.feedback.pending_entities || []).join(", ")}`
                : ""),
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
