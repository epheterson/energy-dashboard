# Home Assistant package — Energy Dashboard integration

Drop-in HA package that wires the dashboard's cap recommendation directly into your Powerwall control loop.

## What it does

1. **REST sensor** polls the dashboard's `/api/battery/recommended-cap` endpoint hourly
2. **Automation** watches your Powerwall SOC; when SOC crosses the recommended cap, turns OFF the "Allow Charging from Grid" switch
3. Solar then fills the rest of the way during the day → battery hits 100% in the afternoon → maximum self-consumption + minimum grid charge cost

## Install (one-time)

### 1. Enable HA packages

Add this to your HA `configuration.yaml` (if you don't already have packages enabled):

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Create the directory if it doesn't exist:
```bash
mkdir -p <your-ha-config-dir>/packages
```

### 2. Drop in the package

Copy `grid_charge_cap.yaml` into your HA `packages/` directory:

```bash
cp grid_charge_cap.yaml <your-ha-config>/packages/
```

### 3. Edit three things in the file

Open `grid_charge_cap.yaml` and edit:

1. **REST resource URL** — point at where your dashboard is running (default `http://localhost:8400` only works if HA is on the same host)
2. **SOC sensor entity ID** — replace `sensor.solar_power_plant_percentage_charged` with yours
3. **Grid-charge switch entity ID** — replace `switch.solar_power_plant_allow_charging_from_grid` with yours

Find your Powerwall entity IDs: HA → **Developer Tools → States** → search "powerwall" or "solar".

### 4. Restart HA

```bash
docker compose restart homeassistant
# or via UI: Settings → System → Restart
```

### 5. Verify

- HA → **Settings → Devices & Services → Entities** → search "Recommended Grid Charge Cap" → should show current % value
- HA → **Settings → Automations** → "Grid Charge Cap (Weather-Predictive)" → should be enabled

## How it composes with the dashboard

```
Open-Meteo forecast → dashboard /api/battery/recommended-cap (computed)
                            ↓ HA polls hourly
                     sensor.recommended_grid_charge_cap (HA state)
                            ↓ automation watches
              Powerwall SOC > cap → switch.allow_charging_from_grid OFF
                            ↓ next morning
                     overnight grid charge stopped at cap
                            ↓ during day
                     solar fills the gap → battery 100% by afternoon
                            ↓ feeds back
              Tesla SOE timestamp → cap_history → auto-tune coefficient
                            ↓
                     tomorrow's prediction calibrates
```

## Without HA

If you don't run HA, the dashboard's `/api/battery/recommended-cap` is still useful as an advisory — set the cap manually in the Tesla app each morning. Future work: direct Tesla Fleet API write from the dashboard (no HA required) — see GitHub issues.
