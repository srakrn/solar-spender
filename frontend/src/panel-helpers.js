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
      "Use your grid meter. Solar Spender uses export above the amount you keep.",
    production_consumption:
      "Use solar production minus home use. A bigger result means more spare solar.",
    curtailed_production:
      "For zero-export systems. Solar Spender tests one AC at a time because spare solar cannot be measured directly.",
  }[sourceType] || "Choose how to measure spare solar.";
}

export function batteryPolicyDescription(policy) {
  return {
    disabled:
      "Ignore the battery.",
    require_charging:
      "Start an AC only while the battery is charging.",
    charging_or_soc:
      "Start an AC while charging or when the battery reaches the set level.",
    full_idle_for_probe:
      "For zero-export testing, start an AC only when the battery is full and idle.",
  }[policy] || "Choose when the battery allows a new AC.";
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
  if (load.can_be_owned) return { style: "primary", label: "Ready" };
  if (load.blocked_for_cycle) {
    return { style: "warning", label: "Blocked for now" };
  }
  return { style: "secondary", label: "Not owned" };
}

export function statusPresentations(status, options) {
  const enabled = Boolean(status?.enabled);
  const paused = Boolean(status?.paused);
  const stateLabels = {
    blocked_battery: "Battery blocked",
    disabled: "Disabled",
    monitoring: "Watching",
    paused: "Paused",
    probing: "Testing one AC",
    shedding: "Turning off an AC",
    spending: "Using solar",
    waiting_feedback: "Checking",
  };
  const batteryConfigured = options?.battery_policy !== "disabled";

  return {
    controller: enabled && paused
      ? {
          value: "Paused",
          detail: status?.paused_until
            ? `Paused until ${new Date(status.paused_until).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}. ACs stay as they are.`
            : "Paused. ACs stay as they are.",
        }
      : enabled
      ? {
          value: stateLabels[status?.state] || "Starting",
          detail: status?.reason || "Checking for spare solar.",
        }
      : {
          value: "Disabled",
          detail: "Solar Spender will not turn ACs on or off.",
        },
    surplus: paused
      ? {
          value: "Frozen",
          detail: "Not checked while paused.",
        }
      : status?.source_valid
      ? {
          value: status?.waste_headroom_available ? "Yes" : "No",
          detail: (status?.stale_input_entities || []).length
            ? `Stale: ${status.stale_input_entities.join(", ")}`
            : status?.surplus_available
            && !status?.waste_headroom_available
            ? "The battery gets the spare solar first."
            : null,
        }
      : {
          value: "Unknown",
          detail: (status?.stale_input_entities || []).length
            ? `Stale: ${status.stale_input_entities.join(", ")}`
            : "The solar sensors are missing or unavailable.",
        },
    battery: !batteryConfigured
      ? {
          value: "Not configured",
          detail: "The battery is ignored.",
        }
      : !enabled
        ? {
            value: "Inactive",
            detail: "Battery checks run only when Solar Spender is enabled.",
          }
        : paused
          ? {
              value: "Frozen",
              detail: "Not checked while paused.",
            }
          : {
            value: status?.battery_allowed ? "Pass" : "Block",
            detail: `${status?.battery_allowed
              ? "The battery allows a new AC."
              : "The battery does not allow a new AC."}`
              + (status?.battery_direction
                ? ` It is ${status.battery_direction}.`
                : "")
              + (typeof status?.battery_power_w === "number"
                ? ` Battery power: ${Math.round(status.battery_power_w)} W.`
                : ""),
          },
    feedback: !enabled
      ? {
          value: "Idle",
          detail: "Nothing to check.",
        }
      : paused
        ? {
            value: "Paused",
            detail: "Checks will restart after resume.",
          }
        : status?.feedback?.waiting
        ? {
            value: `Checking ${(status.feedback.votes || []).length + 1} of ${status.feedback.sample_count || 1}`,
            detail: `Pass ${(status.feedback.votes || []).filter(Boolean).length} · Fail ${(status.feedback.votes || []).filter((vote) => !vote).length}`
              + ((status.feedback.pending_entities || []).length
                ? ` · Waiting: ${(status.feedback.pending_entities || []).join(", ")}`
                : "")
              + (status.feedback.deadline
                ? ` · Timeout: ${new Date(status.feedback.deadline).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
                : ""),
          }
        : {
            value: "Ready",
            detail: "Nothing to check.",
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
