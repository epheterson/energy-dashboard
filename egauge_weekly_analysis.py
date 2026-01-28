#!/usr/bin/env python3
"""
eGauge Weekly Energy Analysis Script
Analyzes energy consumption and costs by register and time-of-day period.

Features:
- TOU (Time-of-Use) cost calculation
- Historical trend analysis (week-over-week comparison)
- Data persistence for long-term tracking
- Visualization charts (requires matplotlib)
- Email delivery (optional)

Usage:
    python3 egauge_weekly_analysis.py [--days N] [--output FILE] [--email] [--charts]
"""

import argparse
import csv
from datetime import datetime, timedelta
from collections import defaultdict
import subprocess
import sys
from io import StringIO
from pathlib import Path

# Import from centralized config
from config import (
    EGAUGE_URL, EGAUGE_USER, EGAUGE_PASSWORD,
    WINTER_RATES, SUMMER_RATES, EXCLUDE_REGISTERS,
    FURNACE_DAILY_THRESHOLD_KWH, HIGH_PEAK_USAGE_PERCENT,
    check_credentials, get_tou_period, is_summer, get_rate,
    REPORTS_DIR
)

# Import data persistence
from data_store import (
    store_hourly_data, store_daily_summary, store_weekly_report,
    get_previous_week_stats, get_historical_averages, cleanup_old_data
)

# ==========================================
# Data Fetching
# ==========================================

