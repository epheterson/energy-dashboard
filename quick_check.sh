#!/bin/bash
#
# Quick eGauge Status Check
# Shows current power usage and today's cost estimate
#
# Credentials are loaded from .env file or environment variables

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Load credentials from .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
fi

# Check if credentials are set
if [ -z "$EGAUGE_URL" ] || [ -z "$EGAUGE_USER" ] || [ -z "$EGAUGE_PASSWORD" ]; then
    echo "Error: eGauge credentials not configured."
    echo ""
    echo "Please create a .env file with:"
    echo "  EGAUGE_URL=https://your-egauge.egaug.es"
    echo "  EGAUGE_USER=owner"
    echo "  EGAUGE_PASSWORD=your_password"
    echo ""
    echo "Or copy from .env.example:"
    echo "  cp .env.example .env"
    echo "  nano .env"
    exit 1
fi

# Import rates from config (source as environment variables)
# Winter rates (Oct-May) in $/kWh
WINTER_PEAK=0.51928
WINTER_PART_PEAK=0.49193
WINTER_OFF_PEAK=0.29780

# Summer rates (Jun-Sep) in $/kWh
SUMMER_PEAK=0.64639
SUMMER_PART_PEAK=0.52525
SUMMER_OFF_PEAK=0.29780

# Fetch current instantaneous data
echo "Fetching current status from eGauge..."
DATA=$(curl -s -u "$EGAUGE_USER:$EGAUGE_PASSWORD" "$EGAUGE_URL/cgi-bin/egauge?notemp&tot&inst")

if [ $? -ne 0 ]; then
    echo "Error: Could not connect to eGauge"
    exit 1
fi

# Parse and display (simple text processing)
echo ""
echo "======================================="
echo "eGauge Quick Status Check"
echo "======================================="
echo "Time: $(date)"
echo ""

# Extract total usage
TOTAL_POWER=$(echo "$DATA" | grep -o '<r rt="total"[^>]*n="Total Usage"[^>]*><v>[^<]*</v><i>[^<]*</i>' | grep -o '<i>[^<]*</i>' | sed 's/<[^>]*>//g')

if [ ! -z "$TOTAL_POWER" ]; then
    echo "Current Total Usage: ${TOTAL_POWER} W"

    # Estimate cost
    HOUR=$(date +%H)
    MONTH=$(date +%m)

    # Determine TOU period and rate
    if [ $HOUR -ge 16 ] && [ $HOUR -lt 21 ]; then
        PERIOD="PEAK"
        if [ $MONTH -ge 6 ] && [ $MONTH -le 9 ]; then
            RATE=$SUMMER_PEAK
        else
            RATE=$WINTER_PEAK
        fi
    elif [ $HOUR -eq 15 ] || ([ $HOUR -ge 21 ] && [ $HOUR -lt 24 ]); then
        PERIOD="PART-PEAK"
        if [ $MONTH -ge 6 ] && [ $MONTH -le 9 ]; then
            RATE=$SUMMER_PART_PEAK
        else
            RATE=$WINTER_PART_PEAK
        fi
    else
        PERIOD="OFF-PEAK"
        RATE=$WINTER_OFF_PEAK
    fi

    # Calculate hourly and daily cost at current rate
    HOURLY_COST=$(echo "scale=2; ($TOTAL_POWER / 1000) * $RATE" | bc)
    DAILY_EST=$(echo "scale=2; $HOURLY_COST * 24" | bc)

    echo "Current Period: $PERIOD (\$$RATE/kWh)"
    echo "Cost at current rate: \$$HOURLY_COST/hour"
    echo "Daily estimate (if sustained): \$$DAILY_EST/day"
else
    echo "Could not parse current usage"
fi

echo ""
echo "Top 5 Active Loads Right Now:"
echo "---------------------------------------"

# Extract and sort individual registers by instantaneous power
echo "$DATA" | grep '<r t="P"' | grep -v 'rt="total"' | while read line; do
    NAME=$(echo "$line" | grep -o 'n="[^"]*"' | sed 's/n="//;s/"//')
    INST=$(echo "$line" | grep -o '<i>[^<]*</i>' | sed 's/<[^>]*>//g')
    if [ ! -z "$INST" ] && [ ! -z "$NAME" ]; then
        # Skip Grid and Generation totals
        if [[ "$NAME" != "Grid"* ]] && [[ "$NAME" != "Total"* ]]; then
            echo "$INST|$NAME"
        fi
    fi
done | sort -t'|' -k1 -rn | head -5 | while IFS='|' read power name; do
    printf "  %-40s %8.0f W\n" "$name" "$power"
done

echo ""
echo "For detailed analysis, run:"
echo "  python3 $SCRIPT_DIR/egauge_weekly_analysis.py"
echo ""
