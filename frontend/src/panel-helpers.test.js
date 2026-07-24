import test from "node:test";
import assert from "node:assert/strict";

import {
  applySelectorValue,
  batteryConfigurationVisibility,
  relevantBatterySocEntityIds,
  relevantBatteryStatusEntityIds,
  relevantPowerEntityIds,
  shouldLoadPanel,
  sourceConfigurationVisibility,
} from "./panel-helpers.js";

test("entity selector changes are retained for configuration save", () => {
  const original = { binary_entity_id: "" };
  const updated = applySelectorValue(
    original,
    "binary_entity_id",
    "binary_sensor.deye_inverter_solar_system_headroom",
  );

  assert.equal(original.binary_entity_id, "");
  assert.equal(
    updated.binary_entity_id,
    "binary_sensor.deye_inverter_solar_system_headroom",
  );
});

test("live hass updates do not reload an initialized or loading panel", () => {
  assert.equal(shouldLoadPanel(false, false), true);
  assert.equal(shouldLoadPanel(false, true), false);
  assert.equal(shouldLoadPanel(true, false), false);
});

test("source configuration exposes only fields for the selected strategy", () => {
  assert.deepEqual(sourceConfigurationVisibility("binary"), {
    binary: true,
    grid: false,
    production: false,
    curtailed: false,
  });
  assert.deepEqual(sourceConfigurationVisibility("grid_flow"), {
    binary: false,
    grid: true,
    production: false,
    curtailed: false,
  });
  assert.deepEqual(sourceConfigurationVisibility("curtailed_production"), {
    binary: false,
    grid: false,
    production: true,
    curtailed: true,
  });
});

test("battery configuration hides status and SOC when disabled", () => {
  assert.deepEqual(batteryConfigurationVisibility("disabled"), {
    status: false,
    soc: false,
    threshold: false,
  });
  assert.deepEqual(batteryConfigurationVisibility("require_charging"), {
    status: true,
    soc: false,
    threshold: false,
  });
  assert.deepEqual(batteryConfigurationVisibility("charging_or_soc"), {
    status: true,
    soc: true,
    threshold: true,
  });
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
