# Billing Redesign — Accurate PG&E + CCA Bill Estimation

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the energy dashboard produce bill estimates that match actual PG&E/MCE statements within ±5%, separating NEM true-up charges from monthly MCE generation charges, and supporting both CCA and PG&E-bundled customers.

**Architecture:** Keep the existing `get_rate()` path for effective total cost display (circuit breakdown, EV costs), but add a parallel billing rate model with separate delivery and generation rate tables. The billing module calculates three streams: PG&E NEM charges (deferred to true-up), CCA/PG&E generation charges (paid monthly), and fixed charges. The solar integration's `net_cost` becomes the NEM charge estimate. Export credits stay as-is — they're used for NEM credit calculations, not bill display.

**Tech Stack:** Python/FastAPI, SQLite, eGauge, Home Assistant

**Design Principle:** The config.yml rate model should work for anyone on PG&E + optional CCA. No hardcoded MCE-specific logic. A PG&E-bundled customer sets `cca.enabled: false` and gets a simpler (but equally accurate) bill estimate.

---

### Task 1: Redesign config.yml Rate Model

**Files:**
- Modify: `config.yml` (gitignored, deployed separately to Mini)
- Modify: `config.py`

**Step 1: Update config.yml with two-tier rate model**

The rates section keeps `rates.winter/summer` as the **effective total rates** (used by existing eGauge analysis, EV costs, circuit breakdown). Add a new `billing` section with separate delivery + generation rates.

```yaml
# Energy Dashboard Configuration
# PG&E EV2-A + MCE NEM2 — Danville, CA

# Effective total rates (delivery + generation + adjustments)
# Used by circuit cost display, EV charging analysis
# See docs/reference/pge-mce-rate-model.md for derivation
rates:
  plan_name: "PG&E EV2-A + MCE NEM2"
  winter:
    peak: 0.58
    part_peak: 0.55
    off_peak: 0.36
  summer:
    peak: 0.65      # Estimated — calibrate from summer bill
    part_peak: 0.53  # Estimated — calibrate from summer bill
    off_peak: 0.30   # Estimated — calibrate from summer bill
  summer_months: [6, 7, 8, 9]

# TOU Period Hours (0-23). Unlisted hours = off_peak
# Peak: 4-9pm, Part-peak: 3-4pm + 9pm-midnight
tou_periods:
  peak: [16, 17, 18, 19, 20]
  part_peak: [15, 21, 22, 23]

# === BILLING MODEL ===
# Separate rate components for accurate bill estimation.
# Supports PG&E-bundled and CCA (Community Choice Aggregation) customers.
billing:
  nem_version: 2
  trueup_month: 1           # January anniversary
  meter_read_day: 8          # Approximate meter read day of month

  # Community Choice Aggregation (CCA) provider
  # Set enabled: false for PG&E-bundled customers
  cca:
    enabled: true
    provider: "MCE"          # Marin Clean Energy
    storage_credit_monthly: 10.00  # MCE Storage Program Credit

  # PG&E delivery rates ($/kWh) — from NEM detail page of bill
  # For CCA customers: these are the bundled rates PG&E charges on NEM,
  # minus the Generation Credit (backed out because CCA provides generation)
  # For PG&E-bundled: these ARE the full rate (no generation credit)
  delivery:
    winter:
      peak: 0.47013
      part_peak: 0.45343
      off_peak: 0.28474
    summer:
      peak: 0.47013      # TODO: get from summer bill
      part_peak: 0.45343  # TODO: get from summer bill
      off_peak: 0.28474   # TODO: get from summer bill

  # CCA generation rates ($/kWh) — from MCE detail page of bill
  # Only used when cca.enabled is true
  # For PG&E-bundled customers, this section is ignored
  generation:
    winter:
      peak: 0.15500
      part_peak: 0.14400
      off_peak: 0.12100
    summer:
      peak: 0.15500      # TODO: get from summer bill
      part_peak: 0.14400  # TODO: get from summer bill
      off_peak: 0.12100   # TODO: get from summer bill

  # NEM adjustment rates ($/kWh of net usage)
  # These are approximate — calibrated from actual bill line items
  # NBC Net Usage Adj + State Mandated NBC + Generation Credit + PCIA
  nem_adjustments:
    # Net of all per-kWh adjustments from NEM detail page
    # Negative means adjustments reduce cost vs raw delivery rate
    net_per_kwh: -0.046

  # Fixed charges
  fixed:
    # Post-March 2026: Base Services Charge replaces Minimum Delivery
    daily_charge: 0.79343
    daily_charge_effective: "2026-03-01"
    # Pre-March 2026 rate (for historical calculations)
    daily_charge_legacy: 0.40317

# Solar/Battery config stays the same...
```

