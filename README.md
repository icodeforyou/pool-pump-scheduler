# Pool Pump Scheduler for Home Assistant

A custom integration that automatically schedules a pool circulation pump (or any other "must run N hours/day" load) during the cheapest hours of electricity, using a Nord Pool price sensor.

Designed for the Swedish market where Nord Pool publishes day-ahead prices around 13:00 CET, but works anywhere the [Nord Pool integration](https://github.com/custom-components/nordpool) is supported.

## Features

- Reads quarter-hourly prices from a Nord Pool sensor (`raw_today` / `raw_tomorrow` attributes)
- Selects the cheapest combination of slots that satisfies your minimum daily runtime
- **Enforces a minimum continuous run length** (e.g. "no block shorter than 60 minutes") to protect pump relays and motors
- Optional **price ceiling**: never run when the price exceeds a threshold (the integration will warn if the ceiling makes the schedule unsolvable)
- Optional **solar surplus overlay** (v1.3.0): when your solar production exceeds house consumption by the pump's draw, the pump runs from the sun regardless of what the price schedule says. Hysteresis on both edges protects the relay.
- Recalculates daily at a configurable time (default 14:00, after Nord Pool publishes tomorrow's prices)
- Plans today and tomorrow as **independent schedules** — once tomorrow's prices publish, today's plan is preserved, not overwritten
- **Resumes correctly after a Home Assistant restart**, including mid-day restarts; diagnostic timestamps survive reloads via `Store`
- Drives a switch entity (e.g. a Shelly) directly, or just exposes a binary sensor you can use in your own automation
- **Bundled Lovelace card** — no separate HACS install. Shows a 24h price timeline with the scheduled ON blocks highlighted, plus runtime/cost stats.
- Manual recalculate service for testing

## Installation

### Via HACS (recommended)

1. Add this repository as a custom repository in HACS (Integration type)
2. Install "Pool Pump Scheduler"
3. Restart Home Assistant
4. Settings → Devices & Services → Add Integration → "Pool Pump Scheduler"

### Manual

1. Copy `custom_components/pool_pump_scheduler/` into your Home Assistant `custom_components/` directory
2. Restart Home Assistant
3. Settings → Devices & Services → Add Integration → "Pool Pump Scheduler"

## Configuration

Everything is configured through the UI. Settings:

| Setting | Description |
|---|---|
| Name | Display name (e.g. "Pool Pump") |
| Price sensor | Your Nord Pool sensor, e.g. `sensor.nordpool_kwh_se4_sek_3_095_025` |
| Pump switch | The switch entity controlling the pump, e.g. `switch.shelly_pool_pump` |
| Runtime hours | Minimum daily runtime, e.g. `12` |
| Minimum block length | Smallest allowed continuous ON block in minutes, e.g. `60` |
| Recalculation time | When to compute tomorrow's schedule, default `14:00` |
| Automatically control switch | If off, the integration only exposes the binary sensor and you write your own automation |
| Use price ceiling | Optionally skip any slot above a price threshold |
| Maximum price | The price ceiling (SEK/kWh) |

You can change all of these later via Settings → Devices & Services → Pool Pump Scheduler → Configure.

## Entities created

- `binary_sensor.<name>_should_run` — `on` when the pump should currently run. Attributes include the full block list, total runtime, total cost, average price, and next change time.
- `sensor.<name>_next_change` — timestamp of the next ON/OFF transition.
- `sensor.<name>_scheduled_cost` — total electricity cost for the next scheduled period (SEK).
- `sensor.<name>_scheduled_average_price` — average price across selected slots (SEK/kWh).
- `sensor.<name>_cost_today` — accumulating grid-paid cost for today (v1.5.0). Solar-driven slots add zero. `device_class: monetary`, `state_class: total` with a daily `last_reset`, so HA's long-term statistics record per-day totals you can graph as bars.
- `sensor.<name>_lifetime_cost` — lifetime grid-paid cost (v1.5.0). Never resets; persisted in `Store` so it survives restarts. `state_class: total_increasing`.
- `button.<name>_recalculate_schedule` — press to recompute the schedule immediately. Equivalent to the `pool_pump_scheduler.recalculate` service.

The new cost sensors derive from your configured pump wattage and the Nord Pool slot price: each 15-minute grid-driven slot adds `price × (pump_power_w / 1000) × 0.25` to both counters. Solar-driven slots are free.

**Secondary load (v1.6.0):** if you have a second device that runs alongside the pump — typically a pool heat-pump inverter — you can configure its sensors in the options flow and its draw will be folded into the cost. Two sensors, both optional:

- **Power sensor (W)** — instantaneous draw. Folded into cost via slot-end sampling: `inverter_w × 0.25 h` per slot. Approximate for loads that cycle within a slot (e.g. a heat pump that satisfies its setpoint mid-slot and shuts off).
- **Energy sensor (kWh, preferred when available, v1.6.1)** — a `total_increasing` cumulative counter. The integration captures the value at every slot boundary and uses the *delta* between boundaries, which is mathematically exact regardless of how the load cycled in between.

If both are configured the energy sensor wins. If only the power sensor is configured we sample-and-multiply. If the energy sensor ever reports a decrease (e.g. a sensor reset), that one slot falls back to power sampling. Solar-covered slots are free for both loads.

The pump itself is still charged at the fixed `pump_power_w × slot_price × 0.25 h` — if your pump cycles inside a slot you can wire it through the same secondary-load mechanism, or open an issue and we'll add a dedicated `pump_energy_sensor` config.

## Visualization card

The integration ships with a custom Lovelace card that's automatically registered when you install it — **no separate HACS install or resource setup needed** (when Lovelace is in storage mode, which is the default).

To add it: edit your dashboard → "+ Add Card" → search for "Pool Pump Scheduler". Or in YAML:

```yaml
type: custom:pool-pump-scheduler-card
binary_sensor: binary_sensor.pool_pump_should_run
price_sensor: sensor.nordpool_kwh_se4_sek_3_095_025
title: Pool Pump
show_stats: true
```

Config options:

| Option | Required | Default | Description |
|---|---|---|---|
| `binary_sensor` | yes | — | The `should_run` binary sensor created by the integration |
| `price_sensor` | no | auto-detected | Your Nord Pool price sensor. If omitted, the card tries to find one automatically. |
| `title` | no | "Pool Pump Schedule" | Card title |
| `show_stats` | no | `true` | Whether to show the runtime/cost/avg-price stat tiles below the chart |

The chart shows a 24h+ timeline with the price curve drawn on top and the scheduled ON blocks highlighted as green bands. A dashed vertical line marks the current time. Hovering (or tapping on mobile) shows exact price + time for any quarter-hour.

A status pill near the title shows what's driving the pump:

- **Idle** — pump is off
- **On — schedule** (green) — current time falls in a scheduled price-based block
- **On — solar** (yellow ☀) — solar surplus is overriding the schedule; the current block highlight and the "now" line both turn yellow to match

A refresh icon in the header triggers a manual recalculation (same as the button entity / `pool_pump_scheduler.recalculate` service). It briefly spins to confirm the click was received.

### If you're in Lovelace YAML mode

Automatic resource registration only works in storage mode. In YAML mode, add this to your `ui-lovelace.yaml`:

```yaml
resources:
  - url: /pool_pump_scheduler/pool-pump-scheduler-card.js
    type: module
```

The static file is always available at that URL regardless of mode.

## Solar surplus overlay (v1.3.0)

If your house has solar panels, the pump can opportunistically run on excess production rather than from the grid. The integration takes two power sensors — one for solar production, one for house consumption — and computes live surplus as `production − consumption`. When that surplus is at least the pump's draw (you configure the threshold) for `surplus_hysteresis_seconds` continuously, the pump turns on. When surplus drops below the threshold for the same window, control returns to the price-based schedule.

Enable it in Settings → Devices & Services → Pool Pump Scheduler → Configure:

| Setting | Description |
|---|---|
| Run from solar surplus when available | Master toggle |
| Solar production sensor | Power sensor (W) for solar output |
| House consumption sensor | Power sensor (W) for total house draw |
| Pump power draw (W) | The pump's typical wattage — the surplus threshold |
| Surplus hysteresis (s) | Required continuous time above/below threshold before flipping state (default 120 s) |

Both sensors must report watts (`W`), not kilowatts. If either sensor goes unavailable, the overlay falls back to "no surplus" and the price schedule resumes. The `binary_sensor.<name>_should_run` entity exposes `solar_active` and `solar_overlay_enabled` attributes so you can see what's driving the pump from your dashboard.

Note: solar-driven runtime does **not** reduce the price-based daily target — they're additive. On a sunny day the pump may run more than your configured `runtime_hours`, which is normal and free.

## Service

- `pool_pump_scheduler.recalculate` — manually trigger a recalculation. Optionally takes an `entry_id` parameter; if omitted, all configured instances are recalculated.

## Algorithm notes

The scheduler uses dynamic programming to pick a set of non-overlapping blocks that:

1. Together cover at least `runtime_hours` of time
2. Each block is at least `min_block_minutes` long
3. (Optionally) every slot is below the price ceiling
4. Total cost is minimized

This guarantees a true optimum given the constraints, not a greedy approximation. On 96 quarter-hour slots (one day) it completes in well under a second.

Today and tomorrow are solved independently. Today's plan uses only the slots from "now" through end-of-day, with the daily runtime scaled pro-rata to the remaining hours; tomorrow's plan uses the full 24-hour window with the full runtime. Both are kept in memory and the "should run" sensor checks both, which makes restarts and the midnight rollover seamless.

If the constraints are unsatisfiable (e.g. ceiling too low, or block length too large to fit the required runtime in available data), the integration logs a warning and leaves the schedule empty — meaning the pump won't be turned on. Loosen the ceiling or shorten the block length to recover.

## Alternative: plain entities card

If you prefer not to use the custom card, you can build a basic dashboard from the entities directly:

```yaml
type: entities
title: Pool Pump
entities:
  - entity: binary_sensor.pool_pump_should_run
    name: Currently running
  - entity: sensor.pool_pump_next_change
    name: Next change
  - entity: sensor.pool_pump_scheduled_cost
    name: Scheduled cost
  - entity: sensor.pool_pump_scheduled_average_price
    name: Average price
  - entity: switch.shelly_pool_pump
    name: Pump (manual override)
```

## License

MIT.
