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
        return {
            "daytime_kwh": 25.0,
            "overnight_kwh": 15.0,
            "overnight_base_kwh": 10.0,
            "overnight_ev_kwh": 5.0,
            "source": "fallback_defaults",
        }

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
        return {
            "daytime_kwh": 25.0,
            "overnight_kwh": 15.0,
            "overnight_base_kwh": 10.0,
            "overnight_ev_kwh": 5.0,
            "source": "no_history",
        }

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
                v
                for k, v in registers.items()
                if isinstance(v, (int, float))
                and v > 0
                and "Generation" not in k
                and "Total Power" not in k
            )
        ev = registers.get("EV Charger [kWh]", 0)
        # eGauge sometimes stores EV as negative (consumption convention varies)
        ev = abs(ev) if isinstance(ev, (int, float)) else 0
        if total is not None and total >= 0:
            by_hour_total[hour].append(total)
            by_hour_ev[hour].append(ev)

    avg_total = {h: (sum(v) / len(v)) if v else 0.0 for h, v in by_hour_total.items()}
    avg_ev = {h: (sum(v) / len(v)) if v else 0.0 for h, v in by_hour_ev.items()}

    daytime_kwh = sum(avg_total[h] for h in range(sunrise_hour, sunset_hour))
    overnight_total = sum(
        avg_total[h]
        for h in list(range(sunset_hour, 24)) + list(range(0, sunrise_hour))
    )
    overnight_ev = sum(
        avg_ev[h] for h in list(range(sunset_hour, 24)) + list(range(0, sunrise_hour))
    )
    overnight_base = max(0, overnight_total - overnight_ev)

    # Peak hours (4-9pm = 16-20 inclusive): what the BATTERY needs to cover.
    # EV typically isn't charging during peak so peak load ≈ peak base.
    PEAK_HOURS = list(range(16, 21))
    peak_total = sum(avg_total[h] for h in PEAK_HOURS)
    peak_ev = sum(avg_ev[h] for h in PEAK_HOURS)
    peak_base = max(0, peak_total - peak_ev)

    return {
        "daytime_kwh": round(daytime_kwh, 1),
        "overnight_kwh": round(overnight_total, 1),
        "overnight_base_kwh": round(overnight_base, 1),
        "overnight_ev_kwh": round(overnight_ev, 1),
        "peak_kwh": round(peak_total, 1),
        "peak_base_kwh": round(peak_base, 1),
        "source": f"{lookback_days}d_avg",
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


def recommend_charge_cap():
    """Calculate recommended grid charge cap to minimize total daily cost.

    Goal: battery hits ~99% at sunset, but ONLY via solar. Don't grid-charge
    overnight unless the battery genuinely won't make it to backup_reserve
    by sunrise. Grid charging when not needed is net-negative:

      −$0.36 spent on grid-charge
      +$0.12 recovered when displaced solar exports tomorrow
      = −$0.24 net loss per unnecessary kWh

    Math (timing referenced to midnight, NOT call-time):
      sunrise_soc_kwh = current_soc_kwh - drain(now → sunrise)
      cap_floor = backup_reserve_pct + safety_margin   # the hard SOC floor
      cap_target = target_eod - solar_excess × efficiency / capacity
      cap = max(cap_floor, cap_target)

    The cap_floor is the SOC level we MAINTAIN overnight via grid charging.
    If battery's predicted sunrise SOC is already above the floor (because
    we ended today high from solar), the floor never triggers — no overnight
    grid charge happens. Previous formula treated overnight_load_kwh as
    "must reserve this in battery at sunset", which forced grid-charging
    even when battery was naturally well-supplied.
    """
    from datetime import datetime, timedelta
    from config import get_config

    cfg = get_config()
    efficiency = cfg.get("solar", {}).get("battery_efficiency", 0.90)
    safety_margin = 0.05
    backup_reserve_pct = cfg.get("solar", {}).get(
        "backup_reserve_pct", BACKUP_RESERVE_PCT
    )

    prediction = predict_solar_production()
    predicted_solar = prediction["predicted_solar_kwh"]
    loads = predict_loads()
    daytime_load = loads["daytime_kwh"]
    overnight_load = loads["overnight_kwh"]
    overnight_base_load = loads.get("overnight_base_kwh", overnight_load)

    # ── Solar absorption potential ──
    solar_excess = max(0, predicted_solar - daytime_load)
    battery_in = solar_excess * efficiency
    fill_pct_from_solar = battery_in / BATTERY_CAPACITY_KWH

    target_eod = cfg.get("solar", {}).get("target_end_of_day_pct", 1.0)
    target_cap_pct = target_eod - fill_pct_from_solar

    # ── Floor: SOC level we maintain overnight via grid charging ──
    # User principle (2026-05-14): "Minimize battery grid charging, only
    # needed if we think we won't cover next day's peak otherwise. Then
    # just enough."
    #
    # Required sunset SOC (start of peak hours 4-9pm) to cover tomorrow's
    # peak base load + leave backup_reserve afterwards:
    #   required_sunset_kwh = peak_base_load + backup_reserve_kwh
    #
    # Battery's expected solar absorption tomorrow = solar_excess × efficiency.
    # If solar absorption >= required_sunset - sunrise_floor: no overnight
    # grid charge needed beyond reserve.
    # Otherwise: cap = (required_sunset - solar_absorption) / capacity.
    peak_base_load = loads.get("peak_base_kwh", 10.0)
    backup_reserve_kwh = backup_reserve_pct / 100 * BATTERY_CAPACITY_KWH
    required_sunset_kwh = peak_base_load + backup_reserve_kwh
    # Battery absorbs at most `battery_in` from solar, capped by remaining room
    # but if cap is low, room is large enough not to bind.
    required_sunrise_kwh = max(0, required_sunset_kwh - battery_in)
    required_sunrise_pct = required_sunrise_kwh / BATTERY_CAPACITY_KWH
    hard_floor = backup_reserve_pct / 100 + safety_margin
    min_cap_pct = max(hard_floor, required_sunrise_pct)

    # Decision: use the FLOOR (just enough to cover tomorrow's peak). Don't
    # grid-charge beyond that to "fill to 100% by sunset" — that's what
    # target_cap_pct optimizes for, but it (a) costs $0.36/kWh in overnight
    # grid charges, (b) leaves zero headroom for the model being wrong about
    # solar/load, and (c) forces export at $0.12/kWh when the model is even
    # slightly optimistic about consumption. target_cap_pct is preserved in
    # the output for transparency but no longer drives the choice.
    # (User principle 2026-05-18: "if it's putting juice in overnight AT ALL
    # then hitting 100% early that's a problem with our algorithm.")
    chosen_cap_pct = min_cap_pct
    chosen_cap_pct = min(1.0, max(0.05, chosen_cap_pct))
    recommended_cap = int(chosen_cap_pct * 100)

    # ── Project the day cycle from current SOC for transparency + accurate
    # predicted-grid-import calc ──
    # Reference point is MIDNIGHT, not call-time (the function gets called
    # every hour by HA polling; using "now" makes the prediction unstable).
    current_soc_pct = _get_current_soc_pct()
    now = datetime.now()
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    sunrise = midnight.replace(hour=6)
    sunset = midnight.replace(hour=18)
    next_midnight = midnight + timedelta(days=1)
    hours_to_midnight = max(0, (midnight - now).total_seconds() / 3600)

    if current_soc_pct is not None:
        soc_now_kwh = current_soc_pct / 100 * BATTERY_CAPACITY_KWH
        # Evening drain: rough — overnight_base spread over 12 hr night
        eve_drain_per_hr = overnight_base_load / 12
        eve_drain = eve_drain_per_hr * hours_to_midnight
        midnight_soc_kwh = max(0, soc_now_kwh - eve_drain)
        # Midnight → sunrise: rest of overnight base load (~6 hrs)
        sunrise_drain = overnight_base_load * (6 / 12)
        natural_sunrise_kwh = max(0, midnight_soc_kwh - sunrise_drain)
        # Cap kicks in if natural sunrise SOC < cap level
        cap_kwh = chosen_cap_pct * BATTERY_CAPACITY_KWH
        if natural_sunrise_kwh < cap_kwh:
            overnight_grid_charge_kwh = (cap_kwh - natural_sunrise_kwh) / efficiency
            sunrise_soc_kwh = cap_kwh
        else:
            overnight_grid_charge_kwh = 0.0
            sunrise_soc_kwh = natural_sunrise_kwh
        # Daytime: battery absorbs solar up to 100%
        room_for_solar_kwh = BATTERY_CAPACITY_KWH - sunrise_soc_kwh
        solar_to_battery_kwh = min(battery_in, room_for_solar_kwh)
        sunset_soc_kwh = sunrise_soc_kwh + solar_to_battery_kwh
        predicted_export_kwh = max(0, solar_excess - solar_to_battery_kwh / efficiency)
        predicted_overnight_grid_import_kwh = overnight_grid_charge_kwh
        modeled = {
            "current_soc_pct": round(current_soc_pct, 1),
            "midnight_soc_pct": round(midnight_soc_kwh / BATTERY_CAPACITY_KWH * 100, 1),
            "natural_sunrise_soc_pct": round(
                natural_sunrise_kwh / BATTERY_CAPACITY_KWH * 100, 1
            ),
            "post_charge_sunrise_soc_pct": round(
                sunrise_soc_kwh / BATTERY_CAPACITY_KWH * 100, 1
            ),
            "sunset_soc_pct": round(sunset_soc_kwh / BATTERY_CAPACITY_KWH * 100, 1),
        }
    else:
        # No HA reading available — fall back to old aggregate math
        predicted_export_kwh = max(
            0,
            solar_excess - (BATTERY_CAPACITY_KWH * (1 - chosen_cap_pct)) / efficiency,
        )
        predicted_overnight_grid_import_kwh = max(
            0,
            overnight_load - chosen_cap_pct * BATTERY_CAPACITY_KWH * efficiency,
        )
        modeled = {"current_soc_pct": None, "note": "HA unreachable; aggregate math"}

    # Decision reasoning. Cap is always FLOOR — just enough to cover tomorrow's
    # peak from battery. We expose target_cap_pct as informational only.
    if required_sunrise_pct > hard_floor:
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
        "battery_capacity_kwh": BATTERY_CAPACITY_KWH,
        "modeled": modeled,
        "reasoning": (
            f"Tomorrow: {prediction['cloud_cover_pct']:.0f}% cloud, "
            f"~{predicted_solar:.0f} kWh solar predicted. "
            f"Daytime load ~{daytime_load:.0f} kWh, overnight load ~{overnight_load:.0f} kWh. "
            f"Solar excess ~{solar_excess:.0f} kWh → ~{battery_in:.0f} kWh into battery (after {efficiency * 100:.0f}% rt). "
            f"Cap target: {target_cap_pct * 100:.0f}% (hit 99% at sunset). "
            f"Cap floor: {min_cap_pct * 100:.0f}% (backup_reserve {backup_reserve_pct}% + safety). "
            f"Recommend: {recommended_cap}% [{reason_summary}]. "
            f"Predicted export: {predicted_export_kwh:.1f} kWh. "
            f"Predicted overnight grid import: {predicted_overnight_grid_import_kwh:.1f} kWh."
            + (
                f" SOC trajectory: now {modeled['current_soc_pct']:.0f}% → midnight {modeled.get('midnight_soc_pct', '?')}% → sunrise {modeled.get('post_charge_sunrise_soc_pct', '?')}% → sunset {modeled.get('sunset_soc_pct', '?')}%."
                if modeled.get("current_soc_pct") is not None
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
