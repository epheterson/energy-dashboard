#!/bin/bash
#
# Weekly Energy Report Automation Script
# Generates a weekly energy report with optional email delivery
#
# Credentials are loaded from .env file or environment variables
#
# To set up automatic weekly reports, add to crontab:
#   crontab -e
#   0 6 * * 1 /path/to/egauge/weekly_cron.sh
#

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPORT_DIR="$SCRIPT_DIR/reports"
DATE=$(date +%Y%m%d)
REPORT_FILE="$REPORT_DIR/weekly_report_$DATE.txt"

# Load credentials from .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
fi

# Check if credentials are set
if [ -z "$EGAUGE_URL" ] || [ -z "$EGAUGE_USER" ] || [ -z "$EGAUGE_PASSWORD" ]; then
    echo "Error: eGauge credentials not configured."
    echo "Please create a .env file. See .env.example for template."
    exit 1
fi

# Create reports directory if it doesn't exist
mkdir -p "$REPORT_DIR"

# Determine if we should generate charts
CHART_FLAG=""
if command -v python3 &> /dev/null; then
    # Check if matplotlib is available
    if python3 -c "import matplotlib" 2>/dev/null; then
        CHART_FLAG="--charts"
    fi
fi

# Determine if we should send email
EMAIL_FLAG=""
if [ "$EMAIL_ENABLED" = "true" ] && [ ! -z "$EMAIL_TO" ]; then
    EMAIL_FLAG="--email"
fi

# Generate the report
echo "Generating weekly energy report..."
python3 "$SCRIPT_DIR/egauge_weekly_analysis.py" --days 7 --output "$REPORT_FILE" $CHART_FLAG $EMAIL_FLAG

if [ $? -eq 0 ]; then
    echo "Report generated successfully: $REPORT_FILE"

    # Display key highlights
    echo ""
    echo "=== REPORT HIGHLIGHTS ==="
    head -40 "$REPORT_FILE"

    # Keep only last 12 weeks of reports
    find "$REPORT_DIR" -name "weekly_report_*.txt" -mtime +84 -delete

    echo ""
    echo "=== REPORT SAVED ==="
    echo "Full report: $REPORT_FILE"

    # Show chart locations if generated
    if [ ! -z "$CHART_FLAG" ]; then
        echo "Charts: $SCRIPT_DIR/charts/"
    fi

else
    echo "Error generating report"
    exit 1
fi