**Step 2: Add billing rate accessors to config.py**

Add these functions to `config.py` (do NOT modify existing `get_rate()` or `get_tou_period()`):

```python
def get_billing_delivery_rate(date, tou_period):
    """Get PG&E delivery rate for billing/NEM calculations."""
    cfg = _load_config()
    delivery = cfg.get('billing', {}).get('delivery', {})
    season = 'summer' if is_summer(date) else 'winter'
    return delivery.get(season, {}).get(tou_period, get_rate(date, tou_period))

def get_billing_generation_rate(date, tou_period):
    """Get CCA generation rate (MCE/EBCE/etc). Returns 0 if no CCA."""
    cfg = _load_config()
    billing = cfg.get('billing', {})
    if not billing.get('cca', {}).get('enabled', False):
        return 0.0
    gen = billing.get('generation', {})
    season = 'summer' if is_summer(date) else 'winter'
    return gen.get(season, {}).get(tou_period, 0.0)

def get_billing_fixed_daily():
    """Get daily fixed charge (Base Services Charge or Min Delivery)."""
    cfg = _load_config()
    return cfg.get('billing', {}).get('fixed', {}).get('daily_charge', 0.79343)

def get_nem_adjustment():
    """Get net NEM adjustment per kWh."""
    cfg = _load_config()
    return cfg.get('billing', {}).get('nem_adjustments', {}).get('net_per_kwh', 0.0)

def is_cca_enabled():
    """Check if CCA provider is configured."""
    cfg = _load_config()
    return cfg.get('billing', {}).get('cca', {}).get('enabled', False)

def get_cca_storage_credit():
    """Get monthly CCA storage credit."""
    cfg = _load_config()
    return cfg.get('billing', {}).get('cca', {}).get('storage_credit_monthly', 0.0)
```

**Step 3: Verify existing functionality unchanged**

```bash
cd /Users/elp/Repos/energy-dashboard
python3 -c "
from config import get_rate, get_tou_period, is_summer, get_billing_config
from config import get_billing_delivery_rate, get_billing_generation_rate
from datetime import date

d = date(2026, 3, 15)
print('Effective total (unchanged):', get_rate(d, 'off_peak'))  # Should be 0.36
print('Delivery:', get_billing_delivery_rate(d, 'off_peak'))     # Should be 0.28474
print('Generation:', get_billing_generation_rate(d, 'off_peak')) # Should be 0.121
print('All imports OK')
"
```

**Step 4: Commit**

```bash
git add config.py
git commit -m "feat: add billing delivery/generation rate accessors for CCA support"
```

---

### Task 2: Rewrite billing.py with Three-Stream Model

**Files:**
- Modify: `billing.py`

**Step 1: Rewrite billing.py**

The billing module now calculates three separate cost streams from the same solar data:

