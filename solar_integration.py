#!/usr/bin/env python3
"""
Solar + Powerwall integration for eGauge Energy Analysis.

Blends eGauge per-circuit data with Home Assistant Powerwall data
to calculate actual costs accounting for solar self-consumption
and battery arbitrage.

Key concept: eGauge tells us WHAT consumed power.
             Powerwall tells us WHERE the power came from.
             Blending them gives actual grid cost per circuit.

Configuration is loaded from config.yml. When solar.enabled is false,
all public functions return None immediately.
"""

import json
import os
import subprocess
from datetime import datetime, timedelta
from collections import defaultdict

from config import get_tou_period, get_rate, is_summer, is_solar_enabled, get_config

# ==========================================
# Home Assistant Configuration (from config.yml)
# ==========================================

def _get_ha_entities():
    """Get HA entity mapping from config."""
    cfg = get_config()
    return cfg.get('solar', {}).get('ha_entities', {})


def _get_export_credits():
    """Get export credit rates from config."""
    cfg = get_config()
    return cfg.get('solar', {}).get('export_credits', {
        'winter': {'peak': 0.16, 'part_peak': 0.14, 'off_peak': 0.12},
        'summer': {'peak': 0.16, 'part_peak': 0.14, 'off_peak': 0.12},
    })


def get_ha_token():
    """Get HA token from environment variable."""
    if not is_solar_enabled():
        return None
    return os.environ.get('HA_TOKEN')


# Module-level references for backward compatibility (used by app.py imports)
HA_URL = os.environ.get('HA_URL', 'http://homeassistant.local:8123')
HA_ENTITIES = _get_ha_entities() if is_solar_enabled() else {}

# NEM 2.0 export credits — backward-compat module-level exports
_credits = _get_export_credits() if is_solar_enabled() else {}
WINTER_EXPORT_CREDITS = _credits.get('winter', {'peak': 0.16, 'part_peak': 0.14, 'off_peak': 0.12})
SUMMER_EXPORT_CREDITS = _credits.get('summer', {'peak': 0.16, 'part_peak': 0.14, 'off_peak': 0.12})


def get_export_credit(date, tou_period):
    """Get export credit rate for a given date and TOU period."""
    credits = SUMMER_EXPORT_CREDITS if is_summer(date) else WINTER_EXPORT_CREDITS
    return credits[tou_period]


# ==========================================
# HA Data Fetching
# ==========================================