def fetch_egauge_data(days=7):
    """
    Fetch eGauge data for the last N days.
    Returns data in 1-hour intervals.
    """
    check_credentials()

    # Fetch hourly data (n parameter = number of rows)
    n_rows = days * 24

    cmd = [
        'curl', '-s', '-u', f'{EGAUGE_USER}:{EGAUGE_PASSWORD}',
        f'{EGAUGE_URL}/cgi-bin/egauge-show?c&h&n={n_rows}'
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error fetching data: {e}")
        sys.exit(1)

def parse_csv_data(csv_data):
    """
    Parse CSV data from eGauge.
    Returns list of dicts with timestamp and register values.
    """
    reader = csv.DictReader(StringIO(csv_data))
    data = []

    for row in reader:
        # Parse timestamp
        timestamp = int(row['Date & Time'])
        dt = datetime.fromtimestamp(timestamp)

        # Parse register values (they're cumulative, so we'll diff them)
        parsed_row = {
            'timestamp': timestamp,
            'datetime': dt,
            'hour': dt.hour,
            'date': dt.date(),
            'tou_period': get_tou_period(dt.hour),
        }

        # Get all register values
        for key, value in row.items():
            if key != 'Date & Time':
                try:
                    parsed_row[key] = float(value)
                except ValueError:
                    parsed_row[key] = 0.0

        data.append(parsed_row)

    # Sort by timestamp (oldest first)
    data.sort(key=lambda x: x['timestamp'])
    return data

def calculate_hourly_consumption(data):
    """
    Calculate hourly consumption from cumulative values.
    eGauge returns cumulative values, so we need to diff them.
    Negative cumulative values mean consumption.
    """
    hourly_data = []

    for i in range(1, len(data)):
        prev = data[i-1]
        curr = data[i]

        hour_consumption = {
            'datetime': curr['datetime'],
            'date': curr['date'],
            'hour': curr['hour'],
            'tou_period': curr['tou_period'],
        }

        # Calculate consumption for each register
        for key in curr.keys():
            if key.endswith('[kWh]') and key not in EXCLUDE_REGISTERS:
                # Consumption is the absolute difference (registers count down)
                consumption = abs(curr[key] - prev[key])
                hour_consumption[key] = consumption

        hourly_data.append(hour_consumption)

    return hourly_data

# ==========================================
# Analysis
# ==========================================

def analyze_data(hourly_data, days):
    """
    Analyze hourly data and generate statistics.
    """
    # Initialize accumulators
    register_stats = defaultdict(lambda: {
        'total_kwh': 0,
        'total_cost': 0,
        'by_tou': {
            'peak': {'kwh': 0, 'cost': 0, 'percent': 0},
            'part_peak': {'kwh': 0, 'cost': 0, 'percent': 0},
            'off_peak': {'kwh': 0, 'cost': 0, 'percent': 0},
        },
        'by_day': defaultdict(float),
    })

    # Accumulate data
    for hour in hourly_data:
        date = hour['date']
        tou_period = hour['tou_period']
        rate = get_rate(hour['datetime'], tou_period)

        for key, kwh in hour.items():
            if key.endswith('[kWh]'):
                register_stats[key]['total_kwh'] += kwh
                cost = kwh * rate
                register_stats[key]['total_cost'] += cost
                register_stats[key]['by_tou'][tou_period]['kwh'] += kwh
                register_stats[key]['by_tou'][tou_period]['cost'] += cost
                register_stats[key]['by_day'][date] += kwh

    # Calculate percentages and averages
    for register, stats in register_stats.items():
        total = stats['total_kwh']
        if total > 0:
            for period in ['peak', 'part_peak', 'off_peak']:
                stats['by_tou'][period]['percent'] = (stats['by_tou'][period]['kwh'] / total) * 100

        stats['avg_daily_kwh'] = total / days
        stats['avg_daily_cost'] = stats['total_cost'] / days

    return register_stats

def calculate_daily_totals(hourly_data):
    """Calculate daily totals for each day in the data."""
    daily_totals = defaultdict(lambda: {
        'total_kwh': 0,
        'total_cost': 0,
        'peak_kwh': 0,
        'peak_cost': 0,
        'part_peak_kwh': 0,
        'part_peak_cost': 0,
        'off_peak_kwh': 0,
        'off_peak_cost': 0,
        'register_totals': defaultdict(float),
    })

    for hour in hourly_data:
        date = str(hour['date'])
        tou_period = hour['tou_period']
        rate = get_rate(hour['datetime'], tou_period)

        for key, kwh in hour.items():
            if key.endswith('[kWh]'):
                cost = kwh * rate
                daily_totals[date]['total_kwh'] += kwh
                daily_totals[date]['total_cost'] += cost
                daily_totals[date][f'{tou_period}_kwh'] += kwh
                daily_totals[date][f'{tou_period}_cost'] += cost
                daily_totals[date]['register_totals'][key] += kwh

    # Convert to list format
    result = []
    for date, data in sorted(daily_totals.items()):
        result.append({
            'date': date,
            'total_kwh': data['total_kwh'],
            'total_cost': data['total_cost'],
            'peak_kwh': data['peak_kwh'],
            'peak_cost': data['peak_cost'],
            'part_peak_kwh': data['part_peak_kwh'],
            'part_peak_cost': data['part_peak_cost'],
            'off_peak_kwh': data['off_peak_kwh'],
            'off_peak_cost': data['off_peak_cost'],
            'register_totals': dict(data['register_totals']),
        })

    return result

# ==========================================
# Report Generation
# ==========================================

def generate_trend_section(current_stats, previous_week, historical_avg):
    """Generate the trend comparison section of the report."""
    lines = []
    lines.append("")
    lines.append("TREND ANALYSIS")
    lines.append("=" * 100)

    current_total_kwh = sum(s['total_kwh'] for s in current_stats.values())
    current_total_cost = sum(s['total_cost'] for s in current_stats.values())

    if previous_week:
        lines.append("")
        lines.append("Week-over-Week Comparison:")
        lines.append("-" * 100)

        prev_kwh = previous_week['total_kwh']
        prev_cost = previous_week['total_cost']

        kwh_change = current_total_kwh - prev_kwh
        cost_change = current_total_cost - prev_cost
        kwh_pct = (kwh_change / prev_kwh * 100) if prev_kwh > 0 else 0
        cost_pct = (cost_change / prev_cost * 100) if prev_cost > 0 else 0

        arrow_kwh = "^" if kwh_change > 0 else "v" if kwh_change < 0 else "="
        arrow_cost = "^" if cost_change > 0 else "v" if cost_change < 0 else "="

        lines.append(f"  {'Metric':<20} {'Previous':>15} {'Current':>15} {'Change':>15} {'%':>10}")
        lines.append("-" * 100)
        lines.append(f"  {'Energy (kWh)':<20} {prev_kwh:>15.2f} {current_total_kwh:>15.2f} {kwh_change:>+14.2f}{arrow_kwh} {kwh_pct:>+9.1f}%")
        lines.append(f"  {'Cost ($)':<20} ${prev_cost:>14.2f} ${current_total_cost:>14.2f} ${cost_change:>+13.2f}{arrow_cost} {cost_pct:>+9.1f}%")

        # Highlight significant changes
        if abs(kwh_pct) > 20:
            if kwh_change > 0:
                lines.append("")
                lines.append(f"  ! ALERT: Energy usage INCREASED by {kwh_pct:.1f}% vs last week")
            else:
                lines.append("")
                lines.append(f"  * GOOD: Energy usage DECREASED by {abs(kwh_pct):.1f}% vs last week")

    else:
        lines.append("")
        lines.append("  (No previous week data available for comparison)")
        lines.append("  Historical comparisons will be available after the next report.")

    if historical_avg:
        lines.append("")
        lines.append(f"30-Day Historical Average ({historical_avg['days_analyzed']} days of data):")
        lines.append("-" * 100)

        avg_kwh = historical_avg['avg_daily_kwh']
        avg_cost = historical_avg['avg_daily_cost']
        current_daily_kwh = current_total_kwh / 7
        current_daily_cost = current_total_cost / 7

        kwh_vs_avg = current_daily_kwh - avg_kwh
        cost_vs_avg = current_daily_cost - avg_cost

        lines.append(f"  {'Metric':<20} {'30-Day Avg':>15} {'This Week':>15} {'Difference':>15}")
        lines.append("-" * 100)
        lines.append(f"  {'Daily kWh':<20} {avg_kwh:>15.2f} {current_daily_kwh:>15.2f} {kwh_vs_avg:>+15.2f}")
        lines.append(f"  {'Daily Cost':<20} ${avg_cost:>14.2f} ${current_daily_cost:>14.2f} ${cost_vs_avg:>+14.2f}")

    return "\n".join(lines)

def generate_report(register_stats, days, previous_week=None, historical_avg=None):
    """
    Generate a formatted text report.
    """
    # Sort registers by total cost (descending)
    sorted_registers = sorted(
        register_stats.items(),
        key=lambda x: x[1]['total_cost'],
        reverse=True
    )

    report = []
    report.append("=" * 100)
    report.append(f"eGauge Energy Analysis Report - Last {days} Days")
    report.append(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("=" * 100)
    report.append("")

    # Summary table
    report.append("SUMMARY - Ranked by Total Cost")
    report.append("-" * 100)
    report.append(f"{'Register':<45} {'Total kWh':>12} {'Total Cost':>12} {'Avg/Day':>12} {'$/Day':>12}")
    report.append("-" * 100)

    total_kwh = 0
    total_cost = 0

    for register, stats in sorted_registers:
        name = register.replace(' [kWh]', '')
        report.append(
            f"{name:<45} {stats['total_kwh']:>12.2f} "
            f"${stats['total_cost']:>11.2f} {stats['avg_daily_kwh']:>12.2f} "
            f"${stats['avg_daily_cost']:>11.2f}"
        )
        total_kwh += stats['total_kwh']
        total_cost += stats['total_cost']

    report.append("-" * 100)
    report.append(f"{'TOTAL':<45} {total_kwh:>12.2f} ${total_cost:>11.2f}")
    report.append("")

    # Add trend analysis section
    if previous_week or historical_avg:
        report.append(generate_trend_section(register_stats, previous_week, historical_avg))

    # Detailed breakdown by TOU period
    report.append("")
    report.append("")
    report.append("DETAILED BREAKDOWN BY TIME-OF-USE PERIOD")
    report.append("=" * 100)

    for register, stats in sorted_registers:
        name = register.replace(' [kWh]', '')
        report.append("")
        report.append(f"{name}")
        report.append("-" * 100)
        report.append(f"{'Period':<15} {'kWh':>12} {'Cost':>12} {'% of Total':>12} {'Avg Rate':>12}")
        report.append("-" * 100)

        for period in ['peak', 'part_peak', 'off_peak']:
            period_data = stats['by_tou'][period]
            kwh = period_data['kwh']
            cost = period_data['cost']
            percent = period_data['percent']
            avg_rate = cost / kwh if kwh > 0 else 0

            period_name = period.replace('_', '-').title()
            report.append(
                f"{period_name:<15} {kwh:>12.2f} ${cost:>11.2f} "
                f"{percent:>11.1f}% ${avg_rate:>11.4f}"
            )

        report.append("-" * 100)
        report.append(
            f"{'TOTAL':<15} {stats['total_kwh']:>12.2f} ${stats['total_cost']:>11.2f} "
            f"{100.0:>11.1f}%"
        )

    # Alerts section
    report.append("")
    report.append("")
    report.append("ALERTS & RECOMMENDATIONS")
    report.append("=" * 100)

    alerts = []

    # Check furnace usage
    furnace_key = 'CT 14 - Furnace [kWh]'
    if furnace_key in register_stats:
        furnace_daily = register_stats[furnace_key]['avg_daily_kwh']
        if furnace_daily > FURNACE_DAILY_THRESHOLD_KWH:
            alerts.append(
                f"!! FURNACE HIGH USAGE: {furnace_daily:.1f} kWh/day "
                f"(threshold: {FURNACE_DAILY_THRESHOLD_KWH} kWh/day)"
            )

    # Check for high peak usage
    for register, stats in sorted_registers:
        peak_percent = stats['by_tou']['peak']['percent']
        if peak_percent > HIGH_PEAK_USAGE_PERCENT:
            name = register.replace(' [kWh]', '')
            alerts.append(
                f"!! {name}: {peak_percent:.1f}% usage during PEAK hours "
                f"(${stats['by_tou']['peak']['cost']:.2f})"
            )

    # Find most expensive time-shifters
    report.append("")
    report.append("Potential Savings from Time-Shifting:")
    report.append("-" * 100)

    for register, stats in sorted_registers[:5]:  # Top 5 by cost
        name = register.replace(' [kWh]', '')
        peak_kwh = stats['by_tou']['peak']['kwh']
        peak_cost = stats['by_tou']['peak']['cost']

        if peak_kwh > 0:
            # Calculate savings if peak usage was shifted to off-peak
            peak_rate = get_rate(datetime.now(), 'peak')
            offpeak_rate = get_rate(datetime.now(), 'off_peak')
            potential_savings = peak_kwh * (peak_rate - offpeak_rate)

            if potential_savings > 1:  # Only show if >$1 potential savings
                report.append(
                    f"{name:<45} Peak: {peak_kwh:>8.1f} kWh "
                    f"-> Potential savings: ${potential_savings:>6.2f}/week"
                )

    if alerts:
        report.append("")
        report.append("Active Alerts:")
        report.append("-" * 100)
        for alert in alerts:
            report.append(alert)
    else:
        report.append("")
        report.append("[OK] No alerts - all consumption within normal parameters")

    report.append("")
    report.append("=" * 100)
    report.append("")
    report.append("TOU Period Definitions:")
    report.append("  Peak: 4:00 PM - 9:00 PM (highest rates)")
    report.append("  Part-Peak: 3:00 PM - 4:00 PM and 9:00 PM - 12:00 AM (medium rates)")
    report.append("  Off-Peak: 12:00 AM - 3:00 PM (lowest rates)")
    report.append("")

    return "\n".join(report)

# ==========================================
# Main Program
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description='Analyze eGauge energy consumption and costs'
    )
    parser.add_argument(
        '--days', type=int, default=7,
        help='Number of days to analyze (default: 7)'
    )
    parser.add_argument(
        '--output', type=str,
        help='Save report to file instead of printing'
    )
    parser.add_argument(
        '--charts', action='store_true',
        help='Generate visualization charts (requires matplotlib)'
    )
    parser.add_argument(
        '--email', action='store_true',
        help='Send report via email (requires EMAIL_ENABLED=true in .env)'
    )
    parser.add_argument(
        '--no-store', action='store_true',
        help='Do not store data in local database'
    )
    parser.add_argument(
        '--html', type=str,
        help='Save HTML report to file (for preview in browser)'
    )

    args = parser.parse_args()

    print(f"Fetching {args.days} days of data from eGauge...")
    csv_data = fetch_egauge_data(args.days)

    print("Parsing data...")
    data = parse_csv_data(csv_data)

    print("Calculating hourly consumption...")
    hourly_data = calculate_hourly_consumption(data)

    print("Analyzing consumption patterns...")
    register_stats = analyze_data(hourly_data, args.days)

    # Calculate daily totals
    daily_data = calculate_daily_totals(hourly_data)

    # Store data for historical tracking
    if not args.no_store:
        print("Storing data for historical analysis...")
        store_hourly_data(hourly_data)
        for day in daily_data:
            store_daily_summary(day['date'], day)
        cleanup_old_data()

    # Get historical data for trend analysis
    print("Loading historical data for comparison...")
    start_date = datetime.now() - timedelta(days=args.days)
    previous_week = get_previous_week_stats(start_date)
    historical_avg = get_historical_averages(30)

    print("Generating report...")
    report = generate_report(register_stats, args.days, previous_week, historical_avg)

    # Store weekly report
    if not args.no_store:
        total_kwh = sum(s['total_kwh'] for s in register_stats.values())
        total_cost = sum(s['total_cost'] for s in register_stats.values())
        week_start = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
        week_end = datetime.now().strftime('%Y-%m-%d')

        # Convert register_stats for JSON storage
        stats_for_storage = {}
        for reg, stats in register_stats.items():
            stats_for_storage[reg] = {
                'total_kwh': stats['total_kwh'],
                'total_cost': stats['total_cost'],
                'avg_daily_kwh': stats['avg_daily_kwh'],
                'avg_daily_cost': stats['avg_daily_cost'],
                'by_tou': stats['by_tou'],
            }

        store_weekly_report(week_start, week_end, total_kwh, total_cost,
                           stats_for_storage, report)

    # Generate charts if requested
    chart_paths = []
    if args.charts:
        try:
            from visualization import generate_all_charts
            print("Generating charts...")
            chart_paths = generate_all_charts(
                register_stats,
                daily_data=daily_data,
                previous_period=previous_week,
                days=args.days
            )
            if chart_paths:
                print(f"Charts saved to: {chart_paths[0].parent}")
        except ImportError:
            print("Warning: Could not import visualization module. Skipping charts.")

    # Save or print report
    if args.output:
        # Ensure reports directory exists
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w') as f:
            f.write(report)
        print(f"\nReport saved to: {args.output}")
    else:
        print("\n" + report)

    # Generate HTML report if requested
    if args.html:
        try:
            from html_report import generate_html_report
            print("Generating HTML report...")
            html = generate_html_report(
                dict(register_stats),
                args.days,
                previous_week,
                historical_avg,
                daily_data
            )
            html_path = Path(args.html)
            html_path.parent.mkdir(parents=True, exist_ok=True)
            with open(html_path, 'w') as f:
                f.write(html)
            print(f"HTML report saved to: {args.html}")
            print(f"Open in browser: file://{html_path.absolute()}")
        except ImportError:
            print("Warning: Could not import html_report module. Skipping HTML generation.")

    # Send email if requested
    if args.email:
        try:
            from email_notify import send_weekly_report
            print("Sending HTML email...")
            if send_weekly_report(
                report_text=report,
                chart_paths=chart_paths,
                register_stats=dict(register_stats),
                days=args.days,
                previous_week=previous_week,
                historical_avg=historical_avg,
                daily_data=daily_data
            ):
                print("Email sent successfully!")
        except ImportError:
            print("Warning: Could not import email module. Skipping email.")

    # Print chart locations
    if chart_paths:
        print("\nGenerated charts:")
        for path in chart_paths:
            print(f"  - {path}")

if __name__ == '__main__':
    main()
