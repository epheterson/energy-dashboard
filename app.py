#!/usr/bin/env python3
"""
Energy Dashboard — FastAPI app serving live power flow + historical analysis.

Wraps the existing eGauge analysis toolkit with HTTP/WebSocket endpoints
and serves a single-page dashboard frontend.

Usage:
    uvicorn app:app --host 0.0.0.0 --port 8400
"""

import asyncio
import json
import os
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from config import (
    EGAUGE_URL,
    EGAUGE_USER,
    EGAUGE_PASSWORD,
    EXCLUDE_REGISTERS,
    get_tou_period,
    get_rate,
    is_summer,
    WINTER_RATES,
    SUMMER_RATES,
    is_solar_enabled,
    get_config,
)
from solar_integration import HA_URL, HA_ENTITIES, get_ha_token
from ev_integration import is_ev_enabled, fetch_ev_live, get_vehicles, get_ev_config

# ==========================================
# App Setup
# ==========================================

app = FastAPI(title="Energy Dashboard", version="1.0.0")

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ==========================================
# Cache
# ==========================================


class DataCache:
    """Simple in-memory cache with TTL."""

    def __init__(self):
        self._data = {}
        self._timestamps = {}

    def get(self, key, ttl_seconds=5):
        if key in self._data and key in self._timestamps:
            if time.time() - self._timestamps[key] < ttl_seconds:
                return self._data[key]
        return None

    def set(self, key, value):
        self._data[key] = value
        self._timestamps[key] = time.time()


cache = DataCache()

# ==========================================
# eGauge Data Fetching (async)
# ==========================================


async def fetch_egauge_instant():
    """Fetch instantaneous power data from eGauge."""
    cached = cache.get("egauge_instant", ttl_seconds=4)
    if cached:
        return cached

    url = f"{EGAUGE_URL}/cgi-bin/egauge?notemp&tot&inst"
    async with httpx.AsyncClient() as client:
        for attempt in range(3):
            try:
                resp = await client.get(
                    url,
                    auth=(EGAUGE_USER, EGAUGE_PASSWORD),
                    timeout=10,
                )
                resp.raise_for_status()
                break
            except httpx.HTTPError as e:
                if attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                print(f"eGauge fetch failed after 3 attempts: {e}")
                return None

    # Parse XML
    root = ET.fromstring(resp.text)
    circuits = []
    total_usage_w = 0

    for reg in root.findall("r"):
        name = reg.get("n", "")
        rt = reg.get("rt", "")
        inst_el = reg.find("i")
        if inst_el is None:
            continue
        watts = float(inst_el.text)

        if rt == "total":
            if "Usage" in name:
                total_usage_w = watts
            continue

        if any(exc in name for exc in ["Total Power"]):
            continue

        # Watts are negative for consumption in eGauge
        circuits.append({"name": name, "watts": abs(watts)})

    circuits.sort(key=lambda c: c["watts"], reverse=True)

    result = {
        "circuits": circuits,
        "total_usage_w": total_usage_w,
    }
    cache.set("egauge_instant", result)
    return result


async def fetch_ha_live():
    """Fetch live Powerwall state from Home Assistant."""
    if not is_solar_enabled():
        return None

    cached = cache.get("ha_live", ttl_seconds=4)
    if cached:
        return cached

    token = get_ha_token()
    if not token:
        return None

    entity_ids = ",".join(HA_ENTITIES.values())
    url = f"{HA_URL}/api/states"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return None

    states = resp.json()
    current = {}
    for state in states:
        for key, entity_id in HA_ENTITIES.items():
            if state["entity_id"] == entity_id:
                try:
                    current[key] = float(state["state"])
                except (ValueError, TypeError):
                    current[key] = 0.0

    cache.set("ha_live", current)
    return current


async def fetch_egauge_today(target_date=None):
    """Fetch hourly data for today or a specific date from eGauge.

    Args:
        target_date: Optional date string 'YYYY-MM-DD'. None = today (with live partial hour).
    """
    now = datetime.now()
    is_today = target_date is None or target_date == str(now.date())

    cache_key = "egauge_today" if is_today else f"egauge_day_{target_date}"
    ttl = 60 if is_today else 3600  # Cache historical days longer
    cached = cache.get(cache_key, ttl_seconds=ttl)
    if cached:
        return cached

    import csv
    from io import StringIO

    if is_today:
        target_date_str = str(now.date())
        hours_today = now.hour + 1
        n_rows = (
            hours_today + 2
        )  # eGauge returns n-1 data rows; need hours+1 for diffing
        hourly_url = f"{EGAUGE_URL}/cgi-bin/egauge-show?c&h&n={n_rows}"
        # Also fetch current cumulative reading for partial hour
        # Use minute resolution (&m) to get the latest reading; default interval is daily
        # eGauge returns n-1 data rows, so n=2 gives us 1 row
        current_url = f"{EGAUGE_URL}/cgi-bin/egauge-show?c&m&n=2"
    else:
        target_date_str = target_date
        # Historical date: request enough rows to cover today + target date
        # eGauge always returns most recent N hourly readings (no random-access by timestamp)
        target_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
        days_ago = (now.date() - target_dt.date()).days
        # Need 24 hours per day + today's hours + 2 buffer (eGauge n-1 quirk)
        n_rows = (days_ago + 1) * 24 + now.hour + 3
        hourly_url = f"{EGAUGE_URL}/cgi-bin/egauge-show?c&h&n={n_rows}"
        current_url = None  # No partial hour for historical dates

    async with httpx.AsyncClient() as client:
        for attempt in range(3):
            try:
                if current_url:
                    hourly_resp, current_resp = await asyncio.gather(
                        client.get(
                            hourly_url, auth=(EGAUGE_USER, EGAUGE_PASSWORD), timeout=15
                        ),
                        client.get(
                            current_url, auth=(EGAUGE_USER, EGAUGE_PASSWORD), timeout=15
                        ),
                    )
                else:
                    hourly_resp = await client.get(
                        hourly_url, auth=(EGAUGE_USER, EGAUGE_PASSWORD), timeout=15
                    )
                    current_resp = None
                hourly_resp.raise_for_status()
                break
            except httpx.HTTPError as e:
                if attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                print(f"eGauge hourly fetch failed after 3 attempts: {e}")
                return None

    def parse_egauge_rows(text):
        reader = csv.DictReader(StringIO(text))
        result = []
        for row in reader:
            ts = int(row["Date & Time"])
            dt = datetime.fromtimestamp(ts)
            parsed = {
                "datetime": dt,
                "hour": dt.hour,
                "date": str(dt.date()),
                "tou_period": get_tou_period(dt.hour),
            }
            for key, val in row.items():
                if key != "Date & Time":
                    try:
                        parsed[key] = float(val)
                    except ValueError:
                        parsed[key] = 0.0
            result.append(parsed)
        return result

    rows = parse_egauge_rows(hourly_resp.text)
    rows.sort(key=lambda x: x["datetime"])

    # Append current reading for partial hour (today only)
    if is_today and current_resp:
        try:
            current_rows = parse_egauge_rows(current_resp.text)
            if current_rows and rows:
                latest = current_rows[-1]
                if latest["datetime"] > rows[-1]["datetime"]:
                    rows.append(latest)
        except Exception as e:
            print(f"Failed to append current reading for partial hour: {e}")

    # Diff consecutive rows for hourly consumption
    hourly = []
    for i in range(1, len(rows)):
        prev, curr = rows[i - 1], rows[i]
        tou_period = get_tou_period(prev["hour"])
        entry = {
            "hour": prev["hour"],
            "date": prev["date"],
            "tou_period": tou_period,
            "circuits": {},
        }
        total_kwh = 0
        for key in curr:
            if key.endswith("[kWh]") and key not in EXCLUDE_REGISTERS:
                kwh = abs(curr[key] - prev[key])
                name = key.replace(" [kWh]", "")
                rate = get_rate(prev["datetime"], tou_period)
                entry["circuits"][name] = {"kwh": kwh, "cost": kwh * rate}
                total_kwh += kwh
        entry["total_kwh"] = total_kwh
        entry["total_cost"] = sum(c["cost"] for c in entry["circuits"].values())
        hourly.append(entry)

    # Filter to target date only
    day_hours = [h for h in hourly if h.get("date") == target_date_str]

    # Source attribution: discount cost by non-grid share (battery + solar are
    # essentially free; only grid imports cost retail rates). Pull hourly grid
    # vs battery breakdown from /api/solar's data layer if solar is enabled.
    grid_share_by_hour = {}  # {hour_int: 0.0..1.0 (grid fraction of consumption)}
    battery_kwh_by_hour = {}  # {hour_int: kWh discharged from battery}
    try:
        from solar_integration import is_solar_enabled, build_hourly_solar_data

        if is_solar_enabled():
            from datetime import date as _date_cls, datetime as _dt

            try:
                target_d = _dt.strptime(target_date_str, "%Y-%m-%d").date()
                age_days = max(1, (_date_cls.today() - target_d).days + 1)
            except Exception:
                age_days = 7
            solar_data = build_hourly_solar_data(days=min(30, age_days))
            if isinstance(solar_data, dict):
                for (date_str, hour_int), h in solar_data.items():
                    if date_str != target_date_str:
                        continue
                    grid_kwh = h.get("grid_import_kwh", 0) or 0
                    batt_disc = h.get("battery_discharge_kwh", 0) or 0
                    solar_kwh = h.get("solar_kwh", 0) or 0
                    total = max(0.001, grid_kwh + batt_disc + solar_kwh)
                    grid_share_by_hour[hour_int] = max(0.0, min(1.0, grid_kwh / total))
                    battery_kwh_by_hour[hour_int] = batt_disc
    except Exception as _e:
        pass

    # Apply discount + emit battery contribution per hour:
    # cost = circuit_kwh × rate × grid_share_for_this_hour (grid-only)
    # battery_avoided_cost = battery_kwh × tou_rate (what would have been paid at retail)
    # get_rate already imported at module level
    from datetime import datetime as _dt2

    try:
        target_d_obj = _dt2.strptime(target_date_str, "%Y-%m-%d").date()
    except Exception:
        target_d_obj = datetime.now().date()
    for h in day_hours:
        gs = grid_share_by_hour.get(h.get("hour"), 1.0)
        if gs < 1.0:
            for circ in h["circuits"].values():
                circ["cost"] = circ["cost"] * gs
            h["total_cost"] = sum(c["cost"] for c in h["circuits"].values())
        bkwh = battery_kwh_by_hour.get(h.get("hour"), 0)
        h["battery_kwh"] = round(bkwh, 3)
        try:
            rate = get_rate(target_d_obj, h.get("tou_period", "off_peak"))
            h["battery_avoided_cost"] = round(bkwh * rate, 3)
        except Exception:
            h["battery_avoided_cost"] = 0

    circuit_totals = defaultdict(lambda: {"kwh": 0, "cost": 0, "watts": 0})
    total_cost = 0
    total_kwh = 0
    hourly_costs = []

    for h in day_hours:
        hour_circuits = [
            {"name": name, "kwh": round(data["kwh"], 3), "cost": round(data["cost"], 3)}
            for name, data in sorted(
                h["circuits"].items(), key=lambda x: x[1]["cost"], reverse=True
            )
            if data["kwh"] > 0.001
        ]
        entry = {
            "hour": h["hour"],
            "tou_period": h["tou_period"],
            "cost": round(h["total_cost"], 2),
            "kwh": round(h["total_kwh"], 2),
            "circuits": hour_circuits,
            "battery_kwh": h.get("battery_kwh", 0),
            "battery_avoided_cost": h.get("battery_avoided_cost", 0),
        }
        # Mark current partial hour (today only)
        if is_today and h["hour"] == now.hour and h["date"] == target_date_str:
            entry["partial"] = True
        hourly_costs.append(entry)
        total_cost += h["total_cost"]
        total_kwh += h["total_kwh"]
        for name, data in h["circuits"].items():
            circuit_totals[name]["kwh"] += data["kwh"]
            circuit_totals[name]["cost"] += data["cost"]

    result = {
        "date": target_date_str,
        "total_cost": round(total_cost, 2),
        "total_kwh": round(total_kwh, 2),
        "hourly": hourly_costs,
        "circuits": [
            {"name": name, "kwh": round(d["kwh"], 2), "cost": round(d["cost"], 2)}
            for name, d in sorted(
                circuit_totals.items(), key=lambda x: x[1]["cost"], reverse=True
            )
        ],
    }
    cache.set(cache_key, result)
    return result


