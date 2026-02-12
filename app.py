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
    EGAUGE_URL, EGAUGE_USER, EGAUGE_PASSWORD,
    EXCLUDE_REGISTERS, get_tou_period, get_rate, is_summer,
    WINTER_RATES, SUMMER_RATES,
    is_solar_enabled, get_config,
)
from solar_integration import HA_URL, HA_ENTITIES, get_ha_token

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
        n_rows = hours_today + 2  # eGauge returns n-1 data rows; need hours+1 for diffing
        hourly_url = f"{EGAUGE_URL}/cgi-bin/egauge-show?c&h&n={n_rows}"
        # Also fetch current cumulative reading for partial hour
        # Use minute resolution (&m) to get the latest reading; default interval is daily
        # eGauge returns n-1 data rows, so n=2 gives us 1 row
        current_url = f"{EGAUGE_URL}/cgi-bin/egauge-show?c&m&n=2"
    else:
        target_date_str = target_date
        # Historical date: request enough rows to cover today + target date
        # eGauge always returns most recent N hourly readings (no random-access by timestamp)
        target_dt = datetime.strptime(target_date_str, '%Y-%m-%d')
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
                        client.get(hourly_url, auth=(EGAUGE_USER, EGAUGE_PASSWORD), timeout=15),
                        client.get(current_url, auth=(EGAUGE_USER, EGAUGE_PASSWORD), timeout=15),
                    )
                else:
                    hourly_resp = await client.get(hourly_url, auth=(EGAUGE_USER, EGAUGE_PASSWORD), timeout=15)
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
            parsed = {"datetime": dt, "hour": dt.hour, "date": str(dt.date()), "tou_period": get_tou_period(dt.hour)}
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

    circuit_totals = defaultdict(lambda: {"kwh": 0, "cost": 0, "watts": 0})
    total_cost = 0
    total_kwh = 0
    hourly_costs = []

    for h in day_hours:
        hour_circuits = [
            {"name": name, "kwh": round(data["kwh"], 3), "cost": round(data["cost"], 3)}
            for name, data in sorted(h["circuits"].items(), key=lambda x: x[1]["cost"], reverse=True)
            if data["kwh"] > 0.001
        ]
        entry = {
            "hour": h["hour"],
            "tou_period": h["tou_period"],
            "cost": round(h["total_cost"], 2),
            "kwh": round(h["total_kwh"], 2),
            "circuits": hour_circuits,
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
            for name, d in sorted(circuit_totals.items(), key=lambda x: x[1]["cost"], reverse=True)
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
    from egauge_weekly_analysis import fetch_egauge_data, parse_csv_data, calculate_hourly_consumption, analyze_data, calculate_daily_totals

    try:
        csv_data = fetch_egauge_data(days)
        parsed = parse_csv_data(csv_data)
        hourly_data = calculate_hourly_consumption(parsed)
        register_stats = analyze_data(hourly_data, days)
        daily_totals = calculate_daily_totals(hourly_data)
    except Exception as e:
        print(f"Error building history: {e}")
        return None

    # Convert to JSON-serializable format
    circuits = []
    for reg_name, stats in sorted(register_stats.items(), key=lambda x: x[1]["total_cost"], reverse=True):
        name = reg_name.replace(" [kWh]", "")
        circuits.append({
            "name": name,
            "total_kwh": round(stats["total_kwh"], 2),
            "total_cost": round(stats["total_cost"], 2),
            "avg_daily_kwh": round(stats["avg_daily_kwh"], 2),
            "avg_daily_cost": round(stats["avg_daily_cost"], 2),
            "by_tou": {
                period: {
                    "kwh": round(d["kwh"], 2),
                    "cost": round(d["cost"], 2),
                    "percent": round(d["percent"], 1),
                }
                for period, d in stats["by_tou"].items()
            },
        })

    daily = []
    for d in daily_totals:
        daily.append({
            "date": d["date"],
            "total_kwh": round(d["total_kwh"], 2),
            "total_cost": round(d["total_cost"], 2),
            "peak_cost": round(d["peak_cost"], 2),
            "off_peak_cost": round(d["off_peak_cost"], 2),
            "part_peak_cost": round(d["part_peak_cost"], 2),
        })

    # Generate optimization opportunities
    opportunities = []
    for c in circuits:
        peak_pct = c["by_tou"].get("peak", {}).get("percent", 0)
        peak_cost = c["by_tou"].get("peak", {}).get("cost", 0)
        if peak_pct > 20 and peak_cost > 1.0:
            # Estimate savings if shifted to off-peak using actual config rates
            peak_rate = get_rate(datetime.now(), 'peak')
            off_peak_rate = get_rate(datetime.now(), 'off_peak')
            potential_savings = peak_cost * (1 - off_peak_rate / peak_rate)
            opportunities.append({
                "circuit": c["name"],
                "peak_pct": round(peak_pct, 1),
                "peak_cost": round(peak_cost, 2),
                "potential_savings": round(potential_savings, 2),
                "total_cost": c["total_cost"],
                "avg_daily_cost": c["avg_daily_cost"],
            })
    opportunities.sort(key=lambda x: x["potential_savings"], reverse=True)

    # Per-hour consumption totals for source chart battery inference
    hourly_detail = []
    for h in hourly_data:
        total_h = sum(v for k, v in h.items() if isinstance(k, str) and k.endswith("[kWh]"))
        hourly_detail.append({
            "date": str(h["date"]),
            "hour": h["hour"],
            "kwh": round(total_h, 3),
        })

    return {
        "days": days,
        "circuits": circuits,
        "daily": daily,
        "opportunities": opportunities,
        "total_kwh": round(sum(c["total_kwh"] for c in circuits), 2),
        "total_cost": round(sum(c["total_cost"] for c in circuits), 2),
        "_hourly_detail": hourly_detail,
    }


async def fetch_solar(days=7):
    """Fetch solar blending data — wraps solar_integration.py."""
    if not is_solar_enabled():
        return None

    cache_key = f"solar_{days}"
    cached = cache.get(cache_key, ttl_seconds=3600)
    if cached:
        return cached

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _build_solar, days)
    if result:
        cache.set(cache_key, result)
    return result


