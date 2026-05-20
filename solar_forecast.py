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

# NWS gridpoint cache. Resolved from config.billing.lat/lon on first use,
# falling back to a Bay Area gridpoint for legacy installs that lack
# billing config. Override directly by setting config.solar.nws_gridpoint_url
# (e.g., "https://api.weather.gov/gridpoints/MTR/100,104").
_DEFAULT_NWS_GRIDPOINT_URL = "https://api.weather.gov/gridpoints/MTR/100,104"
_resolved_gridpoint_url = None


def _nws_gridpoint_url():
    """Resolve the NWS gridpoint forecast URL.

    Priority: explicit config override → lat/lon-derived (one-time NWS
    /points lookup, cached) → hardcoded fallback.
    """
    global _resolved_gridpoint_url
    if _resolved_gridpoint_url:
        return _resolved_gridpoint_url
    try:
        from config import get_config

        cfg = get_config()
        override = cfg.get("solar", {}).get("nws_gridpoint_url")
        if override:
            _resolved_gridpoint_url = override
            return _resolved_gridpoint_url
        lat = cfg.get("billing", {}).get("lat")
        lon = cfg.get("billing", {}).get("lon")
        if lat and lon:
            points_url = f"https://api.weather.gov/points/{lat},{lon}"
            ua = _nws_user_agent()
            result = subprocess.run(
                ["curl", "-s", "-A", ua, points_url],
                capture_output=True,
                text=True,
                timeout=10,
            )
            data = json.loads(result.stdout)
            forecast_url = data.get("properties", {}).get("forecastGridData")
            if forecast_url:
                _resolved_gridpoint_url = forecast_url
                return _resolved_gridpoint_url
    except Exception as e:
        print(f"Warning: NWS gridpoint resolution failed ({e}); using fallback")
    _resolved_gridpoint_url = _DEFAULT_NWS_GRIDPOINT_URL
    return _resolved_gridpoint_url


def _nws_user_agent():
    """NWS requires a User-Agent with a contact email. Reads from CONTACT_EMAIL
    env var, falls back to a generic identifier.
    """
    import os

    email = (
        os.environ.get("CONTACT_EMAIL")
        or os.environ.get("EMAIL_FROM")
        or "contact@example.com"
    )
    return f"EnergyDashboard/1.0 ({email})"


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

BATTERY_CAPACITY_KWH = (
    40.5  # Default fallback; respects config.solar.battery_capacity_kwh
)
BACKUP_RESERVE_PCT = 20  # Default fallback; respects config.solar.backup_reserve_pct


def _battery_capacity_kwh():
    """Battery storage capacity (kWh). Reads from config, falls back to 40.5
    (3× Powerwall 2). Single Powerwall 2 = 13.5 kWh nameplate.
    """
    try:
        from config import get_config

        v = get_config().get("solar", {}).get("battery_capacity_kwh")
        if v and v > 0:
            return float(v)
    except Exception:
        pass
    return BATTERY_CAPACITY_KWH


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
            _nws_user_agent(),
            _nws_gridpoint_url(),
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