async def fetch_history(days=7):
    """Fetch historical data — wraps the existing analysis pipeline."""
    cache_key = f"history_{days}"
    cached = cache.get(cache_key, ttl_seconds=3600)
    if cached:
        return cached

    # Run the heavy lifting in a thread to avoid blocking
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _build_history, days)
    if result:
        cache.set(cache_key, result)
    return result


def _build_history(days):
    """Synchronous history builder using existing toolkit."""
    from egauge_weekly_analysis import (
        fetch_egauge_data,
        parse_csv_data,
        calculate_hourly_consumption,
        analyze_data,
        calculate_daily_totals,
    )

    try:
        csv_data = fetch_egauge_data(days)
        parsed = parse_csv_data(csv_data)
        hourly_data = calculate_hourly_consumption(parsed)
        register_stats = analyze_data(hourly_data, days)
        daily_totals = calculate_daily_totals(hourly_data)
    except Exception as e:
        print(f"Error building history: {e}")
        return None

    # Date range for sparkline alignment (oldest -> newest)
    sorted_dates = sorted({d["date"] for d in daily_totals})

    # Convert to JSON-serializable format
    circuits = []
    for reg_name, stats in sorted(
        register_stats.items(), key=lambda x: x[1]["total_cost"], reverse=True
    ):
        name = reg_name.replace(" [kWh]", "")
        by_day = stats.get("by_day", {}) or {}
        # by_day keys are datetime.date objects; sorted_dates are strings
        by_day_str = {
            (k.isoformat() if hasattr(k, "isoformat") else str(k)): v
            for k, v in by_day.items()
        }
        daily_kwh = [round(by_day_str.get(dt, 0.0), 3) for dt in sorted_dates]
        # Anomaly: today's kWh > 2x mean of prior days (and >= 0.5 kWh to ignore noise)
        anomaly = None
        if len(daily_kwh) >= 3:
            today_v = daily_kwh[-1]
            prior = daily_kwh[:-1]
            prior_mean = sum(prior) / len(prior) if prior else 0
            if today_v >= 0.5 and prior_mean > 0 and today_v > 2.0 * prior_mean:
                anomaly = {
                    "today_kwh": today_v,
                    "prior_avg_kwh": round(prior_mean, 2),
                    "ratio": round(today_v / prior_mean, 1),
                }
        circuits.append(
            {
                "name": name,
                "total_kwh": round(stats["total_kwh"], 2),
                "total_cost": round(stats["total_cost"], 2),
                "avg_daily_kwh": round(stats["avg_daily_kwh"], 2),
                "avg_daily_cost": round(stats["avg_daily_cost"], 2),
                "daily_kwh": daily_kwh,
                "anomaly": anomaly,
                "by_tou": {
                    period: {
                        "kwh": round(d["kwh"], 2),
                        "cost": round(d["cost"], 2),
                        "percent": round(d["percent"], 1),
                    }
                    for period, d in stats["by_tou"].items()
                },
            }
        )

    daily = []
    for d in daily_totals:
        daily.append(
            {
                "date": d["date"],
                "total_kwh": round(d["total_kwh"], 2),
                "total_cost": round(d["total_cost"], 2),
                "peak_cost": round(d["peak_cost"], 2),
                "off_peak_cost": round(d["off_peak_cost"], 2),
                "part_peak_cost": round(d["part_peak_cost"], 2),
            }
        )

    # Generate optimization opportunities
    opportunities = []
    for c in circuits:
        peak_pct = c["by_tou"].get("peak", {}).get("percent", 0)
        peak_cost = c["by_tou"].get("peak", {}).get("cost", 0)
        if peak_pct > 20 and peak_cost > 1.0:
            # Estimate savings if shifted to off-peak using actual config rates
            peak_rate = get_rate(datetime.now(), "peak")
            off_peak_rate = get_rate(datetime.now(), "off_peak")
            if peak_rate <= 0:
                continue
            potential_savings = peak_cost * (1 - off_peak_rate / peak_rate)
            opportunities.append(
                {
                    "circuit": c["name"],
                    "peak_pct": round(peak_pct, 1),
                    "peak_cost": round(peak_cost, 2),
                    "potential_savings": round(potential_savings, 2),
                    "total_cost": c["total_cost"],
                    "avg_daily_cost": c["avg_daily_cost"],
                }
            )
    opportunities.sort(key=lambda x: x["potential_savings"], reverse=True)

    # Per-hour consumption totals for source chart battery inference
    hourly_detail = []
    for h in hourly_data:
        total_h = sum(
            v for k, v in h.items() if isinstance(k, str) and k.endswith("[kWh]")
        )
        hourly_detail.append(
            {
                "date": str(h["date"]),
                "hour": h["hour"],
                "kwh": round(total_h, 3),
            }
        )

    # Per-day grid_share discount: cost should reflect that battery+solar
    # covered part of consumption (already paid for or free), not full retail.
    try:
        from solar_integration import is_solar_enabled, build_hourly_solar_data

        if is_solar_enabled():
            solar_hourly = build_hourly_solar_data(days=days)
            if solar_hourly:
                # Compute per-day grid_share = grid_import / (solar+batt+grid)
                # Also accumulate per-day battery_kwh + battery_avoided_cost (battery × hourly tou_rate)
                # imports already present at module level
                from datetime import datetime as _dt_h

                # Compute per-day per-TOU grid cost DIRECTLY from per-hour
                # data (grid_import × tou_rate), not via gross-retail × share.
                # The share approximation under-counts because the denominator
                # (grid + battery + solar) includes flows that did NOT go to
                # home consumption (export surplus, grid-to-battery charging).
                # That inflates the denominator, depresses the share, and
                # under-counts the cost by 30-40% in the off-peak bucket.
                #
                # By summing actual grid imports × actual hourly TOU rate
                # per (date, period), we get the same number /api/solar's
                # by_tou.<period>.grid_cost computes — the two endpoints
                # finally reconcile. (User caught the 40% off-peak under-count
                # 2026-05-11; this is the structural fix.)
                from solar_integration import get_export_credit as _gec

                day_period_grid_cost = {}  # {(date, period): $}
                day_period_export_credit = {}  # {(date, period): $}
                day_period_grid_kwh = {}  # {(date, period): kWh imported}
                day_period_home_kwh = {}  # {(date, period): kWh consumed at home}
                day_share = {}  # kept for backcompat
                day_battery_kwh = {}
                day_battery_avoided = {}
                for (date_str, hour_int), h in solar_hourly.items():
                    grid_kwh = h.get("grid_import_kwh", 0) or 0
                    export_kwh = h.get("grid_export_kwh", 0) or 0
                    batt = h.get("battery_discharge_kwh", 0) or 0
                    sol = h.get("solar_kwh", 0) or 0
                    period = get_tou_period(int(hour_int))
                    try:
                        d_obj = _dt_h.strptime(date_str, "%Y-%m-%d").date()
                        rate = get_rate(d_obj, period)
                        credit = _gec(d_obj, period)
                    except Exception:
                        rate = 0.0
                        credit = 0.0
                    pkey = (date_str, period)
                    day_period_grid_cost.setdefault(pkey, 0.0)
                    day_period_grid_cost[pkey] += grid_kwh * rate
                    day_period_export_credit.setdefault(pkey, 0.0)
                    day_period_export_credit[pkey] += export_kwh * credit
                    day_period_grid_kwh.setdefault(pkey, 0.0)
                    day_period_grid_kwh[pkey] += grid_kwh
                    day_period_home_kwh.setdefault(pkey, 0.0)
                    # Best-effort hourly "home consumption": solar + battery + grid in,
                    # minus export. Used only for per-TOU grid_share on circuits.
                    day_period_home_kwh[pkey] += max(
                        0.0, sol + batt + grid_kwh - export_kwh
                    )
                    # Per-day overall share (per-circuit fallback)
                    if date_str not in day_share:
                        day_share[date_str] = [0.0, 0.0]
                        day_battery_kwh[date_str] = 0.0
                        day_battery_avoided[date_str] = 0.0
                    day_share[date_str][0] += grid_kwh
                    day_share[date_str][1] += grid_kwh + batt + sol
                    day_battery_kwh[date_str] += batt
                    if batt > 0:
                        day_battery_avoided[date_str] += batt * rate
                day_grid_share = {
                    d: (g / max(0.001, total)) for d, (g, total) in day_share.items()
                }

                # Per-circuit per-TOU grid_share (used to scale circuit by_tou
                # costs to match what was actually drawn from grid in each TOU
                # bucket). Reviewer flagged the prior version applied a single
                # avg_share across all TOU periods, same bug as the daily one.
                def _period_share(date_str, period):
                    g = day_period_grid_kwh.get((date_str, period), 0.0)
                    h = day_period_home_kwh.get((date_str, period), 0.0)
                    if h <= 0:
                        return 1.0
                    return min(1.0, g / h)

                # Window-average grid_share PER TOU PERIOD (for circuit by_tou
                # — circuits don't have date granularity in this aggregate).
                period_window_share = {}
                for period in ("peak", "part_peak", "off_peak"):
                    g_sum = sum(
                        v for (d, p), v in day_period_grid_kwh.items() if p == period
                    )
                    h_sum = sum(
                        v for (d, p), v in day_period_home_kwh.items() if p == period
                    )
                    period_window_share[period] = (
                        min(1.0, g_sum / h_sum) if h_sum > 0 else 1.0
                    )

                # Write per-day per-TOU costs DIRECTLY from solar_hourly.
                # Bars now sum to solar's grid_cost; per-period numbers match
                # /api/solar by_tou.<period>.grid_cost exactly.
                for d in daily:
                    date = d.get("date")
                    d["peak_cost"] = round(
                        day_period_grid_cost.get((date, "peak"), 0), 2
                    )
                    d["part_peak_cost"] = round(
                        day_period_grid_cost.get((date, "part_peak"), 0), 2
                    )
                    d["off_peak_cost"] = round(
                        day_period_grid_cost.get((date, "off_peak"), 0), 2
                    )
                    d["total_cost"] = round(
                        d["peak_cost"] + d["part_peak_cost"] + d["off_peak_cost"], 2
                    )
                    d["export_credit"] = round(
                        sum(
                            day_period_export_credit.get((date, p), 0)
                            for p in ("peak", "part_peak", "off_peak")
                        ),
                        2,
                    )
                    d["net_cost"] = round(d["total_cost"] - d["export_credit"], 2)
                    d["battery_kwh"] = round(day_battery_kwh.get(date, 0), 2)
                    d["battery_avoided_cost"] = round(
                        day_battery_avoided.get(date, 0), 2
                    )

                # Apply per-TOU grid_share to per-circuit by_tou costs (was
                # using a single window-average grid_share across all periods
                # — same family as the daily bug, caught by reviewer 2026-05-11).
                # The frontend prefers solar-blended actual_cost from /api/solar,
                # so this mostly affects API consumers, but EV-charger-class
                # circuits (off-peak-only) were being under-counted ~60% here.
                # Structural fix (2026-05-11): per-circuit cost data now flows
                # directly from solar's per-hour per-circuit blend, NOT from
                # scaling gross-retail by window-average shares. The reviewer's
                # observation: "never leave the per-hour loop" — every
                # attribution should be finalized inside the hour iterator and
                # accumulated into typed buckets. blend_egauge_with_solar
                # already does that for per-circuit per-TOU; we just need to
                # USE its output instead of re-deriving via approximation.
                try:
                    from solar_integration import blend_egauge_with_solar as _blend

                    blended, _sys = _blend(hourly_data, solar_hourly)
                    by_name = {
                        reg.replace(" [kWh]", ""): st for reg, st in blended.items()
                    }
                    days_count = max(1, len(daily))
                    for c in circuits:
                        s = by_name.get(c["name"])
                        if not s:
                            continue
                        # Per-hour-precise: actual cost = grid_cost + battery_cost
                        # (the second is amortized grid-charge cost of energy
                        # the battery later discharged to this circuit).
                        c["grid_kwh"] = round(s.get("grid_kwh", 0), 2)
                        c["solar_kwh"] = round(s.get("solar_kwh", 0), 2)
                        c["battery_kwh"] = round(s.get("battery_kwh", 0), 2)
                        c["grid_cost"] = round(s.get("grid_cost", 0), 2)
                        c["battery_cost"] = round(s.get("battery_cost", 0), 2)
                        c["actual_cost"] = round(s.get("actual_cost", 0), 2)
                        c["full_rate_cost"] = round(s.get("full_rate_cost", 0), 2)
                        c["total_cost"] = c["actual_cost"]  # what UI reads
                        c["avg_daily_cost"] = round(c["total_cost"] / days_count, 2)
                        # NOTE: solar_savings here = full_rate - actual_cost,
                        # which is solar-offset PLUS battery-arbitrage savings.
                        # Field name is technically misleading (caught by
                        # reviewer 2026-05-11) but renaming touches multiple
                        # frontend consumers — left as-is with this note.
                        c["solar_savings"] = round(s.get("solar_savings", 0), 2)
                        # Per-period: cost from solar's per-hour per-TOU
                        # accumulator (grid_cost + battery_cost = actual cost
                        # of energy that flowed to this circuit in that TOU
                        # bucket). Replaces the prior period-window-share
                        # approximation.
                        for period in ("peak", "part_peak", "off_peak"):
                            src = (s.get("by_tou", {}) or {}).get(period, {})
                            c.setdefault("by_tou", {}).setdefault(period, {})
                            c["by_tou"][period]["kwh"] = round(src.get("kwh", 0), 2)
                            c["by_tou"][period]["grid_kwh"] = round(
                                src.get("grid_kwh", 0), 2
                            )
                            c["by_tou"][period]["cost"] = round(
                                src.get("grid_cost", 0) + src.get("battery_cost", 0),
                                2,
                            )
                except Exception as _ce:
                    # Solar blend failed — leave existing circuit data alone
                    # rather than corrupt with stale scaling.
                    pass
    except Exception as _e:
        # Solar unconfigured or fetch failed — leave costs at gross retail
        pass

    return {
        "days": days,
        "circuits": circuits,
        "daily": daily,
        "opportunities": opportunities,
        "total_kwh": round(sum(c["total_kwh"] for c in circuits), 2),
        "total_cost": round(sum(c["total_cost"] for c in circuits), 2),
        "_hourly_detail": hourly_detail,
    }