def fetch_ha_history(entity_id, days=7):
    """Fetch history from Home Assistant for an entity."""
    if not is_solar_enabled():
        return None

    token = get_ha_token()
    if not token:
        return None

    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
    end = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    cmd = [
        'curl', '-s',
        '-H', f'Authorization: Bearer {token}',
        f'{HA_URL}/api/history/period/{start}?end_time={end}&filter_entity_id={entity_id}&significant_changes_only=0&no_attributes'
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
        data = json.loads(result.stdout)
        if data and len(data) > 0:
            return data[0]
        return []
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        print(f"Warning: Failed to fetch HA history for {entity_id}: {e}")
        return None


def fetch_ha_current():
    """Fetch current state of all power entities."""
    if not is_solar_enabled():
        return None

    token = get_ha_token()
    if not token:
        return None

    ha_entities = _get_ha_entities()
    cmd = [
        'curl', '-s',
        '-H', f'Authorization: Bearer {token}',
        f'{HA_URL}/api/states'
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
        states = json.loads(result.stdout)
        current = {}
        for state in states:
            for key, entity_id in ha_entities.items():
                if state['entity_id'] == entity_id:
                    try:
                        current[key] = float(state['state'])
                    except (ValueError, TypeError):
                        current[key] = 0.0
        return current
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


def build_hourly_solar_data(days=7):
    """
    Build hourly solar/grid/battery data from HA history.

    Returns dict keyed by (date, hour) with:
        - solar_kwh: solar production
        - grid_import_kwh: energy imported from grid
        - grid_export_kwh: energy exported to grid
        - battery_charge_kwh: energy into battery
        - battery_discharge_kwh: energy out of battery
        - source_mix: {'solar': %, 'grid': %, 'battery': %}
    """
    if not is_solar_enabled():
        return None

    ha_entities = _get_ha_entities()
    required_entities = ['solar_generated', 'grid_imported', 'grid_exported', 'battery_charged', 'battery_discharged']
    missing = [e for e in required_entities if e not in ha_entities]
    if missing:
        print(f"Warning: Missing HA entity config for: {', '.join(missing)}. Check solar.ha_entities in config.yml.")
        return None

    print("Fetching Powerwall data from Home Assistant...")

    # Fetch cumulative counters (more reliable than instantaneous)
    solar_hist = fetch_ha_history(ha_entities['solar_generated'], days)
    grid_import_hist = fetch_ha_history(ha_entities['grid_imported'], days)
    grid_export_hist = fetch_ha_history(ha_entities['grid_exported'], days)
    battery_charged_hist = fetch_ha_history(ha_entities['battery_charged'], days)
    battery_discharged_hist = fetch_ha_history(ha_entities['battery_discharged'], days)

    if not solar_hist or not grid_import_hist:
        print("Warning: Could not fetch sufficient HA data for solar integration.")
        return None

    # Build hourly buckets from cumulative data
    hourly = {}

    def ensure_bucket(date_str, hour):
        """Ensure an hourly bucket exists."""
        key = (date_str, hour)
        if key not in hourly:
            hourly[key] = {
                'solar_kwh': 0, 'grid_import_kwh': 0,
                'grid_export_kwh': 0,
                'battery_charge_kwh': 0, 'battery_discharge_kwh': 0,
                'date': date_str, 'hour': hour
            }
        return key

    def parse_ts(ts_str):
        """Parse HA timestamp to local datetime."""
        try:
            if '+' in ts_str or ts_str.endswith('Z'):
                dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            else:
                dt = datetime.fromisoformat(ts_str)
            return dt.astimezone()
        except (ValueError, TypeError):
            return None

    def process_cumulative(history, field_name):
        """
        Convert cumulative HA readings to hourly deltas.
        Interpolates across hour boundaries when readings span multiple hours.
        """
        if not history:
            return

        # Filter to valid numeric entries with timestamps
        readings = []
        for entry in history:
            try:
                val = float(entry['state'])
            except (ValueError, TypeError):
                continue
            ts = entry.get('last_changed', entry.get('last_updated', ''))
            dt = parse_ts(ts) if ts else None
            if dt:
                readings.append((dt, val))

        if len(readings) < 2:
            return

        # Sort by time
        readings.sort(key=lambda x: x[0])

        # Walk through consecutive readings, distribute delta into hourly buckets
        for i in range(1, len(readings)):
            prev_dt, prev_val = readings[i - 1]
            curr_dt, curr_val = readings[i]

            delta = curr_val - prev_val
            if delta <= 0:
                continue  # Counter reset or no change

            # If both in same hour, assign all to that hour
            prev_hour_key = (prev_dt.strftime('%Y-%m-%d'), prev_dt.hour)
            curr_hour_key = (curr_dt.strftime('%Y-%m-%d'), curr_dt.hour)

            if prev_hour_key == curr_hour_key:
                key = ensure_bucket(*curr_hour_key)
                hourly[key][field_name] += delta
            else:
                # Span multiple hours — distribute proportionally by time
                total_seconds = (curr_dt - prev_dt).total_seconds()
                if total_seconds <= 0:
                    continue

                # Walk hour by hour
                t = prev_dt
                remaining = delta
                while t < curr_dt:
                    # End of this hour
                    hour_end = t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                    if hour_end > curr_dt:
                        hour_end = curr_dt

                    frac = (hour_end - t).total_seconds() / total_seconds
                    portion = delta * frac

                    key = ensure_bucket(t.strftime('%Y-%m-%d'), t.hour)
                    hourly[key][field_name] += portion
                    remaining -= portion

                    t = hour_end

        print(f"    {field_name}: {len(readings)} data points → {len([k for k in hourly if hourly[k].get(field_name, 0) > 0])} hourly buckets")

    process_cumulative(solar_hist, 'solar_kwh')
    process_cumulative(grid_import_hist, 'grid_import_kwh')
    process_cumulative(grid_export_hist, 'grid_export_kwh')
    process_cumulative(battery_charged_hist, 'battery_charge_kwh')
    process_cumulative(battery_discharged_hist, 'battery_discharge_kwh')

    return hourly


# ==========================================
# Blended Analysis
# ==========================================

def _get_battery_efficiency():
    """Get battery round-trip efficiency from config."""
    cfg = get_config()
    return cfg.get('solar', {}).get('battery_efficiency', 0.90)


def _compute_battery_charge_source(solar_kwh, grid_import_kwh, grid_export_kwh,
                                   battery_charge_kwh, battery_discharge_kwh):
    """
    Determine how much of battery charge came from solar vs grid.

    Powerwall strategy: solar serves home first, then charges battery,
    then exports surplus. Grid only charges battery when solar can't cover it
    (e.g., off-peak grid charging for peak arbitrage).

    Returns (solar_to_battery, grid_to_battery) in kWh.
    """
    if battery_charge_kwh <= 0:
        return 0, 0

    # Solar used on-site (not exported) = total solar - exports
    solar_local = max(0, solar_kwh - grid_export_kwh)

    # Home load (from energy balance)
    # supply_to_home = solar_local + grid_import + battery_discharge
    # but battery_charge is a separate load alongside home
    home_load = max(0, solar_local + grid_import_kwh + battery_discharge_kwh - battery_charge_kwh)

    # Solar serves home first (Powerwall priority)
    solar_to_home = min(solar_local, home_load)
    solar_surplus = max(0, solar_local - solar_to_home)

    # Surplus solar goes to battery
    solar_to_battery = min(solar_surplus, battery_charge_kwh)
    grid_to_battery = max(0, battery_charge_kwh - solar_to_battery)

    return solar_to_battery, grid_to_battery


def blend_egauge_with_solar(egauge_hourly, solar_hourly):
    """
    Blend eGauge circuit data with solar/grid source data.

    Two-pass approach:
      Pass 1: Compute battery charge source attribution and battery_cost_per_kwh
      Pass 2: Apply source mix + battery cost to per-circuit consumption

    Returns enhanced register stats with actual_cost accounting for battery.
    """
    if not solar_hourly:
        return None

    battery_efficiency = _get_battery_efficiency()

    # ── Pass 1: Battery economics ──
    # Accumulate battery charge costs to derive battery_cost_per_kwh
    total_solar_to_battery = 0
    total_grid_to_battery = 0
    total_grid_charge_cost = 0
    total_charge_kwh = 0
    total_discharge_kwh = 0

    for hour_data in egauge_hourly:
        date_str = str(hour_data['date'])
        hour = hour_data['hour']
        hour_key = (date_str, hour)
        tou_period = hour_data['tou_period']
        dt = hour_data['datetime']
        rate = get_rate(dt, tou_period)

        solar_data = solar_hourly.get(hour_key)
        if not solar_data:
            continue

        s = solar_data
        solar_kwh = s.get('solar_kwh', 0)
        grid_import_kwh = s.get('grid_import_kwh', 0)
        grid_export_kwh = s.get('grid_export_kwh', 0)
        battery_charge_kwh = s.get('battery_charge_kwh', 0)
        battery_discharge_kwh = s.get('battery_discharge_kwh', 0)

        total_charge_kwh += battery_charge_kwh
        total_discharge_kwh += battery_discharge_kwh

        if battery_charge_kwh > 0:
            stb, gtb = _compute_battery_charge_source(
                solar_kwh, grid_import_kwh, grid_export_kwh,
                battery_charge_kwh, battery_discharge_kwh
            )
            total_solar_to_battery += stb
            total_grid_to_battery += gtb
            total_grid_charge_cost += gtb * rate

    # Battery cost per kWh discharged
    # Efficiency is naturally captured: less comes out than goes in,
    # so the per-kWh-out cost is higher than the per-kWh-in cost
    if total_discharge_kwh > 0:
        battery_cost_per_kwh = total_grid_charge_cost / total_discharge_kwh
    else:
        battery_cost_per_kwh = 0

    # Measured efficiency (should be ~0.90 for Powerwall 2)
    measured_efficiency = (total_discharge_kwh / total_charge_kwh) if total_charge_kwh > 0 else 0

    # Battery solar percentage
    battery_solar_pct = (total_solar_to_battery / total_charge_kwh * 100) if total_charge_kwh > 0 else 0

    print(f"  Battery: {total_charge_kwh:.1f} kWh in → {total_discharge_kwh:.1f} kWh out "
          f"({measured_efficiency:.0%} eff), {battery_solar_pct:.0f}% solar-charged, "
          f"${battery_cost_per_kwh:.3f}/kWh discharge cost")

    # ── Pass 2: Per-circuit cost attribution ──
    register_stats = defaultdict(lambda: {
        'total_kwh': 0,
        'grid_kwh': 0,
        'solar_kwh': 0,
        'battery_kwh': 0,
        'grid_cost': 0,
        'battery_cost': 0,
        'actual_cost': 0,  # grid_cost + battery_cost
        'full_rate_cost': 0,
        'solar_savings': 0,
        'by_tou': {
            'peak': {'kwh': 0, 'grid_kwh': 0, 'solar_kwh': 0, 'battery_kwh': 0, 'grid_cost': 0, 'battery_cost': 0, 'full_cost': 0},
            'part_peak': {'kwh': 0, 'grid_kwh': 0, 'solar_kwh': 0, 'battery_kwh': 0, 'grid_cost': 0, 'battery_cost': 0, 'full_cost': 0},
            'off_peak': {'kwh': 0, 'grid_kwh': 0, 'solar_kwh': 0, 'battery_kwh': 0, 'grid_cost': 0, 'battery_cost': 0, 'full_cost': 0},
        },
    })

    system = {
        'total_solar_kwh': 0,
        'total_grid_import_kwh': 0,
        'total_grid_export_kwh': 0,
        'total_battery_charge_kwh': total_charge_kwh,
        'total_battery_discharge_kwh': total_discharge_kwh,
        'total_consumption_kwh': 0,
        'total_grid_cost': 0,
        'total_battery_cost': 0,
        'total_export_credit': 0,
        'net_cost': 0,
        # Battery economics
        'battery_cost_per_kwh': battery_cost_per_kwh,
        'battery_solar_pct': round(battery_solar_pct, 1),
        'battery_solar_charge_kwh': round(total_solar_to_battery, 2),
        'battery_grid_charge_kwh': round(total_grid_to_battery, 2),
        'battery_grid_charge_cost': round(total_grid_charge_cost, 2),
        'battery_efficiency_measured': round(measured_efficiency, 3),
        'battery_efficiency_config': battery_efficiency,
        'battery_energy_lost_kwh': round(total_charge_kwh - total_discharge_kwh, 2),
        'by_tou': {
            'peak': {'solar': 0, 'grid_import': 0, 'grid_export': 0, 'battery_discharge': 0, 'consumption': 0, 'grid_cost': 0, 'battery_cost': 0, 'export_credit': 0},
            'part_peak': {'solar': 0, 'grid_import': 0, 'grid_export': 0, 'battery_discharge': 0, 'consumption': 0, 'grid_cost': 0, 'battery_cost': 0, 'export_credit': 0},
            'off_peak': {'solar': 0, 'grid_import': 0, 'grid_export': 0, 'battery_discharge': 0, 'consumption': 0, 'grid_cost': 0, 'battery_cost': 0, 'export_credit': 0},
        },
    }

    matched_hours = 0
    unmatched_hours = 0

    for hour_data in egauge_hourly:
        date_str = str(hour_data['date'])
        hour = hour_data['hour']
        hour_key = (date_str, hour)
        tou_period = hour_data['tou_period']
        dt = hour_data['datetime']
        rate = get_rate(dt, tou_period)
        export_credit = get_export_credit(dt, tou_period)

        solar_data = solar_hourly.get(hour_key)
        if solar_data:
            matched_hours += 1

            s = solar_data
            solar_kwh = s.get('solar_kwh', 0)
            grid_import_kwh = s.get('grid_import_kwh', 0)
            grid_export_kwh = s.get('grid_export_kwh', 0)
            battery_charge_kwh = s.get('battery_charge_kwh', 0)
            battery_discharge_kwh = s.get('battery_discharge_kwh', 0)

            # 3-way source mix: solar, grid, battery → home consumption
            total_supply = solar_kwh + grid_import_kwh + battery_discharge_kwh
            if total_supply > 0:
                source_mix = {
                    'solar': solar_kwh / total_supply,
                    'grid': grid_import_kwh / total_supply,
                    'battery': battery_discharge_kwh / total_supply,
                }
            else:
                source_mix = {'solar': 0, 'grid': 0, 'battery': 0}

            # System-level tracking
            system['total_solar_kwh'] += solar_kwh
            system['total_grid_import_kwh'] += grid_import_kwh
            system['total_grid_export_kwh'] += grid_export_kwh

            grid_import_cost = grid_import_kwh * rate
            battery_discharge_cost = battery_discharge_kwh * battery_cost_per_kwh
            export_earn = grid_export_kwh * export_credit

            system['total_grid_cost'] += grid_import_cost
            system['total_battery_cost'] += battery_discharge_cost
            system['total_export_credit'] += export_earn

            system['by_tou'][tou_period]['solar'] += solar_kwh
            system['by_tou'][tou_period]['grid_import'] += grid_import_kwh
            system['by_tou'][tou_period]['grid_export'] += grid_export_kwh
            system['by_tou'][tou_period]['battery_discharge'] += battery_discharge_kwh
            system['by_tou'][tou_period]['grid_cost'] += grid_import_cost
            system['by_tou'][tou_period]['battery_cost'] += battery_discharge_cost
            system['by_tou'][tou_period]['export_credit'] += export_earn
        else:
            source_mix = {'solar': 0, 'grid': 1.0, 'battery': 0}
            unmatched_hours += 1

        # Apply source mix + battery cost to each circuit
        for key, kwh in hour_data.items():
            if not isinstance(key, str) or not key.endswith('[kWh]'):
                continue

            grid_fraction = source_mix.get('grid', 1.0)
            solar_fraction = source_mix.get('solar', 0)
            battery_fraction = source_mix.get('battery', 0)

            circuit_grid_kwh = kwh * grid_fraction
            circuit_solar_kwh = kwh * solar_fraction
            circuit_battery_kwh = kwh * battery_fraction
            grid_cost = circuit_grid_kwh * rate
            bat_cost = circuit_battery_kwh * battery_cost_per_kwh
            full_cost = kwh * rate

            stats = register_stats[key]
            stats['total_kwh'] += kwh
            stats['grid_kwh'] += circuit_grid_kwh
            stats['solar_kwh'] += circuit_solar_kwh
            stats['battery_kwh'] += circuit_battery_kwh
            stats['grid_cost'] += grid_cost
            stats['battery_cost'] += bat_cost
            stats['actual_cost'] += grid_cost + bat_cost
            stats['full_rate_cost'] += full_cost
            stats['solar_savings'] += full_cost - (grid_cost + bat_cost)

            tou = stats['by_tou'][tou_period]
            tou['kwh'] += kwh
            tou['grid_kwh'] += circuit_grid_kwh
            tou['solar_kwh'] += circuit_solar_kwh
            tou['battery_kwh'] += circuit_battery_kwh
            tou['grid_cost'] += grid_cost
            tou['battery_cost'] += bat_cost
            tou['full_cost'] += full_cost

            system['total_consumption_kwh'] += kwh
            system['by_tou'][tou_period]['consumption'] += kwh

    system['net_cost'] = system['total_grid_cost'] + system['total_battery_cost'] - system['total_export_credit']

    if matched_hours + unmatched_hours > 0:
        print(f"  Solar data matched: {matched_hours}/{matched_hours + unmatched_hours} hours")

    return register_stats, system


def generate_solar_report(register_stats, system, days):
    """Generate report blending circuit data with solar economics."""
    report = []
    report.append("=" * 110)
    report.append(f"Energy Report with Solar — Last {days} Days")
    report.append(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("=" * 110)

    # System overview
    report.append("")
    report.append("SYSTEM OVERVIEW")
    report.append("-" * 110)

    solar = system['total_solar_kwh']
    grid_in = system['total_grid_import_kwh']
    grid_out = system['total_grid_export_kwh']
    consumption = system['total_consumption_kwh']

    report.append(f"  Solar Generated:     {solar:>8.1f} kWh")
    report.append(f"  Grid Imported:       {grid_in:>8.1f} kWh    (cost: ${system['total_grid_cost']:>8.2f})")
    report.append(f"  Grid Exported:       {grid_out:>8.1f} kWh    (credit: ${system['total_export_credit']:>8.2f})")
    report.append(f"  Total Consumption:   {consumption:>8.1f} kWh    (eGauge circuits)")
    self_sufficiency = ((consumption - grid_in) / consumption * 100) if consumption > 0 else 0
    report.append(f"  Self-Sufficiency:    {self_sufficiency:>7.1f}%")
    report.append("")
    report.append(f"  Grid Cost:           ${system['total_grid_cost']:>8.2f}")
    report.append(f"  Export Credits:      -${system['total_export_credit']:>7.2f}")
    report.append(f"  NET COST:            ${system['net_cost']:>8.2f}    (${system['net_cost'] / days:>6.2f}/day)")

    # Source mix by TOU period
    report.append("")
    report.append("")
    report.append("SOURCE MIX BY TOU PERIOD")
    report.append("-" * 110)
    report.append(f"  {'Period':<12} {'Solar kWh':>10} {'Grid In kWh':>12} {'Grid Out kWh':>13} {'Grid Cost':>10} {'Export Cr':>10} {'Net Cost':>10}")
    report.append("-" * 110)

    for period in ['peak', 'part_peak', 'off_peak']:
        p = system['by_tou'][period]
        net = p['grid_cost'] - p['export_credit']
        period_name = period.replace('_', '-').title()
        report.append(
            f"  {period_name:<12} {p['solar']:>10.1f} {p['grid_import']:>12.1f} "
            f"{p['grid_export']:>13.1f} ${p['grid_cost']:>9.2f} ${p['export_credit']:>9.2f} ${net:>9.2f}"
        )

    total_net = system['total_grid_cost'] - system['total_export_credit']
    report.append("-" * 110)
    report.append(
        f"  {'TOTAL':<12} {solar:>10.1f} {grid_in:>12.1f} "
        f"{grid_out:>13.1f} ${system['total_grid_cost']:>9.2f} ${system['total_export_credit']:>9.2f} ${total_net:>9.2f}"
    )

    # Circuit breakdown with solar attribution
    report.append("")
    report.append("")
    report.append("CIRCUIT COSTS — ACTUAL (Grid Only) vs WITHOUT SOLAR")
    report.append("-" * 110)
    report.append(
        f"  {'Circuit':<38} {'Total kWh':>10} {'Grid kWh':>10} {'Solar kWh':>10} "
        f"{'Actual $':>10} {'W/O Solar':>10} {'Savings':>10}"
    )
    report.append("-" * 110)

    sorted_registers = sorted(
        register_stats.items(),
        key=lambda x: x[1]['grid_cost'],
        reverse=True
    )

    total_grid_cost = 0
    total_full_cost = 0
    total_savings = 0

    for register, stats in sorted_registers:
        name = register.replace(' [kWh]', '')
        if len(name) > 37:
            name = name[:34] + '...'

        report.append(
            f"  {name:<38} {stats['total_kwh']:>10.1f} {stats['grid_kwh']:>10.1f} "
            f"{stats['solar_kwh']:>10.1f} ${stats['grid_cost']:>9.2f} "
            f"${stats['full_rate_cost']:>9.2f} ${stats['solar_savings']:>9.2f}"
        )
        total_grid_cost += stats['grid_cost']
        total_full_cost += stats['full_rate_cost']
        total_savings += stats['solar_savings']

    report.append("-" * 110)
    report.append(
        f"  {'TOTAL':<38} {'':>10} {'':>10} {'':>10} "
        f"${total_grid_cost:>9.2f} ${total_full_cost:>9.2f} ${total_savings:>9.2f}"
    )
    report.append("")
    report.append(f"  Solar saves ${total_savings:.2f}/week (${total_savings / days * 30:.2f}/month estimated)")

    # Top time-shifting opportunities
    report.append("")
    report.append("")
    report.append("TIME-SHIFTING OPPORTUNITIES (move to off-peak or solar hours)")
    report.append("-" * 110)

    for register, stats in sorted_registers[:8]:
        name = register.replace(' [kWh]', '')
        peak = stats['by_tou']['peak']
        if peak['kwh'] > 0.5:
            peak_pct = peak['kwh'] / stats['total_kwh'] * 100 if stats['total_kwh'] > 0 else 0
            # Savings if shifted to off-peak using actual config rates
            peak_rate = get_rate(datetime.now(), 'peak')
            offpeak_rate = get_rate(datetime.now(), 'off_peak')
            savings = peak['grid_kwh'] * (peak_rate - offpeak_rate)
            if savings > 0.50:
                report.append(
                    f"  {name:<38} Peak: {peak['kwh']:>6.1f} kWh ({peak_pct:>4.1f}%) "
                    f"→ shift savings: ${savings:.2f}/week"
                )

    report.append("")
    report.append("=" * 110)
    report.append("")

    return "\n".join(report)


# ==========================================
# Main Entry Point
# ==========================================

def run_blended_report(egauge_hourly, days=7):
    """
    Run a blended eGauge + Solar report.

    Args:
        egauge_hourly: Hourly consumption data from eGauge
        days: Number of days analyzed
    """
    if not is_solar_enabled():
        print("Solar integration disabled in config.yml.")
        return None, None, None

    solar_hourly = build_hourly_solar_data(days)
    if not solar_hourly:
        print("Could not fetch solar data. Run eGauge-only report instead.")
        return None, None, None

    result = blend_egauge_with_solar(egauge_hourly, solar_hourly)
    if not result:
        return None, None, None

    register_stats, system = result
    report = generate_solar_report(register_stats, system, days)
    return report, system, register_stats


if __name__ == '__main__':
    print("This module is used by egauge_weekly_analysis.py")
    print("Run: python3 egauge_weekly_analysis.py --solar")
