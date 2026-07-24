import bootstrapCss from "bootstrap/dist/css/bootstrap.min.css";
import Tooltip from "bootstrap/js/dist/tooltip";
import {
  applySelectorValue,
  batteryConfigurationVisibility,
  relevantBatterySocEntityIds,
  relevantBatteryStatusEntityIds,
  relevantPowerEntityIds,
  shouldLoadPanel,
  sourceConfigurationVisibility,
} from "./panel-helpers.js";

const DEFAULT_OPTIONS = {
  enabled: false,
  source_type: "binary",
  binary_entity_id: "",
  grid_entity_id: "",
  grid_export_positive: true,
  production_entity_id: "",
  consumption_entity_id: "",
  entry_threshold_w: 300,
  exit_threshold_w: 100,
  export_reserve_w: 0,
  settling_seconds: 120,
  loads: [],
  battery_policy: "disabled",
  battery_soc_entity_id: "",
  battery_status_entity_id: "",
  battery_full_threshold: 98,
  charging_states: ["charging"],
  discharging_states: ["discharging"],
};

const PANEL_VERSION = "0.1.4";

const SELECT_OPTIONS = {
  enabled: [["true", "Enabled"], ["false", "Disabled"]],
  source_type: [
    ["binary", "Binary headroom"],
    ["grid_flow", "Grid flow / export"],
    ["production_consumption", "Production minus consumption"],
    ["curtailed_production", "Curtailed-production probe"],
  ],
  grid_export_positive: [
    ["true", "Export is positive"],
    ["false", "Import is positive"],
  ],
  battery_policy: [
    ["disabled", "Disabled"],
    ["require_charging", "Require charging"],
    ["charging_or_soc", "Charging or SOC threshold"],
    ["full_idle_for_probe", "Full and idle for probes"],
  ],
};

