#!/usr/bin/env python3
"""Rebuild cap_history.json from prediction_audit.jsonl + backfilled actuals.

Run inside the energy-dashboard container or where solar_integration imports
resolve. Idempotent — merges with whatever's currently in cap_history.json.

Why this exists: on 2026-05-11 a concurrent-write race in _load_history made
the next _save_history wipe 14 days of accumulated history. Without enough
samples, _observed_peak_solar() falls back to the static MONTHLY_PEAK_SOLAR
table and under-predicts solar by ~30%. This script restores what's
recoverable from the append-only audit log + the eGauge SQLite DB.
"""

import json
import sys
from pathlib import Path
from datetime import datetime, date as _date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solar_integration import is_solar_enabled, build_hourly_solar_data

DATA = ROOT / "data"
AUDIT = DATA / "prediction_audit.jsonl"
HIST = DATA / "cap_history.json"


def load_audit_predictions():
    """Latest prediction per date from append-only audit log."""
    by_date = {}
    if not AUDIT.exists():
        return by_date
    for line in AUDIT.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        sp = e.get("solar_prediction") or {}
        date = sp.get("date")
        if not date:
            continue
        # Latest wins (audit log is chronological)
        by_date[date] = {
            "date": date,
            "predicted_solar": sp.get("predicted_solar_kwh"),
            "predicted_daytime_load": e.get("predicted_daytime_load_kwh"),
            "predicted_overnight_load": e.get("predicted_overnight_load_kwh"),
            "cloud_cover": sp.get("cloud_cover_pct"),
            "recommended_cap": e.get("recommended_cap"),
            "target_cap_pct": e.get("target_cap_pct"),
            "floor_cap_pct": e.get("floor_cap_pct"),
            "reasoning_mode": e.get("reasoning_mode"),
            "logged_at": e.get("logged_at"),
            "actual_full_hour": None,
            "actual_solar": None,
            "actual_export_kwh": None,
            "actual_grid_import_kwh": None,
        }
    return by_date


def load_existing():
    if not HIST.exists():
        return {}
    try:
        rows = json.loads(HIST.read_text())
        return {r["date"]: r for r in rows if r.get("date")}
    except Exception:
        return {}


def backfill_actuals(entries):
    """For each non-future date, fill actual_solar/export/import from solar_hourly."""
    if not is_solar_enabled():
        print("Solar not enabled — skipping actuals backfill.")
        return
    today = _date.today()
    dates = [
        d for d in entries.keys() if datetime.strptime(d, "%Y-%m-%d").date() < today
    ]
    if not dates:
        return
    # Pull a wide window to cover all dates we need
    earliest = min(datetime.strptime(d, "%Y-%m-%d").date() for d in dates)
    days = max(1, (today - earliest).days + 2)
    print(f"Building hourly solar data over last {days} days...")
    hourly = build_hourly_solar_data(min(90, days))
    if not hourly:
        print("No solar hourly data — skipping backfill.")
        return
    # Aggregate per date
    by_date = {}
    for (d, h), data in hourly.items():
        agg = by_date.setdefault(d, {"export": 0.0, "imp": 0.0, "solar": 0.0})
        agg["export"] += data.get("grid_export_kwh", 0) or 0
        agg["imp"] += data.get("grid_import_kwh", 0) or 0
        agg["solar"] += data.get("solar_kwh", 0) or 0
    for d in dates:
        agg = by_date.get(d)
        if not agg:
            continue
        if agg["solar"] == 0 and agg["imp"] == 0 and agg["export"] == 0:
            continue
        entries[d]["actual_export_kwh"] = round(agg["export"], 2)
        entries[d]["actual_grid_import_kwh"] = round(agg["imp"], 2)
        entries[d]["actual_solar"] = round(agg["solar"], 2)
        print(
            f"  {d}: solar={agg['solar']:.1f} export={agg['export']:.1f} import={agg['imp']:.1f}"
        )


def main():
    print(f"Recovering {HIST}")
    audit = load_audit_predictions()
    existing = load_existing()
    print(f"  audit log: {len(audit)} dates")
    print(f"  existing cap_history: {len(existing)} dates")
    # Merge: existing wins for fields it already has filled, audit fills gaps
    merged = {}
    for d, row in audit.items():
        merged[d] = dict(row)
    for d, row in existing.items():
        merged.setdefault(d, {}).update({k: v for k, v in row.items() if v is not None})
        # Ensure required keys are present
        for k in (
            "predicted_solar",
            "cloud_cover",
            "recommended_cap",
            "actual_solar",
            "actual_export_kwh",
            "actual_grid_import_kwh",
            "actual_full_hour",
        ):
            merged[d].setdefault(k, None)
        merged[d]["date"] = d
    print(f"  merged: {len(merged)} dates")
    # Backfill actuals where missing
    backfill_actuals(merged)
    rows = sorted(merged.values(), key=lambda r: r["date"])
    HIST.write_text(json.dumps(rows, indent=2))
    print(f"Wrote {len(rows)} entries to {HIST}")


if __name__ == "__main__":
    main()
