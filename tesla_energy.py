"""
Tesla Fleet API energy data source for billing.

Piggybacks on Home Assistant's Tesla Fleet OAuth token -- no separate auth flow.
Provides 100% coverage historical energy data vs HA's limited retention.
"""

import json
import subprocess
from datetime import datetime, timedelta
from collections import defaultdict

from config import get_tou_period, get_config
from solar_integration import HA_URL, get_ha_token


TESLA_API_BASE = "https://fleet-api.prd.na.vn.cloud.tesla.com"


def _get_tesla_config():
    """Get Tesla energy site config from billing config."""
    cfg = get_config()
    tesla = cfg.get('billing', {}).get('tesla', {})
    return tesla.get('site_id'), tesla.get('token')


def fetch_tesla_energy(days=30):
    """Fetch daily energy data from Tesla Fleet API.

    Args:
        days: Number of days of history to fetch.

    Returns dict with daily energy data aggregated by TOU period, or None on failure.
    """
    site_id, token = _get_tesla_config()
    if not site_id or not token:
        return None

    end = datetime.now()
    start = end - timedelta(days=days)

    url = (
        f"{TESLA_API_BASE}/api/1/energy_sites/{site_id}/calendar_history"
        f"?kind=energy&period=month"
        f"&end_date={end.strftime('%Y-%m-%dT23:59:59-07:00')}"
        f"&time_zone=America/Los_Angeles"
    )

    try:
        cmd = [
            'curl', '-s', '-H', f'Authorization: Bearer {token}',
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
    except Exception as e:
        print(f"Warning: Tesla API fetch failed: {e}")
        return None

    response = data.get('response')
    if not response:
        error = data.get('error', data.get('error_description', str(data)[:200]))
        print(f"Warning: Tesla API returned no response: {error}")
        return None
    series = response.get('time_series', [])
    if not series:
        print("Warning: Tesla API returned empty time_series")
        return None

    # Aggregate 15-min intervals into daily + TOU breakdown
    daily = defaultdict(lambda: {
        'grid_import': 0, 'grid_export': 0, 'solar': 0,
        'battery_to_home': 0, 'grid_to_battery': 0, 'solar_to_battery': 0,
        'consumption': 0,
    })

    by_tou = defaultdict(lambda: {
        'grid_import': 0, 'grid_export': 0, 'solar': 0,
        'consumption': 0, 'export_credit': 0,
    })

    for entry in series:
        ts = entry.get('timestamp', '')
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            continue

        # Skip entries before our start date
        if dt.date() < start.date():
            continue

        day_key = dt.strftime('%Y-%m-%d')
        hour = dt.hour
        tou = get_tou_period(hour)

        # All Tesla values are in Wh, convert to kWh
        gi = entry.get('consumer_energy_imported_from_grid', 0) / 1000
        bi = entry.get('battery_energy_imported_from_grid', 0) / 1000
        grid_import = gi + bi

        ge_solar = entry.get('grid_energy_exported_from_solar', 0) / 1000
        ge_batt = entry.get('grid_energy_exported_from_battery', 0) / 1000
        grid_export = ge_solar + ge_batt

        solar = entry.get('solar_energy_exported', 0) / 1000
        batt_to_home = entry.get('consumer_energy_imported_from_battery', 0) / 1000
        solar_to_batt = entry.get('battery_energy_imported_from_solar', 0) / 1000
        consumption = gi + entry.get('consumer_energy_imported_from_solar', 0) / 1000 + batt_to_home

        d = daily[day_key]
        d['grid_import'] += grid_import
        d['grid_export'] += grid_export
        d['solar'] += solar
        d['battery_to_home'] += batt_to_home
        d['grid_to_battery'] += bi
        d['solar_to_battery'] += solar_to_batt
        d['consumption'] += consumption

        t = by_tou[tou]
        t['grid_import'] += grid_import
        t['grid_export'] += grid_export
        t['solar'] += solar
        t['consumption'] += consumption

    total_grid_import = sum(d['grid_import'] for d in daily.values())
    total_grid_export = sum(d['grid_export'] for d in daily.values())
    total_solar = sum(d['solar'] for d in daily.values())
    total_consumption = sum(d['consumption'] for d in daily.values())

    return {
        'source': 'tesla_fleet_api',
        'days': len(daily),
        'grid_import_kwh': round(total_grid_import, 1),
        'grid_export_kwh': round(total_grid_export, 1),
        'solar_kwh': round(total_solar, 1),
        'consumption_kwh': round(total_consumption, 1),
        'net_kwh': round(total_grid_import - total_grid_export, 1),
        'daily': {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in sorted(daily.items())},
        'by_tou': {
            period: {
                'grid_import': round(t['grid_import'], 1),
                'grid_export': round(t['grid_export'], 1),
                'solar': round(t['solar'], 1),
                'consumption': round(t['consumption'], 1),
            }
            for period, t in by_tou.items()
        },
    }
