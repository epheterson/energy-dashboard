#!/usr/bin/env python3
"""
Centralized configuration for eGauge Energy Analysis Toolkit.
All settings, credentials, and rates are managed here.

Credentials are loaded from environment variables or .env file.
"""

import os
from pathlib import Path

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not installed, use environment variables directly

# ==========================================
# CREDENTIALS (from environment variables)
# ==========================================

EGAUGE_URL = os.environ.get('EGAUGE_URL', 'https://egauge83159.egaug.es')
EGAUGE_USER = os.environ.get('EGAUGE_USER', '')
EGAUGE_PASSWORD = os.environ.get('EGAUGE_PASSWORD', '')

def check_credentials():
    """Verify that credentials are configured."""
    if not EGAUGE_USER or not EGAUGE_PASSWORD:
        raise ValueError(
            "eGauge credentials not configured.\n"
            "Set EGAUGE_USER and EGAUGE_PASSWORD environment variables,\n"
            "or create a .env file in the project directory.\n"
            "See .env.example for the template."
        )

# ==========================================
# TOU RATES ($/kWh) - SJCE / PG&E EV2-A
# ==========================================

# Winter rates (October - May)
WINTER_RATES = {
    'peak': 0.51928,       # 4-9 PM
    'part_peak': 0.49193,  # 3-4 PM, 9 PM-12 AM
    'off_peak': 0.29780,   # 12 AM-3 PM
}

# Summer rates (June - September)
SUMMER_RATES = {
    'peak': 0.64639,
    'part_peak': 0.52525,
    'off_peak': 0.29780,
}

# ==========================================
# TOU PERIOD DEFINITIONS
# ==========================================

def get_tou_period(hour):
    """
    Get TOU period for a given hour (0-23).
    Peak: 4pm-9pm (16-20)
    Part-Peak: 3pm-4pm (15) and 9pm-12am (21-23)
    Off-Peak: 12am-3pm (0-14)
    """
    if 16 <= hour < 21:
        return 'peak'
    elif hour == 15 or 21 <= hour < 24:
        return 'part_peak'
    else:
        return 'off_peak'

def is_summer(date):
    """Check if date is in summer season (June-September)."""
    return 6 <= date.month <= 9

def get_rate(date, tou_period):
    """Get the rate for a given date and TOU period."""
    rates = SUMMER_RATES if is_summer(date) else WINTER_RATES
    return rates[tou_period]

# ==========================================
# REGISTER CONFIGURATION
# ==========================================

# Registers to exclude from analysis (totals/aggregates)
EXCLUDE_REGISTERS = [
    'Usage [kWh]',
    'Generation [kWh]',
    'Grid [kWh]',
    'Grid+ [kWh]',
]

# Known device mappings (CT number -> device name for analysis)
DEVICE_REGISTERS = {
    'CT 14 - Furnace': {
        'description': 'Gas furnace blower motor',
        'optimal_kwh_per_day': (2.0, 4.0),  # Expected range for efficient operation
        'alert_threshold_kwh_per_day': 50,
    },
    'CT 5 & 6 - EV Charger': {
        'description': 'Electric vehicle charger',
        'optimal_peak_percent': 10,  # Should charge mostly off-peak
    },
    'CT 16': {
        'description': 'Kegerator / Mini Fridge',
        'optimal_kwh_per_day': (1.5, 3.0),
    },
    'CT 12 - Back Rooms': {
        'description': 'Back rooms circuit (includes mattress pad heater)',
    },
}

# ==========================================
# ALERT THRESHOLDS
# ==========================================

FURNACE_DAILY_THRESHOLD_KWH = 50  # Alert if furnace uses more than this per day
HIGH_PEAK_USAGE_PERCENT = 30      # Alert if any register uses >30% during peak hours

# ==========================================
# EMAIL CONFIGURATION
# ==========================================

EMAIL_ENABLED = os.environ.get('EMAIL_ENABLED', 'false').lower() == 'true'
EMAIL_TO = os.environ.get('EMAIL_TO', '')
EMAIL_FROM = os.environ.get('EMAIL_FROM', '')
SMTP_HOST = os.environ.get('SMTP_HOST', 'localhost')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '25'))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'false').lower() == 'true'

# ==========================================
# DATA STORAGE
# ==========================================

DATA_DIR = Path(__file__).parent / 'data'
REPORTS_DIR = Path(__file__).parent / 'reports'
CHARTS_DIR = Path(__file__).parent / 'charts'

# How long to keep historical data (days)
DATA_RETENTION_DAYS = 365

# ==========================================
# REPORT SETTINGS
# ==========================================

# Number of weeks to keep in reports directory
REPORT_RETENTION_WEEKS = 12
