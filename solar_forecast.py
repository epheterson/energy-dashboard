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


def _auto_tune_ratio():
    """Auto-tune the solar-to-battery ratio from historical data.

    Looks at recent days where we have both prediction and actual data.
    If battery consistently fills too early (before 1pm), ratio is too low.
    If battery doesn't reach 100%, ratio is too high.
    Target: battery hits 100% between 1pm and 3pm.

    Returns the tuned ratio, or the default if not enough data.
    """
    history = _load_history()
    if len(history) < 3:
        return 0.50  # Default until we have enough data

    # Look at last 14 days with complete data
    recent = [h for h in history[-14:] if h.get("actual_full_hour") is not None]
    if len(recent) < 3:
        return 0.50

    # Calculate average fill hour and adjust ratio
    avg_full_hour = sum(h["actual_full_hour"] for h in recent) / len(recent)
    current_ratio = recent[-1].get("ratio_used", 0.50)

    # Target: fill at hour 14 (2pm)
    # If filling too early (< 13), increase ratio (lower cap, more room for solar)
    # If filling too late (> 15) or not at all, decrease ratio (higher cap)
    if avg_full_hour < 12.5:
        # Way too early — big adjustment
        new_ratio = min(0.70, current_ratio + 0.05)
    elif avg_full_hour < 13.5:
        # Slightly too early — small adjustment
        new_ratio = min(0.70, current_ratio + 0.02)
    elif avg_full_hour > 15.5:
        # Too late — lower ratio
        new_ratio = max(0.30, current_ratio - 0.03)
    elif avg_full_hour > 14.5:
        # Slightly late
        new_ratio = max(0.30, current_ratio - 0.01)
    else:
        # 13.5-14.5 = perfect range, no change
        new_ratio = current_ratio

    return round(new_ratio, 3)


def recommend_charge_cap():
    """Calculate recommended grid charge cap percentage.

    Uses auto-tuned ratio based on historical fill times.
    Logs every decision for future tuning.

    Returns dict with recommendation and reasoning.
    """
    prediction = predict_solar_production()

    predicted_kwh = prediction["predicted_solar_kwh"]

    # Auto-tune ratio from historical data
    ratio = _auto_tune_ratio()
    solar_to_battery_kwh = predicted_kwh * ratio
    solar_fill_pct = solar_to_battery_kwh / BATTERY_CAPACITY_KWH * 100

    # Cap = 100% minus solar fill (so grid + solar = ~100%)
    # Minimum 40% (sunniest day still needs baseline)
    # Maximum 90% (cloudy days need more grid)
    recommended_cap = max(40, min(90, int(100 - solar_fill_pct)))

    # Calculate economics
    # Grid off-peak: ~$0.28/kWh. Solar: $0.00
    # Savings from leaving headroom for solar
    savings_per_night = solar_to_battery_kwh * 0.28  # Off-peak delivery rate

    result = {
        "recommended_cap": recommended_cap,
        "solar_prediction": prediction,
        "solar_to_battery_kwh": round(solar_to_battery_kwh, 1),
        "solar_fill_pct": round(solar_fill_pct, 0),
        "estimated_savings_vs_full": round(savings_per_night, 2),
        "ratio_used": ratio,
        "reasoning": (
            f"Tomorrow: {prediction['cloud_cover_pct']:.0f}% cloud cover, "
            f"~{predicted_kwh:.0f} kWh solar expected. "
            f"~{solar_to_battery_kwh:.0f} kWh can go to battery ({solar_fill_pct:.0f}% of {BATTERY_CAPACITY_KWH} kWh). "
            f"Grid charge to {recommended_cap}%, let solar fill the rest. "
            f"(ratio={ratio:.3f})"
        ),
    }

    # Log decision for auto-tuning
    history = _load_history()
    history.append(
        {
            "date": prediction.get("date"),
            "predicted_solar": predicted_kwh,
            "cloud_cover": prediction.get("cloud_cover_pct"),
            "recommended_cap": recommended_cap,
            "ratio_used": ratio,
            "actual_full_hour": None,  # Filled in later by record_actual_fill
            "actual_solar": None,
            "logged_at": datetime.now().isoformat(),
        }
    )
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