def _observed_peak_solar(month):
    """Back-calculate clear-sky peak production from recent sunny-day actuals.

    For each recent day with both actual_solar and cloud_cover, invert the
    cloud-factor formula to derive what clear-sky peak would explain it:
        implied_peak = actual_solar / (1 - cloud_pct/100 * 0.85)

    Uses the target month plus adjacent months (similar sun angle). Returns
    median of qualifying days, or None if fewer than 5 usable samples.
    """
    target_months = {month, (month - 2) % 12 + 1, month % 12 + 1}
    history = _load_history()
    implied = []
    for h in history[-45:]:
        actual = h.get("actual_solar")
        cloud = h.get("cloud_cover")
        if actual is None or cloud is None:
            continue
        try:
            d_month = datetime.strptime(h["date"], "%Y-%m-%d").month
        except Exception:
            continue
        if d_month not in target_months:
            continue
        cloud = float(cloud)
        actual = float(actual)
        # Skip noisy samples: heavy clouds, rain, or trivial production
        if cloud > 50 or actual < 5 or h.get("precip_mm", 0) > 0.5:
            continue
        cloud_factor = 1 - (cloud / 100 * 0.85)
        if cloud_factor < 0.3:
            continue
        implied.append(actual / cloud_factor)

    # Threshold of 3 (was 5): the cap_history can get wiped by concurrent-write
    # races (mitigated as of 2026-05-11) which leaves us with only the audit log
    # to rebuild from. 3 sunny-day medians is enough signal; if the median is way
    # off we'll re-evaluate next session.
    if len(implied) < 3:
        return None
    implied.sort()
    return round(implied[len(implied) // 2], 1)


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

    # Prefer observed peak from recent history; fall back to seasonal table.
    # The static table consistently under-predicts in spring/summer (observed
    # +26-49% error in May 2026), so observed-when-available > hardcoded.
    observed_peak = _observed_peak_solar(month)
    table_peak = MONTHLY_PEAK_SOLAR.get(month, 25.0)
    peak_solar = observed_peak if observed_peak is not None else table_peak
    peak_source = "observed" if observed_peak is not None else "table"

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
        "peak_source": peak_source,
        "table_peak_kwh": round(table_peak, 1),
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
    """Save prediction history. Atomic write (tmp + rename) to avoid race
    where a concurrent reader sees a half-written file, _load_history returns
    [], and the next save wipes everything (this happened 2026-05-11 — lost
    14 days of actuals that powered _observed_peak_solar calibration)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    history_file = CACHE_DIR / "cap_history.json"
    # Refuse to clobber a populated file with a tiny one — almost certainly
    # the load failed and we'd be wiping good data.
    if history_file.exists() and len(history) < 3:
        try:
            existing = json.loads(history_file.read_text())
            if isinstance(existing, list) and len(existing) > len(history) + 2:
                print(
                    f"[cap_history] refusing to shrink history from "
                    f"{len(existing)} → {len(history)} entries (likely "
                    f"a partial-load race). Skipping save."
                )
                return
        except Exception:
            pass
    history = history[-90:]
    tmp = history_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(history, indent=2))
    tmp.replace(history_file)


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


def predict_loads(
    lookback_days=21,
    sunrise_hour=6,
    sunset_hour=19,
    target_date=None,
):
    """Predict tomorrow's load profile, separating EV charging.

    EV charging draws from grid directly (Powerwall does not serve EV in
    self-consumption mode), so the cap loop ignores EV when sizing the
    floor for peak coverage. EV is included in informational totals for
    transparency.

    Same-day-of-week observations are weighted 2x to capture weekly
    rhythm (e.g., weekend AC patterns differ from weekday).

    Returns:
      hourly_base_kwh: dict {hour: avg kWh excluding EV} — drives the
                       per-hour simulation in recommend_charge_cap
      hourly_ev_kwh:   dict {hour: avg EV kWh}
      daytime_base_kwh, overnight_base_kwh, peak_base_kwh: aggregates
                       (no EV)
      daytime_kwh, overnight_kwh, peak_kwh: aggregates (incl EV)
                       — informational only
    """
    import sqlite3, json
    from pathlib import Path
    from datetime import datetime, timedelta

    db = Path(__file__).parent / "data" / "egauge_history.db"
    fallback = {
        "hourly_base_kwh": {h: 0.5 for h in range(24)},
        "hourly_ev_kwh": {h: 0.0 for h in range(24)},
        "daytime_kwh": 25.0,
        "daytime_base_kwh": 15.0,
        "overnight_kwh": 15.0,
        "overnight_base_kwh": 10.0,
        "overnight_ev_kwh": 5.0,
        "peak_kwh": 8.0,
        "peak_base_kwh": 8.0,
        "source": "fallback_defaults",
        "samples_per_hour": 0,
        "lookback_days": lookback_days,
    }
    if not db.exists():
        return fallback

    if target_date is None:
        target_date = (datetime.now() + timedelta(days=1)).date()
    target_is_weekend = target_date.weekday() >= 5

    cutoff_ts = int((datetime.now() - timedelta(days=lookback_days)).timestamp())
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        """SELECT hc.date, hc.hour, hc.register_data FROM hourly_consumption hc
           WHERE hc.timestamp >= ?
           AND hc.timestamp = (SELECT MAX(timestamp) FROM hourly_consumption
                               WHERE date = hc.date AND hour = hc.hour)""",
        (cutoff_ts,),
    ).fetchall()
    conn.close()

    if not rows:
        return fallback

    # weight=2.0 for same-weekday-bucket as target, weight=1.0 for others
    weighted_total = {h: [] for h in range(24)}  # list of (kwh, weight)
    weighted_ev = {h: [] for h in range(24)}
    for date_str, hour, register_json in rows:
        try:
            registers = json.loads(register_json)
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            continue
        total = registers.get("Usage [kWh]")
        if total is None or total == 0:
            total = sum(
                v
                for k, v in registers.items()
                if isinstance(v, (int, float))
                and v > 0
                and "Generation" not in k
                and "Total Power" not in k
            )
        ev = registers.get("EV Charger [kWh]", 0)
        ev = abs(ev) if isinstance(ev, (int, float)) else 0
        if total is None or total < 0:
            continue
        d_is_weekend = d.weekday() >= 5
        weight = 2.0 if d_is_weekend == target_is_weekend else 1.0
        weighted_total[hour].append((total, weight))
        weighted_ev[hour].append((ev, weight))

    def w_avg(samples):
        if not samples:
            return 0.0
        ws = sum(w for _, w in samples)
        return sum(v * w for v, w in samples) / ws if ws > 0 else 0.0

    avg_total = {h: w_avg(weighted_total[h]) for h in range(24)}
    avg_ev = {h: w_avg(weighted_ev[h]) for h in range(24)}
    avg_base = {h: max(0.0, avg_total[h] - avg_ev[h]) for h in range(24)}

    samples_per_hour = (
        round(sum(len(weighted_total[h]) for h in range(24)) / 24.0, 1) if rows else 0
    )

    daytime_kwh = sum(avg_total[h] for h in range(sunrise_hour, sunset_hour))
    daytime_base = sum(avg_base[h] for h in range(sunrise_hour, sunset_hour))
    overnight_hours = list(range(sunset_hour, 24)) + list(range(0, sunrise_hour))
    overnight_total = sum(avg_total[h] for h in overnight_hours)
    overnight_ev = sum(avg_ev[h] for h in overnight_hours)
    overnight_base = max(0, overnight_total - overnight_ev)

    PEAK_HOURS = list(range(16, 21))
    peak_total = sum(avg_total[h] for h in PEAK_HOURS)
    peak_ev = sum(avg_ev[h] for h in PEAK_HOURS)
    peak_base = max(0, peak_total - peak_ev)

    return {
        "hourly_base_kwh": {h: round(avg_base[h], 3) for h in range(24)},
        "hourly_ev_kwh": {h: round(avg_ev[h], 3) for h in range(24)},
        "daytime_kwh": round(daytime_kwh, 1),
        "daytime_base_kwh": round(daytime_base, 1),
        "overnight_kwh": round(overnight_total, 1),
        "overnight_base_kwh": round(overnight_base, 1),
        "overnight_ev_kwh": round(overnight_ev, 1),
        "peak_kwh": round(peak_total, 1),
        "peak_base_kwh": round(peak_base, 1),
        "source": f"{lookback_days}d_weighted_dow",
        "samples_per_hour": samples_per_hour,
        "target_date": str(target_date),
        "target_is_weekend": target_is_weekend,
        "lookback_days": lookback_days,
    }


def _get_current_soc_pct():
    """Fetch current battery SOC % from HA. None if unavailable."""
    try:
        import os
        import subprocess

        ha_url = os.environ.get("HA_URL", "")
        ha_token = os.environ.get("HA_TOKEN", "")
        if not ha_url or not ha_token:
            return None
        result = subprocess.run(
            [
                "curl",
                "-s",
                "-m",
                "5",
                "-H",
                f"Authorization: Bearer {ha_token}",
                f"{ha_url}/api/states/sensor.solar_power_plant_percentage_charged",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        data = json.loads(result.stdout)
        return float(data.get("state"))
    except Exception:
        return None


def _solar_hourly_profile(total_kwh, sunrise=6, sunset=19):
    """Distribute predicted total solar into hourly kWh using a sin curve.

    Bell-curve from 0 at sunrise, peak at solar noon, 0 at sunset. Sum of
    hourly values equals total_kwh.
    """
    import math

    profile = {h: 0.0 for h in range(24)}
    day_length = sunset - sunrise
    if day_length <= 0 or total_kwh <= 0:
        return profile
    raw = {}
    for h in range(sunrise, sunset):
        x = (h + 0.5 - sunrise) / day_length
        raw[h] = math.sin(math.pi * x)
    total_raw = sum(raw.values())
    if total_raw <= 0:
        return profile
    for h, v in raw.items():
        profile[h] = v / total_raw * total_kwh
    return profile


def _tou_period_for_hour(h):
    """PG&E EV2-A weekday TOU bucket for a given hour."""
    if 16 <= h <= 20:
        return "peak"  # 4pm-9pm
    if h == 15 or 21 <= h <= 23:
        return "part_peak"  # 3pm-4pm, 9pm-midnight
    return "off_peak"


def _simulate_day(
    sunrise_soc_pct,
    hourly_load,
    hourly_solar,
    capacity_kwh,
    max_charge_kw,
    max_discharge_kw,
    efficiency,
    reserve_pct,
    sunrise_hour=6,
):
    """Simulate one full 24h cycle starting at sunrise_hour with the given SOC.

    Returns export, hourly grid imports by TOU period, and per-hour SOC.
    Models Tesla taper above 90% SOC (linear decline to 0 charge rate at 100%).
    """
    soc_kwh = sunrise_soc_pct / 100.0 * capacity_kwh
    reserve_kwh = reserve_pct / 100.0 * capacity_kwh
    total_export = 0.0
    grid_by_tou = {"peak": 0.0, "part_peak": 0.0, "off_peak": 0.0}
    soc_trace = {}

    # Walk hours starting from sunrise — full 24h cycle wraps to next sunrise
    hours_order = list(range(sunrise_hour, 24)) + list(range(0, sunrise_hour))
    for h in hours_order:
        load = float(hourly_load.get(h, 0.0))
        solar = float(hourly_solar.get(h, 0.0))
        net = solar - load
        if net >= 0:
            # Surplus → charge battery (Tesla tapers above 90% SOC)
            soc_frac = soc_kwh / capacity_kwh if capacity_kwh > 0 else 1.0
            if soc_frac >= 1.0:
                rate_kw = 0.0
            elif soc_frac > 0.9:
                rate_kw = max_charge_kw * max(0.0, (1.0 - soc_frac) / 0.1)
            else:
                rate_kw = max_charge_kw
            room_kwh = max(0.0, capacity_kwh - soc_kwh)
            # Each hour: can pull up to rate_kw × 1h from the inverter; that
            # becomes rate_kw × efficiency stored in battery. The bind is the
            # most restrictive of {surplus solar, room, rate-limited inflow}.
            into_battery = min(net * efficiency, room_kwh, rate_kw * efficiency)
            soc_kwh += into_battery
            consumed_from_solar = into_battery / efficiency if efficiency > 0 else 0
            export_h = max(0.0, net - consumed_from_solar)
            total_export += export_h
        else:
            # Deficit → discharge battery, then grid
            need = -net
            available = max(0.0, soc_kwh - reserve_kwh)
            from_battery = min(need, available, max_discharge_kw)
            soc_kwh -= from_battery
            from_grid = need - from_battery
            tou = _tou_period_for_hour(h)
            grid_by_tou[tou] += from_grid
        soc_trace[h] = soc_kwh

    return {
        "end_soc_kwh": soc_kwh,
        "sunset_soc_kwh": soc_trace.get(18, soc_kwh),
        "min_soc_kwh": min(soc_trace.values()) if soc_trace else soc_kwh,
        "total_export_kwh": total_export,
        "grid_by_tou_kwh": grid_by_tou,
        "soc_at_hour": soc_trace,
    }


# Battery round-trip efficiency. Observed value on this Powerwall setup is
# ~0.82 over 7-day windows; the config default of 0.90 is optimistic and led
# to under-prediction of export. Caller respects an explicit config override
# if present, otherwise uses this empirically-grounded default.
DEFAULT_BATTERY_EFFICIENCY = 0.82


def recommend_charge_cap():
    """Per-hour simulation chooses the cap with the lowest total daily cost.

    Algorithm:
      1. Build tomorrow's hourly load profile (base, EV excluded) from
         predict_loads with same-day-of-week weighting.
      2. Build hourly solar profile (sin curve scaled to predicted total).
      3. For each candidate cap (hard_floor → 75% in 5% steps):
         simulate 24h starting at sunrise_soc = cap, compute cost as
         (overnight grid-charge + per-TOU grid imports − export credits).
      4. Pick lowest-cost cap.

    Models: measured (not config) battery efficiency, Tesla SOC taper
    above 90%, max charge/discharge rate, backup reserve floor.

    Replaces the old "fill to 100% by sunset" heuristic, which forced
    overnight grid-charging that exceeded the savings it provided.
    """
    from datetime import datetime, timedelta
    from config import get_config

    cfg = get_config()
    safety_margin = 0.05
    backup_reserve_pct = cfg.get("solar", {}).get(
        "backup_reserve_pct", BACKUP_RESERVE_PCT
    )

    # Efficiency: respect an explicit config override; otherwise use the
    # empirical default (0.82) since config 0.90 is optimistic vs observed.
    config_eff = cfg.get("solar", {}).get("battery_efficiency")
    if config_eff and config_eff != 0.90:
        efficiency = float(config_eff)
        efficiency_source = "config"
    else:
        efficiency = DEFAULT_BATTERY_EFFICIENCY
        efficiency_source = "observed_default_0.82"

    # Powerwall 2 = 5 kW continuous per unit; 3 units = 15 kW total. Discharge
    # similar. (Could be made configurable.)
    max_charge_kw = cfg.get("solar", {}).get("max_charge_kw", 15.0)
    max_discharge_kw = cfg.get("solar", {}).get("max_discharge_kw", 15.0)
    capacity_kwh = _battery_capacity_kwh()

    # Pull live TOU rates + export credits from config so the simulator
    # cost model adapts to whichever utility plan the user has.
    from config import get_rate
    from datetime import datetime as _dt

    tomorrow_dt = _dt.now() + timedelta(days=1)
    rates = {
        "peak": float(get_rate(tomorrow_dt, "peak")),
        "part_peak": float(get_rate(tomorrow_dt, "part_peak")),
        "off_peak": float(get_rate(tomorrow_dt, "off_peak")),
    }
    try:
        from solar_integration import get_export_credit

        export_credit = float(get_export_credit(tomorrow_dt, "off_peak"))
    except Exception:
        export_credit = 0.14  # NEM2 + MCE generation rate fallback
    overnight_charge_rate = rates["off_peak"]

    prediction = predict_solar_production()
    predicted_solar = prediction["predicted_solar_kwh"]
    loads = predict_loads()
    hourly_load = loads.get("hourly_base_kwh", {h: 0.5 for h in range(24)})
    overnight_base_load = loads.get("overnight_base_kwh", 10.0)
    peak_base_load = loads.get("peak_base_kwh", 10.0)
    # Daytime/overnight totals for informational fields & reasoning
    daytime_load = loads.get("daytime_base_kwh", loads.get("daytime_kwh", 25.0))
    overnight_load = loads.get("overnight_kwh", 15.0)

    hourly_solar = _solar_hourly_profile(predicted_solar)

    backup_reserve_kwh = backup_reserve_pct / 100 * capacity_kwh
    hard_floor = backup_reserve_pct / 100 + safety_margin

    # ── Steady-state per-cap evaluation ──
    # For each cap, find the equilibrium sunrise SOC (fixed point of
    # day-cycle iteration). For caps BELOW the natural equilibrium, the
    # cap doesn't matter — battery just operates above cap naturally with
    # zero overnight grid-charge. For caps ABOVE equilibrium, overnight
    # grid-charging lifts SOC up to cap each night, costing money.
    def _equilibrium(cap_pct):
        sunrise_pct = cap_pct * 100
        sim = _simulate_day(
            sunrise_soc_pct=sunrise_pct,
            hourly_load=hourly_load,
            hourly_solar=hourly_solar,
            capacity_kwh=capacity_kwh,
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
            efficiency=efficiency,
            reserve_pct=backup_reserve_pct,
        )
        for _ in range(7):
            # Where battery lands after a full 24h. If above cap, that's
            # the natural sunrise tomorrow (no grid charge). If below, the
            # cap forces grid-charge up to cap.
            end_pct = sim["end_soc_kwh"] / capacity_kwh * 100
            new_sunrise_pct = max(cap_pct * 100, end_pct)
            if abs(new_sunrise_pct - sunrise_pct) < 0.5:
                sunrise_pct = new_sunrise_pct
                break
            sunrise_pct = new_sunrise_pct
            sim = _simulate_day(
                sunrise_soc_pct=sunrise_pct,
                hourly_load=hourly_load,
                hourly_solar=hourly_solar,
                capacity_kwh=capacity_kwh,
                max_charge_kw=max_charge_kw,
                max_discharge_kw=max_discharge_kw,
                efficiency=efficiency,
                reserve_pct=backup_reserve_pct,
            )
        return sunrise_pct, sim

    candidates = []
    for cap_int in range(int(hard_floor * 100), 76, 5):
        cap_pct = cap_int / 100.0
        eq_sunrise_pct, sim = _equilibrium(cap_pct)
        # Overnight grid charge needed = max(0, cap - end_soc) when cap forces
        # the floor. In steady state if equilibrium > cap, no grid charge.
        eq_sunrise_kwh = eq_sunrise_pct / 100 * capacity_kwh
        overnight_grid_to_battery = (
            max(0.0, cap_pct * capacity_kwh - sim["end_soc_kwh"]) / efficiency
        )
        daytime_grid_cost = sum(sim["grid_by_tou_kwh"][p] * rates[p] for p in rates)
        overnight_cost = overnight_grid_to_battery * overnight_charge_rate
        export_revenue = sim["total_export_kwh"] * export_credit
        total_cost = daytime_grid_cost + overnight_cost - export_revenue
        candidates.append(
            {
                "cap_pct": cap_pct,
                "cap_int": cap_int,
                "sim": sim,
                "equilibrium_sunrise_pct": round(eq_sunrise_pct, 1),
                "overnight_grid_to_battery": overnight_grid_to_battery,
                "total_cost": total_cost,
                "export_kwh": sim["total_export_kwh"],
                "grid_by_tou": sim["grid_by_tou_kwh"],
            }
        )

    # Pick lowest cost. Tie-break with LOWER cap (less battery-cycling wear,
    # more headroom for prediction errors).
    best = min(candidates, key=lambda c: (round(c["total_cost"], 2), c["cap_pct"]))
    chosen_cap_pct = best["cap_pct"]
    recommended_cap = best["cap_int"]
    best_sim = best["sim"]
    predicted_export_kwh = best["export_kwh"]
    predicted_overnight_grid_import_kwh = best["overnight_grid_to_battery"]

    # ── EXPERIMENT (2026-05-19): "Let's try just not grid charging for a
    # few days." Force cap = backup_reserve_pct exactly, so the cap loop
    # never lifts the Powerwall reserve above its hardware floor. Note this
    # doesn't stop Tesla's autonomous self-consumption-mode grid topups,
    # which are independent of our reserve setting.
    if cfg.get("solar", {}).get("experiment_no_grid_charge"):
        chosen_cap_pct = backup_reserve_pct / 100.0
        recommended_cap = int(backup_reserve_pct)
        # Re-pull the candidate matching this cap (or closest) so downstream
        # fields (predicted_export, predicted_overnight_grid, sim trace) come
        # from the actual scenario we're forcing, not the optimizer's pick.
        match = min(candidates, key=lambda c: abs(c["cap_int"] - recommended_cap))
        best = match
        best_sim = match["sim"]
        predicted_export_kwh = match["export_kwh"]
        predicted_overnight_grid_import_kwh = match["overnight_grid_to_battery"]

    # Legacy fields preserved for backwards compat in HA sensor + audit log
    solar_excess = max(0, predicted_solar - daytime_load)
    battery_in = solar_excess * efficiency
    target_cap_pct = max(0.0, 1.0 - battery_in / capacity_kwh)
    required_sunrise_kwh = max(0, peak_base_load + backup_reserve_kwh - battery_in)
    required_sunrise_pct = required_sunrise_kwh / capacity_kwh
    min_cap_pct = max(hard_floor, required_sunrise_pct)

    # SOC trajectory: predict tonight's midnight + natural sunrise SOC from
    # the current SOC + tonight's evening drain. The simulation gave us the
    # sunset SOC for tomorrow's cycle.
    current_soc_pct = _get_current_soc_pct()
    now = datetime.now()
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    hours_to_midnight = max(0, (midnight - now).total_seconds() / 3600)
    midnight_soc_pct = None
    natural_sunrise_pct = None
    if current_soc_pct is not None:
        soc_now_kwh = current_soc_pct / 100 * capacity_kwh
        eve_drain = (overnight_base_load / 12) * hours_to_midnight
        midnight_soc_kwh = max(0, soc_now_kwh - eve_drain)
        sunrise_drain = overnight_base_load * (6 / 12)
        natural_sunrise_kwh = max(0, midnight_soc_kwh - sunrise_drain)
        midnight_soc_pct = round(midnight_soc_kwh / capacity_kwh * 100, 1)
        natural_sunrise_pct = round(natural_sunrise_kwh / capacity_kwh * 100, 1)

    sunset_soc_pct = round(best_sim["sunset_soc_kwh"] / capacity_kwh * 100, 1)
    modeled = {
        "current_soc_pct": (
            round(current_soc_pct, 1) if current_soc_pct is not None else None
        ),
        "midnight_soc_pct": midnight_soc_pct,
        "natural_sunrise_soc_pct": natural_sunrise_pct,
        "post_charge_sunrise_soc_pct": int(chosen_cap_pct * 100),
        "sunset_soc_pct": sunset_soc_pct,
        "candidates": [
            {
                "cap": c["cap_int"],
                "cost": round(c["total_cost"], 2),
                "export_kwh": round(c["export_kwh"], 1),
                "overnight_grid_kwh": round(c["overnight_grid_to_battery"], 1),
                "peak_grid_kwh": round(c["grid_by_tou"]["peak"], 1),
            }
            for c in candidates
        ],
        "efficiency_used": round(efficiency, 3),
        "efficiency_source": efficiency_source,
    }

    # Decision reasoning. Cap is always FLOOR — just enough to cover tomorrow's
    # peak from battery. We expose target_cap_pct as informational only.
    if cfg.get("solar", {}).get("experiment_no_grid_charge"):
        reason_summary = (
            f"EXPERIMENT no_grid_charge: cap pinned to backup_reserve "
            f"({backup_reserve_pct}%) — our loop will not grid-charge. "
            f"Tesla autonomous behavior still possible."
        )
    elif required_sunrise_pct > hard_floor:
        reason_summary = (
            f"FLOOR (need {required_sunrise_pct*100:.0f}% at sunrise to cover "
            f"~{peak_base_load:.0f} kWh peak)"
        )
    else:
        reason_summary = (
            f"FLOOR (backup_reserve {backup_reserve_pct}% + "
            f"{int(safety_margin*100)}% safety) — solar will refill battery, "
            f"no overnight grid-charge needed"
        )

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
        "battery_capacity_kwh": capacity_kwh,
        "modeled": modeled,
        "reasoning": (
            f"Tomorrow: {prediction['cloud_cover_pct']:.0f}% cloud, "
            f"~{predicted_solar:.0f} kWh solar predicted "
            f"({'weekend' if loads.get('target_is_weekend') else 'weekday'} load profile). "
            f"Daytime base ~{daytime_load:.0f} kWh (EV excluded), "
            f"overnight ~{overnight_load:.0f} kWh, peak base ~{peak_base_load:.0f} kWh. "
            f"Per-hour simulation tested {len(candidates)} caps from "
            f"{int(hard_floor * 100)}% → 75%; cheapest = {recommended_cap}% "
            f"(efficiency {efficiency*100:.0f}% [{efficiency_source}]). "
            f"Predicted: {predicted_export_kwh:.1f} kWh export, "
            f"{predicted_overnight_grid_import_kwh:.1f} kWh overnight grid-charge, "
            f"{best_sim['grid_by_tou_kwh']['peak']:.1f} kWh peak-rate grid."
            + (
                f" SOC: now {modeled['current_soc_pct']:.0f}% → midnight {midnight_soc_pct}% → "
                f"sunrise {natural_sunrise_pct}% (cap to {recommended_cap}%) → "
                f"sunset {sunset_soc_pct}%."
                if current_soc_pct is not None
                else ""
            )
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


def record_actual_fill(
    date,
    full_hour,
    actual_solar_kwh=None,
    actual_export_kwh=None,
    actual_grid_import_kwh=None,
):
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
