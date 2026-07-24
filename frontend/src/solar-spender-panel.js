import bootstrapCss from "bootstrap/dist/css/bootstrap.min.css";
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

const DEFAULT_OPTIONS = {
  enabled: false,
  source_type: "production_consumption",
  grid_entity_id: "",
  grid_export_positive: true,
  production_entity_id: "",
  consumption_entity_id: "",
  entry_threshold_w: 300,
  exit_threshold_w: 100,
  export_reserve_w: 0,
  settling_seconds: 300,
  feedback_sample_count: 3,
  feedback_sample_interval_minutes: 5,
  next_load_delay_minutes: 5,
  loads: [],
  battery_policy: "disabled",
  battery_soc_entity_id: "",
  battery_status_entity_id: "",
  battery_power_entity_id: "",
  battery_direction_source: "power",
  battery_power_charging_positive: true,
  battery_power_threshold_w: 50,
  battery_full_threshold: 98,
  charging_states: ["charging"],
  discharging_states: ["discharging"],
};

const PANEL_VERSION = "0.4.0";
const KEEP_CURRENT = "__keep_current__";

const SELECT_OPTIONS = {
  enabled: [["true", "Enabled"], ["false", "Disabled"]],
  source_type: [
    ["grid_flow", "Grid export"],
    ["production_consumption", "Solar production minus home use"],
    ["curtailed_production", "Zero-export solar · test one AC at a time"],
  ],
  grid_export_positive: [
    ["true", "Export is positive"],
    ["false", "Import is positive"],
  ],
  battery_policy: [
    ["disabled", "Disabled"],
    ["require_charging", "Require charging"],
    ["charging_or_soc", "Charging or SOC threshold"],
    ["full_idle_for_probe", "Full battery before zero-export testing"],
  ],
  battery_direction_source: [
    ["power", "Battery power sensor"],
    ["status", "Charging status entity"],
  ],
  battery_power_charging_positive: [
    ["true", "Positive means charging"],
    ["false", "Negative means charging"],
  ],
};

const ENTITY_SELECTORS = {
  load_entity_id: {
    entity: {
      filter: [{ domain: "climate" }],
    },
  },
};

