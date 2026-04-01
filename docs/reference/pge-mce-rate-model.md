# PG&E + MCE Rate Model — Actual Bill Data

Reference data extracted from 15 real bills (Jan 2025 through Mar 2026) for calibrating the energy dashboard.

## Bill Structure (NEM 2.0 + MCE CCA)

Eric's electricity cost has THREE separate streams:

### 1. PG&E NEM Charges (deferred to annual true-up in January)
The bulk of the electricity cost. Calculated monthly but accumulated and paid at true-up.
- Gross charges at PG&E bundled rate (delivery + generation)
- Minus Generation Credit (backs out PG&E generation since MCE provides it)
- Plus NBC adjustments and PCIA
- Minus cumulative Minimum Delivery Charges at true-up settlement

### 2. MCE Generation Charges (paid monthly)
MCE provides generation in place of PG&E. Charged separately on each bill.
- Per-kWh TOU rates (different from PG&E rates)
- MCE Storage Program Credit: -$10/month flat
- Energy Commission Tax: ~$0.50

### 3. Fixed Monthly Charges (paid monthly)
- Pre-March 2026: Minimum Delivery Charge @ $0.40317/day (~$12.10/month)
- Post-March 2026: Base Services Charge @ $0.79343/day (~$24.20/month)
- Gas charges (~$34-68/month, separate meter, not electric)

---

## Exact Rates from Bills

### PG&E NEM Rates (bundled, on NEM detail pages)

| Season | Period | 2025 Rate | 2026 Pre-March | 2026 Post-March |
|--------|--------|-----------|----------------|-----------------|
| Winter | Peak | $0.48575 | $0.47013 | $0.41 |
| Winter | Part Peak | $0.46905 | $0.45343 | $0.39 |
| Winter | Off Peak | $0.30036 | $0.28474 | $0.23 |
| Summer | Peak | ? | ? | $0.54 |
| Summer | Part Peak | ? | ? | $0.43 |
| Summer | Off Peak | ? | ? | $0.23 |

Note: Post-March rates from PG&E rate card, not yet seen on an actual bill.

### MCE Generation Rates (NEM EV2A)

| Season | Period | Rate |
|--------|--------|------|
| Winter (Oct-May) | Off Peak | $0.12100 |
| Winter (Oct-May) | Peak | $0.15500 |
| Winter (Oct-May) | Part Peak | $0.14400 |
| Summer (Jun-Sep) | Off Peak | $0.12800 |
| Summer (Jun-Sep) | Peak | $0.21000 |
| Summer (Jun-Sep) | Part Peak | $0.16700 |

Plus: MCE Storage Program Credit: -$10.00/month flat

### NEM Adjustment Line Items (per billing period)

These apply to net usage (imports - eligible exports):

| Line Item | Description | Feb 2026 Amount | Approx $/kWh |
|-----------|-------------|-----------------|--------------|
| NBC Net Usage Adjustment | Prevents double-counting NBCs | -$58.90 | -$0.0345 |
| State Mandated NBC | Public Purpose, Nuclear Decom, Wildfire, Competition Transition | +$63.76 | +$0.0373 |
| Generation Credit | Backs out PG&E generation (MCE provides instead) | -$146.42 | -$0.0856 |
| PCIA | Power Charge Indifference Adjustment | +$62.59 | +$0.0366 |
| **Net** | | **-$78.97** | **-$0.0462** |

### Effective Total Rates (PG&E delivery + MCE gen + adjustments)

| Period | PG&E Bundled | Gen Credit | MCE Gen | Adj | Effective Total |
|--------|-------------|------------|---------|-----|-----------------|
| Off Peak (winter) | $0.28474 | ~-$0.086 | $0.121 | +$0.004 | **~$0.36** |
| Peak (winter) | $0.47013 | ~-$0.086 | $0.155 | +$0.004 | **~$0.58** |
| Part Peak (winter) | $0.45343 | ~-$0.086 | $0.144 | +$0.004 | **~$0.55** |
| Off Peak (summer) | ? | ? | $0.128 | ? | **TBD** |
| Peak (summer) | ? | ? | $0.210 | ? | **TBD** |
| Part Peak (summer) | ? | ? | $0.167 | ? | **TBD** |

Note: Summer effective totals need PG&E summer delivery rates from an actual summer bill to calibrate.

