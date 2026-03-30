# Bill Estimation & True-Up Tracking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Estimate monthly PG&E bills and track NEM 2.0 true-up balance so there are no surprises at the January anniversary.

**Architecture:** Add a billing config section to config.yml with cycle dates, base charge, and NEM anniversary. Build a new `/api/billing` endpoint that uses existing eGauge + solar data to estimate current month's bill and project the annual true-up. Store monthly snapshots in SQLite for accuracy comparison against real bills.

**Tech Stack:** Python/FastAPI (existing), SQLite (existing data_store.py), eGauge + HA data (existing)

---

### Task 1: Add Billing Config to config.yml

**Files:**
- Modify: `config.yml`
- Modify: `config.py`

**Step 1: Add billing section to config.yml**

Add after the `base_services_charge_monthly` line in rates:

```yaml
# Billing cycle & NEM True-Up
billing:
  nem_version: 2  # NEM 1 or NEM 2
  trueup_month: 1  # January
  billing_day: 15  # Approximate statement date (mid-month)
  base_services_charge: 24.49  # Monthly fixed charge (March 2026)
```

**Step 2: Add config accessors in config.py**

```python
def get_billing_config():
    """Get billing cycle configuration."""
    cfg = _load_config()
    return cfg.get('billing', {})
```

**Step 3: Commit**

```bash
git add config.yml config.py
git commit -m "feat: add billing cycle and NEM true-up config"
```

---

### Task 2: Add Billing Estimation Logic

**Files:**
- Create: `billing.py`
- Test: manual via `/api/billing` endpoint

**Step 1: Create billing.py**

Core logic:
- `estimate_current_month()` — pulls eGauge data for current billing period, applies TOU rates, adds base charge
- `estimate_trueup()` — accumulates monthly net costs (grid import cost - export credits) since last true-up anniversary
- `get_billing_history()` — returns stored monthly snapshots for accuracy comparison

Key calculations:
- **Monthly bill** = base_services_charge + net_energy_cost (grid_import_cost - export_credits)
- **True-up projection** = sum of monthly net_energy_costs from anniversary to now, extrapolated to 12 months
- **Daily burn rate** = current month net cost / days elapsed → project full month

Data sources:
- eGauge hourly data (already in `egauge_weekly_analysis.py`) for per-circuit consumption
- Solar integration data (already in `solar_integration.py`) for grid import/export and credits
- Both already apply TOU rates correctly

**Step 2: Implement billing.py**

```python
"""
Bill estimation and NEM 2.0 true-up tracking.
Uses existing eGauge + solar data to estimate bills and project true-up.
"""

from datetime import datetime, date, timedelta
from config import get_billing_config, get_rate, get_tou_period, is_summer

def estimate_current_month(history_data, solar_data):
    """Estimate the current month's PG&E bill.

    Args:
        history_data: Result from _build_history() for current billing period
        solar_data: Result from _build_solar() for current billing period

    Returns dict with bill estimate breakdown.
    """
    billing = get_billing_config()
    base_charge = billing.get('base_services_charge', 24.49)

    today = date.today()
    days_in_month = (date(today.year, today.month % 12 + 1, 1) - timedelta(days=1)).day if today.month < 12 else 31
    days_elapsed = today.day

    # Grid costs from eGauge (all circuits)
    total_grid_cost = history_data.get('total_cost', 0) if history_data else 0

    # Solar export credits offset
    export_credit = 0
    net_energy_cost = total_grid_cost
    if solar_data and 'total_export_credit' in solar_data:
        export_credit = solar_data['total_export_credit']
        net_energy_cost = solar_data.get('net_cost', total_grid_cost)

    # Project full month from daily rate
    daily_rate = net_energy_cost / max(days_elapsed, 1)
    projected_energy = daily_rate * days_in_month
    projected_bill = base_charge + projected_energy

    return {
        'period': f"{today.strftime('%Y-%m')}-01 to {today.strftime('%Y-%m-%d')}",
        'days_elapsed': days_elapsed,
        'days_in_month': days_in_month,
        'base_services_charge': base_charge,
        'energy_cost_to_date': round(net_energy_cost, 2),
        'export_credits_to_date': round(export_credit, 2),
        'grid_cost_to_date': round(total_grid_cost, 2),
        'daily_energy_rate': round(daily_rate, 2),
        'projected_energy_cost': round(projected_energy, 2),
        'projected_bill': round(projected_bill, 2),
    }


def estimate_trueup(monthly_snapshots):
    """Project NEM 2.0 true-up balance.

    Args:
        monthly_snapshots: List of monthly billing snapshots from DB

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

    # Sum YTD net energy costs from snapshots
    ytd_net = sum(s.get('net_energy_cost', 0) for s in monthly_snapshots)

    # Project to 12 months
    monthly_avg = ytd_net / max(months_elapsed, 1)
    projected_annual = monthly_avg * 12

    return {
        'anniversary_month': trueup_month,
        'next_trueup': f"{anniversary_year + 1}-{trueup_month:02d}",
        'months_elapsed': months_elapsed,
        'months_remaining': months_remaining,
        'ytd_net_energy_cost': round(ytd_net, 2),
        'monthly_average': round(monthly_avg, 2),
        'projected_annual_trueup': round(projected_annual, 2),
        'pge_reported_ytd': None,  # Can be manually entered for comparison
    }
```

