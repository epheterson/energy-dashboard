# Energy Dashboard

Real-time energy monitoring dashboard for [eGauge](https://www.egauge.net/) meters. Tracks per-circuit power consumption, calculates costs by TOU (Time-of-Use) period, and optionally integrates with Home Assistant for solar/battery source tracking.

![Dashboard Screenshot](https://img.shields.io/badge/status-active-green)

## Features

- **Live Power Flow** — WebSocket-powered 5-second updates showing per-circuit watts
- **TOU Cost Tracking** — Automatic rate calculation based on your utility's peak/off-peak schedule
- **Hourly & Historical Charts** — Stacked bar charts by TOU period with click-to-drilldown
- **Circuit Breakdown** — Sortable table with today's kWh and cost per circuit
- **Optimization Opportunities** — Identifies circuits with high peak usage and potential savings
- **Weekly Email Reports** — Automated Monday morning cost summaries
- **Solar + Battery Integration** (optional) — Home Assistant Powerwall data for 3-way source attribution (solar/battery/grid)

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

## Solar Integration

When `solar.enabled: true` in config.yml, the dashboard:

- Shows live solar generation, battery state, and grid flow
- Calculates actual grid cost (vs full-rate cost without solar)
- Displays source mix bars (solar/battery/grid) per circuit
- Tracks savings and self-sufficiency percentage
- Includes solar data in weekly email reports

When disabled, all solar UI elements are hidden and no Home Assistant calls are made.

## Architecture

- **Backend**: FastAPI (Python) with async eGauge polling + WebSocket broadcast
- **Frontend**: Single-page vanilla HTML/JS with Chart.js
- **Data**: SQLite for historical storage, in-memory cache for live data
- **Container**: Docker with mounted config and persistent data volume

## License

MIT