const ENTITY_SELECTORS = {
  binary_entity_id: {
    entity: {
      filter: [{ domain: ["binary_sensor", "input_boolean"] }],
    },
  },
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
    this._tooltips = [];
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
        .form-control,
        .form-select,
        .input-group-text {
          color: var(--primary-text-color);
          background-color: var(--input-fill-color, var(--secondary-background-color));
          border-color: var(--divider-color);
        }
        .form-control:focus,
        .form-select:focus {
          color: var(--primary-text-color);
          background-color: var(--input-fill-color, var(--secondary-background-color));
          border-color: var(--primary-color);
          box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-color) 30%, transparent);
        }
        .form-control::placeholder {
          color: var(--secondary-text-color);
        }
        .text-body-secondary,
        .form-text {
          color: var(--secondary-text-color) !important;
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
  disconnectedCallback() { this._disposeTooltips(); }

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
    this._disposeTooltips();
    if (this._error) {
      app.innerHTML = `<div class="alert alert-info"><h1 class="h4">Solar Spender</h1><p class="mb-0">${this._escape(this._error)} Add the integration from Settings → Devices & services, then return here.</p></div>`;
      return;
    }
    const status = this._status || {};
    app.innerHTML = `
      <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
        <div><h1 class="h3 mb-1">Solar Spender <span class="badge text-bg-secondary fs-6 align-middle">v${PANEL_VERSION}</span></h1><p class="text-body-secondary mb-0">Use spare solar power for climate loads.</p></div>
        <button class="btn btn-outline-primary" id="refresh" type="button">Refresh</button>
      </div>
      <div class="row g-3 mb-3">
        ${this._card("Controller", status.state || "Not configured", status.reason || "")}
        ${this._card("Surplus", status.surplus_available ? "Available" : "Unavailable", this._watts(status.headroom_w))}
        ${this._card("Battery gate", status.battery_allowed ? "Open" : "Closed", "")}
        ${this._card("Owned ACs", String(status.owned_loads?.length || 0), "Only these can be released automatically")}
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
              <div class="row g-3">
                <div class="col-md-6">${this._numberField("settling_seconds", "Measurement settling", "Seconds to wait after an AC change before making the next decision.", 0, null, "seconds")}</div>
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
    this._fillStandardFields();
    this._activateTooltips();
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
        const options = this._collectOptions();
        options[key] = key === "enabled" || key === "grid_export_positive"
          ? value === "true"
          : value;
        this._options = options;
        if (key === "source_type" || key === "battery_policy") {
          this._render();
        }
      });
    });
    this._shadow.querySelectorAll("ha-selector[data-key]").forEach((selector) => {
      const key = selector.dataset.key;
      selector.hass = this._hass;
      selector.selector = this._entitySelector(key);
      selector.value = this._options[key] || "";
      selector.addEventListener("value-changed", (event) => {
        this._options = applySelectorValue(
          this._options,
          key,
          event.detail?.value,
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
        const options = this._collectOptions();
        options.loads[index].entity_id = event.detail.value || "";
        this._options = options;
        this._render();
      });
    });
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
      key === "consumption_entity_id"
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

  _fillStandardFields() {
    const form = this._shadow.querySelector("#settings");
    ["export_reserve_w", "entry_threshold_w", "exit_threshold_w", "battery_full_threshold", "settling_seconds"].forEach((key) => {
      if (form.elements[key]) form.elements[key].value = String(this._options[key]);
    });
    this._options.loads.forEach((load, index) => {
      ["hvac_mode", "temperature", "fan_mode", "priority", "expected_power_w", "utility", "min_on_seconds", "min_off_seconds"].forEach((key) => { const element = form.elements[`load_${index}_${key}`]; if (element) element.value = load[key] ?? ""; });
    });
  }

  _collectOptions() {
    const form = this._shadow.querySelector("#settings");
    const options = { ...this._options };
    ["export_reserve_w", "entry_threshold_w", "exit_threshold_w", "battery_full_threshold", "settling_seconds"].forEach((key) => {
      if (form.elements[key]) options[key] = Number(form.elements[key].value);
    });
    options.loads = [...this._shadow.querySelectorAll("[data-load-row]")].map((row) => {
      const index = Number(row.dataset.loadRow);
      const value = (key) => form.elements[`load_${index}_${key}`].value;
      const number = (key, fallback) => value(key) === "" ? fallback : Number(value(key));
      return { entity_id: row.querySelector("ha-selector").value || "", hvac_mode: value("hvac_mode") || null, temperature: number("temperature", null), fan_mode: value("fan_mode") || null, priority: number("priority", 100), expected_power_w: number("expected_power_w", null), utility: number("utility", 1), min_on_seconds: number("min_on_seconds", 900), min_off_seconds: number("min_off_seconds", 900), enabled: true };
    });
    return options;
  }

  async _save(event) {
    event.preventDefault();
    const result = this._shadow.querySelector("#save_result");
    try {
      const options = this._collectOptions();
      await this._hass.connection.sendMessagePromise({ type: "solar_spender/config/update", options });
      result.className = "small text-success";
      result.textContent = "Saved. Reloading Solar Spender…";
      this._options = options;
      window.setTimeout(() => this._load(), 800);
    } catch (error) {
      result.className = "small text-danger";
      result.textContent = error?.message || "Configuration could not be saved.";
    }
  }

  _addLoad() { const options = this._collectOptions(); options.loads.push({ entity_id: "", hvac_mode: "dry", temperature: null, fan_mode: null, priority: 100, expected_power_w: null, utility: 1, min_on_seconds: 900, min_off_seconds: 900, enabled: true }); this._options = options; this._render(); }
  _removeLoad(index) { const options = this._collectOptions(); options.loads.splice(index, 1); this._options = options; this._render(); }
  _entityField(key, label, help) { return `${this._label(label, help)}<ha-selector data-key="${key}"></ha-selector>`; }
  _numberField(key, label, help, min, max, unit) { return `${this._label(label, help)}<div class="input-group"><input class="form-control" type="number" id="${key}" name="${key}" min="${min}" ${max === null ? "" : `max="${max}"`}><span class="input-group-text">${unit}</span></div>`; }
  _selectField(key, label, help) { return `${this._label(label, help)}<ha-selector data-select-key="${key}"></ha-selector>`; }
  _label(label, help) { const id = `tip_${label.replaceAll(/[^a-z0-9]/gi, "_")}`; return `<div class="d-flex align-items-center gap-1 mb-1"><label class="form-label mb-0">${this._escape(label)}</label><button class="btn btn-sm btn-link p-0 text-decoration-none" type="button" aria-label="Help: ${this._escape(label)}" data-bs-toggle="tooltip" data-bs-title="${this._escape(help)}">?</button></div>`; }
  _sourceConfiguration() {
    const visibility = sourceConfigurationVisibility(this._options.source_type);
    let fields;
    if (visibility.binary) {
      fields = `
        <div class="col-12">${this._entityField("binary_entity_id", "Headroom entity", "On means spare solar is available. The upstream entity is responsible for deciding when headroom exists.")}</div>`;
    } else if (visibility.grid) {
      fields = `
        <div class="col-md-6">${this._entityField("grid_entity_id", "Grid-flow entity", "A measurement power sensor that reports import and export in W or kW.")}</div>
        <div class="col-md-6">${this._selectField("grid_export_positive", "Grid sensor sign", "Tell Solar Spender which sign means export so it can normalize the measurement.")}</div>
        <div class="col-md-4">${this._numberField("export_reserve_w", "Export reserve", "Watts intentionally left available for export.", 0, null, "W")}</div>
        <div class="col-md-4">${this._numberField("entry_threshold_w", "Entry margin", "Spend only when export above the reserve reaches this margin.", 0, null, "W")}</div>
        <div class="col-md-4">${this._numberField("exit_threshold_w", "Exit margin", "Stop spending at this lower margin to prevent oscillation.", 0, null, "W")}</div>`;
    } else {
      fields = `
        <div class="col-md-6">${this._entityField("production_entity_id", "Production power entity", "Current solar production from a measurement power sensor using W or kW.")}</div>
        <div class="col-md-6">${this._entityField("consumption_entity_id", "Consumption power entity", "Whole-home consumption measured at the same electrical boundary as production.")}</div>
        <div class="col-md-6">${this._numberField("entry_threshold_w", "Entry threshold", "Surplus becomes available at or above this value.", 0, null, "W")}</div>
        <div class="col-md-6">${this._numberField("exit_threshold_w", "Exit threshold", "Surplus remains latched until it falls to this lower value.", 0, null, "W")}</div>
        ${visibility.curtailed
          ? `<div class="col-12"><div class="alert alert-warning mb-0">Curtailed probing also requires the battery policy <strong>Full and idle for probes</strong>.</div></div>`
          : ""}`;
    }
    return `
      <div class="config-section">
        <h3 class="h6 mb-3 section-heading">Solar source · ${this._escape(
          SELECT_OPTIONS.source_type.find(([value]) => value === this._options.source_type)?.[1] || this._options.source_type,
        )}</h3>
        <div class="row g-3">${fields}</div>
      </div>`;
  }
  _batteryConfiguration() {
    const policy = this._options.battery_policy;
    const visibility = batteryConfigurationVisibility(policy);
    let fields = "";
    if (visibility.status && !visibility.soc) {
      fields = `
        <div class="col-md-6">${this._entityField("battery_status_entity_id", "Battery status entity", "Must report a configured charging state or use the battery-charging binary sensor class.")}</div>`;
    } else if (visibility.soc) {
      fields = `
        <div class="col-md-4">${this._entityField("battery_soc_entity_id", "Battery SOC entity", "Battery state of charge from a measurement battery sensor using %.")}</div>
        <div class="col-md-4">${this._entityField("battery_status_entity_id", "Battery status entity", "Reports charging, idle, or discharging.")}</div>
        <div class="col-md-4">${this._numberField("battery_full_threshold", "Full/SOC threshold", "Battery percentage required by the selected policy.", 0, 100, "%")}</div>`;
    }
    return `
      <div class="config-section">
        <h3 class="h6 mb-3 section-heading">Battery gate</h3>
        <div class="row g-3">
          <div class="col-md-6">${this._selectField("battery_policy", "Battery policy", "Choose whether battery state may block new AC activation.")}</div>
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
      const modes = [["", "Keep current"], ...capabilities.hvacModes.map((mode) => [mode, mode])];
      const fanModes = [["", "Keep current"], ...capabilities.fanModes.map((mode) => [mode, mode])];
      return `<ha-card class="load-card mb-3" data-load-row="${index}"><div class="card-content">
        <div class="d-flex justify-content-between align-items-center mb-3"><div><strong>${this._escape(this._hass?.states?.[load.entity_id]?.attributes?.friendly_name || `AC ${index + 1}`)}</strong><div class="small text-body-secondary">${this._escape(load.entity_id || "Select a climate entity")}</div></div><button class="btn btn-sm btn-outline-danger" type="button" data-remove-load="${index}">Remove</button></div>
        <div class="row g-3">
          <div class="col-md-6">${this._label("Climate entity", "The air conditioner Solar Spender may start and later release.")}<ha-selector data-load-index="${index}"></ha-selector></div>
          <div class="col-md-6">${this._loadSelect(index, "hvac_mode", "Desired mode", "Only modes reported by the selected climate entity are offered.", modes)}</div>
          <div class="col-md-6">${this._loadSelect(index, "fan_mode", "Fan mode", "Optional fan setting. Only fan modes reported by the selected climate entity are offered.", fanModes)}</div>
          <div class="col-md-6">${this._loadNumber(index, "temperature", "Target temperature", `Optional target in the selected AC's ${capabilities.minTemp}–${capabilities.maxTemp} °C range.`, capabilities.minTemp, capabilities.maxTemp, "°C", capabilities.tempStep)}</div>
          <div class="col-md-4">${this._loadNumber(index, "priority", "Priority", "Lower numbers are preferred before higher numbers.", 0, null, "")}</div>
          <div class="col-md-4">${this._loadNumber(index, "utility", "Utility", "Higher utility wins before priority when selecting ACs.", 0, null, "")}</div>
          <div class="col-md-4">${this._loadNumber(index, "expected_power_w", "Expected draw", "Optional conservative running-power estimate for budget-aware selection.", 0, null, "W")}</div>
          <div class="col-md-6">${this._loadNumber(index, "min_on_seconds", "Minimum on", "Seconds the AC must stay on before Solar Spender may release it.", 0, null, "seconds")}</div>
          <div class="col-md-6">${this._loadNumber(index, "min_off_seconds", "Minimum off", "Seconds Solar Spender waits before it can start this AC again.", 0, null, "seconds")}</div>
        </div></div></ha-card>`;
    }).join("");
  }
  _loadNumber(index, key, label, help, min, max, unit, step = "any") { return `${this._label(label, help)}<div class="input-group"><input class="form-control" type="number" name="load_${index}_${key}" min="${min}" step="${step}" ${max === null ? "" : `max="${max}"`}><span class="input-group-text">${unit}</span></div>`; }
  _loadSelect(index, key, label, help, options) { return `${this._label(label, help)}<select class="form-select" name="load_${index}_${key}">${options.map(([value, text]) => `<option value="${value}">${text}</option>`).join("")}</select>`; }
  _activateTooltips() { this._tooltips = [...this._shadow.querySelectorAll('[data-bs-toggle="tooltip"]')].map((element) => new Tooltip(element)); }
  _disposeTooltips() { this._tooltips.forEach((tooltip) => tooltip.dispose()); this._tooltips = []; }
  _card(title, value, detail) { return `<div class="col-12 col-sm-6 col-xl-3"><ha-card class="status-card"><div class="card-content"><div class="text-body-secondary small">${this._escape(title)}</div><div class="fs-4 fw-semibold">${this._escape(value)}</div><div class="small text-body-secondary">${this._escape(detail)}</div></div></ha-card></div>`; }
  _loads(loads) { return loads.length ? `<ul class="list-group list-group-flush">${loads.map((load) => `<li class="list-group-item px-0 d-flex justify-content-between"><span>${this._escape(load.entity_id)}</span><span class="badge text-bg-${load.owned ? "success" : "secondary"}">${load.owned ? "Owned" : this._escape(load.state || "unknown")}</span></li>`).join("")}</ul>` : `<p class="text-body-secondary mb-0">No climate loads configured.</p>`; }
  _history(history) { return history.length ? `<ul class="list-group list-group-flush">${history.slice().reverse().map((item) => `<li class="list-group-item px-0 small"><div>${this._escape(item.message)}</div><div class="text-body-secondary">${this._escape(item.at)}</div></li>`).join("")}</ul>` : `<p class="text-body-secondary mb-0">No decisions yet.</p>`; }
  _watts(value) { return typeof value === "number" ? `${Math.round(value)} W` : "—"; }
  _escape(value) { return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]); }
}

customElements.define("solar-spender-panel", SolarSpenderPanelHost);
