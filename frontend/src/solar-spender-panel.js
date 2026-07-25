import bootstrapCss from "bootstrap/dist/css/bootstrap.min.css";
import {
  applySelectorValue,
  batteryConfigurationVisibility,
  batteryPolicyDescription,
  constrainNumberValue,
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
  minimum_production_w: 300,
  export_reserve_w: 0,
  settling_seconds: 300,
  feedback_sample_count: 3,
  feedback_timeout_minutes: 15,
  input_max_age_minutes: 15,
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

const PANEL_VERSION = "0.8.0";
const KEEP_CURRENT = "__keep_current__";

const SELECT_OPTIONS = {
  enabled: [["true", "On"], ["false", "Off"]],
  source_type: [
    ["grid_flow", "Grid meter"],
    ["production_consumption", "Solar minus home use"],
    ["curtailed_production", "Zero-export system"],
  ],
  grid_export_positive: [
    ["true", "Export is positive"],
    ["false", "Import is positive"],
  ],
  battery_policy: [
    ["disabled", "Ignore battery"],
    ["require_charging", "Battery must be charging"],
    ["charging_or_soc", "Charging or above set level"],
    ["full_idle_for_probe", "Full and idle"],
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
    this._narrow = false;
    this._shellActionsWired = false;
    this._shadow = this.attachShadow({ mode: "open" });
    this._shadow.innerHTML = `
      <style>
        ${bootstrapCss}
        :host {
          --panel-space: 16px;
          color: var(--primary-text-color);
          background: var(--primary-background-color);
          display: block;
          min-height: 100%;
        }
        .top-bar {
          position: sticky;
          z-index: 10;
          top: 0;
          min-height: calc(64px + env(safe-area-inset-top, 0px));
          padding: env(safe-area-inset-top, 0px) var(--panel-space) 0;
          color: var(--app-header-text-color, var(--primary-text-color));
          background: var(--app-header-background-color, var(--primary-background-color));
          border-bottom: 1px solid var(--divider-color);
        }
        .top-bar-content {
          display: flex;
          align-items: center;
          min-height: 64px;
          max-width: 1680px;
          margin: 0 auto;
          gap: 8px;
        }
        .top-bar-title {
          overflow: hidden;
          font-size: 20px;
          font-weight: 500;
          line-height: 1.2;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        #app {
          max-width: 1680px;
          margin: 0 auto;
          padding: var(--panel-space);
          padding-bottom: calc(var(--panel-space) + env(safe-area-inset-bottom, 0px));
          color: var(--primary-text-color);
        }
        [hidden] {
          display: none !important;
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
        .page-heading {
          min-width: 0;
        }
        .page-actions {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          justify-content: flex-end;
          gap: 8px;
        }
        .pause-actions {
          display: flex;
          gap: 4px;
        }
        ha-button,
        ha-icon-button {
          --mdc-icon-size: 20px;
        }
        ha-button {
          min-height: 44px;
        }
        ha-button ha-icon {
          margin-inline-end: 7px;
        }
        ha-icon-button {
          display: inline-flex;
          min-width: 44px;
          min-height: 44px;
          align-items: center;
          justify-content: center;
        }
        .section-title,
        .status-title {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .section-title ha-icon {
          flex: 0 0 auto;
          color: var(--primary-color);
        }
        .status-title ha-icon {
          width: 18px;
          height: 18px;
          color: var(--secondary-text-color);
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
        .action-row {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .load-card:last-child {
          margin-bottom: 0 !important;
        }
        @media (max-width: 767.98px) {
          :host {
            --panel-space: 12px;
          }
          .top-bar {
            padding-inline: 4px 12px;
          }
          .page-header {
            align-items: flex-start !important;
          }
          .page-heading,
          .page-actions {
            width: 100%;
          }
          .page-actions {
            justify-content: stretch;
          }
          .pause-actions {
            display: grid;
            flex: 1 1 100%;
            grid-template-columns: repeat(3, minmax(0, 1fr));
          }
          .pause-actions ha-button,
          .page-actions > ha-button {
            width: 100%;
          }
          .page-actions > ha-button {
            flex: 1 1 calc(50% - 4px);
          }
          .card-content,
          .config-section {
            padding: 16px;
          }
          .field-help {
            min-height: 0;
          }
          .action-row {
            align-items: stretch;
            flex-direction: column;
          }
          .action-row ha-button {
            width: 100%;
          }
        }
        @media (max-width: 575.98px) {
          .status-card .card-content {
            min-height: 0;
          }
          .status-card .fs-4 {
            font-size: 1.25rem !important;
          }
          .load-card-header {
            align-items: flex-start !important;
            gap: 12px;
          }
        }
        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after {
            scroll-behavior: auto !important;
            transition: none !important;
          }
        }
      </style>
      <header class="top-bar">
        <div class="top-bar-content">
          <ha-icon-button id="menu" aria-label="Open Home Assistant menu" title="Open Home Assistant menu">
            <ha-icon icon="mdi:menu"></ha-icon>
          </ha-icon-button>
          <ha-icon icon="mdi:solar-power" aria-hidden="true"></ha-icon>
          <div class="top-bar-title">Solar Spender</div>
        </div>
      </header>
      <main id="app"></main>`;
  }

  set hass(value) {
    this._hass = value;
    this._syncSelectorHass();
    if (shouldLoadPanel(this._loaded, this._loading)) {
      this._load();
    }
  }

  get hass() { return this._hass; }
  set narrow(value) {
    this._narrow = Boolean(value);
    this._syncNarrow();
  }

  get narrow() { return this._narrow; }

  connectedCallback() {
    this._wireShellActions();
    this._syncNarrow();
    this._render();
  }

  _wireShellActions() {
    if (this._shellActionsWired) return;
    this._shadow.querySelector("#menu").addEventListener("click", () => {
      this.dispatchEvent(new CustomEvent("hass-toggle-menu", {
        bubbles: true,
        composed: true,
      }));
    });
    this._shellActionsWired = true;
  }

  _syncNarrow() {
    const menu = this._shadow?.querySelector("#menu");
    if (menu) menu.hidden = !this._narrow;
  }

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
      this._error = error?.message || "Solar Spender is not set up.";
    } finally {
      this._loading = false;
    }
    this._render();
  }

  _render() {
    const app = this._shadow?.querySelector("#app");
    if (!app) return;
    if (this._error) {
      app.innerHTML = `<div class="alert alert-info"><h1 class="h4">Solar Spender</h1><p class="mb-0">${this._escape(this._error)} Add it in Settings → Devices & services, then come back here.</p></div>`;
      return;
    }
    const status = this._status || {};
    const cards = statusPresentations(status, this._options);
    app.innerHTML = `
      <div class="page-header d-flex flex-wrap align-items-center justify-content-between gap-3 mb-3">
        <div class="page-heading"><h1 class="h3 mb-1">Solar Spender <span class="badge text-bg-secondary fs-6 align-middle">v${PANEL_VERSION}</span></h1><p class="text-body-secondary mb-0">Run your ACs on spare solar.</p></div>
        <div class="page-actions">
          <div class="pause-actions" role="group" aria-label="Temporarily pause Solar Spender">
            <ha-button data-pause-minutes="5" ${status.enabled ? "" : "disabled"}><ha-icon icon="mdi:pause"></ha-icon>Pause 5 min</ha-button>
            <ha-button data-pause-minutes="15" ${status.enabled ? "" : "disabled"}><ha-icon icon="mdi:pause"></ha-icon>15 min</ha-button>
            <ha-button data-pause-minutes="30" ${status.enabled ? "" : "disabled"}><ha-icon icon="mdi:pause"></ha-icon>30 min</ha-button>
          </div>
          <ha-button id="resume" ${status.paused ? "" : "disabled"}><ha-icon icon="mdi:play"></ha-icon>Resume now</ha-button>
          <ha-button id="refresh"><ha-icon icon="mdi:refresh"></ha-icon>Refresh</ha-button>
        </div>
      </div>
      <div class="small mb-3 ${status.paused ? "text-warning" : "text-body-secondary"}" id="pause_result" role="status">${this._escape(this._pauseSummary(status))}</div>
      <div class="row g-3 mb-3">
        ${this._card("Solar Spender", cards.controller.value, cards.controller.detail, "mdi:solar-power")}
        ${this._card("Spare solar", cards.surplus.value, cards.surplus.detail ?? this._surplusDetail(status), "mdi:weather-sunny")}
        ${this._card("Battery check", cards.battery.value, cards.battery.detail, "mdi:battery-medium")}
        ${this._card("AC check", cards.feedback.value, cards.feedback.detail, "mdi:air-conditioner")}
        ${this._card("Solar limit", this._learnedRange(status), "Rough limit for the current period of spare solar.", "mdi:gauge")}
        ${this._card("ACs", `${status.owned_loads?.length || 0} owned`, status.discarded_lease_count ? `${status.discarded_lease_count} saved AC state(s) could not be trusted. Those ACs were left alone.` : status.restored_lease_count ? `${status.restored_lease_count} owned AC(s) restored after restart.` : "Solar Spender turns off only ACs it turned on.", "mdi:home-lightning-bolt")}
      </div>
      <div class="row g-3">
        <section class="col-12 col-xxl-8"><ha-card><div class="card-content">
          ${this._sectionTitle("Settings", "mdi:cog", "h2", "h5 mb-3")}
          <form id="settings" class="row g-3" novalidate>
            <div class="col-md-6">${this._selectField("enabled", "Solar Spender", "When off, Solar Spender will not change any AC.")}</div>
            <div class="col-md-6">${this._selectField("source_type", "How to find spare solar", "Choose the sensors that match your solar system.")}</div>
            <div class="col-12">${this._sourceConfiguration()}</div>
            <div class="col-12">${this._batteryConfiguration()}</div>
            <div class="col-12 config-section">
              <h3 class="h6 mb-3 section-heading">Check timing</h3>
              <div class="row g-3">
                <div class="col-md-6">${this._numberField("settling_seconds", "Wait before first check", "Wait this long after changing an AC.", 0, null, "seconds")}</div>
                <div class="col-md-6">${this._numberField("feedback_sample_count", "Number of checks", "How many distinct complete sensor reports to use. More than half must pass.", 1, 9, "checks", 2)}</div>
                <div class="col-md-6">${this._numberField("feedback_timeout_minutes", "Check timeout", "Fail closed if enough fresh sensor reports do not arrive within this time.", 1, 1440, "minutes")}</div>
                <div class="col-md-6">${this._numberField("input_max_age_minutes", "Maximum input age", "Treat older solar and configured battery readings as unavailable.", 1, 1440, "minutes")}</div>
                <div class="col-md-6">${this._numberField("next_load_delay_minutes", "Wait before next AC", "After a pass, wait this long before trying another AC.", 0, 60, "minutes")}</div>
                <div class="col-12"><div class="alert alert-secondary mb-0">After the first-check wait, each new complete sensor report can count. By default, 3 checks must finish within 15 minutes. Sensor readings older than 15 minutes are unavailable.</div></div>
              </div>
            </div>
            <div class="col-12"><div class="d-flex justify-content-between align-items-center gap-2">${this._sectionTitle("ACs", "mdi:air-conditioner", "h3", "h6 mb-0")}<ha-button id="add_load"><ha-icon icon="mdi:plus"></ha-icon>Add AC</ha-button></div><p class="form-text mb-0">Solar Spender turns off only ACs it turned on.</p></div>
            <div class="col-12" id="load_rows">${this._loadRows()}</div>
            <div class="col-12 action-row"><ha-button id="save"><ha-icon icon="mdi:content-save"></ha-icon>Save settings</ha-button><span id="save_result" class="small" role="status"></span></div>
          </form>
        </div></ha-card></section>
        <section class="col-12 col-xxl-4">
          <div class="d-grid gap-3">
            <ha-card><div class="card-content">${this._sectionTitle("ACs now", "mdi:air-conditioner", "h2", "h5 mb-3")}${this._loads(status.loads || [])}</div></ha-card>
            <ha-card><div class="card-content">${this._sectionTitle("Recent activity", "mdi:history", "h2", "h5 mb-3")}${this._history(status.history || [])}</div></ha-card>
          </div>
        </section>
      </div>`;
    this._hydrateHaSelectors();
    app.querySelector("#refresh").addEventListener("click", () => this._load());
    app.querySelector("#resume").addEventListener("click", () => this._setPause(0));
    app.querySelectorAll("[data-pause-minutes]").forEach((button) => {
      button.addEventListener("click", () => this._setPause(Number(button.dataset.pauseMinutes)));
    });
    app.querySelector("#settings").addEventListener("submit", (event) => this._save(event));
    app.querySelector("#save").addEventListener("click", (event) => this._save(event));
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
        const previousSource = this._options.source_type;
        const options = this._collectOptions();
        options[key] = key === "enabled"
          || key === "grid_export_positive"
          || key === "battery_power_charging_positive"
          ? value === "true"
          : value;
        if (key === "source_type" && value === "curtailed_production") {
          options.entry_threshold_w = Math.min(
            Number(options.entry_threshold_w),
            Number(options.exit_threshold_w),
          );
          options.exit_threshold_w = Math.max(
            Number(this._options.entry_threshold_w),
            Number(this._options.exit_threshold_w),
          );
          if (options.entry_threshold_w === options.exit_threshold_w) {
            options.exit_threshold_w += 200;
          }
        } else if (
          key === "source_type"
          && previousSource === "curtailed_production"
        ) {
          const lower = Math.min(
            Number(options.entry_threshold_w),
            Number(options.exit_threshold_w),
          );
          const higher = Math.max(
            Number(options.entry_threshold_w),
            Number(options.exit_threshold_w),
          );
          options.entry_threshold_w = higher;
          options.exit_threshold_w = lower;
        }
        this._options = options;
        if (
          key === "source_type"
          || key === "battery_policy"
          || key === "battery_direction_source"
          || key === "battery_power_charging_positive"
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
        const constrained = constrainNumberValue(
          value,
          Number(selector.dataset.min),
          Number(selector.dataset.max),
          this._options[key],
        );
        reflectSelectorValue(selector, constrained);
        this._options = {
          ...this._options,
          [key]: constrained,
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
    this._shadow.querySelectorAll("ha-selector[data-load-power-index]").forEach((selector) => {
      const index = Number(selector.dataset.loadPowerIndex);
      const load = this._options.loads[index] || {};
      selector.hass = this._hass;
      selector.selector = this._entitySelector("load_power_entity_id");
      selector.value = load.power_entity_id || "";
      selector.addEventListener("value-changed", (event) => {
        const value = event.detail?.value || "";
        reflectSelectorValue(selector, value);
        this._updateLoadOption(index, "power_entity_id", value);
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
          : constrainNumberValue(
            rawValue,
            Number(selector.dataset.min),
            Number(selector.dataset.max),
            load[key] ?? null,
          );
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
      key === "battery_power_entity_id" ||
      key === "load_power_entity_id"
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
    event?.preventDefault();
    const result = this._shadow.querySelector("#save_result");
    try {
      const options = this._collectOptions();
      const response = await this._hass.connection.sendMessagePromise({ type: "solar_spender/config/update", options });
      result.className = "small text-success";
      result.textContent = response.reloading
        ? "Saved. Reloading…"
        : "Saved.";
      this._options = options;
      window.setTimeout(() => this._load(), 800);
    } catch (error) {
      result.className = "small text-danger";
      result.textContent = error?.message || "Could not save.";
    }
  }

  async _setPause(minutes) {
    const result = this._shadow.querySelector("#pause_result");
    try {
      result.className = "small mb-3 text-body-secondary";
      result.textContent = minutes
        ? `Pausing for ${minutes} minutes…`
        : "Resuming…";
      this._status = await this._hass.connection.sendMessagePromise({
        type: "solar_spender/control/set_pause",
        minutes,
      });
      this._render();
    } catch (error) {
      result.className = "small mb-3 text-danger";
      result.textContent = error?.message || "Could not change the pause.";
    }
  }

  _pauseSummary(status) {
    if (!status.enabled) {
      return "Turn on Solar Spender to use pause.";
    }
    if (!status.paused) {
      return "Pause keeps every AC as it is.";
    }
    const minutes = Math.max(
      1,
      Math.ceil(Number(status.pause_remaining_seconds || 0) / 60),
    );
    return `Paused for about ${minutes} more minute${minutes === 1 ? "" : "s"}. ACs stay as they are.`;
  }

  _addLoad() { const options = this._collectOptions(); options.loads.push({ entity_id: "", hvac_mode: "dry", temperature: null, fan_mode: null, priority: 100, expected_power_w: null, power_entity_id: "", min_on_seconds: 300, min_off_seconds: 900, enabled: true }); this._options = options; this._render(); }
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
        <div class="col-md-6">${this._entityField("grid_entity_id", "Grid power sensor", "Sensor that shows grid import and export in W or kW.")}</div>
        <div class="col-md-6">${this._selectField("grid_export_positive", "Grid sensor direction", "Choose which sign means export.")}</div>
        <div class="col-md-4">${this._numberField("export_reserve_w", "Keep exporting", "Solar Spender leaves this many watts for export.", 0, null, "W")}</div>
        <div class="col-md-4">${this._numberField("entry_threshold_w", "Start above", "Extra export needed before starting an AC.", 0, null, "W")}</div>
        <div class="col-md-4">${this._numberField("exit_threshold_w", "Stop below", "Turn off an owned AC when extra export falls this low.", 0, null, "W")}</div>`;
    } else {
      const entryLabel = visibility.curtailed
        ? "Start test below"
        : "Start above";
      const entryHelp = visibility.curtailed
        ? "Test one AC when home use minus solar is this value or less."
        : "Spare solar must reach this value before starting an AC.";
      const exitLabel = visibility.curtailed
        ? "Stop test above"
        : "Stop below";
      const exitHelp = visibility.curtailed
        ? "Stop testing when home use minus solar reaches this larger value."
        : "Turn off an owned AC when spare solar falls this low.";
      fields = `
        <div class="col-md-6">${this._entityField("production_entity_id", "Solar power sensor", "Current solar production in W or kW.")}</div>
        <div class="col-md-6">${this._entityField("consumption_entity_id", "Home power sensor", "Current whole-home use in W or kW.")}</div>
        ${visibility.curtailed
          ? `<div class="col-md-4">${this._numberField("minimum_production_w", "Minimum solar power", "Do not test an AC below this solar power.", 0, null, "W")}</div>`
          : ""}
        <div class="${visibility.curtailed ? "col-md-4" : "col-md-6"}">${this._numberField("entry_threshold_w", entryLabel, entryHelp, 0, null, "W")}</div>
        <div class="${visibility.curtailed ? "col-md-4" : "col-md-6"}">${this._numberField("exit_threshold_w", exitLabel, exitHelp, 0, null, "W")}</div>
        ${visibility.curtailed
          ? `<div class="col-12"><div class="alert alert-secondary mb-0"><strong>Gap = home use − solar.</strong> Test at ${this._escape(this._options.entry_threshold_w)} W or less. Stop at ${this._escape(this._options.exit_threshold_w)} W or more.</div></div><div class="col-12"><div class="alert alert-warning mb-0">A small gap does not prove spare solar exists. The battery must be full and idle. Solar Spender tests only one AC at a time.</div></div>`
          : `<div class="col-12"><div class="alert alert-secondary mb-0"><strong>Spare solar = solar − home use.</strong> Start at the higher value. Stop at the lower value.</div></div>`}`;
    }
    return `
      <div class="config-section">
        <h3 class="h6 mb-3 section-heading">Solar sensors · ${this._escape(
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
      fields += `<div class="col-md-6">${this._selectField("battery_direction_source", "How to read the battery", "Use a status entity or a battery power sensor.")}</div>`;
    }
    if (visibility.status) {
      fields += `<div class="col-md-6">${this._entityField("battery_status_entity_id", "Battery status entity", "Must show charging, idle, or discharging.")}</div>`;
    }
    if (visibility.power) {
      const negativeCharging = this._options.battery_power_charging_positive === false;
      const chargingExample = negativeCharging ? "−200 W" : "+200 W";
      const dischargingExample = negativeCharging ? "+200 W" : "−200 W";
      fields += `
        <div class="col-md-4">${this._entityField("battery_power_entity_id", "Battery power sensor", "Current battery power in W or kW.")}</div>
        <div class="col-md-4">${this._selectField("battery_power_charging_positive", "Charging direction", "Choose which sign means charging.")}</div>
        <div class="col-md-4">${this._numberField("battery_power_threshold_w", "Idle range", "Power from minus this value to plus this value counts as idle.", 0, null, "W")}</div>
        <div class="col-12"><div class="alert alert-secondary mb-0">With a 100 W idle range, −50 W and +50 W are idle. ${chargingExample} is charging. ${dischargingExample} is discharging.</div></div>`;
    }
    if (visibility.soc) {
      fields += `
        <div class="col-md-6">${this._entityField("battery_soc_entity_id", "Battery level sensor", "Battery level in percent.")}</div>
        <div class="col-md-6">${this._numberField("battery_full_threshold", "Required battery level", "Battery must reach this percentage.", 0, 100, "%")}</div>`;
    }
    return `
      <div class="config-section">
        <h3 class="h6 mb-3 section-heading">Battery</h3>
        <div class="row g-3">
          <div class="col-md-6">${this._selectField("battery_policy", "Battery rule", "Choose when the battery allows a new AC.")}</div>
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
    if (!this._options.loads.length) return `<div class="alert alert-secondary mb-0">No ACs added.</div>`;
    return this._options.loads.map((load, index) => {
      const capabilities = this._climateCapabilities(load.entity_id);
      return `<ha-card class="load-card mb-3" data-load-row="${index}"><div class="card-content">
        <div class="load-card-header d-flex justify-content-between align-items-center mb-3"><div class="text-break"><strong>${this._escape(this._hass?.states?.[load.entity_id]?.attributes?.friendly_name || `AC ${index + 1}`)}</strong><div class="small text-body-secondary">${this._escape(load.entity_id || "Choose an AC")}</div></div><ha-button data-remove-load="${index}"><ha-icon icon="mdi:delete-outline"></ha-icon>Remove</ha-button></div>
        <div class="row g-3">
          <div class="col-md-6">${this._label("AC entity")}<ha-selector data-load-index="${index}"></ha-selector>${this._help("The AC Solar Spender may control.")}</div>
          <div class="col-md-6">${this._label("Use this AC")}<ha-selector data-load-enabled-index="${index}"></ha-selector>${this._help("Turning this off does not turn off the AC.")}</div>
          <div class="col-md-6">${this._loadSelect(index, "hvac_mode", "Mode", "Mode to use when starting this AC.")}</div>
          <div class="col-md-6">${this._loadSelect(index, "fan_mode", "Fan", "Fan setting to use. Leave unchanged if you do not care.")}</div>
          <div class="col-md-6">${this._loadNumber(index, "temperature", "Temperature", `Choose ${capabilities.minTemp}–${capabilities.maxTemp} °C, or leave it unchanged.`, capabilities.minTemp, capabilities.maxTemp, "°C", capabilities.tempStep)}</div>
          <div class="col-md-6">${this._loadNumber(index, "priority", "Start order", "Lower numbers start first.", 0, null, "")}</div>
          <div class="col-md-6">${this._loadNumber(index, "expected_power_w", "Usual power", "Estimated power used by this AC. Optional.", 1, null, "W")}</div>
          <div class="col-md-6">${this._label("AC power sensor")}<ha-selector data-load-power-index="${index}"></ha-selector>${this._help("Current power used by this AC in W or kW. Optional.")}</div>
          <div class="col-md-6">${this._loadNumber(index, "min_on_seconds", "Keep on for", "Shortest time Solar Spender may leave the AC on.", 0, null, "seconds")}</div>
          <div class="col-md-6">${this._loadNumber(index, "min_off_seconds", "Keep off for", "Shortest wait before Solar Spender may start the AC again.", 0, null, "seconds")}</div>
        </div></div></ha-card>`;
    }).join("");
  }
  _loadNumber(index, key, label, help, min, max, unit, step = "any") {
    return `${this._label(label)}<ha-selector data-load-number-index="${index}" data-load-number-key="${key}" data-min="${min}" data-max="${max ?? 1000000}" data-step="${step}" data-unit="${this._escape(unit)}"></ha-selector>${this._help(help)}`;
  }
  _loadSelect(index, key, label, help) {
    return `${this._label(label)}<ha-selector data-load-select-index="${index}" data-load-select-key="${key}"></ha-selector>${this._help(help)}`;
  }
  _sectionTitle(title, icon, level = "h2", className = "h5 mb-3") {
    return `<${level} class="${className} section-heading section-title"><ha-icon icon="${icon}" aria-hidden="true"></ha-icon><span>${this._escape(title)}</span></${level}>`;
  }
  _card(title, value, detail, icon) { return `<div class="col-12 col-sm-6 col-xl"><ha-card class="status-card"><div class="card-content"><div class="status-title text-body-secondary small"><ha-icon icon="${icon}" aria-hidden="true"></ha-icon><span>${this._escape(title)}</span></div><div class="fs-4 fw-semibold mt-1">${this._escape(value)}</div><div class="small text-body-secondary text-break">${this._escape(detail)}</div></div></ha-card></div>`; }
  _loads(loads) { return loads.length ? `<ul class="list-group list-group-flush">${loads.map((load) => {
    const ownership = loadOwnershipPresentation(load);
    const draw = typeof load.current_power_w === "number"
      ? ` · about ${Math.round(load.current_power_w)} W`
      : "";
    return `<li class="list-group-item px-0 d-flex justify-content-between gap-3"><span class="text-break"><span class="d-block">${this._escape(load.entity_id)}</span><span class="small text-body-secondary">${this._escape((load.ownership_reason || "Status unavailable") + draw)}</span></span><span class="badge align-self-start text-bg-${ownership.style}">${this._escape(ownership.label)}</span></li>`;
  }).join("")}</ul>` : `<p class="text-body-secondary mb-0">No ACs added.</p>`; }
  _history(history) { return history.length ? `<ul class="list-group list-group-flush">${history.slice().reverse().map((item) => `<li class="list-group-item px-0 small"><div>${this._escape(item.message)}</div><div class="text-body-secondary">${this._escape(item.at)}</div></li>`).join("")}</ul>` : `<p class="text-body-secondary mb-0">No decisions yet.</p>`; }
  _watts(value) { return typeof value === "number" ? `${Math.round(value)} W` : "—"; }
  _learnedRange(status) {
    const lower = status.learned_range?.supported_at_least_w;
    const upper = status.learned_range?.unsupported_at_or_above_w;
    if (typeof lower === "number" && typeof upper === "number") return `${Math.round(lower)}–<${Math.round(upper)} W`;
    if (typeof upper === "number") return `<${Math.round(upper)} W`;
    if (typeof lower === "number") return `≥${Math.round(lower)} W`;
    return "Not known yet";
  }
  _surplusDetail(status) {
    const ages = Object.values(status.input_report_ages_seconds || {})
      .filter((age) => typeof age === "number");
    const ageDetail = ages.length
      ? ` Oldest required reading: ${Math.max(...ages) < 60
        ? "<1"
        : Math.floor(Math.max(...ages) / 60)} min.`
      : "";
    if (this._options.source_type === "curtailed_production") {
      return typeof status.opportunity_power_w === "number"
        && typeof status.source_deficit_w === "number"
        ? `${Math.round(status.opportunity_power_w)} W solar · ${Math.round(status.source_deficit_w)} W gap · spare solar cannot be measured.${ageDetail}`
        : `Spare solar cannot be measured directly.${ageDetail}`;
    }
    return `${this._watts(status.headroom_w)}${ageDetail}`;
  }
  _escape(value) { return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]); }
}

customElements.define("solar-spender-panel", SolarSpenderPanelHost);