```python
"""
Bill estimation and NEM 2.0 true-up tracking.

Supports PG&E-bundled and CCA (Community Choice Aggregation) customers.
Calculates three cost streams:
  1. NEM charges — PG&E delivery, deferred to annual true-up
  2. Generation charges — CCA (e.g. MCE) or PG&E, paid monthly
  3. Fixed charges — Base Services Charge, paid monthly

The monthly bill = generation + fixed charges (NEM is deferred).
The true-up = accumulated NEM charges - delivery credits.
"""

from datetime import date, timedelta
from config import (
    get_billing_config, get_billing_delivery_rate, get_billing_generation_rate,
    get_billing_fixed_daily, get_nem_adjustment, is_cca_enabled, get_cca_storage_credit,
    get_tou_period, is_summer,
)


def calculate_billing_from_solar(solar_data, days_in_period):
    """Calculate all three billing streams from solar integration data.

    Uses the solar API's per-TOU-period grid import/export data to compute
    NEM charges and generation charges at their respective rates.

    Args:
        solar_data: Dict from _build_solar() with 'by_tou' containing
                    per-period grid_import, grid_export, etc.
        days_in_period: Number of billing days

    Returns dict with all billing components.
    """
    if not solar_data or not isinstance(solar_data, dict) or 'error' in solar_data:
        return None

    today = date.today()
    by_tou = solar_data.get('by_tou', {})
    nem_adjustment_rate = get_nem_adjustment()
    daily_fixed = get_billing_fixed_daily()
    cca_enabled = is_cca_enabled()
    storage_credit = get_cca_storage_credit() if cca_enabled else 0

    # Calculate per-TOU-period charges
    total_delivery_cost = 0
    total_generation_cost = 0
    total_export_credit = 0
    total_net_kwh = 0
    tou_breakdown = {}

    for period in ['peak', 'part_peak', 'off_peak']:
        tou_data = by_tou.get(period, {})
        grid_import = tou_data.get('grid_import', 0)
        grid_export = tou_data.get('grid_export', 0)
        net_kwh = grid_import - grid_export

        delivery_rate = get_billing_delivery_rate(today, period)
        generation_rate = get_billing_generation_rate(today, period)

        # PG&E NEM: delivery rate applied to net usage
        delivery_cost = grid_import * delivery_rate
        # CCA generation: applied to net usage (same kWh basis)
        gen_cost = grid_import * generation_rate if cca_enabled else 0

        # Export credits (already calculated in solar_integration)
        export_credit = tou_data.get('export_credit', 0)

        total_delivery_cost += delivery_cost
        total_generation_cost += gen_cost
        total_export_credit += export_credit
        total_net_kwh += net_kwh

        tou_breakdown[period] = {
            'grid_import_kwh': round(grid_import, 1),
            'grid_export_kwh': round(grid_export, 1),
            'net_kwh': round(net_kwh, 1),
            'delivery_cost': round(delivery_cost, 2),
            'generation_cost': round(gen_cost, 2),
            'export_credit': round(export_credit, 2),
        }

    # NEM adjustments (NBC, PCIA, Generation Credit — net per kWh)
    nem_adjustments = total_net_kwh * nem_adjustment_rate

    # NEM charges = delivery cost + adjustments - export credits
    nem_charges = total_delivery_cost + nem_adjustments - total_export_credit

    # Generation charges = CCA generation - storage credit
    generation_charges = max(0, total_generation_cost - storage_credit)

    # Fixed charges = daily rate × days
    fixed_charges = daily_fixed * days_in_period

    # Monthly bill = generation + fixed (NEM is deferred to true-up)
    monthly_electric_bill = generation_charges + fixed_charges

    return {
        'nem_charges': round(nem_charges, 2),
        'generation_charges': round(generation_charges, 2),
        'fixed_charges': round(fixed_charges, 2),
        'monthly_electric_bill': round(monthly_electric_bill, 2),
        'nem_adjustments': round(nem_adjustments, 2),
        'export_credits': round(total_export_credit, 2),
        'delivery_cost_gross': round(total_delivery_cost, 2),
        'generation_cost_gross': round(total_generation_cost, 2),
        'storage_credit': round(storage_credit, 2),
        'net_kwh': round(total_net_kwh, 1),
        'grid_import_kwh': round(solar_data.get('total_grid_import_kwh', 0), 1),
        'grid_export_kwh': round(solar_data.get('total_grid_export_kwh', 0), 1),
        'solar_kwh': round(solar_data.get('total_solar_kwh', 0), 1),
        'cca_provider': get_billing_config().get('cca', {}).get('provider', 'PG&E'),
        'by_tou': tou_breakdown,
    }


def estimate_current_month(solar_data):
    """Estimate the current month's electrical bill and NEM charges.

    Args:
        solar_data: Dict from _build_solar() for days elapsed this month

    Returns dict with bill estimate and projection.
    """
    today = date.today()
    if today.month == 12:
        days_in_month = 31
    else:
        days_in_month = (date(today.year, today.month + 1, 1) - timedelta(days=1)).day
    days_elapsed = today.day

    billing = calculate_billing_from_solar(solar_data, days_elapsed)
    if not billing:
        # Fallback: no solar data, can't calculate accurate billing
        daily_fixed = get_billing_fixed_daily()
        return {
            'period': f"{today.strftime('%Y-%m')}-01 to {today.strftime('%Y-%m-%d')}",
            'days_elapsed': days_elapsed,
            'days_in_month': days_in_month,
            'error': 'Solar data unavailable — cannot calculate accurate bill',
            'fixed_charges_to_date': round(daily_fixed * days_elapsed, 2),
        }

    # Project full month from daily rate
    if days_elapsed > 0:
        nem_daily = billing['nem_charges'] / days_elapsed
        gen_daily = billing['generation_charges'] / days_elapsed
    else:
        nem_daily = gen_daily = 0

    projected_nem = nem_daily * days_in_month
    projected_gen = gen_daily * days_in_month
    projected_fixed = get_billing_fixed_daily() * days_in_month
    projected_monthly_bill = projected_gen + projected_fixed

    return {
        'period': f"{today.strftime('%Y-%m')}-01 to {today.strftime('%Y-%m-%d')}",
        'days_elapsed': days_elapsed,
        'days_in_month': days_in_month,
        # Current month actuals (to date)
        'nem_charges_to_date': billing['nem_charges'],
        'generation_charges_to_date': billing['generation_charges'],
        'fixed_charges_to_date': billing['fixed_charges'],
        'monthly_electric_bill_to_date': billing['monthly_electric_bill'],
        'export_credits_to_date': billing['export_credits'],
        'grid_import_kwh': billing['grid_import_kwh'],
        'grid_export_kwh': billing['grid_export_kwh'],
        'solar_kwh': billing['solar_kwh'],
        'net_kwh': billing['net_kwh'],
        # Projections
        'projected_nem': round(projected_nem, 2),
        'projected_generation': round(projected_gen, 2),
        'projected_fixed': round(projected_fixed, 2),
        'projected_monthly_bill': round(projected_monthly_bill, 2),
        'projected_total_electric': round(projected_nem + projected_monthly_bill, 2),
        # Provider info
        'cca_provider': billing['cca_provider'],
        # TOU breakdown
        'by_tou': billing['by_tou'],
    }


def estimate_trueup(monthly_snapshots, current_month_nem=0):
    """Project NEM 2.0 true-up balance.

    Args:
        monthly_snapshots: List of monthly billing dicts from DB
        current_month_nem: NEM charges for current (incomplete) month

    Returns dict with true-up projection.
    """
    billing = get_billing_config()
    trueup_month = billing.get('trueup_month', 1)

    today = date.today()

    # Calculate months since last true-up anniversary
    if today.month >= trueup_month:
        anniversary_year = today.year
    else:
        anniversary_year = today.year - 1
    months_elapsed = (today.year - anniversary_year) * 12 + (today.month - trueup_month)
    months_remaining = 12 - months_elapsed

    # Sum YTD NEM charges from completed month snapshots
    ytd_nem = sum(s.get('nem_charges', s.get('net_energy_cost', 0)) for s in monthly_snapshots)
    # Add current month's partial NEM charges
    ytd_nem += current_month_nem

    # Monthly delivery credits (offset at true-up)
    daily_fixed = get_billing_fixed_daily()
    ytd_delivery_credits = daily_fixed * 30.5 * months_elapsed  # Approximate

    # Project to 12 months
    months_for_avg = max(months_elapsed, 1)
    monthly_avg_nem = ytd_nem / months_for_avg
    projected_annual_nem = monthly_avg_nem * 12
    projected_delivery_credits = daily_fixed * 365

    # True-up = NEM charges - delivery credits
    projected_trueup = max(0, projected_annual_nem - projected_delivery_credits)

    return {
        'anniversary_month': trueup_month,
        'next_trueup': f"{anniversary_year + 1}-{trueup_month:02d}",
        'months_elapsed': months_elapsed,
        'months_remaining': months_remaining,
        'ytd_nem_charges': round(ytd_nem, 2),
        'ytd_delivery_credits': round(ytd_delivery_credits, 2),
        'monthly_avg_nem': round(monthly_avg_nem, 2),
        'projected_annual_nem': round(projected_annual_nem, 2),
        'projected_delivery_credits': round(projected_delivery_credits, 2),
        'projected_trueup': round(projected_trueup, 2),
    }
```