**Step 3: Commit**

```bash
git add billing.py
git commit -m "feat: add bill estimation and true-up projection logic"
```

---

### Task 3: Add Monthly Snapshot Storage

**Files:**
- Modify: `data_store.py`

**Step 1: Add monthly_billing table and store/retrieve functions**

```python
# In init_database(), add:
cursor.execute('''
    CREATE TABLE IF NOT EXISTS monthly_billing (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month TEXT UNIQUE NOT NULL,  -- YYYY-MM
        grid_cost REAL NOT NULL,
        export_credit REAL NOT NULL,
        net_energy_cost REAL NOT NULL,
        base_charge REAL NOT NULL,
        total_bill REAL NOT NULL,
        actual_bill REAL,  -- From PG&E for comparison
        days INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
''')
```

Add `store_monthly_billing()` and `get_monthly_billing()` functions.

**Step 2: Commit**

```bash
git add data_store.py
git commit -m "feat: add monthly billing snapshot storage"
```

---

### Task 4: Add /api/billing Endpoint

**Files:**
- Modify: `app.py`

**Step 1: Add billing API endpoint**

```python
@app.get("/api/billing")
async def api_billing():
    """Bill estimation and true-up tracking."""
    # Get current month data
    days_so_far = datetime.now().day
    history = await fetch_history(days_so_far)
    solar = await fetch_solar(days_so_far)

    from billing import estimate_current_month, estimate_trueup
    from data_store import get_monthly_billing

    current = estimate_current_month(history, solar)
    snapshots = get_monthly_billing()
    trueup = estimate_trueup(snapshots)

    return {
        'current_month': current,
        'trueup': trueup,
        'history': snapshots,
    }
```

**Step 2: Add POST endpoint for recording actual bills**

```python
@app.post("/api/billing/actual")
async def api_billing_actual(month: str, amount: float):
    """Record actual PG&E bill for accuracy comparison."""
    from data_store import update_actual_bill
    update_actual_bill(month, amount)
    return {"status": "ok", "month": month, "actual": amount}
```

**Step 3: Commit**

```bash
git add app.py
git commit -m "feat: add /api/billing endpoint with true-up tracking"
```

---

### Task 5: Add End-of-Month Snapshot Automation

**Files:**
- Modify: `app.py` or create a cron-triggered endpoint

**Step 1: Add snapshot endpoint**

```python
@app.post("/api/billing/snapshot")
async def api_billing_snapshot():
    """Take end-of-month billing snapshot. Call on last day of month."""
    # Fetch full month data and store snapshot
    ...
```

This can be triggered by an existing cron job or the nightly agent.

**Step 2: Commit**

```bash
git add app.py
git commit -m "feat: add monthly billing snapshot automation"
```

---

### Task 6: Verify & Deploy

**Step 1: Test locally**
```bash
cd ~/Repos/energy-dashboard
python -c "from billing import *; print('imports ok')"
```

**Step 2: Copy updated files to Mini**
```bash
scp config.yml billing.py config.py data_store.py app.py macmini:/Users/elp/docker/energy-dashboard/
```

**Step 3: Rebuild container**
```bash
ssh macmini 'cd /Users/elp/docker/energy-dashboard && docker compose up -d --build'
```

**Step 4: Verify endpoint**
```bash
ssh macmini 'curl -s http://localhost:8400/api/billing | python3 -m json.tool'
```

**Step 5: Commit all changes**
```bash
git add -A
git commit -m "feat: bill estimation and NEM 2.0 true-up tracking"
```

---

## Future Enhancements (not in scope now)
- Dashboard UI card showing bill estimate and true-up projection
- Email alert when projected true-up exceeds threshold
- Accuracy tracking: compare estimates vs actual bills over time
- Rate change detection (auto-flag when PG&E changes rates)
