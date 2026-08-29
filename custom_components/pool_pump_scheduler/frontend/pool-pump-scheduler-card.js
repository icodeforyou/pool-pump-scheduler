/**
 * Pool Pump Scheduler Card
 *
 * Visualizes electricity prices alongside the computed pump schedule.
 * No build step required — vanilla JS + SVG.
 */

const CARD_VERSION = "1.6.3";

class PoolPumpSchedulerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._resizeObserver = null;
    this._hoverIndex = null;
  }

  static getStubConfig() {
    return {
      binary_sensor: "binary_sensor.pool_pump_should_run",
      price_sensor: "",
      title: "Pool Pump Schedule",
    };
  }

  static getConfigElement() {
    return document.createElement("pool-pump-scheduler-card-editor");
  }

  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    if (!config.binary_sensor) {
      throw new Error(
        "You need to define 'binary_sensor' (the Pool Pump 'should run' sensor)."
      );
    }
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    // HA dispatches hass updates many times per second. Tearing down the
    // shadow DOM on each one made the page scroll-jump on mobile. Skip
    // the render unless the entities we actually read have changed.
    const sig = this._signature();
    if (sig !== null && sig === this._lastSig) return;
    this._lastSig = sig;
    this._render();
  }

  _signature() {
    if (!this._hass || !this._config) return null;
    const bin = this._hass.states[this._config.binary_sensor];
    const price = this._getPriceSensor();
    return `${bin ? bin.state + "|" + bin.last_updated : "?"}` +
      `|${price ? price.last_updated : "?"}`;
  }

  _forceRender() {
    this._lastSig = null;
    this._render();
  }

  getCardSize() {
    return 4;
  }

  connectedCallback() {
    // Debounce resize re-renders. On mobile the address bar showing/hiding
    // triggers a flurry of resizes; without this we re-render multiple
    // times per scroll gesture.
    this._resizeObserver = new ResizeObserver(() => {
      clearTimeout(this._resizeTimer);
      this._resizeTimer = setTimeout(() => this._forceRender(), 200);
    });
    this._resizeObserver.observe(this);
    // Tick every minute so the "now" indicator stays current.
    this._tickInterval = setInterval(() => this._forceRender(), 60_000);
  }

  disconnectedCallback() {
    if (this._resizeObserver) this._resizeObserver.disconnect();
    if (this._tickInterval) clearInterval(this._tickInterval);
    if (this._resizeTimer) clearTimeout(this._resizeTimer);
  }

  // -------------------------------------------------------------- data

  _getBinarySensor() {
    if (!this._hass || !this._config) return null;
    return this._hass.states[this._config.binary_sensor];
  }

  _getPriceSensor() {
    if (!this._hass) return null;
    let id = this._config.price_sensor;
    if (!id) {
      // Auto-detect by walking the binary sensor's blocks: not feasible.
      // Try to find a Nord Pool sensor in the system as a fallback.
      const candidates = Object.keys(this._hass.states).filter(
        (e) =>
          e.startsWith("sensor.nordpool") ||
          (this._hass.states[e].attributes &&
            this._hass.states[e].attributes.raw_today)
      );
      if (candidates.length > 0) id = candidates[0];
    }
    if (!id) return null;
    return this._hass.states[id];
  }

  _parseBlocks(binarySensor) {
    if (!binarySensor) return [];
    const blocks = binarySensor.attributes.blocks || [];
    return blocks
      .map((b) => ({
        start: new Date(b.start),
        end: new Date(b.end),
      }))
      .filter((b) => !isNaN(b.start) && !isNaN(b.end));
  }

  _parsePrices(priceSensor) {
    if (!priceSensor) return [];
    const today = priceSensor.attributes.raw_today || [];
    const tomorrow = priceSensor.attributes.raw_tomorrow || [];
    const all = [...today, ...tomorrow];
    return all
      .map((p) => ({
        start: new Date(p.start),
        end: new Date(p.end),
        value: typeof p.value === "number" ? p.value : parseFloat(p.value),
      }))
      .filter((p) => !isNaN(p.start) && !isNaN(p.end) && !isNaN(p.value));
  }

  // -------------------------------------------------------------- render

  _render() {
    if (!this._hass || !this._config) return;

    const binarySensor = this._getBinarySensor();
    const priceSensor = this._getPriceSensor();
    const blocks = this._parseBlocks(binarySensor);
    const prices = this._parsePrices(priceSensor);

    const title = this._config.title || "Pool Pump Schedule";
    const showStats = this._config.show_stats !== false;

    const isOn = binarySensor && binarySensor.state === "on";
    const attrs = binarySensor ? binarySensor.attributes : {};
    const solarActive = !!attrs.solar_active;
    let pillClass = "";
    let pillText = "Idle";
    if (isOn && solarActive) {
      pillClass = "on solar";
      pillText = "On — solar";
    } else if (isOn) {
      pillClass = "on";
      pillText = "On — schedule";
    }
    const totalRuntime = attrs.total_runtime_minutes
      ? (attrs.total_runtime_minutes / 60).toFixed(2) + " h"
      : "—";
    const blockCount = attrs.block_count ?? "—";
    const avgPrice = attrs.average_price != null ? attrs.average_price.toFixed(3) : "—";
    const costToday = attrs.cost_today != null ? attrs.cost_today.toFixed(2) : "—";
    const nextChange = attrs.next_change
      ? new Date(attrs.next_change).toLocaleString(undefined, {
          weekday: "short",
          hour: "2-digit",
          minute: "2-digit",
        })
      : "—";

    const currency = (priceSensor && priceSensor.attributes.currency) || "SEK";
    const unit =
      (priceSensor && priceSensor.attributes.unit_of_measurement) || `${currency}/kWh`;

    if (!this._rootBuilt) {
      this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
        }
        ha-card {
          padding: 16px;
          display: block;
          contain: layout style;
        }
        .header {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          margin-bottom: 12px;
        }
        .title {
          font-size: var(--ha-font-size-l, 1.1rem);
          font-weight: 500;
        }
        .status {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          font-size: 0.9rem;
          padding: 4px 10px;
          border-radius: 12px;
          background: var(--secondary-background-color);
        }
        .status .dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: var(--disabled-color, #999);
        }
        .status.on .dot {
          background: var(--success-color, #43a047);
          box-shadow: 0 0 0 0 rgba(67, 160, 71, 0.7);
          animation: pulse 2s infinite;
        }
        .status.on.solar .dot {
          background: #f59e0b;
          animation: pulse-solar 2s infinite;
        }
        .status.on.solar {
          background: rgba(245, 158, 11, 0.15);
          color: #b45309;
        }
        @keyframes pulse {
          0% { box-shadow: 0 0 0 0 rgba(67, 160, 71, 0.5); }
          70% { box-shadow: 0 0 0 8px rgba(67, 160, 71, 0); }
          100% { box-shadow: 0 0 0 0 rgba(67, 160, 71, 0); }
        }
        @keyframes pulse-solar {
          0% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.55); }
          70% { box-shadow: 0 0 0 8px rgba(245, 158, 11, 0); }
          100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); }
        }
        .header-actions {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .refresh-btn {
          background: none;
          border: none;
          cursor: pointer;
          padding: 4px;
          color: var(--secondary-text-color);
          display: inline-flex;
          align-items: center;
          border-radius: 50%;
        }
        .refresh-btn:hover {
          color: var(--primary-text-color);
          background: var(--secondary-background-color);
        }
        .refresh-btn.spinning svg {
          animation: spin 0.6s linear;
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        .chart-wrap {
          position: relative;
          width: 100%;
          aspect-ratio: 2.5 / 1;
          min-height: 180px;
          contain: strict;
        }
        svg {
          width: 100%;
          height: 100%;
          display: block;
          font-family: var(--ha-font-family-body, sans-serif);
        }
        .price-line {
          fill: none;
          stroke: var(--primary-text-color);
          stroke-width: 1.5;
          opacity: 0.85;
        }
        .price-area {
          fill: var(--primary-text-color);
          opacity: 0.08;
        }
        .block {
          fill: var(--primary-color);
          opacity: 0.25;
        }
        .block-on {
          fill: var(--success-color, #43a047);
          opacity: 0.35;
        }
        .block-on.solar {
          fill: #f59e0b;
          opacity: 0.45;
        }
        .now-line {
          stroke: var(--accent-color, #ff9800);
          stroke-width: 2;
          stroke-dasharray: 4 3;
        }
        .now-line.solar {
          stroke: #f59e0b;
          stroke-width: 2.5;
          stroke-dasharray: none;
        }
        .axis-text {
          fill: var(--secondary-text-color);
          font-size: 10px;
        }
        .day-divider {
          stroke: var(--divider-color);
          stroke-width: 1;
          stroke-dasharray: 2 4;
        }
        .day-label {
          fill: var(--secondary-text-color);
          font-size: 11px;
          font-weight: 500;
        }
        .tooltip {
          position: absolute;
          background: var(--card-background-color);
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          padding: 6px 10px;
          font-size: 0.85rem;
          pointer-events: none;
          box-shadow: 0 2px 8px rgba(0,0,0,0.15);
          white-space: nowrap;
          z-index: 10;
          color: var(--primary-text-color);
        }
        .stats {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
          gap: 8px;
          margin-top: 12px;
        }
        .stat {
          background: var(--secondary-background-color);
          border-radius: 6px;
          padding: 8px 10px;
        }
        .stat-label {
          font-size: 0.75rem;
          color: var(--secondary-text-color);
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }
        .stat-value {
          font-size: 1rem;
          font-weight: 500;
          color: var(--primary-text-color);
          margin-top: 2px;
        }
        .empty {
          padding: 20px;
          text-align: center;
          color: var(--secondary-text-color);
        }
      </style>
      <ha-card>
        <div class="header">
          <div class="title" id="title-el"></div>
          <div class="header-actions">
            <button class="refresh-btn" id="refresh-btn" title="Recalculate schedule now" aria-label="Recalculate">
              <svg width="18" height="18" viewBox="0 0 24 24">
                <path fill="currentColor" d="M17.65 6.35A7.958 7.958 0 0 0 12 4a8 8 0 1 0 7.74 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4z"/>
              </svg>
            </button>
            <div class="status" id="status-el">
              <span class="dot"></span>
              <span id="status-text-el"></span>
            </div>
          </div>
        </div>
        <div class="chart-wrap" id="chart-wrap"></div>
        ${showStats ? '<div id="stats-wrap"></div>' : ""}
      </ha-card>
    `;
      this._titleEl = this.shadowRoot.getElementById("title-el");
      this._statusEl = this.shadowRoot.getElementById("status-el");
      this._statusTextEl = this.shadowRoot.getElementById("status-text-el");
      this._chartEl = this.shadowRoot.getElementById("chart-wrap");
      this._statsEl = this.shadowRoot.getElementById("stats-wrap");
      const refreshBtn = this.shadowRoot.getElementById("refresh-btn");
      if (refreshBtn) {
        refreshBtn.addEventListener("click", () => this._recalculate(refreshBtn));
      }
      this._rootBuilt = true;
    }

    this._titleEl.textContent = title;
    this._statusEl.className = `status ${pillClass}`;
    this._statusTextEl.textContent = pillText;

    const wrap = this._chartEl;
    if (prices.length === 0) {
      wrap.innerHTML = `<div class="empty">
        No price data available.${
          this._config.price_sensor
            ? ""
            : "<br><small>Tip: set 'price_sensor' in the card config.</small>"
        }
      </div>`;
      if (this._statsEl) this._statsEl.innerHTML = "";
      return;
    }

    this._renderChart(wrap, prices, blocks, solarActive);

    if (this._statsEl) {
      this._statsEl.innerHTML = this._renderStats({
        totalRuntime, blockCount, avgPrice, costToday, nextChange, unit, currency,
      });
    }
  }

  _recalculate(btn) {
    if (!this._hass) return;
    if (btn) {
      btn.classList.remove("spinning");
      // Force reflow so the animation restarts on rapid clicks.
      void btn.offsetWidth;
      btn.classList.add("spinning");
    }
    const binId = this._config.binary_sensor;
    const reg = this._hass.entities && this._hass.entities[binId];
    const data =
      reg && reg.config_entry_id ? { entry_id: reg.config_entry_id } : {};
    this._hass.callService("pool_pump_scheduler", "recalculate", data);
  }

  _renderStats({ totalRuntime, blockCount, avgPrice, costToday, nextChange, unit, currency }) {
    return `
      <div class="stats">
        <div class="stat">
          <div class="stat-label">Runtime</div>
          <div class="stat-value">${totalRuntime}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Blocks</div>
          <div class="stat-value">${blockCount}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Avg price</div>
          <div class="stat-value">${avgPrice} <small>${this._escape(unit)}</small></div>
        </div>
        <div class="stat">
          <div class="stat-label">Cost today</div>
          <div class="stat-value">${costToday} <small>${this._escape(currency)}</small></div>
        </div>
        <div class="stat">
          <div class="stat-label">Next change</div>
          <div class="stat-value">${nextChange}</div>
        </div>
      </div>
    `;
  }

  _renderChart(wrap, prices, blocks, solarActive = false) {
    // Determine x-domain: full extent of price slots.
    const tMin = prices[0].start.getTime();
    const tMax = prices[prices.length - 1].end.getTime();

    // y-domain.
    let vMin = Math.min(...prices.map((p) => p.value));
    let vMax = Math.max(...prices.map((p) => p.value));
    if (vMin === vMax) vMax = vMin + 1;
    // Pad y for readability and to handle negative prices gracefully.
    const yPad = (vMax - vMin) * 0.1;
    vMin = Math.min(vMin - yPad, 0);
    vMax = vMax + yPad;

    const W = wrap.clientWidth || 600;
    const H = Math.max(180, Math.min(280, W * 0.4));
    const margin = { top: 20, right: 10, bottom: 24, left: 36 };
    const innerW = W - margin.left - margin.right;
    const innerH = H - margin.top - margin.bottom;

    const xScale = (t) =>
      margin.left + ((t - tMin) / (tMax - tMin)) * innerW;
    const yScale = (v) =>
      margin.top + innerH - ((v - vMin) / (vMax - vMin)) * innerH;

    // Build price step path (each slot is a flat segment).
    let pathD = "";
    let areaD = "";
    for (let i = 0; i < prices.length; i++) {
      const p = prices[i];
      const x0 = xScale(p.start.getTime());
      const x1 = xScale(p.end.getTime());
      const y = yScale(p.value);
      if (i === 0) {
        pathD += `M${x0},${y}`;
        areaD += `M${x0},${yScale(vMin)} L${x0},${y}`;
      }
      pathD += ` L${x1},${y}`;
      areaD += ` L${x1},${y}`;
    }
    areaD += ` L${xScale(prices[prices.length - 1].end.getTime())},${yScale(vMin)} Z`;

    // Build block rectangles.
    const blockRects = blocks
      .map((b) => {
        const x0 = xScale(b.start.getTime());
        const x1 = xScale(b.end.getTime());
        const now = Date.now();
        const isActive = b.start.getTime() <= now && now < b.end.getTime();
        let cls = "block";
        if (isActive) {
          cls += solarActive ? " block-on solar" : " block-on";
        }
        return `<rect class="${cls}" x="${x0}" y="${margin.top}" width="${Math.max(
          0,
          x1 - x0
        )}" height="${innerH}" />`;
      })
      .join("");

    // X-axis ticks every 3 hours.
    let xTicks = "";
    const start = new Date(tMin);
    start.setMinutes(0, 0, 0);
    for (let t = start.getTime(); t <= tMax; t += 3 * 3600 * 1000) {
      if (t < tMin) continue;
      const x = xScale(t);
      const d = new Date(t);
      const label = d.getHours().toString().padStart(2, "0");
      xTicks += `
        <line x1="${x}" y1="${margin.top + innerH}" x2="${x}" y2="${
        margin.top + innerH + 4
      }" stroke="var(--secondary-text-color)" stroke-width="0.5" />
        <text class="axis-text" x="${x}" y="${
        margin.top + innerH + 14
      }" text-anchor="middle">${label}</text>
      `;
    }

    // Day divider at midnight if data spans two days.
    let dayDividers = "";
    const startDate = new Date(tMin);
    startDate.setHours(24, 0, 0, 0);
    while (startDate.getTime() < tMax) {
      const x = xScale(startDate.getTime());
      dayDividers += `
        <line class="day-divider" x1="${x}" y1="${margin.top}" x2="${x}" y2="${
        margin.top + innerH
      }" />
      `;
      startDate.setDate(startDate.getDate() + 1);
    }

    // Day labels (centered between midnights).
    let dayLabels = "";
    const labelStart = new Date(tMin);
    labelStart.setHours(0, 0, 0, 0);
    while (labelStart.getTime() < tMax) {
      const dayStart = Math.max(labelStart.getTime(), tMin);
      const dayEndDate = new Date(labelStart);
      dayEndDate.setDate(dayEndDate.getDate() + 1);
      const dayEnd = Math.min(dayEndDate.getTime(), tMax);
      const xCenter = (xScale(dayStart) + xScale(dayEnd)) / 2;
      const label = labelStart.toLocaleDateString(undefined, {
        weekday: "short",
        day: "numeric",
        month: "short",
      });
      dayLabels += `<text class="day-label" x="${xCenter}" y="${
        margin.top - 6
      }" text-anchor="middle">${label}</text>`;
      labelStart.setDate(labelStart.getDate() + 1);
    }

    // Y-axis ticks (3 lines).
    let yTicks = "";
    for (let i = 0; i <= 3; i++) {
      const v = vMin + ((vMax - vMin) * i) / 3;
      const y = yScale(v);
      yTicks += `
        <line x1="${margin.left}" y1="${y}" x2="${
        margin.left + innerW
      }" y2="${y}" stroke="var(--divider-color)" stroke-width="0.5" stroke-dasharray="2 3" />
        <text class="axis-text" x="${
          margin.left - 4
        }" y="${y + 3}" text-anchor="end">${v.toFixed(2)}</text>
      `;
    }

    // "Now" line.
    const nowT = Date.now();
    let nowLine = "";
    if (nowT >= tMin && nowT <= tMax) {
      const x = xScale(nowT);
      const nowCls = solarActive ? "now-line solar" : "now-line";
      nowLine = `<line class="${nowCls}" x1="${x}" y1="${margin.top}" x2="${x}" y2="${
        margin.top + innerH
      }" />`;
    }

    wrap.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        ${dayDividers}
        ${blockRects}
        ${yTicks}
        ${xTicks}
        ${dayLabels}
        <path class="price-area" d="${areaD}" />
        <path class="price-line" d="${pathD}" />
        ${nowLine}
        <rect id="hover-target" x="${margin.left}" y="${margin.top}" width="${innerW}" height="${innerH}" fill="transparent" />
      </svg>
      <div class="tooltip" id="tooltip" style="display:none"></div>
    `;

    // Hover handling.
    const svg = wrap.querySelector("svg");
    const tooltip = wrap.querySelector("#tooltip");
    const hoverTarget = wrap.querySelector("#hover-target");

    const onMove = (e) => {
      const rect = svg.getBoundingClientRect();
      const xRatio = (e.clientX - rect.left) / rect.width;
      const xPixel = xRatio * W;
      if (xPixel < margin.left || xPixel > margin.left + innerW) {
        tooltip.style.display = "none";
        return;
      }
      const tHovered = tMin + ((xPixel - margin.left) / innerW) * (tMax - tMin);
      // Find the slot containing tHovered.
      const slot = prices.find(
        (p) => p.start.getTime() <= tHovered && tHovered < p.end.getTime()
      );
      if (!slot) {
        tooltip.style.display = "none";
        return;
      }
      const inBlock = blocks.some(
        (b) => b.start.getTime() <= tHovered && tHovered < b.end.getTime()
      );
      const timeLabel = slot.start.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
      });
      const endLabel = slot.end.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
      });
      tooltip.innerHTML = `
        <strong>${timeLabel}–${endLabel}</strong><br>
        ${slot.value.toFixed(3)} <small>${this._escape(
        (this._getPriceSensor() &&
          this._getPriceSensor().attributes.unit_of_measurement) ||
          "SEK/kWh"
      )}</small>
        ${inBlock ? '<br><span style="color:var(--success-color)">● Pump ON</span>' : ""}
      `;
      tooltip.style.display = "block";
      // Position tooltip relative to wrap.
      const wrapRect = wrap.getBoundingClientRect();
      const tipX = e.clientX - wrapRect.left + 10;
      const tipY = e.clientY - wrapRect.top - 10;
      tooltip.style.left = Math.min(tipX, wrap.clientWidth - 150) + "px";
      tooltip.style.top = Math.max(0, tipY) + "px";
    };

    const onLeave = () => {
      tooltip.style.display = "none";
    };

    hoverTarget.addEventListener("mousemove", onMove);
    hoverTarget.addEventListener("mouseleave", onLeave);
    // Touch: tap to show, tap elsewhere to hide.
    hoverTarget.addEventListener("touchstart", (e) => {
      if (e.touches[0]) onMove(e.touches[0]);
    }, { passive: true });
    hoverTarget.addEventListener("touchmove", (e) => {
      if (e.touches[0]) {
        e.preventDefault();
        onMove(e.touches[0]);
      }
    });
  }

  _escape(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
}

customElements.define("pool-pump-scheduler-card", PoolPumpSchedulerCard);

class PoolPumpSchedulerCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._built = false;
  }

  setConfig(config) {
    this._config = { ...(config || {}) };
    if (this._built) this._syncValues();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built && this._config) {
      this._build();
      this._built = true;
    }
    for (const picker of [this._binPicker, this._pricePicker]) {
      if (picker) picker.hass = hass;
    }
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        .form { display: flex; flex-direction: column; gap: 12px; padding: 8px 0; }
        .row { display: flex; align-items: center; gap: 12px; }
        .hint { font-size: 13px; color: var(--secondary-text-color);
                background: var(--secondary-background-color);
                padding: 10px 12px; border-radius: 8px;
                border-left: 3px solid var(--primary-color); }
        .hint strong { color: var(--primary-text-color); }
        ha-textfield { width: 100%; }
      </style>
      <div class="form" id="form"></div>
    `;
    const form = this.shadowRoot.getElementById("form");

    this._binPicker = document.createElement("ha-entity-picker");
    this._binPicker.hass = this._hass;
    this._binPicker.value = this._config.binary_sensor || "";
    this._binPicker.label = "Should-run binary sensor (required)";
    this._binPicker.includeDomains = ["binary_sensor"];
    this._binPicker.addEventListener("value-changed", (ev) => {
      this._update("binary_sensor", ev.detail.value);
    });
    form.appendChild(this._binPicker);

    this._pricePicker = document.createElement("ha-entity-picker");
    this._pricePicker.hass = this._hass;
    this._pricePicker.value = this._config.price_sensor || "";
    this._pricePicker.label = "Price sensor (Nord Pool — leave blank to auto-detect)";
    this._pricePicker.includeDomains = ["sensor"];
    this._pricePicker.addEventListener("value-changed", (ev) => {
      this._update("price_sensor", ev.detail.value);
    });
    form.appendChild(this._pricePicker);

    this._titleField = document.createElement("ha-textfield");
    this._titleField.label = "Card title";
    this._titleField.value = this._config.title || "";
    this._titleField.addEventListener("input", (ev) => {
      this._update("title", ev.target.value);
    });
    form.appendChild(this._titleField);

    const statsRow = document.createElement("div");
    statsRow.className = "row";
    this._statsToggle = document.createElement("ha-switch");
    this._statsToggle.checked = this._config.show_stats !== false;
    this._statsToggle.addEventListener("change", (ev) => {
      this._update("show_stats", ev.target.checked);
    });
    const statsLabel = document.createElement("span");
    statsLabel.textContent = "Show runtime / cost / average-price tiles";
    statsRow.appendChild(this._statsToggle);
    statsRow.appendChild(statsLabel);
    form.appendChild(statsRow);

    const hint = document.createElement("div");
    hint.className = "hint";
    hint.innerHTML =
      "<strong>Solar surplus</strong> is configured in the integration, not " +
      "the card. Open <em>Settings → Devices &amp; Services → " +
      "Pool Pump Scheduler → Configure</em> to set your solar production " +
      "and house consumption sensors.";
    form.appendChild(hint);
  }

  _syncValues() {
    if (this._binPicker) this._binPicker.value = this._config.binary_sensor || "";
    if (this._pricePicker) this._pricePicker.value = this._config.price_sensor || "";
    if (this._titleField) this._titleField.value = this._config.title || "";
    if (this._statsToggle) this._statsToggle.checked = this._config.show_stats !== false;
  }

  _update(key, value) {
    this._config = { ...this._config, [key]: value };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      })
    );
  }
}

customElements.define(
  "pool-pump-scheduler-card-editor",
  PoolPumpSchedulerCardEditor
);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "pool-pump-scheduler-card",
  name: "Pool Pump Scheduler",
  preview: true,
  description:
    "Visualizes electricity prices and the computed pump schedule on a 24h timeline.",
});

console.info(
  `%c POOL-PUMP-SCHEDULER-CARD %c v${CARD_VERSION} `,
  "color:white;background:#03a9f4;font-weight:700",
  "color:#03a9f4;background:white;font-weight:700"
);
