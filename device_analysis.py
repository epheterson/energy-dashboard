#!/usr/bin/env python3
"""
Device-specific analysis utilities for eGauge Energy Analysis Toolkit.
Provides functions for analyzing individual circuits/registers.
"""

import subprocess
import csv
from datetime import datetime, timedelta
from io import StringIO
from collections import defaultdict
from typing import Dict, List, Tuple, Optional

from config import (
    EGAUGE_URL, EGAUGE_USER, EGAUGE_PASSWORD,
    WINTER_RATES, SUMMER_RATES, EXCLUDE_REGISTERS,
    check_credentials, get_tou_period, is_summer, get_rate,
    DEVICE_REGISTERS
)


def fetch_data(days: int = 7) -> str:
    """Fetch hourly data from eGauge for the specified number of days."""
    check_credentials()

    n_rows = days * 24
    cmd = [
        'curl', '-s', '-u', f'{EGAUGE_USER}:{EGAUGE_PASSWORD}',
        f'{EGAUGE_URL}/cgi-bin/egauge-show?c&h&n={n_rows}'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def parse_and_calculate_hourly(csv_data: str) -> List[Dict]:
    """Parse CSV data and calculate hourly consumption values."""
    reader = csv.DictReader(StringIO(csv_data))
    data = []

    for row in reader:
        timestamp = int(row['Date & Time'])
        dt = datetime.fromtimestamp(timestamp)

        parsed_row = {
            'timestamp': timestamp,
            'datetime': dt,
            'hour': dt.hour,
            'date': dt.date(),
            'tou_period': get_tou_period(dt.hour),
        }

        for key, value in row.items():
            if key != 'Date & Time':
                try:
                    parsed_row[key] = float(value)
                except ValueError:
                    parsed_row[key] = 0.0

        data.append(parsed_row)

    data.sort(key=lambda x: x['timestamp'])

    # Calculate hourly consumption
    hourly = []
    for i in range(1, len(data)):
        prev = data[i-1]
        curr = data[i]

        hour_consumption = {
            'datetime': curr['datetime'],
            'date': curr['date'],
            'hour': curr['hour'],
            'tou_period': curr['tou_period'],
        }

        for key in curr.keys():
            if key.endswith('[kWh]') and key not in EXCLUDE_REGISTERS:
                consumption = abs(curr[key] - prev[key])
                hour_consumption[key] = consumption

        hourly.append(hour_consumption)

    return hourly


def get_register_stats(
    hourly_data: List[Dict],
    register_name: str,
    days: int = 7
) -> Dict:
    """
    Get statistics for a specific register.

    Args:
        hourly_data: List of hourly consumption dictionaries
        register_name: Name of the register (e.g., 'CT 14 - Furnace [kWh]')
        days: Number of days in the analysis period

    Returns dictionary with:
        - total_kwh, total_cost
        - avg_daily_kwh, avg_daily_cost
        - by_tou: breakdown by TOU period
        - by_day: consumption by date
    """
    stats = {
        'total_kwh': 0,
        'total_cost': 0,
        'by_tou': {
            'peak': {'kwh': 0, 'cost': 0, 'percent': 0},
            'part_peak': {'kwh': 0, 'cost': 0, 'percent': 0},
            'off_peak': {'kwh': 0, 'cost': 0, 'percent': 0},
        },
        'by_day': defaultdict(float),
        'by_hour': defaultdict(list),
    }

    for hour in hourly_data:
        if register_name in hour:
            kwh = hour[register_name]
            tou_period = hour['tou_period']
            rate = get_rate(hour['datetime'], tou_period)
            cost = kwh * rate

            stats['total_kwh'] += kwh
            stats['total_cost'] += cost
            stats['by_tou'][tou_period]['kwh'] += kwh
            stats['by_tou'][tou_period]['cost'] += cost
            stats['by_day'][hour['date']] += kwh
            stats['by_hour'][hour['hour']].append(kwh)

    # Calculate percentages
    total = stats['total_kwh']
    if total > 0:
        for period in ['peak', 'part_peak', 'off_peak']:
            stats['by_tou'][period]['percent'] = (
                stats['by_tou'][period]['kwh'] / total * 100
            )

    stats['avg_daily_kwh'] = total / days if days > 0 else 0
    stats['avg_daily_cost'] = stats['total_cost'] / days if days > 0 else 0

    # Calculate hourly averages
    stats['hourly_averages'] = {}
    for hour, values in stats['by_hour'].items():
        stats['hourly_averages'][hour] = sum(values) / len(values) if values else 0

    return stats


def calculate_optimal_cost(
    kwh_per_day: float,
    tou_distribution: Dict[str, float],
    rates: Dict[str, float] = None
) -> float:
    """
    Calculate daily cost given kWh and TOU distribution.

    Args:
        kwh_per_day: Energy consumption per day
        tou_distribution: Dict with 'peak', 'part_peak', 'off_peak' percentages (0-1)
        rates: Optional rate dict (defaults to winter rates)
    """
    if rates is None:
        rates = WINTER_RATES

    cost = 0
    for period, percentage in tou_distribution.items():
        kwh = kwh_per_day * percentage
        cost += kwh * rates[period]
    return cost


def calculate_savings_scenarios(
    current_kwh_per_day: float,
    current_cost_per_day: float,
    optimal_range: Tuple[float, float],
    tou_distribution: Dict[str, float]
) -> List[Dict]:
    """
    Calculate savings for different optimal scenarios.

    Args:
        current_kwh_per_day: Current daily consumption
        current_cost_per_day: Current daily cost
        optimal_range: (min, max) optimal kWh/day
        tou_distribution: Current TOU distribution

    Returns list of scenarios with potential savings.
    """
    scenarios = []
    low, high = optimal_range
    mid = (low + high) / 2

    for name, target_kwh in [
        (f"Low ({low} kWh/day)", low),
        (f"Mid ({mid} kWh/day)", mid),
        (f"High ({high} kWh/day)", high),
    ]:
        target_cost = calculate_optimal_cost(target_kwh, tou_distribution)
        savings_per_day = current_cost_per_day - target_cost
        excess_kwh = current_kwh_per_day - target_kwh

        scenarios.append({
            'name': name,
            'target_kwh': target_kwh,
            'target_cost_per_day': target_cost,
            'excess_kwh_per_day': excess_kwh,
            'savings_per_day': savings_per_day,
            'savings_per_week': savings_per_day * 7,
            'savings_per_month': savings_per_day * 30,
            'savings_per_year': savings_per_day * 365,
        })

    return scenarios


def get_tou_distribution_from_stats(stats: Dict) -> Dict[str, float]:
    """Extract TOU distribution (as decimals 0-1) from register stats."""
    total = stats['total_kwh']
    if total == 0:
        return {'peak': 0.333, 'part_peak': 0.333, 'off_peak': 0.334}

    return {
        'peak': stats['by_tou']['peak']['kwh'] / total,
        'part_peak': stats['by_tou']['part_peak']['kwh'] / total,
        'off_peak': stats['by_tou']['off_peak']['kwh'] / total,
    }


def analyze_before_after(
    hourly_data: List[Dict],
    register_name: str,
    change_date: datetime,
    hours_filter: Optional[Tuple[int, int]] = None
) -> Dict:
    """
    Analyze consumption before and after a change date.

    Args:
        hourly_data: List of hourly consumption dictionaries
        register_name: Name of the register to analyze
        change_date: Date when the change occurred
        hours_filter: Optional (start_hour, end_hour) to filter specific hours
                     e.g., (22, 6) for overnight (10pm-6am)

    Returns dict with before/after statistics and comparison.
    """
    before = defaultdict(list)
    after = defaultdict(list)

    for entry in hourly_data:
        if register_name not in entry:
            continue

        dt = entry['datetime']
        hour = entry['hour']
        usage = entry[register_name]
        date = entry['date']

        # Apply hour filter if specified
        if hours_filter:
            start_h, end_h = hours_filter
            if start_h > end_h:  # Overnight case (e.g., 22-6)
                if not (hour >= start_h or hour < end_h):
                    continue
            else:
                if not (start_h <= hour < end_h):
                    continue

        if dt < change_date:
            before[date].append(usage)
        else:
            after[date].append(usage)

    # Calculate daily totals and averages
    before_days = [sum(hours) for hours in before.values()]
    after_days = [sum(hours) for hours in after.values()]

    avg_before = sum(before_days) / len(before_days) if before_days else 0
    avg_after = sum(after_days) / len(after_days) if after_days else 0
    diff = avg_after - avg_before

    return {
        'before': {
            'daily_totals': dict(zip(before.keys(), before_days)),
            'average': avg_before,
            'days_count': len(before_days),
        },
        'after': {
            'daily_totals': dict(zip(after.keys(), after_days)),
            'average': avg_after,
            'days_count': len(after_days),
        },
        'difference': diff,
        'percent_change': (diff / avg_before * 100) if avg_before > 0 else 0,
    }


def format_currency(value: float) -> str:
    """Format a value as currency."""
    return f"${value:,.2f}"


def format_kwh(value: float) -> str:
    """Format a value as kWh."""
    return f"{value:.2f} kWh"
