#!/usr/bin/env python3
"""
Before/After Comparison Analysis
Compare energy usage before and after a change (e.g., equipment swap).

Example: Mattress pad heater swap from electric coil to water-based heater.

Uses live data from eGauge to provide current analysis.
"""

import argparse
from datetime import datetime, timedelta
from collections import defaultdict

from device_analysis import (
    fetch_data, parse_and_calculate_hourly, get_register_stats,
    analyze_before_after, format_currency, format_kwh
)
from config import WINTER_RATES


def main():
    parser = argparse.ArgumentParser(
        description='Compare energy usage before and after a change'
    )
    parser.add_argument(
        '--days', type=int, default=14,
        help='Number of days to analyze (default: 14, to capture before/after)'
    )
    parser.add_argument(
        '--register', type=str, default='CT 12 - Back Rooms [kWh]',
        help='Register name to analyze'
    )
    parser.add_argument(
        '--change-date', type=str,
        help='Date of the change (YYYY-MM-DD). Default: 7 days ago'
    )
    parser.add_argument(
        '--description', type=str, default='Equipment change',
        help='Description of the change'
    )
    parser.add_argument(
        '--overnight-only', action='store_true',
        help='Only analyze overnight hours (10pm-6am)'
    )
    args = parser.parse_args()

    # Parse change date
    if args.change_date:
        change_date = datetime.strptime(args.change_date, '%Y-%m-%d')
    else:
        change_date = datetime.now() - timedelta(days=7)

    print("Fetching data from eGauge...")
    csv_data = fetch_data(args.days)

    print("Analyzing data...")
    hourly_data = parse_and_calculate_hourly(csv_data)

    # Set up hour filter
    hours_filter = (22, 6) if args.overnight_only else None
    hours_desc = "Overnight (10 PM - 6 AM)" if args.overnight_only else "All hours"

    # Analyze before/after
    comparison = analyze_before_after(
        hourly_data,
        args.register,
        change_date,
        hours_filter
    )

    register_name = args.register.replace(' [kWh]', '')

    print()
    print("=" * 80)
    print("BEFORE/AFTER COMPARISON ANALYSIS")
    print("=" * 80)
    print()
    print(f"Circuit: {register_name}")
    print(f"Change Date: {change_date.strftime('%A, %B %d, %Y')}")
    print(f"Change: {args.description}")
    print(f"Analysis Period: Last {args.days} days")
    print(f"Hours Analyzed: {hours_desc}")
    print()

    print("=" * 80)
    print(f"USAGE COMPARISON ({hours_desc})")
    print("=" * 80)
    print()

    # Before section
    print("BEFORE CHANGE:")
    print("-" * 80)

    if comparison['before']['days_count'] > 0:
        for date, total in sorted(comparison['before']['daily_totals'].items()):
            day_name = datetime.combine(date, datetime.min.time()).strftime('%A') if hasattr(date, 'year') else ''
            print(f"  {date}: {format_kwh(total)}")
        print("-" * 80)
        print(f"  Average: {format_kwh(comparison['before']['average'])} per day ({comparison['before']['days_count']} days)")
    else:
        print("  No data available before the change date")

    print()

    # After section
    print("AFTER CHANGE:")
    print("-" * 80)

    if comparison['after']['days_count'] > 0:
        for date, total in sorted(comparison['after']['daily_totals'].items()):
            day_name = datetime.combine(date, datetime.min.time()).strftime('%A') if hasattr(date, 'year') else ''
            print(f"  {date}: {format_kwh(total)} <- AFTER CHANGE")
        print("-" * 80)
        print(f"  Average: {format_kwh(comparison['after']['average'])} per day ({comparison['after']['days_count']} days)")
    else:
        print("  No data available after the change date")

    print()
    print("=" * 80)
    print("COMPARISON")
    print("=" * 80)
    print()

    before_avg = comparison['before']['average']
    after_avg = comparison['after']['average']
    diff = comparison['difference']
    pct_change = comparison['percent_change']

    if before_avg > 0 and after_avg > 0:
        print(f"Before change: {format_kwh(before_avg)}/day (avg)")
        print(f"After change:  {format_kwh(after_avg)}/day (avg)")
        print(f"Difference:    {diff:+.3f} kWh/day ({pct_change:+.1f}%)")
        print()

        # Calculate cost impact (assuming mostly off-peak for overnight)
        rate = WINTER_RATES['off_peak']

        if diff < -0.05:  # Decrease
            print(f"[OK] DECREASE: Usage REDUCED by {format_kwh(abs(diff))}/day")
            print()
            print(f"Cost savings (at off-peak rate ${rate}/kWh):")

            savings_per_day = abs(diff) * rate
            print(f"  Per day:   {format_currency(savings_per_day)}")
            print(f"  Per week:  {format_currency(savings_per_day * 7)}")
            print(f"  Per month: {format_currency(savings_per_day * 30)}")
            print(f"  Per year:  {format_currency(savings_per_day * 365)}")
            print()
            print("Great upgrade! The new equipment is more efficient.")

        elif diff > 0.05:  # Increase
            print(f"[!] INCREASE: Usage INCREASED by {format_kwh(diff)}/day")
            print()
            print(f"Additional cost (at off-peak rate ${rate}/kWh):")

            cost_per_day = diff * rate
            print(f"  Per day:   {format_currency(cost_per_day)}")
            print(f"  Per year:  {format_currency(cost_per_day * 365)}")
            print()
            print("Possible explanations:")
            print("  - New equipment may be set to higher output")
            print("  - Old equipment wasn't being used much before")
            print("  - Weather/temperature differences between periods")
            print("  - Need more data to see clear trend")

        else:  # No significant change
            print("[=] NO SIGNIFICANT CHANGE (difference < 0.05 kWh)")
            print()
            print("Possible explanations:")
            print("  - Both systems consume similar energy")
            print("  - Need more data to see clear trend")
            print("  - Weather variations masking the difference")
    else:
        print("[!] Not enough data to compare before/after")
        print()
        print("Try running with a longer analysis period:")
        print(f"  python3 mattress_pad_analysis.py --days 21 --change-date {change_date.strftime('%Y-%m-%d')}")

    print()

    # Full 24-hour comparison if we were filtering overnight
    if args.overnight_only:
        print("=" * 80)
        print(f"FULL {register_name} USAGE (24 hours)")
        print("=" * 80)
        print()

        full_comparison = analyze_before_after(
            hourly_data,
            args.register,
            change_date,
            hours_filter=None
        )

        print("Daily usage (all hours):")
        print()

        all_dates = set(full_comparison['before']['daily_totals'].keys()) | set(full_comparison['after']['daily_totals'].keys())
        for date in sorted(all_dates):
            if date in full_comparison['after']['daily_totals']:
                total = full_comparison['after']['daily_totals'][date]
                label = "<- AFTER CHANGE"
            else:
                total = full_comparison['before']['daily_totals'].get(date, 0)
                label = "(before)"
            day_name = datetime.combine(date, datetime.min.time()).strftime('%A') if hasattr(date, 'year') else ''
            print(f"  {date}: {format_kwh(total)} {label}")

        print()

        if full_comparison['before']['average'] > 0 and full_comparison['after']['average'] > 0:
            print(f"Average before: {format_kwh(full_comparison['before']['average'])}/day")
            print(f"Average after:  {format_kwh(full_comparison['after']['average'])}/day")
            print(f"Full-day difference: {full_comparison['difference']:+.2f} kWh/day")

    print()
    print("=" * 80)
    print()

    if args.overnight_only:
        print(f"Note: This analysis focuses on overnight hours ({hours_desc}) when")
        print("the mattress pad heater is most likely to be in use.")
    else:
        print("Tip: Use --overnight-only to focus on specific hours (e.g., for mattress pad analysis)")

    print()


if __name__ == '__main__':
    main()
