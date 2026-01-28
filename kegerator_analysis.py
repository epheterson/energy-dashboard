#!/usr/bin/env python3
"""
Kegerator/Mini Fridge Efficiency Analysis (CT 16)
Analyzes kegerator energy usage and provides optimization tips.

Uses live data from eGauge to provide current analysis.
"""

import argparse
from datetime import datetime

from device_analysis import (
    fetch_data, parse_and_calculate_hourly, get_register_stats,
    get_tou_distribution_from_stats, format_currency, format_kwh
)
from config import WINTER_RATES, SUMMER_RATES, is_summer


REGISTER_NAME = 'CT 16 [kWh]'
TYPICAL_RANGES = {
    'small_mini_fridge': (0.5, 1.0),
    'medium_mini_fridge': (1.0, 2.0),
    'kegerator_single': (2.0, 3.0),
    'kegerator_dual': (3.0, 4.5),
    'full_size_fridge': (1.5, 4.0),
}


def main():
    parser = argparse.ArgumentParser(
        description='Analyze kegerator/mini fridge efficiency'
    )
    parser.add_argument(
        '--days', type=int, default=7,
        help='Number of days to analyze (default: 7)'
    )
    parser.add_argument(
        '--register', type=str, default=REGISTER_NAME,
        help=f'Register name to analyze (default: {REGISTER_NAME})'
    )
    args = parser.parse_args()

    print("Fetching data from eGauge...")
    csv_data = fetch_data(args.days)

    print("Analyzing kegerator usage...")
    hourly_data = parse_and_calculate_hourly(csv_data)
    stats = get_register_stats(hourly_data, args.register, args.days)

    # Get TOU distribution
    tou_dist = get_tou_distribution_from_stats(stats)

    print()
    print("=" * 80)
    print("KEGERATOR/MINI FRIDGE ANALYSIS")
    print(f"Register: {args.register.replace(' [kWh]', '')}")
    print(f"Analysis Period: Last {args.days} days")
    print("=" * 80)
    print()

    print("CURRENT USAGE:")
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
    print("  [OK] Pretty evenly distributed (expected for 24/7 refrigeration)")
    print()

    print("=" * 80)
    print("IS THIS NORMAL?")
    print("=" * 80)
    print()

    print("Typical kegerator/mini fridge energy usage:")
    print()
    print(f"  Small mini fridge (1.7 cu ft):     {TYPICAL_RANGES['small_mini_fridge'][0]} - {TYPICAL_RANGES['small_mini_fridge'][1]} kWh/day")
    print(f"  Medium mini fridge (3-4 cu ft):    {TYPICAL_RANGES['medium_mini_fridge'][0]} - {TYPICAL_RANGES['medium_mini_fridge'][1]} kWh/day")
    print(f"  Kegerator (single tap):            {TYPICAL_RANGES['kegerator_single'][0]} - {TYPICAL_RANGES['kegerator_single'][1]} kWh/day")
    print(f"  Kegerator (dual tap/larger):       {TYPICAL_RANGES['kegerator_dual'][0]} - {TYPICAL_RANGES['kegerator_dual'][1]} kWh/day")
    print(f"  Full-size refrigerator:            {TYPICAL_RANGES['full_size_fridge'][0]} - {TYPICAL_RANGES['full_size_fridge'][1]} kWh/day")
    print()

    print(f"Your kegerator: {format_kwh(stats['avg_daily_kwh'])}/day")
    print()

    # Determine status
    daily = stats['avg_daily_kwh']
    if daily < TYPICAL_RANGES['medium_mini_fridge'][1]:
        status = "[OK] EXCELLENT - Very efficient!"
    elif daily < TYPICAL_RANGES['kegerator_single'][1]:
        status = "[OK] NORMAL - Typical for a kegerator"
    elif daily < TYPICAL_RANGES['kegerator_dual'][1]:
        status = "[!] SLIGHTLY HIGH - Could be more efficient"
    else:
        status = "[!!] HIGH - Check for issues"

    print(f"Status: {status}")
    print()

    print("=" * 80)
    print("EFFICIENCY TIPS")
    print("=" * 80)
    print()

    print("To reduce kegerator energy usage:")
    print()
    print("1. TEMPERATURE SETTING (Biggest impact)")
    print("   - Most kegerators are set too cold (28-32F)")
    print("   - Beer only needs to be 36-40F for most styles")
    print("   - Each degree warmer saves ~5-8% energy")
    print("   - Raising from 32F to 38F could save ~30% energy")
    print()

    print("2. DOOR SEAL")
    print("   - Check door gasket for cracks/wear")
    print("   - Test: Close door on a dollar bill, should have resistance when pulling")
    print("   - Replace gasket if air leaks detected (~$30-50)")
    print()

    print("3. COIL CLEANING")
    print("   - Vacuum condenser coils (back or bottom) every 6 months")
    print("   - Dust buildup makes compressor work harder")
    print("   - Can improve efficiency by 10-25%")
    print()

    print("4. LOCATION")
    print("   - Keep away from heat sources (oven, direct sunlight, furnace)")
    print("   - Ensure adequate ventilation around unit")
    print("   - In garage? Extreme temperatures make it work harder")
    print()

    print("5. USAGE PATTERNS")
    print("   - Minimize door openings")
    print("   - Let warm items cool to room temp before putting in")
    print("   - Keep kegerator reasonably full (thermal mass helps)")
    print()

    print("=" * 80)
    print("POTENTIAL SAVINGS")
    print("=" * 80)
    print()

    # Calculate potential savings scenarios
    scenarios = [
        ("Optimize temperature (38F vs 32F)", 0.70, "Raise temp 6 degrees, save ~30%"),
        ("Clean coils + fix seal", 0.80, "Maintenance improvements"),
        ("Efficient operation (2.0 kWh/day)", 2.0 / daily if daily > 0 else 1.0, "Best-case optimization"),
    ]

    for name, factor, description in scenarios:
        if factor < 1:
            new_kwh = daily * factor
            new_cost = stats['avg_daily_cost'] * factor
        else:
            new_kwh = 2.0
            new_cost = (stats['avg_daily_cost'] / daily) * new_kwh if daily > 0 else 0

        savings_per_day = stats['avg_daily_cost'] - new_cost
        savings_per_year = savings_per_day * 365

        print(f"{name}:")
        print(f"  {description}")
        print(f"  New usage: {format_kwh(new_kwh)}/day @ {format_currency(new_cost)}/day")
        print(f"  Savings: {format_currency(savings_per_day)}/day = {format_currency(savings_per_year)}/year")
        print()

    print("=" * 80)
    print("BOTTOM LINE")
    print("=" * 80)
    print()
    print(f"Current annual cost: {format_currency(stats['avg_daily_cost'] * 365)}")
    print()

    if daily <= TYPICAL_RANGES['kegerator_single'][1]:
        print("Your kegerator usage is NORMAL for a kegerator.")
    else:
        print("Your kegerator usage is HIGHER than typical.")

    print()
    print("Quick wins:")
    print("  - Check temperature setting (raise to 38F if currently lower)")
    print("  - Vacuum the condenser coils")
    print("  - Check door seal")
    print()
    print("Potential savings: $50-100/year with simple optimizations")
    print()

    # Compare to furnace if it exists
    furnace_stats = get_register_stats(hourly_data, 'CT 14 - Furnace [kWh]', args.days)
    if furnace_stats['total_kwh'] > 0:
        print("COMPARISON TO FURNACE:")
        print(f"  Kegerator: {format_currency(stats['avg_daily_cost'] * 365)}/year")
        print(f"  Furnace: {format_currency(furnace_stats['avg_daily_cost'] * 365)}/year")
        if furnace_stats['avg_daily_cost'] > stats['avg_daily_cost'] * 3:
            print()
            print("  -> Focus on the furnace first for bigger savings!")

    print()
    print("=" * 80)


if __name__ == '__main__':
    main()
