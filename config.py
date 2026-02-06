#!/usr/bin/env python3
"""
Centralized configuration for eGauge Energy Analysis Toolkit.
All settings, credentials, and rates are managed here.

Rates and TOU periods are loaded from config.yml.
Credentials are loaded from environment variables or .env file.
"""

import os
import sys
from pathlib import Path

import yaml

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not installed, use environment variables directly

# ==========================================
# LOAD CONFIG FROM YAML
# ==========================================

_config = None


def _load_config():
    """Load configuration from config.yml."""
    global _config
    if _config is not None:
        return _config

    config_path = Path(__file__).parent / 'config.yml'
    if not config_path.exists():
        print(
            "ERROR: config.yml not found.\n"
            "Copy config.example.yml to config.yml and customize it:\n"
            f"  cp {config_path.parent / 'config.example.yml'} {config_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(config_path) as f:
        _config = yaml.safe_load(f)
    return _config


def get_config():
    """Return the full config dict."""
    return _load_config()


def is_solar_enabled():
    """Check if solar integration is enabled in config."""
    cfg = _load_config()
    return cfg.get('solar', {}).get('enabled', False)


# ==========================================
# CREDENTIALS (from environment variables)
# ==========================================

EGAUGE_URL = os.environ.get('EGAUGE_URL', '')
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
# TOU RATES ($/kWh) — loaded from config.yml
# ==========================================

_cfg = _load_config()
_rates = _cfg.get('rates', {})

WINTER_RATES = {
    'peak': _rates.get('winter', {}).get('peak', 0.63),
    'part_peak': _rates.get('winter', {}).get('part_peak', 0.60),
    'off_peak': _rates.get('winter', {}).get('off_peak', 0.40),
}

SUMMER_RATES = {
    'peak': _rates.get('summer', {}).get('peak', 0.63),
    'part_peak': _rates.get('summer', {}).get('part_peak', 0.60),
    'off_peak': _rates.get('summer', {}).get('off_peak', 0.40),
}

SUMMER_MONTHS = set(_rates.get('summer_months', [6, 7, 8, 9]))

# TOU period hour sets from config
_tou = _cfg.get('tou_periods', {})
_PEAK_HOURS = set(_tou.get('peak', [16, 17, 18, 19, 20]))
_PART_PEAK_HOURS = set(_tou.get('part_peak', [15, 21, 22, 23]))

# ==========================================
# TOU PERIOD DEFINITIONS
# ==========================================

def get_tou_period(hour):
    """Get TOU period for a given hour (0-23)."""
    if hour in _PEAK_HOURS:
        return 'peak'
    elif hour in _PART_PEAK_HOURS:
        return 'part_peak'
    else:
        return 'off_peak'

def is_summer(date):
    """Check if date is in summer season."""
    return date.month in SUMMER_MONTHS

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
    'Total Power (Main) [kWh]',
    'Total Power (Aux) [kWh]',
]

# Known device mappings (register name -> device info)
DEVICE_REGISTERS = {
    'LP Garage Laundry': {
        'description': 'Hot tub (shared circuit), garage, washer',
        'alert_threshold_kwh_per_day': 20,
    },
    'Hot Tub': {
        'description': 'SaluSpa hot tub dedicated 20A circuit (S19)',
        'optimal_kwh_per_day': (3.0, 10.0),
        'alert_threshold_kwh_per_day': 20,
    },
    'LP Bedrooms and Bath': {
        'description': 'Office (space heater, NAS, computers)',
        'alert_threshold_kwh_per_day': 15,
    },
    'Furnace': {
        'description': 'Heat pump compressor',
        'optimal_kwh_per_day': (2.0, 8.0),
        'alert_threshold_kwh_per_day': 15,
    },
    'EV Charger': {
        'description': 'Tesla Wall Connector',
        'optimal_peak_percent': 10,  # Should charge mostly off-peak
    },
    'Dryer': {
        'description': 'Clothes dryer',
        'optimal_peak_percent': 20,
    },
}

# ==========================================
# ALERT THRESHOLDS
# ==========================================

_alerts = _cfg.get('alerts', {})
FURNACE_DAILY_THRESHOLD_KWH = 15  # Alert if heat pump uses more than this per day
HIGH_PEAK_USAGE_PERCENT = _alerts.get('high_peak_usage_percent', 30)

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