**Step 2: Verify imports**

```bash
cd /Users/elp/Repos/energy-dashboard
python3 -c "from billing import calculate_billing_from_solar, estimate_current_month, estimate_trueup; print('OK')"
```

**Step 3: Commit**

```bash
git add billing.py
git commit -m "feat: rewrite billing with three-stream model (NEM/generation/fixed)"
```

---

### Task 3: Update data_store.py Schema for NEM Tracking

**Files:**
- Modify: `data_store.py`

**Step 1: Expand monthly_billing table**

Add `nem_charges` and `generation_charges` columns. Keep backward compatibility with existing rows.

```python
# In init_database(), replace the monthly_billing CREATE with:
cursor.execute('''
    CREATE TABLE IF NOT EXISTS monthly_billing (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month TEXT UNIQUE NOT NULL,
        -- Three-stream billing
        nem_charges REAL DEFAULT 0,
        generation_charges REAL DEFAULT 0,
        fixed_charges REAL DEFAULT 0,
        -- Grid data
        grid_import_kwh REAL DEFAULT 0,
        grid_export_kwh REAL DEFAULT 0,
        net_kwh REAL DEFAULT 0,
        -- Legacy fields (keep for backward compat)
        grid_cost REAL DEFAULT 0,
        export_credit REAL DEFAULT 0,
        net_energy_cost REAL DEFAULT 0,
        base_charge REAL DEFAULT 0,
        total_bill REAL DEFAULT 0,
        -- Actual bill for comparison
        actual_bill REAL,
        actual_electric REAL,  -- Electric portion only (excl gas)
        days INTEGER NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
''')
```