def _build_solar(days):
    """Synchronous solar builder."""
    from egauge_weekly_analysis import fetch_egauge_data, parse_csv_data, calculate_hourly_consumption
    from solar_integration import build_hourly_solar_data, blend_egauge_with_solar

    try:
        csv_data = fetch_egauge_data(days)
        parsed = parse_csv_data(csv_data)
        hourly = calculate_hourly_consumption(parsed)
        solar_data = build_hourly_solar_data(days)
        if not solar_data:
            return None
        blended, system = blend_egauge_with_solar(hourly, solar_data)
    except Exception as e:
        print(f"Error building solar data: {e}")
        return None

    if not blended:
        return None

    circuits = []
    for reg_name, stats in sorted(blended.items(), key=lambda x: x[1]["actual_cost"], reverse=True):
        name = reg_name.replace(" [kWh]", "")
        circuits.append({
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
        })

    # Hourly source breakdown for charts
    hourly_source = []
    for key in sorted(solar_data.keys()):
        date_str, hour = key
        h = solar_data[key]
        hourly_source.append({
            "date": date_str,
            "hour": hour,
            "solar_kwh": round(h.get("solar_kwh", 0), 3),
            "grid_import_kwh": round(h.get("grid_import_kwh", 0), 3),
            "battery_discharge_kwh": round(h.get("battery_discharge_kwh", 0), 3),
            "battery_charge_kwh": round(h.get("battery_charge_kwh", 0), 3),
        })

    full_rate_cost = round(sum(c["full_rate_cost"] for c in circuits), 2)
    net_cost = round(system["net_cost"], 2)
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
        "circuits": circuits,
        "hourly": hourly_source,
    }

    # Battery economics (from charge source attribution)
    if system.get("battery_cost_per_kwh") is not None:
        # Compute value displaced: what battery discharge would cost at grid rates per TOU period
        battery_value_displaced = 0
        for period in ['peak', 'part_peak', 'off_peak']:
            discharge_kwh = system['by_tou'][period].get('battery_discharge', 0)
            period_rate = get_rate(datetime.now(), period)
            battery_value_displaced += discharge_kwh * period_rate

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
async def api_solar(days: int = 7):
    """Solar blending report."""
    if not is_solar_enabled():
        return {"error": "Solar not configured"}
    days = min(days, 90)
    data = await fetch_solar(days)
    if not data:
        return {"error": "Could not fetch solar data"}
    return data


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
    grid_supply = max(0, payload["grid_w"])         # positive = importing from grid
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
            next_monday = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=days_until_monday)
            sleep_seconds = (next_monday - now).total_seconds()
            print(f"Weekly email scheduled for {next_monday} ({sleep_seconds/3600:.1f}h from now)")
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
