#!/usr/bin/env python3
"""Seed historical bill data from PG&E statements into the billing database."""

import subprocess
import json

DASHBOARD_URL = "http://localhost:8400"

bills = [
    # (month, total_bill, electric_only, nem_charges, generation, fixed, gas, net_kwh)
    ("2025-01", 2675.97, 2618.01, None, 250.51, 2367.50, 57.96, 2064),  # True-up
    ("2025-02", 300.93, 245.63, 408.74, 234.27, 11.36, 55.30, 1966),
    ("2025-03", 269.75, 207.89, 337.79, 195.25, 12.64, 61.86, 1668),
    ("2025-04", 79.85, 32.89, 254.26, 146.46, 11.69, 46.96, 1308),  # -$125.26 credits
    ("2025-05", 118.36, 71.06, 106.51, 59.37, 11.69, 47.30, 617),
    ("2025-06", 160.06, 115.33, 184.47, 103.23, 12.10, 44.73, 939),
    ("2025-07", 122.40, 85.90, 124.87, 72.60, 13.30, 36.50, 707),
    ("2025-08", 82.65, 48.82, 59.61, 37.13, 11.69, 33.83, 507),
    ("2025-09", 183.13, 141.28, 202.81, 128.38, 12.90, 41.85, 1130),
    ("2025-10", 109.24, 66.03, 178.61, 112.57, 11.69, 43.21, 981),  # -$58.23 credit
    ("2025-11", 162.01, 115.35, 174.94, 103.25, 12.10, 46.66, 944),
    ("2025-12", 280.70, 212.69, 345.89, 199.39, 13.30, 68.01, 1682),
    ("2026-01", 2798.27, 2728.51, 2550.31, 166.11, 12.09, 55.38, 1393),  # True-up
    ("2026-02", 272.89, 210.77, 411.94, 197.87, 12.90, 62.12, 1710),
    ("2026-03", 232.74, 172.64, 301.59, 156.65, 15.99, 60.10, 1385),
]

for month, total, electric, nem, gen, fixed, gas, kwh in bills:
    # Record actual bill
    cmd = f'curl -s -X POST "{DASHBOARD_URL}/api/billing/actual?month={month}&amount={total}&electric={electric}"'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(f"{month}: {result.stdout.strip()}")

print("\nAll bills seeded.")
