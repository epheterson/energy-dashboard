"""
Predictive battery charge cap based on weather forecast.

Uses NWS (National Weather Service) API for sky cover forecast
and Tesla historical data for solar production baselines.
No API key needed — NWS is free and public.
"""

import json
import subprocess
from datetime import datetime, timedelta


# NWS gridpoint for Danville, CA (37.8216, -121.9999)
NWS_GRIDPOINT_URL = "https://api.weather.gov/gridpoints/MTR/100,104"

# Monthly peak solar production (kWh/day) from Tesla historical data
# These represent clear-sky potential for a 9.86 kW system in Danville
# Will be refined as more data accumulates
MONTHLY_PEAK_SOLAR = {
    1: 18.7,   # January
    2: 23.8,   # February
    3: 31.4,   # March
    4: 35.0,   # April (estimated)
    5: 38.0,   # May (estimated)
    6: 40.0,   # June (estimated — near solstice peak)
    7: 39.0,   # July (estimated)
    8: 36.0,   # August (estimated)
    9: 32.0,   # September (estimated)
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
            'curl', '-s', '-A', 'EnergyDashboard/1.0 (epheterson@gmail.com)',
            NWS_GRIDPOINT_URL
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        data = json.loads(result.stdout)
    except Exception as e:
        print(f"Warning: NWS forecast fetch failed: {e}")
        return None

    sky_cover = data.get('properties', {}).get('skyCover', {}).get('values', [])
    if not sky_cover:
        return None

    # Get tomorrow's date
    tomorrow = (datetime.now() + timedelta(days=1)).date()

    # Filter to daylight hours (8am-5pm) tomorrow
    daytime_covers = []
    for entry in sky_cover:
        valid_time = entry.get('validTime', '')
        try:
            # NWS uses ISO 8601 duration format: "2026-03-31T08:00:00+00:00/PT1H"
            dt_str = valid_time.split('/')[0]
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            # Convert to Pacific time (handles PST/PDT automatically)
            try:
                from zoneinfo import ZoneInfo
                local_dt = dt.astimezone(ZoneInfo("America/Los_Angeles"))
            except ImportError:
                local_dt = dt - timedelta(hours=7)  # Fallback: PDT

            if local_dt.date() == tomorrow and 8 <= local_dt.hour <= 17:
                daytime_covers.append(entry.get('value', 50))
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
        'date': tomorrow.strftime('%Y-%m-%d'),
        'month': month,
        'peak_solar_kwh': round(peak_solar, 1),
        'cloud_cover_pct': round(cloud_cover_pct, 0),
        'predicted_solar_kwh': round(predicted_kwh, 1),
        'forecast_available': forecast_available,
    }


def recommend_charge_cap():
    """Calculate recommended grid charge cap percentage.

    Returns dict with recommendation and reasoning.
    """
    prediction = predict_solar_production()

    predicted_kwh = prediction['predicted_solar_kwh']

    # How much of the battery can predicted solar fill?
    # Not all solar goes to battery — home consumes some directly
    # Typically ~60% of solar goes to battery (rest powers home)
    solar_to_battery_kwh = predicted_kwh * 0.60
    solar_fill_pct = solar_to_battery_kwh / BATTERY_CAPACITY_KWH * 100

    # Cap = 100% minus solar headroom, but stay within bounds
    # Minimum 50% (always some grid charging for reliability)
    # Maximum 90% (always some solar headroom)
    recommended_cap = max(50, min(90, int(100 - solar_fill_pct)))

    # Calculate economics
    # Grid off-peak: ~$0.28/kWh. Solar: $0.00
    # Savings from leaving headroom for solar
    savings_per_night = solar_to_battery_kwh * 0.28  # Off-peak delivery rate

    return {
        'recommended_cap': recommended_cap,
        'solar_prediction': prediction,
        'solar_to_battery_kwh': round(solar_to_battery_kwh, 1),
        'solar_fill_pct': round(solar_fill_pct, 0),
        'estimated_savings_vs_full': round(savings_per_night, 2),
        'reasoning': (
            f"Tomorrow: {prediction['cloud_cover_pct']:.0f}% cloud cover, "
            f"~{predicted_kwh:.0f} kWh solar expected. "
            f"~{solar_to_battery_kwh:.0f} kWh can go to battery ({solar_fill_pct:.0f}% of {BATTERY_CAPACITY_KWH} kWh). "
            f"Grid charge to {recommended_cap}%, let solar fill the rest."
        ),
    }
