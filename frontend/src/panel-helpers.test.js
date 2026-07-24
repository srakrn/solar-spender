import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  applySelectorValue,
  batteryConfigurationVisibility,
  batteryPolicyDescription,
  loadOwnershipPresentation,
  relevantBatterySocEntityIds,
  relevantBatteryStatusEntityIds,
  relevantPowerEntityIds,
  reflectSelectorValue,
  shouldLoadPanel,
  sourceConfigurationVisibility,
  sourceModeDescription,
  statusPresentations,
} from "./panel-helpers.js";

test("entity selector changes are retained for configuration save", () => {
  const original = { grid_entity_id: "" };
  const updated = applySelectorValue(
    original,
    "grid_entity_id",
    "sensor.grid_power",
  );

  assert.equal(original.grid_entity_id, "");
  assert.equal(
    updated.grid_entity_id,
    "sensor.grid_power",
  );
});

test("selector changes are reflected immediately in the displayed control", () => {
  const selector = { value: "" };

  reflectSelectorValue(selector, "sensor.grid_power");

  assert.equal(selector.value, "sensor.grid_power");
});

test("live hass updates do not reload an initialized or loading panel", () => {
  assert.equal(shouldLoadPanel(false, false), true);
  assert.equal(shouldLoadPanel(false, true), false);
  assert.equal(shouldLoadPanel(true, false), false);
});

test("source configuration exposes only fields for the selected strategy", () => {
  assert.deepEqual(sourceConfigurationVisibility("grid_flow"), {
    grid: true,
    production: false,
    curtailed: false,
  });
  assert.deepEqual(sourceConfigurationVisibility("curtailed_production"), {
    grid: false,
    production: true,
    curtailed: true,
  });
});

test("battery configuration hides status and SOC when disabled", () => {
  assert.deepEqual(batteryConfigurationVisibility("disabled"), {
    direction: false,
    status: false,
    power: false,
    soc: false,
    threshold: false,
  });
  assert.deepEqual(batteryConfigurationVisibility("require_charging", "power"), {
    direction: true,
    status: false,
    power: true,
    soc: false,
    threshold: false,
  });
  assert.deepEqual(batteryConfigurationVisibility("charging_or_soc", "status"), {
    direction: true,
    status: true,
    power: false,
    soc: true,
    threshold: true,
  });
});

test("disabled status cards describe inactive and unconfigured states honestly", () => {
  const cards = statusPresentations(
    {
      enabled: false,
      state: "disabled",
      battery_allowed: true,
      feedback: { waiting: false },
    },
    { battery_policy: "disabled" },
  );

  assert.deepEqual(cards.controller, {
    value: "Disabled",
    detail: "Automation is paused. Solar Spender will not start or release ACs.",
  });
  assert.equal(cards.surplus.value, "Unknown");
  assert.equal(cards.battery.value, "Not configured");
  assert.equal(cards.feedback.value, "Idle");
});

test("headroom remains visible while automation is disabled", () => {
  const cards = statusPresentations(
    {
      enabled: false,
      state: "disabled",
      source_valid: true,
      surplus_available: true,
      feedback: { waiting: false },
    },
    { battery_policy: "disabled" },
  );

  assert.equal(cards.surplus.value, "Available");
});

test("source and battery modes explain the selected behavior", () => {
  assert.match(sourceModeDescription("curtailed_production"), /zero-export/i);
  assert.match(sourceModeDescription("curtailed_production"), /one.?AC/i);
  assert.match(sourceModeDescription("curtailed_production"), /does not prove/i);
  assert.match(sourceModeDescription("grid_flow"), /live export/i);
  assert.match(batteryPolicyDescription("full_idle_for_probe"), /idle threshold/i);
});

test("load ownership presentation distinguishes eligible, disabled, and manual loads", () => {
  assert.deepEqual(
    loadOwnershipPresentation({ can_be_owned: true, enabled: true, owned: false }),
    { style: "primary", label: "Can be owned" },
  );
  assert.equal(
    loadOwnershipPresentation({ can_be_owned: false, enabled: false, owned: false }).label,
    "Disabled",
  );
  assert.equal(
    loadOwnershipPresentation({ can_be_owned: false, enabled: true, owned: false }).label,
    "Not owned",
  );
});