async def fetch_solar(days=7, today_only=False):
    """Fetch solar blending data — wraps solar_integration.py."""
    if not is_solar_enabled():
        return None

    cache_key = f"solar_{days}_today" if today_only else f"solar_{days}"
    # Today data refreshes more often (partial day, updates every hour)
    ttl = 300 if today_only else 3600
    cached = cache.get(cache_key, ttl_seconds=ttl)
    if cached:
        return cached

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _build_solar, days, today_only)
    if result:
        cache.set(cache_key, result)
    return result


def _build_solar(days, today_only=False):
    """Synchronous solar builder.

    today_only=True restricts aggregation to the current calendar date
    (midnight → now in local time). Used by the glance card so labels
    saying "today" actually reflect today's data, not a rolling 24h window.
    """
    from egauge_weekly_analysis import (
        fetch_egauge_data,
        parse_csv_data,
        calculate_hourly_consumption,
    )
    from solar_integration import build_hourly_solar_data, blend_egauge_with_solar

    # When restricting to today, still fetch ≥2 days so calculate_hourly_consumption
    # has the prior cumulative reading needed to diff today's first hour correctly.
    fetch_days = max(days, 2) if today_only else days

    try:
        csv_data = fetch_egauge_data(fetch_days)
        parsed = parse_csv_data(csv_data)
        hourly = calculate_hourly_consumption(parsed)
        solar_data = build_hourly_solar_data(fetch_days)
        if not solar_data:
            return None

        if today_only:
            today = datetime.now().date()
            hourly = [h for h in hourly if h["date"] == today]
            today_str = str(today)
            solar_data = {k: v for k, v in solar_data.items() if k[0] == today_str}

        blended, system = blend_egauge_with_solar(hourly, solar_data)
    except Exception as e:
        print(f"Error building solar data: {e}")
        return None

    if not blended:
        return None

    circuits = []
    for reg_name, stats in sorted(
        blended.items(), key=lambda x: x[1]["actual_cost"], reverse=True
    ):
        name = reg_name.replace(" [kWh]", "")
        circuits.append(
            {
                "name": name,
                "total_kwh": round(stats["total_kwh"], 2),
                "grid_kwh": round(stats["grid_kwh"], 2),
                "solar_kwh": round(stats["solar_kwh"], 2),
                "battery_kwh": round(stats["battery_kwh"], 2),
                "grid_cost": round(stats["grid_cost"], 2),
                "battery_cost": round(stats["battery_cost"], 2),
                "actual_cost": round(stats["actual_cost"], 2),
                "full_rate_cost": round(stats["full_rate_cost"], 2),
                "solar_savings": round(stats["solar_savings"], 2),
                "by_tou": {
                    period: {
                        "kwh": round(tou_data["kwh"], 2),
                        "grid_kwh": round(tou_data["grid_kwh"], 2),
                    }
                    for period, tou_data in stats["by_tou"].items()
                },
            }
        )

    # Hourly source breakdown for charts.
    # Include per-hour cost/credit so the UI can drill into "where today's net
    # cost came from" without having to know the TOU rate table.
    from solar_integration import get_export_credit

    hourly_source = []
    for key in sorted(solar_data.keys()):
        date_str, hour = key
        h = solar_data[key]
        try:
            dt_for_rate = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=hour)
            tou_period = get_tou_period(hour)
            rate = get_rate(dt_for_rate, tou_period)
            credit = get_export_credit(dt_for_rate, tou_period)
        except Exception:
            tou_period = "off_peak"
            rate = 0.0
            credit = 0.0
        gi = h.get("grid_import_kwh", 0)
        ge = h.get("grid_export_kwh", 0)
        hourly_source.append(
            {
                "date": date_str,
                "hour": hour,
                "tou_period": tou_period,
                "solar_kwh": round(h.get("solar_kwh", 0), 3),
                "grid_import_kwh": round(gi, 3),
                "grid_export_kwh": round(ge, 3),
                "battery_discharge_kwh": round(h.get("battery_discharge_kwh", 0), 3),
                "battery_charge_kwh": round(h.get("battery_charge_kwh", 0), 3),
                "grid_cost": round(gi * rate, 3),
                "export_credit": round(ge * credit, 3),
            }
        )

    full_rate_cost = round(sum(c["full_rate_cost"] for c in circuits), 2)
    net_cost = round(system["net_cost"], 2)

    # Per-hour-precise home-source attribution (single source of truth — every
    # consumer must read these instead of computing (consumption - import)
    # which is wrong on grid-charging hours). Same per-hour logic the sankey
    # uses in /api/energy-flows.
    solar_to_home_kwh = 0.0
    solar_to_battery_kwh = 0.0
    solar_to_export_kwh = 0.0
    grid_to_home_kwh = 0.0
    grid_to_battery_kwh = 0.0
    battery_to_home_kwh = 0.0
    for h in hourly_source:
        s_h = h.get("solar_kwh", 0) or 0
        gi_h = h.get("grid_import_kwh", 0) or 0
        ge_h = h.get("grid_export_kwh", 0) or 0
        bc_h = h.get("battery_charge_kwh", 0) or 0
        bd_h = h.get("battery_discharge_kwh", 0) or 0
        s2b = min(bc_h, s_h)
        g2b = max(0.0, bc_h - s2b)
        s2e = min(ge_h, max(0.0, s_h - s2b))
        s2h = max(0.0, s_h - s2b - s2e)
        g2h = max(0.0, gi_h - g2b)
        solar_to_home_kwh += s2h
        solar_to_battery_kwh += s2b
        solar_to_export_kwh += s2e
        grid_to_home_kwh += g2h
        grid_to_battery_kwh += g2b
        battery_to_home_kwh += bd_h
    home_total_kwh = solar_to_home_kwh + battery_to_home_kwh + grid_to_home_kwh
    self_sufficiency_pct = round(
        100.0 * (solar_to_home_kwh + battery_to_home_kwh) / max(0.001, home_total_kwh),
        1,
    )

    result = {
        "days": days,
        "solar_kwh": round(system["total_solar_kwh"], 2),
        "grid_import_kwh": round(system["total_grid_import_kwh"], 2),
        "grid_export_kwh": round(system["total_grid_export_kwh"], 2),
        "battery_charge_kwh": round(system["total_battery_charge_kwh"], 2),
        "battery_discharge_kwh": round(system["total_battery_discharge_kwh"], 2),
        "consumption_kwh": round(system["total_consumption_kwh"], 2),
        "grid_cost": round(system["total_grid_cost"], 2),
        "export_credit": round(system["total_export_credit"], 2),
        "net_cost": net_cost,
        "full_rate_cost": full_rate_cost,
        # System-level savings: what you'd pay without solar/battery minus what you actually pay
        "solar_savings": round(full_rate_cost - net_cost, 2),
        # Per-hour-precise home-source flows. UI must read these instead of
        # computing self-sufficiency naively. See note above.
        "self_sufficiency_pct": self_sufficiency_pct,
        "home_sources": {
            "solar_kwh": round(solar_to_home_kwh, 2),
            "battery_kwh": round(battery_to_home_kwh, 2),
            "grid_kwh": round(grid_to_home_kwh, 2),
            "total_kwh": round(home_total_kwh, 2),
        },
        "flows": {
            "solar_to_home": round(solar_to_home_kwh, 2),
            "solar_to_battery": round(solar_to_battery_kwh, 2),
            "solar_to_export": round(solar_to_export_kwh, 2),
            "grid_to_home": round(grid_to_home_kwh, 2),
            "grid_to_battery": round(grid_to_battery_kwh, 2),
            "battery_to_home": round(battery_to_home_kwh, 2),
        },
        "circuits": circuits,
        "hourly": hourly_source,
        "by_tou": {
            period: {
                "solar": round(d.get("solar", 0), 2),
                "grid_import": round(d.get("grid_import", 0), 2),
                "grid_export": round(d.get("grid_export", 0), 2),
                "battery_discharge": round(d.get("battery_discharge", 0), 2),
                "consumption": round(d.get("consumption", 0), 2),
                "grid_cost": round(d.get("grid_cost", 0), 2),
                "battery_cost": round(d.get("battery_cost", 0), 2),
                "export_credit": round(d.get("export_credit", 0), 2),
            }
            for period, d in system.get("by_tou", {}).items()
        },
    }

    # Battery economics (from charge source attribution)
    if system.get("battery_cost_per_kwh") is not None:
        # Value displaced: what battery discharge would have cost at grid TOU
        # rates if we'd bought it instead. Reviewer flagged 2026-05-11 that
        # the prior code used `get_rate(datetime.now(), period)` for every
        # hour, which means a 30-day window straddling Oct→summer→winter rate
        # transitions priced ALL discharge at today's season. Fix: compute
        # per-hour from hourly_source (which has the correct date) so summer
        # discharge gets summer rates and vice-versa.
        battery_value_displaced = 0.0
        for h in hourly_source:
            disch = h.get("battery_discharge_kwh", 0) or 0
            if disch <= 0:
                continue
            try:
                d_obj = datetime.strptime(h["date"], "%Y-%m-%d").date()
                period = h.get("tou_period") or get_tou_period(int(h["hour"]))
                battery_value_displaced += disch * get_rate(d_obj, period)
            except Exception:
                pass

        result["battery"] = {
            "cost_per_kwh": round(system["battery_cost_per_kwh"], 4),
            "solar_pct": system["battery_solar_pct"],
            "solar_charge_kwh": system["battery_solar_charge_kwh"],
            "grid_charge_kwh": system["battery_grid_charge_kwh"],
            "grid_charge_cost": system["battery_grid_charge_cost"],
            "discharge_kwh": round(system["total_battery_discharge_kwh"], 2),
            "charge_kwh": round(system["total_battery_charge_kwh"], 2),
            "efficiency": system["battery_efficiency_measured"],
            "energy_lost_kwh": system["battery_energy_lost_kwh"],
            "total_battery_cost": round(system.get("total_battery_cost", 0), 2),
            "value_displaced": round(battery_value_displaced, 2),
        }

    return result


