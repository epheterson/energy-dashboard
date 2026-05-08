"""
Predictive battery charge cap based on weather forecast.

Uses NWS (National Weather Service) API for sky cover forecast
and Tesla historical data for solar production baselines.
No API key needed — NWS is free and public.
"""

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "data"

# NWS gridpoint for Danville, CA (37.8216, -121.9999)
NWS_GRIDPOINT_URL = "https://api.weather.gov/gridpoints/MTR/100,104"

# Monthly peak solar production (kWh/day) from Tesla historical data
# These represent clear-sky potential for a 9.86 kW system in Danville
# Will be refined as more data accumulates
MONTHLY_PEAK_SOLAR = {
    1: 18.7,  # January
    2: 23.8,  # February
    3: 31.4,  # March
    4: 35.0,  # April (estimated)
    5: 38.0,  # May (estimated)
    6: 40.0,  # June (estimated — near solstice peak)
    7: 39.0,  # July (estimated)
    8: 36.0,  # August (estimated)
    9: 32.0,  # September (estimated)
    10: 26.0,  # October (estimated)
    11: 20.0,  # November (estimated)
    12: 16.0,  # December (estimated)
}

BATTERY_CAPACITY_KWH = 40.5
BACKUP_RESERVE_PCT = 20  # Always keep 20% for backup