test("enabled status cards use readable controller state labels", () => {
  const cards = statusPresentations(
    {
      enabled: true,
      state: "waiting_feedback",
      reason: "waiting for fresh feedback",
      battery_allowed: false,
      feedback: {
        waiting: true,
        votes: [true],
        sample_count: 3,
        pending_entities: ["sensor.grid_power"],
      },
    },
    { battery_policy: "charging_or_soc" },
  );

  assert.equal(cards.controller.value, "Waiting for feedback");
  assert.equal(cards.battery.value, "Blocking");
  assert.equal(cards.feedback.value, "Checking 2 of 3");
  assert.equal(
    cards.feedback.detail,
    "Yes 1 · No 0 · Waiting for sensor.grid_power",
  );
});

test("configured battery condition is inactive while Solar Spender is disabled", () => {
  const cards = statusPresentations(
    {
      enabled: false,
      state: "disabled",
      battery_allowed: true,
      feedback: { waiting: false },
    },
    { battery_policy: "charging_or_soc" },
  );

  assert.deepEqual(cards.battery, {
    value: "Inactive",
    detail: "The configured battery condition is evaluated only while Solar Spender is enabled.",
  });
});

test("panel configuration uses Home Assistant selectors instead of native form inputs", () => {
  const source = readFileSync(
    new URL("./solar-spender-panel.js", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(source, /<input\b|<select\b/);
  assert.doesNotMatch(source, /data-bs-toggle=["']tooltip["']/);
  assert.match(source, /<ha-selector data-number-key=/);
  assert.match(source, /<ha-selector data-load-select-index=/);
  assert.match(source, /min_on_seconds: 300/);
  assert.match(source, /feedback_sample_count: 3/);
  assert.doesNotMatch(source, /utility/i);
  assert.doesNotMatch(source, /Binary headroom/);
  assert.match(source, /Equal priorities follow the AC list order/);
  assert.match(source, /data-load-enabled-index/);
  assert.match(source, /battery_power_entity_id/);
  assert.match(source, /Maximum deficit to start testing/);
  assert.match(source, /Deficit that stops testing/);
  assert.match(source, /minimum_production_w/);
  assert.match(source, /Deficit = consumption − production/);
  assert.match(source, /Measured headroom = production − consumption/);
});

test("power entities are restricted to W and kW power sensors", () => {
  const states = {
    "sensor.grid_power": {
      entity_id: "sensor.grid_power",
      attributes: {
        device_class: "power",
        state_class: "measurement",
        unit_of_measurement: "W",
      },
    },
    "sensor.solar_power": {
      entity_id: "sensor.solar_power",
      attributes: {
        device_class: "power",
        state_class: "measurement",
        unit_of_measurement: "kW",
      },
    },
    "sensor.energy": {
      entity_id: "sensor.energy",
      attributes: { device_class: "energy", unit_of_measurement: "kWh" },
    },
    "sensor.power_no_unit": {
      entity_id: "sensor.power_no_unit",
      attributes: { device_class: "power" },
    },
    "input_number.fake_power": {
      entity_id: "input_number.fake_power",
      attributes: { device_class: "power", unit_of_measurement: "W" },
    },
  };

  assert.deepEqual(relevantPowerEntityIds(states), [
    "sensor.grid_power",
    "sensor.solar_power",
  ]);
});

test("battery SOC entities are restricted to percentage battery sensors", () => {
  const states = {
    "sensor.battery_soc": {
      entity_id: "sensor.battery_soc",
      attributes: {
        device_class: "battery",
        state_class: "measurement",
        unit_of_measurement: "%",
      },
    },
    "sensor.battery_power": {
      entity_id: "sensor.battery_power",
      attributes: { device_class: "power", unit_of_measurement: "W" },
    },
  };

  assert.deepEqual(relevantBatterySocEntityIds(states), ["sensor.battery_soc"]);
});

test("battery status entities are restricted to charging status semantics", () => {
  const states = {
    "binary_sensor.battery_charging": {
      entity_id: "binary_sensor.battery_charging",
      state: "off",
      attributes: { device_class: "battery_charging" },
    },
    "sensor.battery_mode": {
      entity_id: "sensor.battery_mode",
      state: "idle",
      attributes: { options: ["charging", "discharging", "idle"] },
    },
    "sensor.random_mode": {
      entity_id: "sensor.random_mode",
      state: "auto",
      attributes: { options: ["auto", "manual"] },
    },
  };

  assert.deepEqual(relevantBatteryStatusEntityIds(states), [
    "binary_sensor.battery_charging",
    "sensor.battery_mode",
  ]);
});
