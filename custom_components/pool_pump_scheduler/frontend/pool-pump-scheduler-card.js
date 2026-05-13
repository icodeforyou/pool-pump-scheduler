/**
 * Pool Pump Scheduler Card
 *
 * Visualizes electricity prices alongside the computed pump schedule.
 * No build step required — vanilla JS + SVG.
 */

const CARD_VERSION = "1.2.0";

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
    this._render();
  }

  getCardSize() {
    return 4;
  }

  connectedCallback() {
    this._resizeObserver = new ResizeObserver(() => this._render());
    this._resizeObserver.observe(this);
    // Tick every minute so the "now" indicator stays current.
    this._tickInterval = setInterval(() => this._render(), 60_000);
  }

  disconnectedCallback() {
    if (this._resizeObserver) this._resizeObserver.disconnect();
    if (this._tickInterval) clearInterval(this._tickInterval);
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
    const totalRuntime = attrs.total_runtime_minutes
      ? (attrs.total_runtime_minutes / 60).toFixed(2) + " h"
      : "—";
    const blockCount = attrs.block_count ?? "—";
    const avgPrice = attrs.average_price != null ? attrs.average_price.toFixed(3) : "—";
    const totalCost = attrs.total_cost != null ? attrs.total_cost.toFixed(2) : "—";
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

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
        }
        ha-card {
          padding: 16px;
          display: block;
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
        @keyframes pulse {
          0% { box-shadow: 0 0 0 0 rgba(67, 160, 71, 0.5); }
          70% { box-shadow: 0 0 0 8px rgba(67, 160, 71, 0); }
          100% { box-shadow: 0 0 0 0 rgba(67, 160, 71, 0); }
        }
        .chart-wrap {
          position: relative;
          width: 100%;
        }
        svg {
          width: 100%;
          height: auto;
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
        .now-line {
          stroke: var(--accent-color, #ff9800);
          stroke-width: 2;
          stroke-dasharray: 4 3;
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
          <div class="title">${this._escape(title)}</div>
          <div class="status ${isOn ? "on" : ""}">
            <span class="dot"></span>
            <span>${isOn ? "Running" : "Idle"}</span>
          </div>
        </div>
        <div class="chart-wrap" id="chart-wrap"></div>
        ${showStats ? this._renderStats({
          totalRuntime, blockCount, avgPrice, totalCost, nextChange, unit, currency,
        }) : ""}
      </ha-card>
    `;

    const wrap = this.shadowRoot.getElementById("chart-wrap");
    if (prices.length === 0) {
      wrap.innerHTML = `<div class="empty">
        No price data available.${
          this._config.price_sensor
            ? ""
            : "<br><small>Tip: set 'price_sensor' in the card config.</small>"
        }
      </div>`;
      return;
    }

    this._renderChart(wrap, prices, blocks);
  }

  _renderStats({ totalRuntime, blockCount, avgPrice, totalCost, nextChange, unit, currency }) {
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
          <div class="stat-label">Total cost</div>
          <div class="stat-value">${totalCost} <small>${this._escape(currency)}</small></div>
        </div>
        <div class="stat">
          <div class="stat-label">Next change</div>
          <div class="stat-value">${nextChange}</div>
        </div>
      </div>
    `;
  }

  _renderChart(wrap, prices, blocks) {
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
        const cls = isActive ? "block block-on" : "block";
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
      nowLine = `<line class="now-line" x1="${x}" y1="${margin.top}" x2="${x}" y2="${
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
