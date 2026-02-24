#!/usr/bin/env python3
"""
EV Charging integration for Energy Dashboard.

Pulls Tesla vehicle data from Home Assistant (tesla_fleet integration)
to show charging costs, efficiency, and gas savings.

Key concept: eGauge has an "EV Charger" circuit showing total wall power.
             HA has per-vehicle charging data (power, energy added, SOC).
             Combining them gives per-vehicle cost + efficiency metrics.
"""

import json
import os
import subprocess
from datetime import datetime, timedelta

from config import get_tou_period, get_rate, get_config

# ==========================================
# Configuration
# ==========================================

def is_ev_enabled():
    """Check if EV integration is enabled in config."""
    cfg = get_config()
    return cfg.get('ev', {}).get('enabled', False)


def get_ev_config():
    """Get full EV configuration."""
    cfg = get_config()
    return cfg.get('ev', {})


def get_vehicles():
    """Get vehicle configurations."""
    return get_ev_config().get('vehicles', {})


def _get_ha_url():
    return os.environ.get('HA_URL', 'http://homeassistant.local:8123')


def _get_ha_token():
    return os.environ.get('HA_TOKEN')


# ==========================================
# HA Data Fetching
# ==========================================

def fetch_ev_live():
    """Fetch current state of all EV entities from HA."""
    if not is_ev_enabled():
        return None

    token = _get_ha_token()
    if not token:
        return None

    ha_url = _get_ha_url()
    vehicles = get_vehicles()

    # Collect all entity IDs we need
    entity_ids = set()
    for veh_key, veh_cfg in vehicles.items():
        for field in ['charger_power', 'charge_energy_added', 'battery_level',
                      'battery_range', 'odometer', 'charging_state']:
            eid = veh_cfg.get(field)
            if eid:
                entity_ids.add(eid)

    if not entity_ids:
        return None

    # Fetch all states from HA
    cmd = [
        'curl', '-s',
        '-H', f'Authorization: Bearer {token}',
        f'{ha_url}/api/states'
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
        states = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None

    # Build lookup
    state_map = {s['entity_id']: s['state'] for s in states}

    # Build per-vehicle data
    ev_data = {}
    for veh_key, veh_cfg in vehicles.items():
        veh = {'id': veh_key, 'name': veh_cfg.get('name', veh_key)}

        for field, parse_type in [
            ('charger_power', 'float'),
            ('charge_energy_added', 'float'),
            ('battery_level', 'float'),
            ('battery_range', 'float'),
            ('odometer', 'float'),
            ('charging_state', 'str'),
        ]:
            entity_id = veh_cfg.get(field)
            if entity_id and entity_id in state_map:
                raw = state_map[entity_id]
                if raw in ('unavailable', 'unknown', None):
                    veh[field] = None
                elif parse_type == 'float':
                    try:
                        veh[field] = float(raw)
                    except (ValueError, TypeError):
                        veh[field] = None
                else:
                    veh[field] = raw
            else:
                veh[field] = None

        # Derived fields
        power_w = veh.get('charger_power')
        if power_w is not None and power_w > 0:
            veh['is_charging'] = True
            veh['charger_power_kw'] = round(power_w / 1000, 2)
        else:
            veh['is_charging'] = False
            veh['charger_power_kw'] = 0

        # Current charging cost rate
        now = datetime.now()
        tou_period = get_tou_period(now.hour)
        rate = get_rate(now, tou_period)
        veh['current_rate'] = rate
        veh['current_tou'] = tou_period

        # Cost per mile at current rate
        efficiency = veh_cfg.get('efficiency_mi_per_kwh', 3.3)
        veh['efficiency_mi_per_kwh'] = efficiency
        veh['cost_per_mile'] = round(rate / efficiency, 3)

        # Gas comparison
        gas_price = get_ev_config().get('gas_price_per_gallon', 4.50)
        gas_mpg = 25  # Average car
        gas_cost_per_mile = gas_price / gas_mpg
        veh['gas_cost_per_mile'] = round(gas_cost_per_mile, 3)
        veh['savings_vs_gas_pct'] = round((1 - veh['cost_per_mile'] / gas_cost_per_mile) * 100, 1) if gas_cost_per_mile > 0 else 0

        ev_data[veh_key] = veh

    return ev_data


def fetch_ev_history(entity_id, days=7):
    """Fetch historical state data for an EV entity from HA."""
    token = _get_ha_token()
    if not token:
        return None

    ha_url = _get_ha_url()
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
    end = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    cmd = [
        'curl', '-s',
        '-H', f'Authorization: Bearer {token}',
        f'{ha_url}/api/history/period/{start}?end_time={end}&filter_entity_id={entity_id}&significant_changes_only=0&no_attributes'
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
        data = json.loads(result.stdout)
        if data and len(data) > 0:
            return data[0]
        return []
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


def build_ev_charging_summary(days=7):
    """Build a summary of EV charging activity over N days.

    Detects charging sessions from charger_power history,
    calculates per-session and aggregate costs.
    """
    if not is_ev_enabled():
        return None

    vehicles = get_vehicles()
    ev_config = get_ev_config()
    gas_price = ev_config.get('gas_price_per_gallon', 4.50)

    summary = {
        'days': days,
        'vehicles': {},
        'totals': {
            'total_kwh': 0,
            'total_cost': 0,
            'total_miles_equivalent': 0,
            'gas_equivalent_cost': 0,
        }
    }

    for veh_key, veh_cfg in vehicles.items():
        power_entity = veh_cfg.get('charger_power')
        if not power_entity:
            continue

        history = fetch_ev_history(power_entity, days)
        if not history:
            continue

        efficiency = veh_cfg.get('efficiency_mi_per_kwh', 3.3)

        # Parse charging sessions from power readings
        sessions = _extract_charging_sessions(history)

        total_kwh = 0
        total_cost = 0
        by_tou = {'peak': 0, 'part_peak': 0, 'off_peak': 0}

        for session in sessions:
            total_kwh += session['kwh']
            total_cost += session['cost']
            by_tou[session['primary_tou']] = by_tou.get(session['primary_tou'], 0) + session['kwh']

        miles = total_kwh * efficiency
        gas_equivalent = miles / 25 * gas_price  # 25 MPG average

        veh_summary = {
            'name': veh_cfg.get('name', veh_key),
            'total_kwh': round(total_kwh, 2),
            'total_cost': round(total_cost, 2),
            'avg_daily_kwh': round(total_kwh / max(days, 1), 2),
            'avg_daily_cost': round(total_cost / max(days, 1), 2),
            'miles_equivalent': round(miles, 1),
            'cost_per_mile': round(total_cost / miles, 3) if miles > 0 else 0,
            'gas_equivalent_cost': round(gas_equivalent, 2),
            'savings_vs_gas': round(gas_equivalent - total_cost, 2),
            'efficiency_mi_per_kwh': efficiency,
            'sessions': len(sessions),
            'by_tou': {k: round(v, 2) for k, v in by_tou.items()},
            'off_peak_pct': round(by_tou['off_peak'] / total_kwh * 100, 1) if total_kwh > 0 else 0,
        }

        summary['vehicles'][veh_key] = veh_summary
        summary['totals']['total_kwh'] += total_kwh
        summary['totals']['total_cost'] += total_cost
        summary['totals']['total_miles_equivalent'] += miles
        summary['totals']['gas_equivalent_cost'] += gas_equivalent

    # Round totals
    for k in summary['totals']:
        summary['totals'][k] = round(summary['totals'][k], 2)

    summary['totals']['savings_vs_gas'] = round(
        summary['totals']['gas_equivalent_cost'] - summary['totals']['total_cost'], 2
    )

    return summary


def _extract_charging_sessions(history):
    """Extract charging sessions from charger_power history.

    A session starts when power > 100W and ends when it drops to 0.
    """
    sessions = []
    current_session = None

    for entry in history:
        try:
            power_w = float(entry['state'])
        except (ValueError, TypeError):
            power_w = 0

        ts_str = entry.get('last_changed', entry.get('last_updated', ''))
        try:
            if '+' in ts_str or ts_str.endswith('Z'):
                dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00')).astimezone()
            else:
                dt = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue

        if power_w > 100:
            if current_session is None:
                current_session = {
                    'start': dt,
                    'readings': [],
                    'peak_power_w': 0,
                }
            current_session['readings'].append((dt, power_w))
            current_session['peak_power_w'] = max(current_session['peak_power_w'], power_w)
        else:
            if current_session and current_session['readings']:
                # End session — calculate energy
                session = _finalize_session(current_session, dt)
                if session and session['kwh'] > 0.1:  # Skip tiny sessions
                    sessions.append(session)
                current_session = None

    # Handle ongoing session
    if current_session and current_session['readings']:
        session = _finalize_session(current_session, datetime.now().astimezone())
        if session and session['kwh'] > 0.1:
            sessions.append(session)

    return sessions


def _finalize_session(session_data, end_dt):
    """Calculate energy and cost for a charging session."""
    readings = session_data['readings']
    if len(readings) < 2:
        return None

    # Trapezoidal integration of power over time
    total_wh = 0
    cost = 0
    tou_kwh = {'peak': 0, 'part_peak': 0, 'off_peak': 0}

    for i in range(1, len(readings)):
        dt_prev, w_prev = readings[i - 1]
        dt_curr, w_curr = readings[i]
        duration_h = (dt_curr - dt_prev).total_seconds() / 3600
        if duration_h > 2:  # Skip gaps > 2 hours (likely missing data)
            continue

        avg_w = (w_prev + w_curr) / 2
        wh = avg_w * duration_h
        kwh = wh / 1000
        total_wh += wh

        tou = get_tou_period(dt_prev.hour)
        rate = get_rate(dt_prev, tou)
        cost += kwh * rate
        tou_kwh[tou] += kwh

    kwh = total_wh / 1000

    # Determine primary TOU period
    primary_tou = max(tou_kwh, key=tou_kwh.get)

    return {
        'start': session_data['start'].isoformat(),
        'end': end_dt.isoformat(),
        'duration_hours': round((end_dt - session_data['start']).total_seconds() / 3600, 2),
        'kwh': round(kwh, 2),
        'cost': round(cost, 2),
        'peak_power_kw': round(session_data['peak_power_w'] / 1000, 2),
        'avg_rate': round(cost / kwh, 3) if kwh > 0 else 0,
        'primary_tou': primary_tou,
    }
