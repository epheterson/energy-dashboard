#!/usr/bin/env python3
"""
Furnace Efficiency Analysis (CT 14)
Analyzes furnace energy usage and calculates potential savings.

Uses live data from eGauge to provide current analysis.
"""

import argparse
from datetime import datetime

from device_analysis import (
    fetch_data, parse_and_calculate_hourly, get_register_stats,
    calculate_savings_scenarios, get_tou_distribution_from_stats,
    format_currency, format_kwh
)
from config import WINTER_RATES, SUMMER_RATES, is_summer


REGISTER_NAME = 'CT 14 - Furnace [kWh]'
OPTIMAL_RANGE = (2.0, 4.0)  # Expected kWh/day for efficient furnace blower


def main():
    parser = argparse.ArgumentParser(
        description='Analyze furnace efficiency and potential savings'
    )
    parser.add_argument(
        '--days', type=int, default=7,
        help='Number of days to analyze (default: 7)'
    )
    args = parser.parse_args()

    print("Fetching data from eGauge...")
    csv_data = fetch_data(args.days)

    print("Analyzing furnace usage...")
    hourly_data = parse_and_calculate_hourly(csv_data)
    stats = get_register_stats(hourly_data, REGISTER_NAME, args.days)

    # Get current rates
    rates = SUMMER_RATES if is_summer(datetime.now()) else WINTER_RATES
    season = "Summer" if is_summer(datetime.now()) else "Winter"

    # Calculate TOU distribution
    tou_dist = get_tou_distribution_from_stats(stats)

    print()
    print("=" * 80)
    print("FURNACE EFFICIENCY SAVINGS ANALYSIS")
    print(f"Analysis Period: Last {args.days} days | Season: {season}")
    print("=" * 80)
    print()

    print("CURRENT USAGE (CT 14 - Furnace):")
    print(f"  Daily: {format_kwh(stats['avg_daily_kwh'])} @ {format_currency(stats['avg_daily_cost'])}/day")
    print(f"  Weekly: {format_kwh(stats['total_kwh'])} @ {format_currency(stats['total_cost'])}")
    print(f"  Monthly (est): {format_kwh(stats['avg_daily_kwh'] * 30)} @ {format_currency(stats['avg_daily_cost'] * 30)}")
    print(f"  Annually (est): {format_kwh(stats['avg_daily_kwh'] * 365)} @ {format_currency(stats['avg_daily_cost'] * 365)}")
    print()

    print("TOU DISTRIBUTION:")
    print(f"  Peak (4-9pm):        {tou_dist['peak']*100:.1f}%")
    print(f"  Part-Peak:           {tou_dist['part_peak']*100:.1f}%")
    print(f"  Off-Peak (12am-3pm): {tou_dist['off_peak']*100:.1f}%")
    print()

    print("OPTIMAL USAGE (Gas Furnace Blower):")
    print(f"  Expected range: {OPTIMAL_RANGE[0]}-{OPTIMAL_RANGE[1]} kWh/day for a properly functioning blower motor")
    print()

    # Assess current usage
    if stats['avg_daily_kwh'] < OPTIMAL_RANGE[0]:
        status = "EXCELLENT - Below typical range"
    elif stats['avg_daily_kwh'] <= OPTIMAL_RANGE[1]:
        status = "NORMAL - Within expected range"
    elif stats['avg_daily_kwh'] <= OPTIMAL_RANGE[1] * 2:
        status = "HIGH - Above expected range"
    else:
        status = "VERY HIGH - Significantly above expected range"

    print(f"Status: {status}")
    print()

    # Calculate savings scenarios
    scenarios = calculate_savings_scenarios(
        stats['avg_daily_kwh'],
        stats['avg_daily_cost'],
        OPTIMAL_RANGE,
        tou_dist
    )

    print("=" * 80)
    print("POTENTIAL SAVINGS SCENARIOS")
    print("=" * 80)
    print()

    for scenario in scenarios:
        print(f"{scenario['name']}:")
        print(f"  Target usage: {format_kwh(scenario['target_kwh'])}/day @ {format_currency(scenario['target_cost_per_day'])}/day")
        print(f"  Excess usage: {format_kwh(scenario['excess_kwh_per_day'])}/day")
        print()
        print(f"  SAVINGS:")
        print(f"    Per day:   {format_currency(scenario['savings_per_day'])}")
        print(f"    Per week:  {format_currency(scenario['savings_per_week'])}")
        print(f"    Per month: {format_currency(scenario['savings_per_month'])}")
        print(f"    Per year:  {format_currency(scenario['savings_per_year'])}")
        print()
        print("-" * 80)
        print()

    # Most likely scenario (mid-range optimal)
    mid_scenario = scenarios[1]  # Mid scenario

    print("=" * 80)
    print(f"MOST LIKELY SCENARIO ({mid_scenario['name']})")
    print("=" * 80)
    print()
    print(f"Current annual cost:  {format_currency(stats['avg_daily_cost'] * 365)}")
    print(f"Optimal annual cost:  {format_currency(mid_scenario['target_cost_per_day'] * 365)}")
    print(f"POTENTIAL ANNUAL SAVINGS: {format_currency(mid_scenario['savings_per_year'])}")
    print()
    print(f"That's {format_currency(mid_scenario['savings_per_year'] / 12)} per month!")
    print()

    print("=" * 80)
    print("WHAT COULD BE CAUSING HIGH USAGE?")
    print("=" * 80)
    print()
    print("If CT 14 is truly measuring only the furnace blower, possible causes:")
    print()
    print("1. OVERSIZED BLOWER MOTOR")
    print("   - Older furnaces may have inefficient, oversized blowers")
    print("   - Modern ECM (electronically commutated motor) blowers use 60-80% less energy")
    print()
    print("2. RESTRICTED AIRFLOW (most common)")
    print("   - Clogged air filter forcing motor to work harder")
    print("   - Blocked return vents")
    print("   - Closed/blocked supply registers")
    print("   -> CHECK YOUR AIR FILTER FIRST!")
    print()
    print("3. EXCESSIVE RUNTIME")
    print("   - Thermostat fan set to 'ON' instead of 'AUTO'")
    print("   - Furnace cycling too frequently (short cycling)")
    print("   - Thermostat set too high")
    print()
    print("4. CT 14 MEASURING MORE THAN JUST THE BLOWER")
    print("   - Could include other equipment on the same circuit")
    print("   - Gas furnace igniter/controls (minimal)")
    print("   - Humidifier")
    print("   - Electronic air cleaner")
    print("   - Whole-house fan")
    print()

    print("=" * 80)
    print("RECOMMENDED ACTIONS")
    print("=" * 80)
    print()
    print("IMMEDIATE (Free/Low Cost):")
    print("  1. Check/replace air filter")
    print("  2. Verify thermostat fan is on 'AUTO' not 'ON'")
    print("  3. Ensure all vents are open and unblocked")
    print("  4. Monitor when CT 14 shows usage (does it match furnace running?)")
    print()
    print("INVESTIGATION:")
    print("  5. Use quick_check.sh at different times to see when CT 14 is active")
    print("  6. Turn furnace breaker OFF and check if CT 14 goes to zero")
    print("  7. If CT 14 doesn't go to zero, something else is on that circuit")
    print()
    print("PROFESSIONAL:")
    print("  8. HVAC technician inspection ($100-150)")
    print("     - Check blower motor amp draw")
    print("     - Test static pressure (airflow restriction)")
    print("     - Evaluate overall system efficiency")
    print()
    print("UPGRADE (if needed):")

    # Calculate payback period for ECM motor upgrade
    upgrade_cost = 600  # Mid-range estimate
    payback_months = upgrade_cost / mid_scenario['savings_per_month'] if mid_scenario['savings_per_month'] > 0 else float('inf')

    print(f"  9. Replace blower motor with ECM motor ($400-800 installed)")
    print(f"     - Payback period: ~{payback_months:.0f} months based on savings above")
    print(f"     - Modern ECM motors use {OPTIMAL_RANGE[0]}-{OPTIMAL_RANGE[1]} kWh/day vs {stats['avg_daily_kwh']:.1f} kWh/day")
    print()
    print("=" * 80)


if __name__ == '__main__':
    main()
