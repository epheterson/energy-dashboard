#!/usr/bin/env python3
"""
Data persistence layer for eGauge Energy Analysis Toolkit.
Stores fetched data locally for historical trend analysis.

Uses SQLite for efficient storage and querying.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

from config import DATA_DIR, DATA_RETENTION_DAYS


def get_db_path() -> Path:
    """Get the path to the SQLite database."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / 'egauge_history.db'


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """Initialize the database schema."""
    conn = get_connection()
    cursor = conn.cursor()

    # Hourly consumption data
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hourly_consumption (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER UNIQUE NOT NULL,
            datetime TEXT NOT NULL,
            date TEXT NOT NULL,
            hour INTEGER NOT NULL,
            tou_period TEXT NOT NULL,
            register_data TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Daily summaries for faster trend queries
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            total_kwh REAL NOT NULL,
            total_cost REAL NOT NULL,
            peak_kwh REAL NOT NULL,
            peak_cost REAL NOT NULL,
            part_peak_kwh REAL NOT NULL,
            part_peak_cost REAL NOT NULL,
            off_peak_kwh REAL NOT NULL,
            off_peak_cost REAL NOT NULL,
            register_totals TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Weekly report snapshots
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS weekly_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            week_end TEXT NOT NULL,
            total_kwh REAL NOT NULL,
            total_cost REAL NOT NULL,
            register_stats TEXT NOT NULL,
            report_text TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create indexes for common queries
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_hourly_timestamp
        ON hourly_consumption(timestamp)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_hourly_date
        ON hourly_consumption(date)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_daily_date
        ON daily_summary(date)
    ''')

    conn.commit()
    conn.close()


def store_hourly_data(hourly_data: List[Dict[str, Any]]):
    """
    Store hourly consumption data.

    Args:
        hourly_data: List of hourly consumption dictionaries with keys:
            - datetime: datetime object
            - date: date object
            - hour: int (0-23)
            - tou_period: str ('peak', 'part_peak', 'off_peak')
            - register values as additional keys (e.g., 'CT 14 - Furnace [kWh]': 1.23)
    """
    init_database()
    conn = get_connection()
    cursor = conn.cursor()

    for entry in hourly_data:
        # Extract register data (all keys ending in [kWh])
        register_data = {
            k: v for k, v in entry.items()
            if isinstance(k, str) and k.endswith('[kWh]')
        }

        timestamp = int(entry['datetime'].timestamp())

        try:
            cursor.execute('''
                INSERT OR REPLACE INTO hourly_consumption
                (timestamp, datetime, date, hour, tou_period, register_data)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                timestamp,
                entry['datetime'].isoformat(),
                str(entry['date']),
                entry['hour'],
                entry['tou_period'],
                json.dumps(register_data)
            ))
        except sqlite3.IntegrityError:
            pass  # Duplicate timestamp, skip

    conn.commit()
    conn.close()


def store_daily_summary(date: str, summary: Dict[str, Any]):
    """
    Store a daily summary.

    Args:
        date: Date string (YYYY-MM-DD)
        summary: Dictionary with keys:
            - total_kwh, total_cost
            - peak_kwh, peak_cost
            - part_peak_kwh, part_peak_cost
            - off_peak_kwh, off_peak_cost
            - register_totals: dict of register -> kwh
    """
    init_database()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT OR REPLACE INTO daily_summary
        (date, total_kwh, total_cost, peak_kwh, peak_cost,
         part_peak_kwh, part_peak_cost, off_peak_kwh, off_peak_cost, register_totals)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        date,
        summary['total_kwh'],
        summary['total_cost'],
        summary.get('peak_kwh', 0),
        summary.get('peak_cost', 0),
        summary.get('part_peak_kwh', 0),
        summary.get('part_peak_cost', 0),
        summary.get('off_peak_kwh', 0),
        summary.get('off_peak_cost', 0),
        json.dumps(summary.get('register_totals', {}))
    ))

    conn.commit()
    conn.close()


def store_weekly_report(week_start: str, week_end: str,
                        total_kwh: float, total_cost: float,
                        register_stats: Dict, report_text: str = None):
    """Store a weekly report snapshot for trend analysis."""
    init_database()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO weekly_reports
        (week_start, week_end, total_kwh, total_cost, register_stats, report_text)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        week_start,
        week_end,
        total_kwh,
        total_cost,
        json.dumps(register_stats, default=str),
        report_text
    ))

    conn.commit()
    conn.close()