# ==========================================
# API Endpoints
# ==========================================


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard page."""
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse("<h1>Energy Dashboard</h1><p>static/index.html not found</p>")


@app.get("/api/config")
async def api_config():
    """Return dashboard configuration for the frontend."""
    cfg = get_config()
    return {
        "solar_enabled": is_solar_enabled(),
        "ev_enabled": is_ev_enabled(),
        "plan_name": cfg.get("rates", {}).get("plan_name", "Custom"),
    }


@app.get("/api/today")
async def api_today(date: str = None):
    """Today's (or a specific date's) running costs and circuit breakdown.

    Args:
        date: Optional 'YYYY-MM-DD'. None = today with live partial hour.
    """
    data = await fetch_egauge_today(target_date=date)
    if not data:
        return {"error": "Could not fetch data"}
    return data


@app.get("/api/history")
async def api_history(days: int = 7):
    """Historical analysis (7d/30d)."""
    days = min(days, 90)
    data = await fetch_history(days)
    if not data:
        return {"error": "Could not fetch data"}
    return data


@app.get("/api/solar")
async def api_solar(days: int = 7, today: bool = False):
    """Solar blending report.

    today=true restricts aggregates to the current calendar date (midnight → now),
    so glance-card "today" labels reflect actual today, not a rolling 24h window.
    """
    if not is_solar_enabled():
        return {"error": "Solar not configured"}
    days = min(days, 90)
    data = await fetch_solar(days, today_only=today)
    if not data:
        return {"error": "Could not fetch solar data"}
    return data


@app.get("/api/energy-flows")
async def api_energy_flows(days: int = 7):
    """Aggregated energy flows for sankey: Solar/Grid -> Home/Battery/Export."""
    if not is_solar_enabled():
        return {"error": "Solar not configured"}
    days = min(max(days, 1), 90)
    data = await fetch_solar(days)
    if not data or data.get("error"):
        return {"error": "Could not fetch solar data"}

    solar = float(data.get("solar_kwh", 0) or 0)
    grid_import = float(data.get("grid_import_kwh", 0) or 0)
    grid_export = float(data.get("grid_export_kwh", 0) or 0)
    bat_charge = float(data.get("battery_charge_kwh", 0) or 0)
    bat_discharge = float(data.get("battery_discharge_kwh", 0) or 0)

    # Per-hour accounting (aggregate math hides overnight grid-charging behind
    # daytime solar surplus). For each hour:
    #   - Solar covers home first, then absorbs into battery, then exports
    #   - Grid covers remaining home load + remaining battery charge
    # Then aggregate across hours.
    solar_to_home = 0.0
    solar_to_battery = 0.0
    solar_to_export = 0.0
    grid_to_home = 0.0
    grid_to_battery = 0.0
    battery_to_home = 0.0
    for h in data.get("hourly", []):
        s_h = float(h.get("solar_kwh", 0) or 0)
        gi_h = float(h.get("grid_import_kwh", 0) or 0)
        ge_h = float(h.get("grid_export_kwh", 0) or 0)
        bc_h = float(h.get("battery_charge_kwh", 0) or 0)
        bd_h = float(h.get("battery_discharge_kwh", 0) or 0)

        # Solar absorbed by battery first (capped at solar and at bat_charge)
        s2b = min(bc_h, s_h)
        # Grid covers the rest of the battery charge
        g2b = max(0.0, bc_h - s2b)
        # Solar export = whatever the meter reports as export, capped at remaining solar
        s2e = min(ge_h, max(0.0, s_h - s2b))
        # Remaining solar covers home
        s2h = max(0.0, s_h - s2b - s2e)
        # Grid covers whatever home load grid_import reports minus the part that
        # went to battery
        g2h = max(0.0, gi_h - g2b)
        b2h = bd_h

        solar_to_battery += s2b
        grid_to_battery += g2b
        solar_to_export += s2e
        solar_to_home += s2h
        grid_to_home += g2h
        battery_to_home += b2h

    # Fallback if hourly is missing (older cached responses): aggregate math
    if not data.get("hourly"):
        solar_to_battery = min(bat_charge, solar)
        grid_to_battery = max(0.0, bat_charge - solar_to_battery)
        solar_to_export = min(grid_export, max(0.0, solar - solar_to_battery))
        solar_to_home = max(0.0, solar - solar_to_battery - solar_to_export)
        battery_to_home = bat_discharge
        grid_to_home = max(0.0, grid_import - grid_to_battery)

    nodes = ["Solar", "Grid Import", "Battery", "Home", "Grid Export"]
    raw_links = [
        ("Solar", "Home", solar_to_home),
        ("Solar", "Battery", solar_to_battery),
        ("Solar", "Grid Export", solar_to_export),
        ("Battery", "Home", battery_to_home),
        ("Grid Import", "Home", grid_to_home),
        ("Grid Import", "Battery", grid_to_battery),
    ]
    links = [
        {"source": s, "target": t, "value": round(v, 2)}
        for s, t, v in raw_links
        if v > 0.05
    ]

    home_total = solar_to_home + battery_to_home + grid_to_home
    return {
        "days": days,
        "nodes": nodes,
        "links": links,
        "totals": {
            "solar_kwh": round(solar, 2),
            "grid_import_kwh": round(grid_import, 2),
            "grid_export_kwh": round(grid_export, 2),
            "battery_charge_kwh": round(bat_charge, 2),
            "battery_discharge_kwh": round(bat_discharge, 2),
            "home_consumption_kwh": round(home_total, 2),
            "self_sufficiency_pct": round(
                100.0 * (solar_to_home + battery_to_home) / max(0.001, home_total), 1
            ),
        },
    }


@app.get("/api/ev")
async def api_ev():
    """Live EV charging status for all vehicles."""
    if not is_ev_enabled():
        return {"error": "EV not configured"}
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, fetch_ev_live)
    if not data:
        return {"error": "Could not fetch EV data"}
    return data


@app.get("/api/ev/history")
async def api_ev_history(days: int = 7):
    """EV charging cost from eGauge 'EV Charger' circuit (real metered data).

    Uses wall-side CT clamp data with TOU cost calculation — much more
    accurate than Tesla Fleet's charger_power entity (which barely reports).
    """
    days = min(days, 90)

    cache_key = f"ev_history_{days}"
    cached = cache.get(cache_key, ttl_seconds=3600)
    if cached:
        return cached

    # Pull from existing eGauge history — already has per-circuit TOU costs
    history = await fetch_history(days)
    if not history:
        return {"error": "Could not fetch history"}

    ev_circuit = None
    for c in history.get("circuits", []):
        if c["name"] == "EV Charger":
            ev_circuit = c
            break

    if not ev_circuit:
        return {"error": "EV Charger circuit not found in eGauge"}

    # Get vehicle efficiencies for miles calculation
    vehicles = get_vehicles()
    efficiencies = [v.get("efficiency_mi_per_kwh", 3.3) for v in vehicles.values()]
    avg_efficiency = sum(efficiencies) / len(efficiencies) if efficiencies else 3.3

    total_kwh = ev_circuit["total_kwh"]
    total_cost = ev_circuit["total_cost"]

    # Prefer solar-blended per-circuit cost when available — /api/history
    # applies a day-level grid_share to every circuit's cost, which incorrectly
    # discounts circuits that run 100% on grid at specific times of day
    # (the EV charges overnight when there's no solar, so its grid_share should
    # be ~100%, but the day-average grid_share is ~75% because the rest of the
    # home was on solar during the day). The solar endpoint computes per-hour
    # per-circuit grid_share, which is the right number for cost.
    # Provenance: where did the car-charge electricity come from over this window?
    # We have per-hour per-circuit attribution in /api/solar:
    #   sc["solar_kwh"]   = solar drawn directly into the car during daylight
    #   sc["grid_kwh"]    = grid drawn directly (overnight off-peak typically)
    #   sc["battery_kwh"] = Powerwall discharge into the car
    # The Powerwall itself is some mix of solar- and grid-charged, so we
    # attribute battery_kwh back to its sources using battery_solar_pct from
    # the system summary, giving an "effective" solar mix for car charging.
    solar_direct_kwh = None
    grid_direct_kwh = None
    battery_kwh = None
    pw_solar_pct = None
    if is_solar_enabled():
        try:
            solar = await fetch_solar(days)
            if solar and not solar.get("error"):
                for sc in solar.get("circuits", []):
                    if sc.get("name") == "EV Charger":
                        total_cost = float(sc.get("actual_cost") or total_cost)
                        solar_direct_kwh = float(sc.get("solar_kwh", 0))
                        grid_direct_kwh = float(sc.get("grid_kwh", 0))
                        battery_kwh = float(sc.get("battery_kwh", 0))
                        break
            # Powerwall provenance: same per-hour physics as the sankey, so
            # the car provenance and Powerwall provenance bars agree.
            flows = await api_energy_flows(days)
            if flows and not flows.get("error"):
                link_by = {
                    f"{l['source']}→{l['target']}": l["value"]
                    for l in flows.get("links", [])
                }
                s2b = float(link_by.get("Solar→Battery", 0))
                g2b = float(link_by.get("Grid Import→Battery", 0))
                if s2b + g2b > 0:
                    pw_solar_pct = s2b / (s2b + g2b) * 100.0
        except Exception as e:
            print(f"EV provenance fetch failed: {e}")

    solar_mix_pct = None
    solar_attributed_kwh = None
    grid_attributed_kwh = None
    if solar_direct_kwh is not None and grid_direct_kwh is not None:
        bat = battery_kwh or 0.0
        pw_solar = (pw_solar_pct or 0.0) / 100.0
        solar_attributed_kwh = solar_direct_kwh + bat * pw_solar
        grid_attributed_kwh = grid_direct_kwh + bat * (1.0 - pw_solar)
        denom = solar_attributed_kwh + grid_attributed_kwh
        solar_mix_pct = (solar_attributed_kwh / denom * 100.0) if denom > 0 else None

    miles = total_kwh * avg_efficiency
    gas_price = get_ev_config().get("gas_price_per_gallon", 4.50)
    gas_equivalent = miles / 25 * gas_price

    data = {
        "days": days,
        "total_kwh": round(total_kwh, 2),
        "total_cost": round(total_cost, 2),
        "avg_daily_kwh": round(total_kwh / max(days, 1), 2),
        "avg_daily_cost": round(total_cost / max(days, 1), 2),
        "cost_per_kwh": round(total_cost / total_kwh, 3) if total_kwh > 0 else 0,
        "cost_per_mile": round(total_cost / miles, 3) if miles > 0 else 0,
        "miles_equivalent": round(miles, 1),
        "gas_equivalent_cost": round(gas_equivalent, 2),
        "savings_vs_gas": round(gas_equivalent - total_cost, 2),
        "avg_efficiency": avg_efficiency,
        "by_tou": ev_circuit.get("by_tou", {}),
        "off_peak_pct": round(
            ev_circuit.get("by_tou", {}).get("off_peak", {}).get("percent", 0), 1
        ),
        # Provenance — only present when solar attribution succeeded
        "solar_direct_kwh": (
            round(solar_direct_kwh, 2) if solar_direct_kwh is not None else None
        ),
        "grid_direct_kwh": (
            round(grid_direct_kwh, 2) if grid_direct_kwh is not None else None
        ),
        "battery_kwh": round(battery_kwh, 2) if battery_kwh is not None else None,
        "powerwall_solar_pct": pw_solar_pct,
        "solar_attributed_kwh": (
            round(solar_attributed_kwh, 2) if solar_attributed_kwh is not None else None
        ),
        "grid_attributed_kwh": (
            round(grid_attributed_kwh, 2) if grid_attributed_kwh is not None else None
        ),
        "solar_mix_pct": round(solar_mix_pct, 1) if solar_mix_pct is not None else None,
    }
    cache.set(cache_key, data)
    return data


@app.get("/api/billing")
async def api_billing():
    """Accurate bill estimation with NEM/generation/fixed separation."""
    from datetime import datetime
    from billing import estimate_current_month, estimate_trueup
    from data_store import get_monthly_billing
    from config import get_billing_config

    days_so_far = datetime.now().day
    solar = await fetch_solar(days_so_far)

    # Try Tesla API for 100% coverage data
    tesla = None
    try:
        from tesla_energy import fetch_tesla_energy

        loop = asyncio.get_event_loop()
        tesla = await loop.run_in_executor(None, fetch_tesla_energy, days_so_far)
    except Exception as e:
        print(f"Tesla energy fetch skipped: {e}")

    current = estimate_current_month(solar, tesla_data=tesla)

    billing_cfg = get_billing_config()
    trueup_month = billing_cfg.get("trueup_month", 1)
    now = datetime.now()
    if now.month >= trueup_month:
        since = f"{now.year}-{trueup_month:02d}"
    else:
        since = f"{now.year - 1}-{trueup_month:02d}"

    snapshots = get_monthly_billing(since_month=since)
    current_nem = current.get("nem_charges_to_date", 0)
    trueup = estimate_trueup(snapshots, current_month_nem=current_nem)

    # Gas data from PG&E/Opower via HA
    gas = None
    try:
        from solar_integration import HA_URL, get_ha_token

        ha_token = get_ha_token()
        if ha_token:
            gas_sensors = {
                "cost_to_date": "sensor.gas_account_4727821245_current_bill_gas_cost_to_date",
                "forecasted_cost": "sensor.gas_account_4727821245_current_bill_gas_forecasted_cost",
                "usage_to_date": "sensor.gas_account_4727821245_current_bill_gas_usage_to_date",
                "forecasted_usage": "sensor.gas_account_4727821245_current_bill_gas_forecasted_usage",
                "typical_cost": "sensor.gas_account_4727821245_typical_monthly_gas_cost",
            }
            import subprocess, json as _json

            gas = {}
            for key, entity in gas_sensors.items():
                cmd = [
                    "curl",
                    "-sf",
                    "--max-time",
                    "5",
                    "-H",
                    f"Authorization: Bearer {ha_token}",
                    f"{HA_URL}/api/states/{entity}",
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                if r.returncode == 0:
                    state = _json.loads(r.stdout).get("state", "0")
                    try:
                        gas[key] = float(state)
                    except (ValueError, TypeError):
                        gas[key] = None
    except Exception as e:
        print(f"Gas data fetch skipped: {e}")

    return {
        "current_month": current,
        "trueup": trueup,
        "history": snapshots,
        "gas": gas,
    }


@app.post("/api/billing/snapshot")
async def api_billing_snapshot():
    """Take monthly billing snapshot from solar data."""
    from datetime import datetime
    from billing import calculate_billing_from_solar
    from data_store import store_monthly_billing

    now = datetime.now()
    month_str = now.strftime("%Y-%m")
    days = now.day

    solar = await fetch_solar(days)
    billing = calculate_billing_from_solar(solar, days)

    if not billing:
        return {"status": "error", "message": "Solar data unavailable"}

    store_monthly_billing(
        month=month_str,
        nem_charges=billing["nem_charges"],
        generation_charges=billing["generation_charges"],
        fixed_charges=billing["fixed_charges"],
        grid_import_kwh=billing["grid_import_kwh"],
        grid_export_kwh=billing["grid_export_kwh"],
        net_kwh=billing["net_kwh"],
        grid_cost=billing["delivery_cost_gross"],
        export_credit=billing["export_credits"],
        net_energy_cost=billing["nem_charges"],
        base_charge=billing["fixed_charges"],
        total_bill=billing["monthly_electric_bill"],
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


@app.get("/api/battery/recommended-cap")
async def api_battery_cap():
    """Predict tomorrow's solar and recommend grid charge cap.
    Auto-logs the prediction to the audit trail for history tracking.
    """
    loop = asyncio.get_event_loop()
    try:
        from solar_forecast import recommend_charge_cap
        from datetime import datetime
        import json as json_mod

        result = await loop.run_in_executor(None, recommend_charge_cap)

        # Auto-log prediction for history (one per day)
        log_path = Path(__file__).parent / "data" / "prediction_audit.jsonl"
        log_path.parent.mkdir(exist_ok=True)
        today_date = result.get("solar_prediction", {}).get("date", "")

        # Check if we already logged today
        already_logged = False
        if log_path.exists():
            with open(log_path) as f:
                for line in f:
                    if today_date and today_date in line:
                        already_logged = True
                        break

        if not already_logged and today_date:
            entry = dict(result)
            entry["logged_at"] = datetime.now().isoformat()
            with open(log_path, "a") as f:
                f.write(json_mod.dumps(entry) + "\n")
            log.info(
                "Battery cap prediction logged for %s: %s%%",
                today_date,
                result.get("recommended_cap"),
            )

        return result
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/battery/prediction-log")
async def api_prediction_log():
    """Log current prediction with actual conditions for audit trail."""
    from solar_forecast import recommend_charge_cap
    from datetime import datetime
    import json as json_mod

    loop = asyncio.get_event_loop()
    prediction = await loop.run_in_executor(None, recommend_charge_cap)
    prediction["logged_at"] = datetime.now().isoformat()

    # Append to audit log file
    log_path = Path(__file__).parent / "data" / "prediction_audit.jsonl"
    log_path.parent.mkdir(exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json_mod.dumps(prediction) + "\n")

    return {"status": "logged", "prediction": prediction}


@app.get("/api/health")
async def api_health():
    """Tier-by-tier health check for first-run validation."""
    import os
    from pathlib import Path

    tiers = {"dashboard": "ok"}

    egauge_url = os.environ.get("EGAUGE_URL", "")
    egauge_user = os.environ.get("EGAUGE_USER", "")
    egauge_pass = os.environ.get("EGAUGE_PASSWORD", "")
    if not (egauge_url and egauge_user and egauge_pass):
        tiers["egauge"] = "not_configured"
    else:
        tiers["egauge"] = f"ok (configured: {egauge_url})"

    ha_url = os.environ.get("HA_URL", "")
    ha_token = os.environ.get("HA_TOKEN", "")
    if not ha_url or not ha_token:
        tiers["home_assistant"] = "not_configured (solar disabled)"
    else:
        try:
            import urllib.request

            req = urllib.request.Request(
                f"{ha_url}/api/", headers={"Authorization": f"Bearer {ha_token}"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                tiers["home_assistant"] = (
                    "ok" if resp.status == 200 else f"http_{resp.status}"
                )
        except Exception as e:
            tiers["home_assistant"] = f"unreachable: {str(e)[:60]}"

    try:
        from tesla_energy import _get_tesla_config

        site_id, token = _get_tesla_config()
        if not site_id:
            tiers["tesla"] = "not_configured"
        elif not token:
            tiers["tesla"] = (
                "site_id set but token missing — check HA Tesla Fleet integration"
            )
        else:
            tiers["tesla"] = "ok"
    except Exception as e:
        tiers["tesla"] = f"error: {str(e)[:60]}"

    cap_path = Path(__file__).parent / "data" / "cap_history.json"
    if not cap_path.exists():
        tiers["forecast"] = "no_data_yet — hit /api/battery/recommended-cap to seed"
    else:
        try:
            import json as _j

            cap = _j.load(open(cap_path))
            tiers["forecast"] = f"ok ({len(cap)} days of history)"
        except Exception as e:
            tiers["forecast"] = f"error: {str(e)[:60]}"

    overall = "ok" if all(v.startswith("ok") for v in tiers.values()) else "degraded"
    return {"status": overall, "tiers": tiers}


@app.get("/api/battery/prediction-history")
async def api_prediction_history():
    """Historical predictions with actuals from cap_history (single source of truth)."""
    import json as json_mod
    from pathlib import Path
    from datetime import date as _date_cls

    cap_path = Path(__file__).parent / "data" / "cap_history.json"
    if not cap_path.exists():
        return {"history": []}

    try:
        with open(cap_path) as f:
            entries = json_mod.load(f)
    except Exception:
        return {"history": []}

    today_str = _date_cls.today().isoformat()
    history = []
    for e in entries:
        date = e.get("date", "")
        if not date or date > today_str:
            continue
        predicted = e.get("predicted_solar")
        # Suppress today/future actual_solar — Tesla still aggregating mid-day
        actual = e.get("actual_solar") if date < today_str else None
        error_pct = None
        if actual is not None and predicted:
            error_pct = round((actual - predicted) / predicted * 100, 0)
        history.append(
            {
                "date": date,
                "predicted_solar": predicted,
                "actual_solar": actual,
                "error_pct": error_pct,
                "cloud_cover": e.get("cloud_cover"),
                "recommended_cap": e.get("recommended_cap"),
                "actual_full_hour": e.get("actual_full_hour"),
                "actual_export_kwh": e.get("actual_export_kwh"),
                "actual_grid_import_kwh": e.get("actual_grid_import_kwh"),
            }
        )

    history.sort(key=lambda x: x.get("date", ""), reverse=True)
    return {"history": history[:14]}


@app.post("/api/battery/record-fill")
async def api_record_fill(date: str, full_hour: float, actual_solar: float = None):
    """Record when battery actually hit 100% for auto-tuning feedback."""
    from solar_forecast import record_actual_fill

    record_actual_fill(date, full_hour, actual_solar)
    return {"status": "ok", "date": date, "full_hour": full_hour}


@app.get("/api/battery/tuning")
async def api_battery_tuning():
    """Show auto-tuning state: history, current ratio, adjustments."""
    from solar_forecast import _load_history, _auto_tune_ratio

    history = _load_history()
    ratio = _auto_tune_ratio()

    # Summary stats
    with_actuals = [h for h in history if h.get("actual_full_hour") is not None]
    avg_fill = (
        sum(h["actual_full_hour"] for h in with_actuals) / len(with_actuals)
        if with_actuals
        else None
    )

    return {
        "current_ratio": ratio,
        "target_fill_hour": 14.0,
        "avg_actual_fill_hour": round(avg_fill, 1) if avg_fill else None,
        "days_tracked": len(history),
        "days_with_actuals": len(with_actuals),
        "recent": history[-14:],
    }


# ==========================================
# WebSocket — Live Power Flow
# ==========================================

connected_clients: set[WebSocket] = set()


async def build_live_payload():
    """Build the live data payload from eGauge + HA."""
    if is_solar_enabled():
        egauge, ha = await asyncio.gather(
            fetch_egauge_instant(),
            fetch_ha_live(),
        )
    else:
        egauge = await fetch_egauge_instant()
        ha = None

    now = datetime.now()
    tou_period = get_tou_period(now.hour)
    rate = get_rate(now, tou_period)

    payload = {
        "timestamp": now.isoformat(),
        "tou_period": tou_period,
        "tou_rate": rate,
        "circuits": [],
        "home_w": 0,
        "solar_w": 0,
        "grid_w": 0,
        "battery_w": 0,
        "battery_soc": 0,
    }

    if egauge:
        payload["circuits"] = egauge["circuits"]
        payload["home_w"] = round(egauge["total_usage_w"])

    if ha:
        # HA reports power in kW — convert to watts
        payload["solar_w"] = round(ha.get("solar_power", 0) * 1000)
        payload["grid_w"] = round(ha.get("grid_power", 0) * 1000)
        # Battery: positive = discharging (to home), negative = charging
        payload["battery_w"] = round(ha.get("battery_power", 0) * 1000)
        payload["battery_soc"] = round(ha.get("soc", 0), 1)

    # Calculate source mix (what % of home power comes from each source)
    # Only count SUPPLY sources: solar generating, battery discharging, grid importing
    # Negative values mean loads/outflows (battery charging, grid exporting) — NOT sources
    solar_supply = max(0, payload["solar_w"])
    battery_supply = max(0, payload["battery_w"])  # positive = discharging to home
    grid_supply = max(0, payload["grid_w"])  # positive = importing from grid
    total_supply = solar_supply + battery_supply + grid_supply
    if total_supply > 0:
        payload["source_mix"] = {
            "solar": round(solar_supply / total_supply * 100, 1),
            "battery": round(battery_supply / total_supply * 100, 1),
            "grid": round(grid_supply / total_supply * 100, 1),
        }
    else:
        payload["source_mix"] = {"solar": 0, "battery": 0, "grid": 0}

    # Add today's running cost (from cached today data if available)
    today = cache.get("egauge_today", ttl_seconds=120)
    if today:
        payload["today_cost"] = today.get("total_cost", 0)
        payload["today_kwh"] = today.get("total_kwh", 0)
    else:
        payload["today_cost"] = 0
        payload["today_kwh"] = 0

    # Add EV charging data (if enabled)
    if is_ev_enabled():
        ev_cached = cache.get("ev_live", ttl_seconds=10)
        if not ev_cached:
            try:
                from ev_integration import fetch_ev_live as _fetch_ev

                ev_cached = _fetch_ev()
                if ev_cached:
                    cache.set("ev_live", ev_cached)
            except Exception:
                pass
        if ev_cached:
            payload["ev"] = ev_cached

    return payload


@app.websocket("/api/live")
async def websocket_live(ws: WebSocket):
    """WebSocket endpoint pushing live data every 5 seconds."""
    await ws.accept()
    connected_clients.add(ws)
    try:
        while True:
            payload = await build_live_payload()
            await ws.send_json(payload)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(ws)


# ==========================================
# Background: refresh today cache periodically
# ==========================================


async def background_today_refresh():
    """Refresh today's cost data every 60 seconds."""
    while True:
        try:
            await fetch_egauge_today()
        except Exception as e:
            print(f"Background today refresh error: {e}")
        await asyncio.sleep(60)


