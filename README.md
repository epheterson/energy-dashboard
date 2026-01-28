# eGauge Energy Analysis Toolkit

Analyze energy consumption and costs from your eGauge energy monitor with Time-of-Use (TOU) pricing optimization and circuit-level analysis.

## Features

- **TOU Cost Analysis**: Calculate costs based on Peak, Part-Peak, and Off-Peak periods
- **Circuit-Level Breakdown**: Analyze individual CT registers/circuits
- **Historical Trend Analysis**: Week-over-week comparisons and 30-day averages
- **Data Persistence**: Store historical data locally for long-term tracking
- **Visualization**: Generate charts (requires matplotlib)
- **Email Notifications**: Automated report delivery (optional)
- **Device-Specific Analysis**: Specialized scripts for furnace, kegerator, etc.

## Quick Start

### 1. Configure Credentials

Copy the example environment file and add your credentials:

```bash
cp .env.example .env
nano .env  # Edit with your eGauge credentials
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run Your First Report

```bash
python3 egauge_weekly_analysis.py
```

## Scripts

### Main Analysis Script

**`egauge_weekly_analysis.py`** - Comprehensive energy analysis

```bash
# Analyze last 7 days
python3 egauge_weekly_analysis.py

# Analyze last 30 days
python3 egauge_weekly_analysis.py --days 30

# Save report to file
python3 egauge_weekly_analysis.py --output report.txt

# Generate charts
python3 egauge_weekly_analysis.py --charts

# Send email report
python3 egauge_weekly_analysis.py --email

# All options combined
python3 egauge_weekly_analysis.py --days 7 --output report.txt --charts --email
```

**Output includes:**
- Summary table ranked by total cost
- Week-over-week trend comparison
- 30-day historical average comparison
- Detailed breakdown by TOU period for each register
- Alerts for high usage
- Time-shifting recommendations

### Quick Status Check

**`quick_check.sh`** - Real-time power usage check

```bash
./quick_check.sh
```

Shows current power consumption, TOU period, and top active loads.

### Weekly Automation

**`weekly_cron.sh`** - Automated weekly reports

Set up automatic weekly reports via cron:

```bash
# Make executable
chmod +x weekly_cron.sh

# Add to crontab (runs every Monday at 6am)
crontab -e
# Add this line:
0 6 * * 1 /path/to/egauge/weekly_cron.sh
```

### Device-Specific Analysis

**`furnace_savings_analysis.py`** - Analyze furnace efficiency

```bash
python3 furnace_savings_analysis.py
python3 furnace_savings_analysis.py --days 14
```

**`kegerator_analysis.py`** - Analyze kegerator/mini-fridge usage

```bash
python3 kegerator_analysis.py
```

**`mattress_pad_analysis.py`** - Before/after comparison for equipment changes

```bash
# Analyze usage before/after a change
python3 mattress_pad_analysis.py --change-date 2026-01-25 --overnight-only

# Customize for other circuits
python3 mattress_pad_analysis.py --register "CT 8 - Laundry [kWh]" --change-date 2026-01-20
```

## Configuration

All configuration is managed through environment variables in `.env`:

```bash
# eGauge Device Credentials (Required)
EGAUGE_URL=https://your-egauge.egaug.es
EGAUGE_USER=owner
EGAUGE_PASSWORD=your_password

# Email Notifications (Optional)
EMAIL_ENABLED=true
EMAIL_TO=your@email.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_USE_TLS=true
```

Rate configuration and thresholds are in `config.py`.

## File Structure

```
egauge/
├── .env                    # Your credentials (git-ignored)
├── .env.example            # Template for .env
├── config.py               # Centralized configuration
├── egauge_weekly_analysis.py   # Main analysis script
├── quick_check.sh          # Quick status check
├── weekly_cron.sh          # Cron automation script
├── data_store.py           # Data persistence layer
├── visualization.py        # Chart generation
├── email_notify.py         # Email notifications
├── device_analysis.py      # Device-specific utilities
├── furnace_savings_analysis.py
├── kegerator_analysis.py
├── mattress_pad_analysis.py
├── data/                   # Historical data (git-ignored)
├── reports/                # Generated reports (git-ignored)
└── charts/                 # Generated charts (git-ignored)
```

## TOU Period Definitions

Based on PG&E EV2-A schedule:
- **Peak:** 4:00 PM - 9:00 PM (highest rates)
- **Part-Peak:** 3:00 PM - 4:00 PM and 9:00 PM - 12:00 AM (medium rates)
- **Off-Peak:** 12:00 AM - 3:00 PM (lowest rates)

## Rate Information

Current rates for SJCE (San Jose Clean Energy) on PG&E EV2-A:

| Season | Peak | Part-Peak | Off-Peak |
|--------|------|-----------|----------|
| Winter (Oct-May) | $0.51928 | $0.49193 | $0.29780 |
| Summer (Jun-Sep) | $0.64639 | $0.52525 | $0.29780 |

Update rates in `config.py` when they change.

## Requirements

- Python 3.8+
- curl (for fetching data from eGauge)
- python-dotenv (for environment variable loading)
- matplotlib (optional, for charts)

## Troubleshooting

**"eGauge credentials not configured"**
- Create a `.env` file from `.env.example` and add your credentials

**"Authentication required" errors**
- Check eGauge credentials in `.env`
- Verify the eGauge domain is accessible

**No data returned**
- Check that eGauge is online and accessible
- Try accessing your eGauge URL in a browser

**Charts not generating**
- Install matplotlib: `pip install matplotlib`

**Email not sending**
- Set `EMAIL_ENABLED=true` in `.env`
- Configure SMTP settings for your email provider
- For Gmail, use an App Password (not your regular password)