def get_hourly_data(start_date: datetime, end_date: datetime) -> List[Dict]:
    """
    Retrieve hourly consumption data for a date range.

    Returns list of dictionaries with hourly data.
    """
    init_database()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM hourly_consumption
        WHERE timestamp >= ? AND timestamp < ?
        ORDER BY timestamp
    ''', (
        int(start_date.timestamp()),
        int(end_date.timestamp())
    ))

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        entry = {
            'timestamp': row['timestamp'],
            'datetime': datetime.fromisoformat(row['datetime']),
            'date': row['date'],
            'hour': row['hour'],
            'tou_period': row['tou_period'],
        }
        # Merge register data
        register_data = json.loads(row['register_data'])
        entry.update(register_data)
        result.append(entry)

    return result


def get_daily_summaries(start_date: str, end_date: str) -> List[Dict]:
    """
    Retrieve daily summaries for a date range.

    Args:
        start_date: Start date string (YYYY-MM-DD)
        end_date: End date string (YYYY-MM-DD)

    Returns list of daily summary dictionaries.
    """
    init_database()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM daily_summary
        WHERE date >= ? AND date <= ?
        ORDER BY date
    ''', (start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        result.append({
            'date': row['date'],
            'total_kwh': row['total_kwh'],
            'total_cost': row['total_cost'],
            'peak_kwh': row['peak_kwh'],
            'peak_cost': row['peak_cost'],
            'part_peak_kwh': row['part_peak_kwh'],
            'part_peak_cost': row['part_peak_cost'],
            'off_peak_kwh': row['off_peak_kwh'],
            'off_peak_cost': row['off_peak_cost'],
            'register_totals': json.loads(row['register_totals']),
        })

    return result


def get_previous_week_stats(current_week_start: datetime) -> Optional[Dict]:
    """
    Get stats from the previous week for comparison.

    Args:
        current_week_start: Start of the current analysis period

    Returns dictionary with previous week stats, or None if not available.
    """
    init_database()
    conn = get_connection()
    cursor = conn.cursor()

    # Look for a weekly report from around 7 days ago
    prev_week_end = current_week_start - timedelta(days=1)
    prev_week_start = prev_week_end - timedelta(days=6)

    cursor.execute('''
        SELECT * FROM weekly_reports
        WHERE week_start <= ? AND week_end >= ?
        ORDER BY week_end DESC
        LIMIT 1
    ''', (str(prev_week_start.date()), str(prev_week_start.date())))

    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            'week_start': row['week_start'],
            'week_end': row['week_end'],
            'total_kwh': row['total_kwh'],
            'total_cost': row['total_cost'],
            'register_stats': json.loads(row['register_stats']),
        }

    return None


def get_historical_averages(days: int = 30) -> Optional[Dict]:
    """
    Calculate historical averages over the past N days.

    Returns dictionary with average daily consumption and cost.
    """
    init_database()
    conn = get_connection()
    cursor = conn.cursor()

    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days)

    cursor.execute('''
        SELECT
            AVG(total_kwh) as avg_kwh,
            AVG(total_cost) as avg_cost,
            COUNT(*) as days_count
        FROM daily_summary
        WHERE date >= ? AND date <= ?
    ''', (str(start_date), str(end_date)))

    row = cursor.fetchone()
    conn.close()

    if row and row['days_count'] > 0:
        return {
            'avg_daily_kwh': row['avg_kwh'],
            'avg_daily_cost': row['avg_cost'],
            'days_analyzed': row['days_count'],
        }

    return None


def cleanup_old_data():
    """Remove data older than DATA_RETENTION_DAYS."""
    init_database()
    conn = get_connection()
    cursor = conn.cursor()

    cutoff = datetime.now() - timedelta(days=DATA_RETENTION_DAYS)
    cutoff_timestamp = int(cutoff.timestamp())
    cutoff_date = str(cutoff.date())

    cursor.execute('''
        DELETE FROM hourly_consumption WHERE timestamp < ?
    ''', (cutoff_timestamp,))

    cursor.execute('''
        DELETE FROM daily_summary WHERE date < ?
    ''', (cutoff_date,))

    deleted_hourly = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted_hourly


def get_register_history(register_name: str, days: int = 30) -> List[Dict]:
    """
    Get daily consumption history for a specific register.

    Args:
        register_name: Name of the register (e.g., 'CT 14 - Furnace [kWh]')
        days: Number of days of history to retrieve

    Returns list of {date, kwh} dictionaries.
    """
    init_database()
    conn = get_connection()
    cursor = conn.cursor()

    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days)

    cursor.execute('''
        SELECT date, register_totals FROM daily_summary
        WHERE date >= ? AND date <= ?
        ORDER BY date
    ''', (str(start_date), str(end_date)))

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        register_totals = json.loads(row['register_totals'])
        if register_name in register_totals:
            result.append({
                'date': row['date'],
                'kwh': register_totals[register_name],
            })

    return result


# Initialize database on module import
init_database()