/** Home Assistant's required panel-registration host; all form controls are HA selectors or Bootstrap markup. */
class SolarSpenderPanelHost extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._options = { ...DEFAULT_OPTIONS };
    this._status = null;
    this._loaded = false;
    this._loading = false;
    this._shadow = this.attachShadow({ mode: "open" });
    this._shadow.innerHTML = `
      <style>
        ${bootstrapCss}
        :host {
          color: var(--primary-text-color);
          background: var(--primary-background-color);
        }
        #app {
          max-width: 1680px;
          color: var(--primary-text-color);
        }
        ha-card {
          display: block;
          height: 100%;
          color: var(--primary-text-color);
          background: var(--ha-card-background, var(--card-background-color));
          border: 1px solid var(--divider-color);
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, none);
        }
        .card-content {
          padding: 20px;
        }
        .status-card .card-content {
          min-height: 118px;
        }
        .config-section {
          padding: 16px;
          background: var(--secondary-background-color);
          border: 1px solid var(--divider-color);
          border-radius: 12px;
        }
        .load-card {
          height: auto;
          background: var(--secondary-background-color);
        }
        .text-body-secondary,
        .form-text {
          color: var(--secondary-text-color) !important;
        }
        .form-label {
          color: var(--primary-text-color);
          font-weight: 500;
        }
        .field-help {
          min-height: 2.4em;
          line-height: 1.35;
        }
        .list-group-item {
          color: var(--primary-text-color);
          background: transparent;
          border-color: var(--divider-color);
        }
        .section-heading {
          color: var(--primary-text-color);
        }
        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after {
            scroll-behavior: auto !important;
            transition: none !important;
          }
        }
      </style>
      <main class="container-fluid py-3" id="app"></main>`;
  }

  set hass(value) {
    this._hass = value;
    this._syncSelectorHass();
    if (shouldLoadPanel(this._loaded, this._loading)) {
      this._load();
    }
  }

  get hass() { return this._hass; }
  connectedCallback() { this._render(); }

  async _load() {
    if (!this._hass?.connection || this._loading) return;
    this._loading = true;
    try {
      const [status, options] = await Promise.all([
        this._hass.connection.sendMessagePromise({ type: "solar_spender/status/get" }),
        this._hass.connection.sendMessagePromise({ type: "solar_spender/config/get" }),
      ]);
      this._status = status;
      this._options = { ...DEFAULT_OPTIONS, ...options };
      this._error = null;
      this._loaded = true;
    } catch (error) {
      this._error = error?.message || "Solar Spender is not configured yet.";
    } finally {
      this._loading = false;
    }
    this._render();
  }

  _render() {
    const app = this._shadow?.querySelector("#app");
    if (!app) return;
    if (this._error) {
      app.innerHTML = `<div class="alert alert-info"><h1 class="h4">Solar Spender</h1><p class="mb-0">${this._escape(this._error)} Add the integration from Settings → Devices & services, then return here.</p></div>`;
      return;
    }
    const status = this._status || {};
    const cards = statusPresentations(status, this._options);
    app.innerHTML = `
      <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
        <div><h1 class="h3 mb-1">Solar Spender <span class="badge text-bg-secondary fs-6 align-middle">v${PANEL_VERSION}</span></h1><p class="text-body-secondary mb-0">Use spare solar power for climate loads.</p></div>
        <button class="btn btn-outline-primary" id="refresh" type="button">Refresh</button>
      </div>
      <div class="row g-3 mb-3">
        ${this._card("Solar Spender", cards.controller.value, cards.controller.detail)}
        ${this._card("Solar headroom", cards.surplus.value, cards.surplus.detail ?? this._surplusDetail(status))}
        ${this._card("Battery condition", cards.battery.value, cards.battery.detail)}
        ${this._card("Source feedback", cards.feedback.value, cards.feedback.detail)}
        ${this._card("Learned capacity", this._learnedRange(status), "Temporary estimate for the current solar opportunity.")}
        ${this._card("Solar Spender ACs", `${status.owned_loads?.length || 0} owned`, "Only ACs started by Solar Spender can be released automatically.")}
      </div>
      <div class="row g-3">
        <section class="col-12 col-xxl-8"><ha-card><div class="card-content">
          <h2 class="h5 mb-3 section-heading">Configuration</h2>
          <form id="settings" class="row g-3" novalidate>
            <div class="col-md-6">${this._selectField("enabled", "Automation", "Solar Spender never calls a climate service while disabled.")}</div>
            <div class="col-md-6">${this._selectField("source_type", "Surplus source", "Choose the measurement strategy for this solar installation.")}</div>
            <div class="col-12">${this._sourceConfiguration()}</div>
            <div class="col-12">${this._batteryConfiguration()}</div>
            <div class="col-12 config-section">
              <h3 class="h6 mb-3 section-heading">Confirmation timing</h3>
              <div class="row g-3">
                <div class="col-md-6">${this._numberField("settling_seconds", "First check delay", "Ignore sensor updates for this long after an AC changes. For activation, the AC's minimum-on time must also finish.", 0, null, "seconds")}</div>
                <div class="col-md-6">${this._numberField("feedback_sample_count", "Confirmation checks", "Number of fresh checks used for the decision. A strict majority must report headroom.", 1, 9, "checks", 2)}</div>
                <div class="col-md-6">${this._numberField("feedback_sample_interval_minutes", "Time between checks", "Minimum spacing between fresh sensor reports that may count as confirmation votes.", 1, 60, "minutes")}</div>
                <div class="col-md-6">${this._numberField("next_load_delay_minutes", "Wait before next AC", "After confirmation succeeds, wait this long before considering another AC.", 0, 60, "minutes")}</div>
                <div class="col-12"><div class="alert alert-secondary mb-0">Default cadence: first check after 5 minutes, 3 fresh checks 5 minutes apart, then wait 5 minutes before another AC.</div></div>
              </div>
            </div>
            <div class="col-12"><div class="d-flex justify-content-between align-items-center"><h3 class="h6 mb-0 section-heading">Climate loads</h3><button type="button" class="btn btn-sm btn-outline-primary" id="add_load">Add AC</button></div><p class="form-text mb-0">Solar Spender controls only ACs it started itself.</p></div>
            <div class="col-12" id="load_rows">${this._loadRows()}</div>
            <div class="col-12 d-flex align-items-center gap-2"><button class="btn btn-primary" type="submit">Save configuration</button><span id="save_result" class="small" role="status"></span></div>
          </form>
        </div></ha-card></section>
        <section class="col-12 col-xxl-4">
          <div class="d-grid gap-3">
            <ha-card><div class="card-content"><h2 class="h5 section-heading">Loads now</h2>${this._loads(status.loads || [])}</div></ha-card>
            <ha-card><div class="card-content"><h2 class="h5 section-heading">Recent decisions</h2>${this._history(status.history || [])}</div></ha-card>
          </div>
        </section>
      </div>`;
    this._hydrateHaSelectors();
    app.querySelector("#refresh").addEventListener("click", () => this._load());
    app.querySelector("#settings").addEventListener("submit", (event) => this._save(event));
    app.querySelector("#add_load").addEventListener("click", () => this._addLoad());
    app.querySelectorAll("[data-remove-load]").forEach((button) => button.addEventListener("click", () => this._removeLoad(Number(button.dataset.removeLoad))));
  }

  _hydrateHaSelectors() {
    this._shadow.querySelectorAll("ha-selector[data-select-key]").forEach((selector) => {
      const key = selector.dataset.selectKey;
      selector.hass = this._hass;
      selector.selector = {
        select: {
          mode: "dropdown",
          options: SELECT_OPTIONS[key].map(([value, label]) => ({ value, label })),
        },
      };
      selector.value = String(this._options[key]);
      selector.addEventListener("value-changed", (event) => {
        const value = event.detail?.value;
        if (value === undefined || value === String(this._options[key])) return;
        reflectSelectorValue(selector, value);
        const options = this._collectOptions();
        options[key] = key === "enabled"
          || key === "grid_export_positive"
          || key === "battery_power_charging_positive"
          ? value === "true"
          : value;
        this._options = options;
        if (
          key === "source_type"
          || key === "battery_policy"
          || key === "battery_direction_source"
        ) {
          this._render();
        }
      });
    });
    this._shadow.querySelectorAll("ha-selector[data-number-key]").forEach((selector) => {
      const key = selector.dataset.numberKey;
      selector.hass = this._hass;
      selector.selector = this._numberSelector(selector.dataset);
      selector.value = this._options[key];
      selector.addEventListener("value-changed", (event) => {
        const value = event.detail?.value;
        if (value === undefined || value === null || value === "") return;
        reflectSelectorValue(selector, Number(value));
        this._options = {
          ...this._options,
          [key]: Number(value),
        };
      });
    });
    this._shadow.querySelectorAll("ha-selector[data-key]").forEach((selector) => {
      const key = selector.dataset.key;
      selector.hass = this._hass;
      selector.selector = this._entitySelector(key);
      selector.value = this._options[key] || "";
      selector.addEventListener("value-changed", (event) => {
        const value = event.detail?.value || "";
        reflectSelectorValue(selector, value);
        this._options = applySelectorValue(
          this._options,
          key,
          value,
        );
      });
    });
    this._shadow.querySelectorAll("ha-selector[data-load-index]").forEach((selector) => {
      const index = Number(selector.dataset.loadIndex);
      const load = this._options.loads[index] || {};
      selector.hass = this._hass;
      selector.selector = this._entitySelector("load_entity_id");
      selector.value = load.entity_id || "";
      selector.addEventListener("value-changed", (event) => {
        const value = event.detail?.value || "";
        reflectSelectorValue(selector, value);
        const options = this._collectOptions();
        options.loads[index].entity_id = value;
        this._options = options;
        this._render();
      });
    });
    this._shadow.querySelectorAll("ha-selector[data-load-number-index]").forEach((selector) => {
      const index = Number(selector.dataset.loadNumberIndex);
      const key = selector.dataset.loadNumberKey;
      const load = this._options.loads[index] || {};
      selector.hass = this._hass;
      selector.selector = this._numberSelector(selector.dataset);
      selector.value = load[key] ?? null;
      selector.addEventListener("value-changed", (event) => {
        const rawValue = event.detail?.value;
        const value = rawValue === undefined || rawValue === null || rawValue === ""
          ? null
          : Number(rawValue);
        reflectSelectorValue(selector, value);
        this._updateLoadOption(index, key, value);
      });
    });
    this._shadow.querySelectorAll("ha-selector[data-load-select-index]").forEach((selector) => {
      const index = Number(selector.dataset.loadSelectIndex);
      const key = selector.dataset.loadSelectKey;
      const load = this._options.loads[index] || {};
      const capabilities = this._climateCapabilities(load.entity_id);
      const values = key === "hvac_mode"
        ? capabilities.hvacModes
        : capabilities.fanModes;
      selector.hass = this._hass;
      selector.selector = {
        select: {
          mode: "dropdown",
          options: [
            { value: KEEP_CURRENT, label: "Keep current" },
            ...values.map((value) => ({ value, label: value })),
          ],
        },
      };
      selector.value = load[key] ?? KEEP_CURRENT;
      selector.addEventListener("value-changed", (event) => {
        const value = event.detail?.value;
        if (value === undefined) return;
        reflectSelectorValue(selector, value);
        this._updateLoadOption(
          index,
          key,
          value === KEEP_CURRENT ? null : value,
        );
      });
    });
    this._shadow.querySelectorAll("ha-selector[data-load-enabled-index]").forEach((selector) => {
      const index = Number(selector.dataset.loadEnabledIndex);
      const load = this._options.loads[index] || {};
      selector.hass = this._hass;
      selector.selector = {
        select: {
          mode: "dropdown",
          options: [
            { value: "true", label: "Enabled" },
            { value: "false", label: "Disabled" },
          ],
        },
      };
      selector.value = String(load.enabled !== false);
      selector.addEventListener("value-changed", (event) => {
        const value = event.detail?.value;
        if (value === undefined) return;
        reflectSelectorValue(selector, value);
        this._updateLoadOption(index, "enabled", value === "true");
        this._render();
      });
    });
  }

  _numberSelector(dataset) {
    const number = {
      mode: "box",
      min: Number(dataset.min),
      max: Number(dataset.max),
      step: dataset.step === "any" ? "any" : Number(dataset.step),
    };
    if (dataset.unit) number.unit_of_measurement = dataset.unit;
    return { number };
  }

  _updateLoadOption(index, key, value) {
    this._options = {
      ...this._options,
      loads: this._options.loads.map((load, loadIndex) => (
        loadIndex === index ? { ...load, [key]: value } : load
      )),
    };
  }

  _syncSelectorHass() {
    this._shadow
      ?.querySelectorAll("ha-selector")
      .forEach((selector) => {
        selector.hass = this._hass;
      });
  }

  _entitySelector(key) {
    if (
      key === "grid_entity_id" ||
      key === "production_entity_id" ||
      key === "consumption_entity_id" ||
      key === "battery_power_entity_id"
    ) {
      return {
        entity: {
          include_entities: relevantPowerEntityIds(this._hass?.states),
        },
      };
    }
    if (key === "battery_soc_entity_id") {
      return {
        entity: {
          include_entities: relevantBatterySocEntityIds(this._hass?.states),
        },
      };
    }
    if (key === "battery_status_entity_id") {
      return {
        entity: {
          include_entities: relevantBatteryStatusEntityIds(
            this._hass?.states,
            [
              ...(this._options.charging_states || []),
              ...(this._options.discharging_states || []),
            ],
          ),
        },
      };
    }
    return ENTITY_SELECTORS[key];
  }

  _collectOptions() {
    return {
      ...this._options,
      loads: this._options.loads.map((load) => ({ ...load })),
    };
  }

  async _save(event) {
    event.preventDefault();
    const result = this._shadow.querySelector("#save_result");
    try {
      const options = this._collectOptions();
      const response = await this._hass.connection.sendMessagePromise({ type: "solar_spender/config/update", options });
      result.className = "small text-success";
      result.textContent = response.reloading
        ? "Saved. Reloading Solar Spender…"
        : "Saved.";
      this._options = options;
      window.setTimeout(() => this._load(), 800);
    } catch (error) {
      result.className = "small text-danger";
      result.textContent = error?.message || "Configuration could not be saved.";
    }
  }

  _addLoad() { const options = this._collectOptions(); options.loads.push({ entity_id: "", hvac_mode: "dry", temperature: null, fan_mode: null, priority: 100, expected_power_w: null, min_on_seconds: 300, min_off_seconds: 900, enabled: true }); this._options = options; this._render(); }
  _removeLoad(index) { const options = this._collectOptions(); options.loads.splice(index, 1); this._options = options; this._render(); }
  _entityField(key, label, help) { return `${this._label(label)}<ha-selector data-key="${key}"></ha-selector>${this._help(help)}`; }
  _numberField(key, label, help, min, max, unit, step = 1) {
    return `${this._label(label)}<ha-selector data-number-key="${key}" data-min="${min}" data-max="${max ?? 1000000}" data-step="${step}" data-unit="${this._escape(unit)}"></ha-selector>${this._help(help)}`;
  }
  _selectField(key, label, help) { return `${this._label(label)}<ha-selector data-select-key="${key}"></ha-selector>${this._help(help)}`; }
  _label(label) { return `<label class="form-label mb-1">${this._escape(label)}</label>`; }
  _help(help) { return `<div class="form-text field-help mt-1">${this._escape(help)}</div>`; }
  _sourceConfiguration() {
    const visibility = sourceConfigurationVisibility(this._options.source_type);
    let fields;
    if (visibility.grid) {
      fields = `
        <div class="col-md-6">${this._entityField("grid_entity_id", "Grid-flow entity", "A measurement power sensor that reports import and export in W or kW.")}</div>
        <div class="col-md-6">${this._selectField("grid_export_positive", "Grid sensor sign", "Tell Solar Spender which sign means export so it can normalize the measurement.")}</div>
        <div class="col-md-4">${this._numberField("export_reserve_w", "Export reserve", "Watts intentionally left available for export.", 0, null, "W")}</div>
        <div class="col-md-4">${this._numberField("entry_threshold_w", "Entry margin", "Spend only when export above the reserve reaches this margin.", 0, null, "W")}</div>
        <div class="col-md-4">${this._numberField("exit_threshold_w", "Exit margin", "Stop spending at this lower margin to prevent oscillation.", 0, null, "W")}</div>`;
    } else {
      const entryLabel = visibility.curtailed
        ? "Minimum solar production"
        : "Entry threshold";
      const entryHelp = visibility.curtailed
        ? "Begin considering a one-AC test only when solar production reaches this level."
        : "Surplus becomes available at or above this value.";
      const exitLabel = visibility.curtailed
        ? "Stop-testing threshold"
        : "Exit threshold";
      const exitHelp = visibility.curtailed
        ? "Stop considering hidden-capacity tests when production falls to this lower level."
        : "Surplus remains latched until it falls to this lower value.";
      fields = `
        <div class="col-md-6">${this._entityField("production_entity_id", "Production power entity", "Current solar production from a measurement power sensor using W or kW.")}</div>
        <div class="col-md-6">${this._entityField("consumption_entity_id", "Consumption power entity", "Whole-home consumption measured at the same electrical boundary as production.")}</div>
        <div class="col-md-6">${this._numberField("entry_threshold_w", entryLabel, entryHelp, 0, null, "W")}</div>
        <div class="col-md-6">${this._numberField("exit_threshold_w", exitLabel, exitHelp, 0, null, "W")}</div>
        ${visibility.curtailed
          ? `<div class="col-12"><div class="alert alert-warning mb-0">Zero-export testing requires <strong>Full battery before zero-export testing</strong>. Solar Spender changes only one AC before checking fresh readings.</div></div>`
          : ""}`;
    }
    return `
      <div class="config-section">
        <h3 class="h6 mb-3 section-heading">Solar source · ${this._escape(
          SELECT_OPTIONS.source_type.find(([value]) => value === this._options.source_type)?.[1] || this._options.source_type,
        )}</h3>
        <div class="alert alert-info">${this._escape(sourceModeDescription(this._options.source_type))}</div>
        <div class="row g-3">${fields}</div>
      </div>`;
  }
  _batteryConfiguration() {
    const policy = this._options.battery_policy;
    const visibility = batteryConfigurationVisibility(
      policy,
      this._options.battery_direction_source,
    );
    let fields = "";
    if (visibility.direction) {
      fields += `<div class="col-md-6">${this._selectField("battery_direction_source", "How to detect charging", "Use a status entity when available, or infer direction from a live battery power sensor.")}</div>`;
    }
    if (visibility.status) {
      fields += `<div class="col-md-6">${this._entityField("battery_status_entity_id", "Battery status entity", "Must report charging, idle, or discharging, or use the battery-charging binary sensor class.")}</div>`;
    }
    if (visibility.power) {
      fields += `
        <div class="col-md-4">${this._entityField("battery_power_entity_id", "Battery power entity", "Live battery charge/discharge power from a measurement sensor using W or kW.")}</div>
        <div class="col-md-4">${this._selectField("battery_power_charging_positive", "Battery power sign", "Select which sensor sign means power is flowing into the battery.")}</div>
        <div class="col-md-4">${this._numberField("battery_power_threshold_w", "Idle threshold", "Power at or below this magnitude is treated as idle, absorbing sensor noise and standby use.", 0, null, "W")}</div>`;
    }
    if (visibility.soc) {
      fields += `
        <div class="col-md-6">${this._entityField("battery_soc_entity_id", "Battery SOC entity", "Battery state of charge from a measurement battery sensor using %.")}</div>
        <div class="col-md-6">${this._numberField("battery_full_threshold", "Full/SOC threshold", "Battery percentage required by the selected policy.", 0, 100, "%")}</div>`;
    }
    return `
      <div class="config-section">
        <h3 class="h6 mb-3 section-heading">Battery condition</h3>
        <div class="row g-3">
          <div class="col-md-6">${this._selectField("battery_policy", "Battery policy", "Choose whether battery state may block new AC activation.")}</div>
          <div class="col-12"><div class="alert alert-info mb-0">${this._escape(batteryPolicyDescription(policy))}</div></div>
          ${fields}
        </div>
      </div>`;
  }
  _climateCapabilities(entityId) {
    const attributes = this._hass?.states?.[entityId]?.attributes || {};
    return {
      hvacModes: (attributes.hvac_modes || []).filter((mode) => mode !== "off"),
      fanModes: attributes.fan_modes || [],
      minTemp: Number.isFinite(Number(attributes.min_temp)) ? Number(attributes.min_temp) : 16,
      maxTemp: Number.isFinite(Number(attributes.max_temp)) ? Number(attributes.max_temp) : 32,
      tempStep: Number.isFinite(Number(attributes.target_temp_step)) ? Number(attributes.target_temp_step) : 0.5,
    };
  }
  _loadRows() {
    if (!this._options.loads.length) return `<div class="alert alert-secondary mb-0">No ACs configured. Add an AC to enable automatic climate control.</div>`;
    return this._options.loads.map((load, index) => {
      const capabilities = this._climateCapabilities(load.entity_id);
      return `<ha-card class="load-card mb-3" data-load-row="${index}"><div class="card-content">
        <div class="d-flex justify-content-between align-items-center mb-3"><div><strong>${this._escape(this._hass?.states?.[load.entity_id]?.attributes?.friendly_name || `AC ${index + 1}`)}</strong><div class="small text-body-secondary">${this._escape(load.entity_id || "Select a climate entity")}</div></div><button class="btn btn-sm btn-outline-danger" type="button" data-remove-load="${index}">Remove</button></div>
        <div class="row g-3">
          <div class="col-md-6">${this._label("Climate entity")}<ha-selector data-load-index="${index}"></ha-selector>${this._help("The air conditioner Solar Spender may start and later release.")}</div>
          <div class="col-md-6">${this._label("Solar Spender control")}<ha-selector data-load-enabled-index="${index}"></ha-selector>${this._help("Disabled ACs stay configured but will not be started. Disabling never turns off an AC.")}</div>
          <div class="col-md-6">${this._loadSelect(index, "hvac_mode", "Desired mode", "Only modes reported by the selected climate entity are offered.")}</div>
          <div class="col-md-6">${this._loadSelect(index, "fan_mode", "Fan mode", "Optional fan setting. Only fan modes reported by the selected climate entity are offered.")}</div>
          <div class="col-md-6">${this._loadNumber(index, "temperature", "Target temperature", `Optional target in the selected AC's ${capabilities.minTemp}–${capabilities.maxTemp} °C range.`, capabilities.minTemp, capabilities.maxTemp, "°C", capabilities.tempStep)}</div>
          <div class="col-md-6">${this._loadNumber(index, "priority", "Priority", "Lower numbers start first and stop last. Equal priorities follow the AC list order.", 0, null, "")}</div>
          <div class="col-md-6">${this._loadNumber(index, "expected_power_w", "Expected draw", "Optional conservative running-power estimate used only to decide whether the AC fits.", 0, null, "W")}</div>
          <div class="col-md-6">${this._loadNumber(index, "min_on_seconds", "Minimum on", "Seconds the AC must stay on before Solar Spender may release it.", 0, null, "seconds")}</div>
          <div class="col-md-6">${this._loadNumber(index, "min_off_seconds", "Minimum off", "Seconds Solar Spender waits before it can start this AC again.", 0, null, "seconds")}</div>
        </div></div></ha-card>`;
    }).join("");
  }
  _loadNumber(index, key, label, help, min, max, unit, step = "any") {
    return `${this._label(label)}<ha-selector data-load-number-index="${index}" data-load-number-key="${key}" data-min="${min}" data-max="${max ?? 1000000}" data-step="${step}" data-unit="${this._escape(unit)}"></ha-selector>${this._help(help)}`;
  }
  _loadSelect(index, key, label, help) {
    return `${this._label(label)}<ha-selector data-load-select-index="${index}" data-load-select-key="${key}"></ha-selector>${this._help(help)}`;
  }
  _card(title, value, detail) { return `<div class="col-12 col-sm-6 col-xl"><ha-card class="status-card"><div class="card-content"><div class="text-body-secondary small">${this._escape(title)}</div><div class="fs-4 fw-semibold">${this._escape(value)}</div><div class="small text-body-secondary text-break">${this._escape(detail)}</div></div></ha-card></div>`; }
  _loads(loads) { return loads.length ? `<ul class="list-group list-group-flush">${loads.map((load) => {
    const ownership = loadOwnershipPresentation(load);
    return `<li class="list-group-item px-0 d-flex justify-content-between gap-3"><span class="text-break"><span class="d-block">${this._escape(load.entity_id)}</span><span class="small text-body-secondary">${this._escape(load.ownership_reason || "Ownership status unavailable")}</span></span><span class="badge align-self-start text-bg-${ownership.style}">${this._escape(ownership.label)}</span></li>`;
  }).join("")}</ul>` : `<p class="text-body-secondary mb-0">No climate loads configured.</p>`; }
  _history(history) { return history.length ? `<ul class="list-group list-group-flush">${history.slice().reverse().map((item) => `<li class="list-group-item px-0 small"><div>${this._escape(item.message)}</div><div class="text-body-secondary">${this._escape(item.at)}</div></li>`).join("")}</ul>` : `<p class="text-body-secondary mb-0">No decisions yet.</p>`; }
  _watts(value) { return typeof value === "number" ? `${Math.round(value)} W` : "—"; }
  _learnedRange(status) {
    const lower = status.learned_range?.supported_at_least_w;
    const upper = status.learned_range?.unsupported_at_or_above_w;
    if (typeof lower === "number" && typeof upper === "number") return `${Math.round(lower)}–<${Math.round(upper)} W`;
    if (typeof upper === "number") return `<${Math.round(upper)} W`;
    if (typeof lower === "number") return `≥${Math.round(lower)} W`;
    return "Learning";
  }
  _surplusDetail(status) {
    if (this._options.source_type === "curtailed_production") {
      return typeof status.opportunity_power_w === "number"
        ? `${Math.round(status.opportunity_power_w)} W solar production; hidden headroom cannot be measured directly.`
        : "Hidden headroom cannot be measured directly.";
    }
    return this._watts(status.headroom_w);
  }
  _escape(value) { return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]); }
}

customElements.define("solar-spender-panel", SolarSpenderPanelHost);