---

## Gas Charges Summary

Gas is a separate meter, billed monthly. Not part of electric/NEM calculations.

| Month | Gas Charge | Notes |
|-------|-----------|-------|
| 2025-01 | $57.96 | True-up bill |
| 2025-02 | $55.30 | |
| 2025-03 | $61.86 | |
| 2025-04 | $46.96 | -$67.03 CA Climate Credit applied |
| 2025-05 | $47.30 | |
| 2025-06 | $44.73 | |
| 2025-07 | $36.50 | Summer low |
| 2025-08 | $33.83 | Annual minimum |
| 2025-09 | $41.85 | |
| 2025-10 | $43.21 | |
| 2025-11 | $46.66 | |
| 2025-12 | $68.01 | Winter peak |
| 2026-01 | $55.38 | True-up bill |
| 2026-02 | $62.12 | |
| 2026-03 | $60.10 | |

Annual gas total (Feb 2025 - Jan 2026): $594.38. Monthly average: ~$49.53.

---

## CA Climate Credit Schedule

Applied twice per year to electric bills, once per year to gas:

| Month | Electric Credit | Gas Credit | Total | Notes |
|-------|----------------|------------|-------|-------|
| April 2025 | -$58.23 | -$67.03 | -$125.26 | |
| October 2025 | -$58.23 | $0.00 | -$58.23 | |
| April 2026 | TBD | -$46.26 | TBD | Gas credit reduced from $67.03 |
| **2025 Annual** | **-$116.46** | **-$67.03** | **-$183.49** | |

---

## Rate Plan Comparison (from Oct 2025 bill)

Based on Eric's actual 12-month usage pattern:

| Plan | Annual Cost | vs EV2-A |
|------|-------------|----------|
| **E-1 Tiered** | **$2,630** | **-$2,035 (44% less)** |
| EV2-A (current) | $4,665 | baseline |
| E-ELEC | $5,575 | +$910 (20% more) |
| E-TOU-D | $6,775 | +$2,110 (45% more) |

E-1 Tiered appears significantly cheaper. However, EV2-A has favorable off-peak rates for EV charging (midnight-3pm) that E-1 doesn't. The comparison assumes current usage patterns which may not account for the value of cheap overnight charging.

---

## NEM YTD Progression (Jan 2025 - Jan 2026 True-Up)

Shows how NEM charges accumulate toward the annual true-up.

| Bill Period End | Net kWh | NEM Charges | YTD NEM | YTD Delivery Credits | YTD NEM at True-Up |
|----------------|---------|-------------|---------|---------------------|-------------------|
| 2025-02-05 | 1,966 | $408.74 | $408.74 | $11.36 | $397.38 |
| 2025-03-09 | 1,668 | $337.79 | $746.53 | $23.00 | $723.53 |
| 2025-04-07 | 1,308 | $254.26 | $1,000.79 | $34.69 | $966.10 |
| 2025-05-06 | 617 | $106.51 | $1,107.30 | $46.38 | $1,060.92 |
| 2025-06-05 | 939 | $184.47 | $1,291.77 | $58.48 | $1,233.29 |
| 2025-07-08 | 707 | $124.87 | $1,416.64 | $71.78 | $1,344.86 |
| 2025-08-06 | 507 | $59.61 | $1,476.25 | $83.47 | $1,392.78 |
| 2025-09-08 | 1,130 | $202.81 | $1,679.06 | $97.37 | $1,581.69 |
| 2025-10-07 | 981 | $178.61 | $1,857.67 | $109.06 | $1,748.61 |
| 2025-11-06 | 944 | $174.94 | $2,032.61 | $121.16 | $1,911.45 |
| 2025-12-09 | 1,682 | $345.89 | $2,378.50 | $134.46 | $2,244.04 |
| 2026-01-07 | 1,393 | $318.36 | $2,696.86 | $146.55 | $2,550.31 |

**True-up settlement: $2,550.31** (NEM $2,696.86 minus delivery credits $146.55)

### New NEM Year (Jan 2026 onward)

| Bill Period End | Net kWh | NEM Charges | YTD NEM | YTD Delivery Credits | YTD NEM at True-Up |
|----------------|---------|-------------|---------|---------------------|-------------------|
| 2026-02-08 | 1,710 | $411.94 | $411.94 | $12.90 | $399.04 |
| 2026-03-10 | 1,385 | $301.59 | $713.53 | $20.96 | $692.57 |

