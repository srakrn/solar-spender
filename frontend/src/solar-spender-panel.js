import bootstrapCss from "bootstrap/dist/css/bootstrap.min.css";

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

/**
 * This is only Home Assistant's required panel host. Its content is ordinary
 * Bootstrap HTML; it deliberately does not define a Solar Spender component API.
 */
class SolarSpenderPanelHost extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._options = { ...DEFAULT_OPTIONS };
    this._status = null;
    this._shadow = this.attachShadow({ mode: "open" });
    this._shadow.innerHTML = `<style>${bootstrapCss}</style><main class="container-fluid py-3" id="app"></main>`;
  }

  set hass(value) {
    this._hass = value;
    this._load();
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    this._render();
  }

  async _load() {
    if (!this._hass?.connection) return;
    try {
      const [status, options] = await Promise.all([
        this._hass.connection.sendMessagePromise({ type: "solar_spender/status/get" }),
        this._hass.connection.sendMessagePromise({ type: "solar_spender/config/get" }),
      ]);
      this._status = status;
      this._options = { ...DEFAULT_OPTIONS, ...options };
      this._error = null;
    } catch (error) {
      this._error = error?.message || "Solar Spender is not configured yet.";
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
    app.innerHTML = `
      <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
        <div><h1 class="h3 mb-1">Solar Spender</h1><p class="text-body-secondary mb-0">Use spare solar power for climate loads.</p></div>
        <button class="btn btn-outline-primary" id="refresh">Refresh</button>
      </div>
      <div class="row g-3 mb-3">
        ${this._card("Controller", status.state || "Not configured", status.reason || "")}
        ${this._card("Surplus", status.surplus_available ? "Available" : "Unavailable", this._watts(status.headroom_w))}
        ${this._card("Battery gate", status.battery_allowed ? "Open" : "Closed", "")}
        ${this._card("Owned ACs", String(status.owned_loads?.length || 0), "Only these can be released automatically")}
      </div>
      <div class="row g-3">
        <section class="col-12 col-xl-7"><div class="card shadow-sm"><div class="card-body">
          <h2 class="h5">Configuration</h2>
          <form id="settings" class="row g-3">
            <div class="col-md-6"><label class="form-label" for="source_type">Source</label>
              <select class="form-select" id="source_type" name="source_type">
                <option value="binary">Binary headroom</option><option value="grid_flow">Grid flow / export</option><option value="production_consumption">Production minus consumption</option><option value="curtailed_production">Curtailed production probe</option>
              </select></div>
            <div class="col-md-6"><label class="form-label" for="enabled">Automation</label>
              <select class="form-select" id="enabled" name="enabled"><option value="true">Enabled</option><option value="false">Disabled</option></select></div>
            <div class="col-12"><label class="form-label" for="binary_entity_id">Binary headroom entity</label><input class="form-control" id="binary_entity_id" name="binary_entity_id" placeholder="binary_sensor.solar_headroom"></div>
            <div class="col-md-6"><label class="form-label" for="grid_entity_id">Grid-flow entity</label><input class="form-control" id="grid_entity_id" name="grid_entity_id" placeholder="sensor.grid_power"></div>
            <div class="col-md-6"><label class="form-label" for="export_reserve_w">Export reserve (W)</label><input class="form-control" type="number" min="0" id="export_reserve_w" name="export_reserve_w"></div>
            <div class="col-md-6"><label class="form-label" for="grid_export_positive">Grid sensor sign</label><select class="form-select" id="grid_export_positive" name="grid_export_positive"><option value="true">Export is positive</option><option value="false">Import is positive</option></select></div>
            <div class="col-md-6"><label class="form-label" for="production_entity_id">Production power entity</label><input class="form-control" id="production_entity_id" name="production_entity_id" placeholder="sensor.solar_power"></div>
            <div class="col-md-6"><label class="form-label" for="consumption_entity_id">Consumption power entity</label><input class="form-control" id="consumption_entity_id" name="consumption_entity_id" placeholder="sensor.home_power"></div>
            <div class="col-md-6"><label class="form-label" for="entry_threshold_w">Entry threshold (W)</label><input class="form-control" type="number" min="0" id="entry_threshold_w" name="entry_threshold_w"></div>
            <div class="col-md-6"><label class="form-label" for="exit_threshold_w">Exit threshold (W)</label><input class="form-control" type="number" min="0" id="exit_threshold_w" name="exit_threshold_w"></div>
            <div class="col-md-6"><label class="form-label" for="battery_policy">Battery policy</label><select class="form-select" id="battery_policy" name="battery_policy"><option value="disabled">Disabled</option><option value="require_charging">Require charging</option><option value="charging_or_soc">Charging or SOC threshold</option><option value="full_idle_for_probe">Full and idle for probes</option></select></div>
            <div class="col-md-6"><label class="form-label" for="battery_full_threshold">Full/SOC threshold (%)</label><input class="form-control" type="number" min="0" max="100" id="battery_full_threshold" name="battery_full_threshold"></div>
            <div class="col-md-6"><label class="form-label" for="battery_soc_entity_id">Battery SOC entity</label><input class="form-control" id="battery_soc_entity_id" name="battery_soc_entity_id" placeholder="sensor.battery_soc"></div>
            <div class="col-md-6"><label class="form-label" for="battery_status_entity_id">Battery status entity</label><input class="form-control" id="battery_status_entity_id" name="battery_status_entity_id" placeholder="sensor.battery_status"></div>
            <div class="col-12"><label class="form-label" for="loads_json">Climate loads (JSON)</label><textarea class="form-control font-monospace" rows="9" id="loads_json" aria-describedby="loads_help"></textarea><div class="form-text" id="loads_help">Each load needs entity_id plus hvac_mode or temperature. Example: [{"entity_id":"climate.bedroom","hvac_mode":"dry","min_on_seconds":900,"min_off_seconds":900}]</div></div>
            <div class="col-12 d-flex align-items-center gap-2"><button class="btn btn-primary" type="submit">Save configuration</button><span id="save_result" class="small" role="status"></span></div>
          </form>
        </div></div></section>
        <section class="col-12 col-xl-5"><div class="card shadow-sm"><div class="card-body"><h2 class="h5">Loads</h2>${this._loads(status.loads || [])}<hr><h2 class="h5">Recent decisions</h2>${this._history(status.history || [])}</div></div></section>
      </div>`;
    this._fillForm();
    app.querySelector("#refresh").addEventListener("click", () => this._load());
    app.querySelector("#settings").addEventListener("submit", (event) => this._save(event));
  }

  _fillForm() {
    const form = this._shadow.querySelector("#settings");
    for (const [key, value] of Object.entries(this._options)) {
      const input = form.elements.namedItem(key);
      if (input && typeof value !== "object") input.value = String(value);
    }
    form.elements.enabled.value = String(Boolean(this._options.enabled));
    form.elements.loads_json.value = JSON.stringify(this._options.loads || [], null, 2);
  }

  async _save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const result = this._shadow.querySelector("#save_result");
    try {
      const options = { ...this._options };
      for (const key of ["source_type", "binary_entity_id", "grid_entity_id", "production_entity_id", "consumption_entity_id", "battery_policy", "battery_soc_entity_id", "battery_status_entity_id"]) options[key] = form.elements[key].value.trim();
      for (const key of ["export_reserve_w", "entry_threshold_w", "exit_threshold_w", "battery_full_threshold"]) options[key] = Number(form.elements[key].value);
      options.enabled = form.elements.enabled.value === "true";
      options.grid_export_positive = form.elements.grid_export_positive.value === "true";
      options.loads = JSON.parse(form.elements.loads_json.value || "[]");
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

  _card(title, value, detail) { return `<div class="col-12 col-sm-6 col-xl-3"><div class="card h-100 shadow-sm"><div class="card-body"><div class="text-body-secondary small">${this._escape(title)}</div><div class="fs-4 fw-semibold">${this._escape(value)}</div><div class="small text-body-secondary">${this._escape(detail)}</div></div></div></div>`; }
  _loads(loads) { return loads.length ? `<ul class="list-group list-group-flush">${loads.map((load) => `<li class="list-group-item px-0 d-flex justify-content-between"><span>${this._escape(load.entity_id)}</span><span class="badge text-bg-${load.owned ? "success" : "secondary"}">${load.owned ? "Owned" : this._escape(load.state || "unknown")}</span></li>`).join("")}</ul>` : `<p class="text-body-secondary mb-0">No climate loads configured.</p>`; }
  _history(history) { return history.length ? `<ul class="list-group list-group-flush">${history.slice().reverse().map((item) => `<li class="list-group-item px-0 small"><div>${this._escape(item.message)}</div><div class="text-body-secondary">${this._escape(item.at)}</div></li>`).join("")}</ul>` : `<p class="text-body-secondary mb-0">No decisions yet.</p>`; }
  _watts(value) { return typeof value === "number" ? `${Math.round(value)} W` : "—"; }
  _escape(value) { return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]); }
}

customElements.define("solar-spender-panel", SolarSpenderPanelHost);
