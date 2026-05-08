# Energy Dashboard

Real-time home energy monitoring dashboard built around [eGauge](https://www.egauge.net/) circuit monitors and [Tesla Powerwall](https://www.tesla.com/powerwall) via Home Assistant. Tracks per-circuit power consumption, calculates costs by TOU (Time-of-Use) period, and provides full solar/battery/grid source attribution.

Works great without solar too — set `solar.enabled: false` for a grid-only cost tracking dashboard.

![Dashboard Screenshot](https://img.shields.io/badge/status-active-green)

## Features

- **Live Power Flow** — WebSocket-powered 5-second updates showing per-circuit watts
- **TOU Cost Tracking** — Automatic rate calculation based on your utility's peak/off-peak schedule
- **Hourly & Historical Charts** — Stacked bar charts by TOU period with click-to-drilldown
- **Circuit Breakdown** — Sortable table with today's kWh and cost per circuit
- **Solar + Battery Tracking** — Live solar generation, battery charge/discharge, grid import/export via Home Assistant + Tesla Powerwall
- **Battery Economics** — Cost per kWh, charge source breakdown (solar vs grid), round-trip efficiency, savings vs peak rates
- **Optimization Opportunities** — Identifies circuits with high peak usage and potential savings
- **Weekly Email Reports** — Automated Monday morning cost summaries

## Quick Start

```bash
git clone https://github.com/epheterson/energy-dashboard
cd energy-dashboard
cp config.example.yml config.yml   # Edit rates for your utility
cp .env.example .env               # Add your eGauge credentials
docker compose up -d               # Dashboard at http://localhost:8400
```

## Configuration

### config.yml

Defines your utility rates, TOU schedule, and optional solar integration:

| Section | Purpose |
|---------|---------|
| `rates` | $/kWh by season and TOU period |
| `tou_periods` | Which hours are peak, part-peak, off-peak |
| `solar` | Home Assistant integration (set `enabled: false` if no solar) |
| `alerts` | Threshold for high peak usage warnings |

### .env

Contains credentials (never committed to git):

| Variable | Required | Description |
|----------|----------|-------------|
| `EGAUGE_URL` | Yes | Your eGauge device URL |
| `EGAUGE_USER` | Yes | eGauge username |
| `EGAUGE_PASSWORD` | Yes | eGauge password |
| `HA_URL` | If solar | Home Assistant URL |
| `HA_TOKEN` | If solar | HA long-lived access token |
| `EMAIL_ENABLED` | No | Set `true` for weekly email reports |
| `SMTP_*` | If email | SMTP server settings |

## Solar + Battery Integration

Designed around the Tesla Powerwall + Home Assistant integration. When `solar.enabled: true` in config.yml, the dashboard pulls real-time data from HA sensors:

- **Live power flow** — Solar generation, battery charge/discharge rate, grid import/export
- **Source attribution** — Per-hour and per-circuit breakdown of solar vs battery vs grid
- **Battery economics** — Cost per kWh stored, charge source (solar vs grid), round-trip efficiency
- **Savings tracking** — Dollar savings from solar + battery vs full grid rates, self-sufficiency %
- **Weekly reports** — Email summaries include solar/battery data

When `solar.enabled: false`, all solar/battery UI is cleanly hidden — you get a pure grid cost tracking dashboard with no Home Assistant dependency.

## Architecture

- **Backend**: FastAPI (Python) with async eGauge polling + WebSocket broadcast
- **Frontend**: Single-page vanilla HTML/JS with Chart.js
- **Data**: SQLite for historical storage, in-memory cache for live data
- **Container**: Docker with mounted config and persistent data volume

## License

MIT

## Step-by-step setup

### 1. Prerequisites

- Docker + Docker Compose
- eGauge meter on your network
- (Optional) Home Assistant + Tesla Fleet integration — required for solar/Powerwall features

### 2. Clone + configure

```bash
git clone https://github.com/epheterson/energy-dashboard
cd energy-dashboard
cp .env.example .env
cp config.example.yml config.yml
```

### 3. Edit `.env`

Minimum:
```bash
EGAUGE_URL=https://your-egauge.egaug.es
EGAUGE_USER=owner
EGAUGE_PASSWORD=your_password
HA_URL=http://homeassistant.local:8123     # optional (solar)
HA_TOKEN=long_lived_token                  # optional (solar)
CONTACT_EMAIL=you@example.com              # for Open-Meteo User-Agent
```

### 4. Edit `config.yml`

- **Rates**: replace example PG&E values with your utility plan
- **TOU periods**: hours match your utility schedule
- **Solar**: set `enabled: true`, map your HA entity IDs (see below for discovery)
- **EV**: set `enabled: true`, fill in vehicle entity IDs + your `gas_baseline_mpg`
- **Billing**: set `nem_version` (2 or 3), CCA if applicable, lat/lon, solar capacity

### 5. Edit `docker-compose.yml`

The HA mount line points at a specific path — **change it to YOUR HA `.storage` directory**:
```yaml
- /path/to/your/homeassistant/.storage/core.config_entries:/app/ha_config_entries.json:ro
```
Comment it out entirely if you do not have HA Tesla Fleet integration.

### 6. First boot

```bash
docker compose up -d
./validate.sh                  # tier-by-tier health check
```

You should see all green. Any red `✗` tells you exactly which tier is broken.

### 7. Seed prediction history (one-time, if Powerwall + cap recommendation)

```bash
curl http://localhost:8400/api/battery/recommended-cap
```

This generates the first prediction. After that, the in-process daily scheduler (23:50 PT) backfills yesterday's actuals nightly. Auto-tune calibrates over ~30 days.

## Discovering your HA entity IDs

Open HA → **Developer Tools → States** → search keywords:
- `solar` → solar power, generation
- `grid` → grid import/export
- `battery` or `powerwall` → battery state, SOC
- `tesla` or your car name → vehicle entities

Copy the `entity_id` column (looks like `sensor.solar_power_plant_solar_power`) and paste into the matching field in `config.yml`. Tesla Fleet integration auto-creates the standard names.

If you have Enphase, SolarEdge, LG Chem, etc. — just use whatever entity IDs YOUR HA exposes. The dashboard is brand-agnostic.

## Discovering your Tesla site_id

HA → Settings → Devices & Services → your Tesla Fleet integration → click any Powerwall device → copy the integer "Energy Site ID". Paste into `config.yml` under `billing.tesla.site_id`. Token is auto-pulled from HA — no manual refresh ever.

## First-run validation

`./validate.sh` and `/api/health` give tier-by-tier status:
```
✓ All tiers healthy
  ✓ dashboard            ok
  ✓ egauge               ok
  ✓ home_assistant       ok
  ✓ tesla                ok
  ✓ forecast             ok (14 days of history)
```

If something is red, check container logs:
```bash
docker compose logs -f --tail=50
```

## Bill estimate accuracy — known limitations

The dashboard's calculated `total_bill` matches actual PG&E bills within **~25%** for typical months. Validated: actual $172.64 vs calculated ~$128 (-26%). Remaining gap = PG&E line items the simplified model doesn't include yet:

- **PCIA** (Power Charge Indifference Adjustment) — 5–15%, varies monthly
- **Franchise Fees** — 0.5–1%
- **Climate Credit** — twice/year credit (~$50-200)
- **Taxes**

For "is my dashboard tracking energy correctly?" → trust the kWh numbers (validated 100% accurate vs Tesla and eGauge directly).
For "will my next PG&E bill be exactly $X?" → directional indicator only, not bill-perfect.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Tesla data stale (>6h old) | HA's Tesla Fleet integration broke — re-auth in HA Settings → Integrations |
| `/api/battery/prediction-history` returns `{"history": []}` | hit `/api/battery/recommended-cap` once to seed |
| `validate.sh` shows egauge unreachable | check EGAUGE_URL hostname + credentials in .env |
| Solar shows 0 always | HA token expired or `solar.ha_entities` IDs wrong — check HA Developer Tools |
| Email never arrives | use SMTP App Password not regular; enable 2FA on Gmail |

## Updating

Source is COPY'd into the Docker image at build time:
```bash
docker compose up -d --build
```
A simple `docker restart` does NOT pick up code changes.

## Optional: auto-enforce the cap via Home Assistant

The dashboard *recommends* a daily grid-charge cap based on tomorrow's solar forecast — but doesn't push the setting to your Powerwall directly. To close the loop, add a REST sensor + automation in your HA config:

### 1. Poll the dashboard's recommendation (HA `configuration.yaml`)

```yaml
# Predictive battery charge cap from energy dashboard
rest:
  - resource: http://your-dashboard-host:8400/api/battery/recommended-cap
    scan_interval: 3600  # Update hourly
    sensor:
      - name: "Recommended Grid Charge Cap"
        unique_id: recommended_grid_charge_cap
        value_template: "{{ value_json.recommended_cap }}"
        unit_of_measurement: "%"
        json_attributes:
          - solar_prediction
          - solar_to_battery_kwh
          - reasoning
```

### 2. Stop grid charging when SOC reaches the cap (HA `automations.yaml`)

```yaml
- id: grid_charge_cap_enforce
  alias: Grid Charge Cap (Weather-Predictive)
  description: >
    Stop grid charging when SOC reaches the weather-predicted cap.
    Cap is set by energy dashboard based on solar forecast.
  triggers:
    - trigger: template
      value_template: >
        {% set soc = states("sensor.solar_power_plant_percentage_charged") | float(0) %}
        {% set cap = states("sensor.recommended_grid_charge_cap") | float(80) %}
        {{ soc > cap }}
  conditions:
    - condition: state
      entity_id: switch.solar_power_plant_allow_charging_from_grid
      state: 'on'
  actions:
    - action: switch.turn_off
      target:
        entity_id: switch.solar_power_plant_allow_charging_from_grid
  mode: single
```

### How the loop works

1. Powerwall is configured (in Tesla app) to charge from grid during off-peak hours
2. HA REST sensor polls dashboard hourly → reads recommended cap (e.g. 54%)
3. Automation watches SOC; when it crosses the cap, turns OFF grid charging
4. Solar then fills the rest during the day → battery hits 100% later in the afternoon
5. Maximizes self-consumption + minimizes grid charge cost

Replace entity IDs (`solar_power_plant_*`) with your own Powerwall/HA entity names.
