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