@app.on_event("startup")
async def startup():
    asyncio.create_task(background_today_refresh())
    asyncio.create_task(background_daily_backfill())


# ==========================================
# Background: daily backfill of yesterday's actual export/import
# ==========================================
async def _backfill_one(date_str: str) -> bool:
    """Fill in actual_export_kwh + actual_grid_import_kwh for a single date.
    Returns True if values were written, False if data unavailable."""
    try:
        from solar_integration import is_solar_enabled, build_hourly_solar_data
        from solar_forecast import _load_history, _save_history

        if not is_solar_enabled():
            return False
        history = _load_history()
        # Need at least one entry for this date to update
        target = next((e for e in history if e.get("date") == date_str), None)
        if not target:
            return False
        # Skip if already filled
        if (
            target.get("actual_export_kwh") is not None
            and target.get("actual_grid_import_kwh") is not None
        ):
            return False
        # Pull 14-day window so we cover any older missing dates too
        from datetime import datetime as _dt, date as _date

        try:
            d_obj = _dt.strptime(date_str, "%Y-%m-%d").date()
            age_days = max(1, (_date.today() - d_obj).days + 1)
        except Exception:
            age_days = 7
        loop = asyncio.get_event_loop()
        solar_hourly = await loop.run_in_executor(
            None, build_hourly_solar_data, min(30, age_days)
        )
        if not solar_hourly:
            return False
        export = 0.0
        imp = 0.0
        solar = 0.0
        for (d, h), data in solar_hourly.items():
            if d != date_str:
                continue
            export += data.get("grid_export_kwh", 0) or 0
            imp += data.get("grid_import_kwh", 0) or 0
            solar += data.get("solar_kwh", 0) or 0
        if export == 0 and imp == 0 and solar == 0:
            return False
        target["actual_export_kwh"] = round(export, 2)
        target["actual_grid_import_kwh"] = round(imp, 2)
        if target.get("actual_solar") is None and solar > 0:
            target["actual_solar"] = round(solar, 2)
        _save_history(history)
        print(
            f"[backfill] {date_str}: export={export:.2f} import={imp:.2f} solar={solar:.2f}"
        )
        return True
    except Exception as e:
        print(f"[backfill] error for {date_str}: {e}")
        return False


