import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  applySelectorValue,
  batteryConfigurationVisibility,
  batteryPolicyDescription,
  constrainNumberValue,
  loadPowerDescription,
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

test("number selectors reject values outside their configured range", () => {
  assert.equal(constrainNumberValue(-1000, 0, 1000000, 50), 50);
  assert.equal(constrainNumberValue(120, 0, 1000000, 50), 120);
  assert.equal(constrainNumberValue(101, 0, 100, 98), 98);
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
    detail: "Solar Spender will not turn ACs on or off.",
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
      waste_headroom_available: true,
      feedback: { waiting: false },
    },
    { battery_policy: "disabled" },
  );

  assert.equal(cards.surplus.value, "Yes");
});

test("battery charging removes waste headroom without hiding source opportunity", () => {
  const cards = statusPresentations(
    {
      enabled: true,
      source_valid: true,
      surplus_available: true,
      waste_headroom_available: false,
      battery_direction: "charging",
      feedback: { waiting: false },
    },
    { battery_policy: "charging_or_soc" },
  );

  assert.equal(cards.surplus.value, "No");
  assert.match(cards.surplus.detail, /battery gets the spare solar first/i);
});

test("source and battery modes explain the selected behavior", () => {
  assert.match(sourceModeDescription("curtailed_production"), /zero-export/i);
  assert.match(sourceModeDescription("curtailed_production"), /one AC/i);
  assert.match(sourceModeDescription("curtailed_production"), /cannot be measured/i);
  assert.match(sourceModeDescription("grid_flow"), /grid meter/i);
  assert.match(batteryPolicyDescription("full_idle_for_probe"), /full and idle/i);
});

test("load ownership presentation distinguishes eligible, disabled, and manual loads", () => {
  assert.deepEqual(
    loadOwnershipPresentation({ can_be_owned: true, enabled: true, owned: false }),
    { style: "primary", label: "Ready" },
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

test("derived AC power explains an assumed zero", () => {
  assert.equal(
    loadPowerDescription({
      current_power_w: 0,
      current_power_assumed_zero: true,
    }),
    " · 0 W — no recent energy increment",
  );
  assert.equal(
    loadPowerDescription({
      current_power_w: 423.6,
      current_power_assumed_zero: false,
    }),
    " · about 424 W",
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

  assert.equal(cards.controller.value, "Checking");
  assert.equal(cards.battery.value, "Block");
  assert.equal(cards.feedback.value, "Checking 2 of 3");
  assert.match(cards.feedback.detail, /Pass 1 · Fail 0/);
  assert.match(cards.feedback.detail, /Waiting: sensor.grid_power/);
});

test("stale inputs are named in source status", () => {
  const cards = statusPresentations(
    {
      enabled: true,
      source_valid: false,
      stale_input_entities: ["sensor.grid_power"],
      feedback: { waiting: false },
    },
    { battery_policy: "disabled" },
  );

  assert.equal(cards.surplus.detail, "Stale: sensor.grid_power");
});

test("timed pause freezes every live decision card", () => {
  const cards = statusPresentations(
    {
      enabled: true,
      paused: true,
      paused_until: "2026-07-25T12:05:00+00:00",
      source_valid: true,
      surplus_available: false,
      battery_allowed: false,
      feedback: { waiting: true, votes: [false] },
    },
    { battery_policy: "charging_or_soc" },
  );

  assert.equal(cards.controller.value, "Paused");
  assert.equal(cards.surplus.value, "Frozen");
  assert.equal(cards.battery.value, "Frozen");
  assert.equal(cards.feedback.value, "Paused");
  assert.match(cards.feedback.detail, /restart after resume/i);
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
    detail: "Battery checks run only when Solar Spender is enabled.",
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
  assert.match(source, /feedback_timeout_minutes: 15/);
  assert.match(source, /input_max_age_minutes: 15/);
  assert.doesNotMatch(source, /feedback_sample_interval_minutes/);
  assert.doesNotMatch(source, /utility/i);
  assert.doesNotMatch(source, /Binary headroom/);
  assert.match(source, /Lower numbers start first/i);
  assert.match(source, /data-load-enabled-index/);
  assert.match(source, /battery_power_entity_id/);
  assert.match(source, /Start test below/);
  assert.match(source, /Stop test above/);
  assert.match(source, /minimum_production_w/);
  assert.match(source, /Gap = home use − solar/);
  assert.match(source, /Spare solar = solar − home use/);
  assert.match(source, /Idle range/);
  assert.match(source, /data-load-power-index/);
  assert.match(source, /AC power sensor/);
  assert.match(source, /Spare solar/);
  assert.match(source, /owned AC\(s\) restored after restart/);
  assert.match(source, /saved AC state\(s\) could not be trusted/);
  assert.match(source, /Pause 5 min/);
  assert.match(source, /solar_spender\/control\/set_pause/);
  assert.match(source, /Resume now/);
});

test("panel uses the Home Assistant mobile shell and controls", () => {
  const source = readFileSync(
    new URL("./solar-spender-panel.js", import.meta.url),
    "utf8",
  );

  assert.match(source, /set narrow\(value\)/);
  assert.match(source, /hass-toggle-menu/);
  assert.match(source, /bubbles: true/);
  assert.match(source, /composed: true/);
  assert.match(source, /<ha-icon-button id="menu"/);
  assert.match(source, /<ha-button data-pause-minutes="5"/);
  assert.match(source, /<ha-button id="save"/);
  assert.match(source, /env\(safe-area-inset-top/);
  assert.match(source, /min-height: 44px/);
  assert.doesNotMatch(source, /<button\b/);
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