def fetch_tomorrow_cloud_cover():
    """Fetch tomorrow's daytime sky cover from NWS.

    Returns average cloud cover percentage (0-100) for daylight hours,
    or None if forecast unavailable.
    """
    try:
        cmd = [
            "curl",
            "-s",
            "-A",
            "EnergyDashboard/1.0 (epheterson@gmail.com)",
            NWS_GRIDPOINT_URL,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        data = json.loads(result.stdout)
    except Exception as e:
        print(f"Warning: NWS forecast fetch failed: {e}")
        return None

    sky_cover = data.get("properties", {}).get("skyCover", {}).get("values", [])
    if not sky_cover:
        return None

    # Get tomorrow's date
    tomorrow = (datetime.now() + timedelta(days=1)).date()

    # Filter to daylight hours (8am-5pm) tomorrow
    daytime_covers = []
    for entry in sky_cover:
        valid_time = entry.get("validTime", "")
        try:
            # NWS uses ISO 8601 duration format: "2026-03-31T08:00:00+00:00/PT1H"
            dt_str = valid_time.split("/")[0]
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            # Convert to Pacific time (handles PST/PDT automatically)
            try:
                from zoneinfo import ZoneInfo

                local_dt = dt.astimezone(ZoneInfo("America/Los_Angeles"))
            except ImportError:
                local_dt = dt - timedelta(hours=7)  # Fallback: PDT

            if local_dt.date() == tomorrow and 8 <= local_dt.hour <= 17:
                daytime_covers.append(entry.get("value", 50))
        except (ValueError, TypeError):
            continue

    if not daytime_covers:
        return None

    return sum(daytime_covers) / len(daytime_covers)


def predict_solar_production(cloud_cover_pct=None):
    """Predict tomorrow's solar production in kWh.

    Args:
        cloud_cover_pct: Average daytime cloud cover (0-100).
                        If None, fetches from NWS.

    Returns dict with prediction details.
    """
    if cloud_cover_pct is None:
        cloud_cover_pct = fetch_tomorrow_cloud_cover()

    tomorrow = datetime.now() + timedelta(days=1)
    month = tomorrow.month
    peak_solar = MONTHLY_PEAK_SOLAR.get(month, 25.0)

    if cloud_cover_pct is not None:
        # Cloud cover reduces solar production
        # 0% clouds = 100% of peak, 100% clouds = ~15% of peak
        cloud_factor = 1 - (cloud_cover_pct / 100 * 0.85)
        predicted_kwh = peak_solar * cloud_factor
        forecast_available = True
    else:
        # No forecast — use 70% of peak as conservative estimate
        predicted_kwh = peak_solar * 0.70
        cloud_cover_pct = 30  # Assume partly cloudy
        forecast_available = False

    return {
        "date": tomorrow.strftime("%Y-%m-%d"),
        "month": month,
        "peak_solar_kwh": round(peak_solar, 1),
        "cloud_cover_pct": round(cloud_cover_pct, 0),
        "predicted_solar_kwh": round(predicted_kwh, 1),
        "forecast_available": forecast_available,
    }


def _load_history():
    """Load prediction history for auto-tuning."""
    history_file = CACHE_DIR / "cap_history.json"
    if history_file.exists():
        try:
            return json.loads(history_file.read_text())
        except Exception:
            pass
    return []


def _save_history(history):
    """Save prediction history."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    history_file = CACHE_DIR / "cap_history.json"
    # Keep last 90 days
    history = history[-90:]
    history_file.write_text(json.dumps(history, indent=2))


def _get_yesterday_fill_hour():
    """Check Tesla SOC data for when battery hit 100% yesterday.

    Reads from the Tesla cache (already fetched by billing endpoint).
    Returns the hour (float) or None if battery never hit 100%.
    """
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    cache_file = CACHE_DIR / "tesla_energy_30d.json"
    if not cache_file.exists():
        return None

    try:
        data = json.loads(cache_file.read_text())
        # Tesla cache has daily data but not SOC — need the SOC endpoint
        # For now, check if we already recorded it
        history = _load_history()
        for h in reversed(history):
            if h.get("date") == yesterday and h.get("actual_full_hour") is not None:
                return h["actual_full_hour"]
    except Exception:
        pass
    return None


def _auto_tune_ratio():
    """Auto-tune the solar-to-battery ratio from historical data.

    Looks at recent days where we have both prediction and actual fill data.
    Only considers SUNNY days (cloud < 50%) — cloudy days aren't useful for tuning.
    Target: battery hits 100% between 1pm and 3pm.

    Returns the tuned ratio, or the default if not enough data.
    """
    history = _load_history()
    if len(history) < 3:
        return 0.50  # Default until we have enough data

    # Only tune on sunny days — cloudy days have unpredictable solar
    recent = [
        h
        for h in history[-14:]
        if h.get("actual_full_hour") is not None and h.get("cloud_cover", 100) < 50
    ]
    if len(recent) < 3:
        return 0.50

    # Calculate average fill hour on sunny days and adjust ratio
    avg_full_hour = sum(h["actual_full_hour"] for h in recent) / len(recent)
    current_ratio = recent[-1].get("ratio_used", 0.50)

    # Target: fill at hour 14 (2pm)
    if avg_full_hour < 12.5:
        new_ratio = min(0.70, current_ratio + 0.05)
    elif avg_full_hour < 13.5:
        new_ratio = min(0.70, current_ratio + 0.02)
    elif avg_full_hour > 15.5:
        new_ratio = max(0.30, current_ratio - 0.03)
    elif avg_full_hour > 14.5:
        new_ratio = max(0.30, current_ratio - 0.01)
    else:
        new_ratio = current_ratio

    return round(new_ratio, 3)


def predict_loads(lookback_days=14, sunrise_hour=6, sunset_hour=19):
    """Predict tomorrow daytime + overnight loads, separating EV charging.

    EV charging draws from grid directly (Powerwall does not serve EV by
    default). So overnight EV load should NOT count toward the battery
    floor — battery only needs to cover non-EV base load.

    Returns dict with:
      daytime_kwh         — total daytime consumption (for solar excess calc)
      overnight_kwh       — total overnight consumption (informational)
      overnight_base_kwh  — overnight WITHOUT EV (drives floor calc)
      overnight_ev_kwh    — overnight EV charging (informational)
    """
    import sqlite3, json
    from pathlib import Path
    from datetime import datetime, timedelta

    db = Path(__file__).parent / "data" / "egauge_history.db"
    if not db.exists():
        return {"daytime_kwh": 25.0, "overnight_kwh": 15.0, "overnight_base_kwh": 10.0, "overnight_ev_kwh": 5.0, "source": "fallback_defaults"}

    cutoff_ts = int((datetime.now() - timedelta(days=lookback_days)).timestamp())
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        """SELECT hc.hour, hc.register_data FROM hourly_consumption hc
           WHERE hc.timestamp >= ?
           AND hc.timestamp = (SELECT MAX(timestamp) FROM hourly_consumption
                               WHERE date = hc.date AND hour = hc.hour)""",
        (cutoff_ts,),
    ).fetchall()
    conn.close()

    if not rows:
        return {"daytime_kwh": 25.0, "overnight_kwh": 15.0, "overnight_base_kwh": 10.0, "overnight_ev_kwh": 5.0, "source": "no_history"}

    by_hour_total = {h: [] for h in range(24)}
    by_hour_ev = {h: [] for h in range(24)}
    for hour, register_json in rows:
        try:
            registers = json.loads(register_json)
        except Exception:
            continue
        total = registers.get("Usage [kWh]")
        if total is None or total == 0:
            total = sum(
                v for k, v in registers.items()
                if isinstance(v, (int, float)) and v > 0
                and "Generation" not in k and "Total Power" not in k
            )
        ev = registers.get("EV Charger [kWh]", 0)
        # eGauge sometimes stores EV as negative (consumption convention varies)
        ev = abs(ev) if isinstance(ev, (int, float)) else 0
        if total is not None and total >= 0:
            by_hour_total[hour].append(total)
            by_hour_ev[hour].append(ev)

    avg_total = {h: (sum(v)/len(v)) if v else 0.0 for h, v in by_hour_total.items()}
    avg_ev = {h: (sum(v)/len(v)) if v else 0.0 for h, v in by_hour_ev.items()}

    daytime_kwh = sum(avg_total[h] for h in range(sunrise_hour, sunset_hour))
    overnight_total = sum(avg_total[h] for h in list(range(sunset_hour, 24)) + list(range(0, sunrise_hour)))
    overnight_ev = sum(avg_ev[h] for h in list(range(sunset_hour, 24)) + list(range(0, sunrise_hour)))
    overnight_base = max(0, overnight_total - overnight_ev)

    return {
        "daytime_kwh": round(daytime_kwh, 1),
        "overnight_kwh": round(overnight_total, 1),
        "overnight_base_kwh": round(overnight_base, 1),
        "overnight_ev_kwh": round(overnight_ev, 1),
        "source": f"{lookback_days}d_avg",
        "lookback_days": lookback_days,
    }


def recommend_charge_cap():
    """Calculate recommended grid charge cap to maximize self-sufficiency.

    Goal: battery hits ~99% at sunset (NOT 100% — leaves headroom for solar
    all day). Minimize export. Minimize overnight grid import.

    Math:
      solar_excess = max(0, predicted_solar - predicted_daytime_load)
      battery_in_from_solar = solar_excess * efficiency
      target_cap = 0.99 - battery_in_from_solar / capacity
      floor_cap = (predicted_overnight_load / efficiency) / capacity + safety_margin
      recommended_cap = clamp(max(target_cap, floor_cap), 0.05, 1.0)

    Returns dict with recommendation + reasoning + ALL inputs for transparency.
    """
    from datetime import datetime
    from config import get_config

    cfg = get_config()
    efficiency = cfg.get("solar", {}).get("battery_efficiency", 0.90)
    safety_margin = 0.05  # 5% buffer above predicted overnight load

    prediction = predict_solar_production()
    predicted_solar = prediction["predicted_solar_kwh"]
    loads = predict_loads()
    daytime_load = loads["daytime_kwh"]
    overnight_load = loads["overnight_kwh"]
    overnight_base_load = loads.get("overnight_base_kwh", overnight_load)

    # How much solar is left over after covering daytime load? That goes to battery.
    solar_excess = max(0, predicted_solar - daytime_load)
    battery_in = solar_excess * efficiency
    fill_pct_from_solar = battery_in / BATTERY_CAPACITY_KWH

    # Target cap: battery hits 0.99 at sunset
    # End-of-day target: 1.0 = zero-export goal. Configurable via solar.target_end_of_day_pct.
    # Caveat: targeting 1.0 means battery may hit 100% early if forecast underpredicts solar
    # (then exports excess). 0.99 leaves a small buffer at cost of always exporting ~1% capacity.
    target_eod = cfg.get("solar", {}).get("target_end_of_day_pct", 1.0)
    target_cap_pct = target_eod - fill_pct_from_solar

    # Floor cap: battery must have enough for overnight + safety
    # Floor uses BASE overnight load only — EV charging draws from grid directly,
    # not from Powerwall (default Tesla behavior).
    min_cap_pct = (overnight_base_load / efficiency) / BATTERY_CAPACITY_KWH + safety_margin

    chosen_cap_pct = max(min_cap_pct, target_cap_pct)
    chosen_cap_pct = min(1.0, max(0.05, chosen_cap_pct))
    recommended_cap = int(chosen_cap_pct * 100)

    # Predicted outcome
    predicted_export_kwh = max(
        0, solar_excess - (BATTERY_CAPACITY_KWH * (1 - chosen_cap_pct)) / efficiency
    )
    predicted_overnight_grid_import_kwh = max(
        0, overnight_load - chosen_cap_pct * BATTERY_CAPACITY_KWH * efficiency
    )

    # Decision reasoning
    if min_cap_pct > target_cap_pct:
        reason_summary = "FLOOR (overnight load drives cap)"
    else:
        reason_summary = "TARGET (solar excess fills battery to 99% by sunset)"

    result = {
        "recommended_cap": recommended_cap,
        "solar_prediction": prediction,
        "predicted_daytime_load_kwh": daytime_load,
        "predicted_overnight_load_kwh": overnight_load,
        "solar_excess_kwh": round(solar_excess, 1),
        "battery_in_from_solar_kwh": round(battery_in, 1),
        "predicted_export_kwh": round(predicted_export_kwh, 1),
        "predicted_overnight_grid_import_kwh": round(
            predicted_overnight_grid_import_kwh, 1
        ),
        "target_cap_pct": round(target_cap_pct * 100, 1),
        "floor_cap_pct": round(min_cap_pct * 100, 1),
        "reasoning_mode": reason_summary,
        "battery_efficiency": efficiency,
        "battery_capacity_kwh": BATTERY_CAPACITY_KWH,
        "reasoning": (
            f"Tomorrow: {prediction['cloud_cover_pct']:.0f}% cloud, "
            f"~{predicted_solar:.0f} kWh solar predicted. "
            f"Daytime load ~{daytime_load:.0f} kWh, overnight load ~{overnight_load:.0f} kWh. "
            f"Solar excess ~{solar_excess:.0f} kWh → ~{battery_in:.0f} kWh into battery (after {efficiency*100:.0f}% rt). "
            f"Cap target: {target_cap_pct*100:.0f}% (hit 99% at sunset). "
            f"Cap floor: {min_cap_pct*100:.0f}% (overnight need). "
            f"Recommend: {recommended_cap}% [{reason_summary}]. "
            f"Predicted export: {predicted_export_kwh:.1f} kWh. "
            f"Predicted overnight grid import: {predicted_overnight_grid_import_kwh:.1f} kWh."
        ),
    }

    # Log decision for auto-tuning + audit
    # UPSERT — update existing entry for this date, dont append duplicates.
    # recommend_charge_cap() may be called many times per day (every page load,
    # HA REST sensor poll, etc.) — we want one entry per date, not 100.
    history = _load_history()
    target_date = prediction.get("date")
    new_data = {
        "date": target_date,
        "predicted_solar": predicted_solar,
        "predicted_daytime_load": daytime_load,
        "predicted_overnight_load": overnight_load,
        "cloud_cover": prediction.get("cloud_cover_pct"),
        "recommended_cap": recommended_cap,
        "target_cap_pct": round(target_cap_pct * 100, 1),
        "floor_cap_pct": round(min_cap_pct * 100, 1),
        "reasoning_mode": reason_summary,
        "logged_at": datetime.now().isoformat(),
    }
    found = False
    for i, e in enumerate(history):
        if e.get("date") == target_date:
            # Preserve actuals (filled by backfill scheduler), update predictions
            for k, v in new_data.items():
                history[i][k] = v
            found = True
            break
    if not found:
        new_data["actual_full_hour"] = None
        new_data["actual_solar"] = None
        new_data["actual_export_kwh"] = None
        new_data["actual_grid_import_kwh"] = None
        history.append(new_data)
    _save_history(history)

    return result



def record_actual_fill(date, full_hour, actual_solar_kwh=None):
    """Record when battery actually hit 100% for auto-tuning feedback.

    Called by the energy dashboard with Tesla SOC data to close the loop.
    """
    history = _load_history()
    for entry in reversed(history):
        if entry.get("date") == date:
            entry["actual_full_hour"] = full_hour
            if actual_solar_kwh is not None:
                entry["actual_solar"] = actual_solar_kwh
            break
    _save_history(history)