async def background_daily_backfill():
    """Backfill yesterday + any missing recent days. Runs at startup and daily at 03:00."""
    # Initial catch-up: any of last 14 days that are missing actuals
    await asyncio.sleep(20)  # let app warm up + HA settle
    try:
        from solar_forecast import _load_history
        from datetime import date as _date, timedelta as _td

        history = _load_history()
        today_str = str(_date.today())
        # Backfill any past entry without actuals (skip today — not done yet)
        candidates = [
            e.get("date")
            for e in history
            if e.get("date")
            and e.get("date") < today_str
            and (
                e.get("actual_export_kwh") is None
                or e.get("actual_grid_import_kwh") is None
            )
        ]
        # Limit to last 14 distinct dates
        candidates = sorted(set(candidates), reverse=True)[:14]
        for d in candidates:
            await _backfill_one(d)
            await asyncio.sleep(1)
    except Exception as e:
        print(f"[backfill] startup catch-up error: {e}")

    # Daily loop: sleep until 03:00 local, then backfill yesterday
    while True:
        try:
            from datetime import datetime as _dt, timedelta as _td, date as _date

            now = _dt.now()
            target = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if target <= now:
                target = target + _td(days=1)
            sleep_s = (target - now).total_seconds()
            print(f"[backfill] next run in {sleep_s/3600:.1f}h ({target})")
            await asyncio.sleep(sleep_s)
            yesterday = str((_date.today() - _td(days=1)))
            await _backfill_one(yesterday)
        except Exception as e:
            print(f"[backfill] daily loop error: {e}")
            await asyncio.sleep(3600)  # back off on error