---

## Bill Validation Data

### True-Up (Jan 2025) — NEM2PS Settlement
- Total bill: $2,675.97
- PG&E delivery: $2,367.50 (includes NEM settlement)
- MCE generation: $250.51
- Gas: $57.96

### True-Up (Jan 2026) — Period: 01/08/2025 to 01/07/2026
- Total NEM Charges Before Taxes: $2,696.86
- Total Minimum Delivery Charges: -$146.55
- **Total NEM Charges: $2,550.31**
- Plus: MCE generation $166.11, PG&E delivery $12.09, Gas $55.38, Tax $14.38
- **Total bill: $2,798.27**

Full year NEM breakdown:
| Bill Period End | Net Peak | Net PP | Net OP | Net kWh | NEM $ |
|----------------|----------|--------|--------|---------|-------|
| 02/05/2025 | 38 | 196 | 1732 | 1966 | $408.74 |
| 03/09/2025 | 33 | 78 | 1557 | 1668 | $337.79 |
| 04/07/2025 | -63 | -2 | 1373 | 1308 | $254.26 |
| 05/06/2025 | -103 | -86 | 806 | 617 | $106.51 |
| 06/05/2025 | -72 | 32 | 979 | 939 | $184.47 |
| 07/08/2025 | -114 | 32 | 789 | 707 | $124.87 |
| 08/06/2025 | -178 | -85 | 770 | 507 | $59.61 |
| 09/08/2025 | -93 | 26 | 1197 | 1130 | $202.81 |
| 10/07/2025 | -49 | 47 | 984 | 981 | $178.61 |
| 11/06/2025 | -43 | 8 | 979 | 944 | $174.94 |
| 12/09/2025 | 53 | 157 | 1472 | 1682 | $345.89 |
| 01/08/2026 | 79 | 196 | 1118 | 1393 | $318.36 |
| **TOTAL** | **-512** | **599** | **13756** | **13842** | **$2,696.86** |

### Feb 2026 — Period: 01/08 to 02/08 (32 days)
- Imports: 1,895.871 kWh, Exports: -186.111 kWh, Net: 1,709.760 kWh
- Solar Generation (estimated): 723 kWh
- PG&E NEM: $411.94
- MCE Generation: $197.87
- Monthly charges: $12.90 delivery + $62.12 gas
- **Total: $272.89**

### Mar 2026 — Period: 02/09 to 03/10 (30 days)
- Imports: 1,657.612 kWh, Exports: -272.298 kWh, Net: 1,385.314 kWh
- PG&E NEM: $301.59 (for Mar period, $411.94 for Feb period within this bill)
- MCE Generation: $156.65
- Monthly charges: $15.99 delivery + $60.10 gas
- **Total: $232.74**

### Meter Reads and Billing Periods
- Meter read dates: ~8th-9th of each month
- Statement dates: ~13th-17th
- Due dates: ~3 weeks after statement
- Billing periods do NOT align with calendar months

---

## Dashboard Implications

### Current config.yml is wrong because:
1. Uses single bundled rate -- should separate PG&E delivery + MCE generation
2. Rates ($0.41/$0.39/$0.23) are post-March PG&E bundled, but MCE generation is additional
3. Export credits ($0.12-0.16) happen to match MCE generation rates but that's a different thing
4. No accounting for NEM adjustments (NBC, PCIA, Generation Credit)

### What needs to change:
1. Config should model PG&E delivery rates + MCE generation rates separately
2. NEM adjustments need to be accounted for (approx -$0.046/net-kWh)
3. Bill estimation needs to separate: NEM (true-up) vs MCE (monthly) vs fixed charges
4. True-up tracking should match PG&E's NEM summary format
5. MCE Storage Credit (-$10/month) needs to be factored in

### Simplest accurate approach:
Use effective total rates (calibrated from bills) instead of trying to model each line item:
- Winter Off Peak: $0.36/kWh
- Winter Peak: $0.58/kWh
- Winter Part Peak: $0.55/kWh
- Summer rates: TBD (need PG&E summer delivery rates from actual bill to derive effective totals)
- MCE Storage Credit: -$10/month
- Base Services Charge: $0.79343/day (post-March), $0.40317/day (pre-March)