**Step 2: Update store function**

```python
def store_monthly_billing(month, nem_charges=0, generation_charges=0, fixed_charges=0,
                          grid_import_kwh=0, grid_export_kwh=0, net_kwh=0,
                          grid_cost=0, export_credit=0, net_energy_cost=0,
                          base_charge=0, total_bill=0, days=0):
    """Store or update a monthly billing snapshot."""
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO monthly_billing
            (month, nem_charges, generation_charges, fixed_charges,
             grid_import_kwh, grid_export_kwh, net_kwh,
             grid_cost, export_credit, net_energy_cost, base_charge, total_bill, days, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(month) DO UPDATE SET
            nem_charges=excluded.nem_charges,
            generation_charges=excluded.generation_charges,
            fixed_charges=excluded.fixed_charges,
            grid_import_kwh=excluded.grid_import_kwh,
            grid_export_kwh=excluded.grid_export_kwh,
            net_kwh=excluded.net_kwh,
            grid_cost=excluded.grid_cost,
            export_credit=excluded.export_credit,
            net_energy_cost=excluded.net_energy_cost,
            base_charge=excluded.base_charge,
            total_bill=excluded.total_bill,
            days=excluded.days,
            updated_at=CURRENT_TIMESTAMP
    ''', (month, nem_charges, generation_charges, fixed_charges,
          grid_import_kwh, grid_export_kwh, net_kwh,
          grid_cost, export_credit, net_energy_cost, base_charge, total_bill, days))
    conn.commit()
    conn.close()
```

**Step 3: Add actual electric bill recording**

```python
def update_actual_electric(month, electric_amount):
    """Record actual electric bill amount (excluding gas) for comparison."""
    init_database()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE monthly_billing SET actual_electric = ?, updated_at = CURRENT_TIMESTAMP
        WHERE month = ?
    ''', (electric_amount, month))
    if cursor.rowcount == 0:
        cursor.execute('''
            INSERT INTO monthly_billing (month, actual_electric, days)
            VALUES (?, ?, 0)
        ''', (month, electric_amount))
    conn.commit()
    conn.close()
```

**Step 4: Commit**

```bash
git add data_store.py
git commit -m "feat: expand monthly_billing schema for three-stream billing"
```

---

### Task 4: Update /api/billing Endpoint

**Files:**
- Modify: `app.py`

**Step 1: Rewrite billing endpoints**

Replace the existing billing endpoints with:

```python
@app.get("/api/billing")
async def api_billing():
    """Accurate bill estimation with NEM/generation/fixed separation."""
    from datetime import datetime
    from billing import estimate_current_month, estimate_trueup
    from data_store import get_monthly_billing
    from config import get_billing_config

    # Fetch solar data for current month
    days_so_far = datetime.now().day
    solar = await fetch_solar(days_so_far)

    current = estimate_current_month(solar)

    # Get snapshots since last true-up anniversary
    billing_cfg = get_billing_config()
    trueup_month = billing_cfg.get('trueup_month', 1)
    now = datetime.now()
    if now.month >= trueup_month:
        since = f"{now.year}-{trueup_month:02d}"
    else:
        since = f"{now.year - 1}-{trueup_month:02d}"

    snapshots = get_monthly_billing(since_month=since)
    current_nem = current.get('nem_charges_to_date', 0)
    trueup = estimate_trueup(snapshots, current_month_nem=current_nem)

    return {
        'current_month': current,
        'trueup': trueup,
        'history': snapshots,
    }


@app.post("/api/billing/snapshot")
async def api_billing_snapshot():
    """Take monthly billing snapshot from solar data."""
    from datetime import datetime
    from billing import calculate_billing_from_solar
    from data_store import store_monthly_billing
    from config import get_billing_fixed_daily

    now = datetime.now()
    month_str = now.strftime('%Y-%m')
    days = now.day

    solar = await fetch_solar(days)
    from billing import calculate_billing_from_solar
    billing = calculate_billing_from_solar(solar, days)

    if not billing:
        return {"status": "error", "message": "Solar data unavailable"}

    store_monthly_billing(
        month=month_str,
        nem_charges=billing['nem_charges'],
        generation_charges=billing['generation_charges'],
        fixed_charges=billing['fixed_charges'],
        grid_import_kwh=billing['grid_import_kwh'],
        grid_export_kwh=billing['grid_export_kwh'],
        net_kwh=billing['net_kwh'],
        grid_cost=billing['delivery_cost_gross'],
        export_credit=billing['export_credits'],
        net_energy_cost=billing['nem_charges'],
        base_charge=billing['fixed_charges'],
        total_bill=billing['monthly_electric_bill'],
        days=days,
    )

    return {"status": "ok", "month": month_str, "billing": billing}


@app.post("/api/billing/actual")
async def api_billing_actual(month: str, amount: float, electric: float = None):
    """Record actual PG&E bill. Optionally separate electric from gas."""
    from data_store import update_actual_bill, update_actual_electric
    update_actual_bill(month, amount)
    if electric is not None:
        update_actual_electric(month, electric)
    return {"status": "ok", "month": month, "total": amount, "electric": electric}
```