# ==========================================
# Weekly Email Scheduler
# ==========================================


def schedule_weekly_email():
    """Run weekly email report on Monday 6 AM PST."""
    from config import EMAIL_ENABLED

    if not EMAIL_ENABLED:
        print("Email not enabled, skipping weekly scheduler.")
        return

    def _email_loop():
        while True:
            now = datetime.now()
            # Next Monday 6 AM
            days_until_monday = (7 - now.weekday()) % 7
            if days_until_monday == 0 and now.hour >= 6:
                days_until_monday = 7
            next_monday = now.replace(
                hour=6, minute=0, second=0, microsecond=0
            ) + timedelta(days=days_until_monday)
            sleep_seconds = (next_monday - now).total_seconds()
            print(
                f"Weekly email scheduled for {next_monday} ({sleep_seconds/3600:.1f}h from now)"
            )
            time.sleep(sleep_seconds)

            # Run the report — only add --solar when enabled
            try:
                import subprocess

                script = str(Path(__file__).parent / "egauge_weekly_analysis.py")
                cmd = ["python3", script, "--days", "7", "--email"]
                if is_solar_enabled():
                    cmd.append("--solar")
                subprocess.run(cmd, check=True, timeout=300)
                print(f"Weekly email sent at {datetime.now()}")
            except Exception as e:
                print(f"Weekly email error: {e}")

    thread = threading.Thread(target=_email_loop, daemon=True)
    thread.start()


@app.on_event("startup")
async def startup_email():
    schedule_weekly_email()