**Step 2: Commit**

```bash
git add app.py
git commit -m "feat: rewrite /api/billing with three-stream bill estimation"
```

---

### Task 5: Deploy, Validate Against Actual Bills, and Seed History

**Step 1: Deploy to Mini**

```bash
scp config.yml billing.py config.py data_store.py app.py macmini:/Users/elp/docker/energy-dashboard/
ssh macmini 'cd /Users/elp/docker/energy-dashboard && docker compose up -d --build'
```

**Step 2: Verify /api/billing returns three-stream data**

```bash
ssh macmini 'curl -s http://localhost:8400/api/billing | python3 -m json.tool'
```

**Step 3: Validate against Feb 2026 bill**

The Feb 2026 bill (01/08-02/08) had:
- PG&E NEM: $411.94
- MCE Generation: $197.87
- Fixed (Min Delivery): $12.90
- Gas: $62.12 (not tracked)
- Total bill: $272.89
- Electric portion: $272.89 - $62.12 = $210.77

**Step 4: Seed actual bill data**

```bash
# Record actual bills with electric-only amounts
ssh macmini 'curl -s -X POST "http://localhost:8400/api/billing/actual?month=2026-01&amount=2798.27&electric=2728.89"'
ssh macmini 'curl -s -X POST "http://localhost:8400/api/billing/actual?month=2026-02&amount=272.89&electric=210.77"'
ssh macmini 'curl -s -X POST "http://localhost:8400/api/billing/actual?month=2026-03&amount=232.74&electric=172.64"'
```

**Step 5: Take March snapshot and compare**

```bash
ssh macmini 'curl -s -X POST http://localhost:8400/api/billing/snapshot | python3 -m json.tool'
```

Compare `monthly_electric_bill` to actual electric ($172.64 for March).

**Step 6: Final commit**

```bash
git add -A
git commit -m "feat: complete billing redesign — three-stream PG&E+CCA bill estimation"
```

---

## Accuracy Calibration Notes

The key to accuracy is getting the NEM adjustment rate right. From Feb 2026 bill:
- Raw delivery charges: $490.91 (1710 kWh at bundled rates)
- Generation Credit: -$146.42
- NBC adjustments: +$4.86
- PCIA: +$62.59
- **Net NEM: $411.94**
- Adjustments as fraction of delivery: ($411.94 - $490.91) / $490.91 = -16.1%

If estimates don't match within 5%, adjust `nem_adjustments.net_per_kwh` in config.yml.

## Gas Data (Future)

PG&E's "Share My Data" program provides Green Button data including gas. Also investigate:
- Smart meter pulse sensors with HA integration
- PG&E API via OAuth (requires customer authorization)
- Scraping PG&E's online portal (last resort)

For now, gas is tracked only via `actual_bill` vs `actual_electric` delta.

## Distribution Notes

For other users:
1. Copy `config.example.yml` and fill in their rates
2. If PG&E-bundled (no CCA): set `cca.enabled: false`, rates from PG&E rate card
3. If CCA: get generation rates from CCA bill page, delivery rates from PG&E NEM detail
4. NEM adjustment rate: start at -0.046, calibrate after first bill comparison
5. All rate logic is in config.yml — no hardcoded provider-specific code
