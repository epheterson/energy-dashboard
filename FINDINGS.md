# eGauge Analysis - Key Findings for Last 7 Days

## Summary

**Total Energy:** 282.40 kWh
**Total Cost:** $106.17
**Average per day:** $15.17

---

## Top 5 Energy Consumers (by cost)

1. **CT 14 - Furnace:** $40.14/week ($5.73/day) - 14.90 kWh/day
   - **20.9% during PEAK hours** → Potential savings: $4.83/week if shifted
   - **21.0% during PART-PEAK**
   - **58.1% during OFF-PEAK** ✓ (good!)

2. **CT 5 & 6 - EV Charger:** $18.64/week ($2.66/day) - 7.63 kWh/day
   - **22.8% during PEAK hours** → Potential savings: $2.70/week if shifted
   - **0.3% during PART-PEAK** ✓
   - **76.9% during OFF-PEAK** ✓ (excellent!)

3. **CT 12 - Back Rooms:** $8.70/week ($1.24/day) - 3.22 kWh/day
   - **21.8% during PEAK hours**
   - **20.3% during PART-PEAK**
   - **57.9% during OFF-PEAK**

4. **CT 13 - Front Rooms:** $8.43/week ($1.20/day) - 3.19 kWh/day
   - **21.3% during PEAK hours**
   - **16.6% during PART-PEAK**
   - **62.1% during OFF-PEAK**

5. **CT 15 - Overhead Lights:** $8.25/week ($1.18/day) - 2.87 kWh/day
   - **26.8% during PEAK hours** (highest peak usage %)
   - **27.1% during PART-PEAK** (highest part-peak usage %)
   - **46.1% during OFF-PEAK**

---

## About Your Furnace Usage

**Current Status:** 14.90 kWh/day average

Your furnace is using about 21% of its energy during peak hours (4-9 PM). This is reasonable for winter heating, as the coldest part of the day overlaps with peak pricing.

**Is 14.90 kWh/day normal for a furnace?**
- For an electric heat pump: Yes, this is typical for winter in San Jose
- For a gas furnace with electric blower: The blower should only use ~3-5 kWh/day
- For electric resistance heating: This would be on the lower side

**Your furnace usage is NOT abnormally high** - it's below the 50 kWh/day alert threshold.

However, if this is just a gas furnace blower motor using 15 kWh/day, that would be quite high and worth investigating.

---

## Potential Cost Savings

### Quick Wins:

1. **EV Charging:** Already optimized! 77% during off-peak = saving ~$10-12/week vs peak charging

2. **Overhead Lights (CT 15):**
   - Currently: 27% peak + 27% part-peak = 54% during expensive hours
   - Could save ~$1.20/week by using lights more during off-peak times (before 3 PM)

3. **Dishwasher & Hot Water (CT 11):**
   - Currently: Only 15.4% peak usage ✓
   - Already well-optimized for off-peak (74%)

### Harder to Change:

- **Furnace peak usage** - Hard to shift heating to off-peak hours
- **Room lighting** - Usually matches when people are home

**Total potential weekly savings from easy shifts:** ~$2-3/week (~$100-150/year)

---

## Recommendations

### Immediate:

1. ✓ **Keep EV charging on current schedule** - you're already doing great here!

2. ✓ **CT 16 - Kegerator/Mini Fridge** - Using 2.52 kWh/day ($354/year) which is NORMAL for a kegerator. See `kegerator_analysis.py` for efficiency tips (potential $50-100/year savings).

3. **Use timer switches for overhead lights** - Could shift some lighting to daytime/off-peak

### Investigate:

1. **Verify CT 14 is actually the furnace** - 15 kWh/day seems high for just a blower motor. If you have a gas furnace, the blower should use 2-4 kWh/day max. Consider:
   - Is this an electric heat pump? (normal usage)
   - Is this electric resistance heating? (normal, but expensive)
   - Is this a gas furnace blower using 15 kWh/day? (investigate - may be oversized or running inefficiently)

2. **Check if you can set furnace/thermostat to pre-heat** - Heat the house 2-3 PM (off-peak) and let it coast during peak hours

### Monthly Review:

Run this report monthly to track:
- Seasonal changes in consumption
- Impact of any changes you make
- Unusual spikes in specific circuits

---

## How to Use Your New Tools

### Quick Daily Check:
```bash
~/Projects/egauge/quick_check.sh
```
Shows current power usage and today's cost estimate.

### Weekly Analysis:
```bash
python3 ~/Projects/egauge/egauge_weekly_analysis.py
```

### Save Monthly Reports:
```bash
python3 ~/Projects/egauge/egauge_weekly_analysis.py --days 30 --output ~/Projects/egauge/monthly_$(date +%Y%m).txt
```

### Automate Weekly Reports (optional):
```bash
# Edit weekly_cron.sh to add your email
nano ~/Projects/egauge/weekly_cron.sh

# Add to crontab (runs every Monday at 6am)
crontab -e
# Add this line:
0 6 * * 1 /Users/twfarley/Projects/egauge/weekly_cron.sh
```

---

## Next Steps

1. Identify what CT 16 is monitoring
2. Determine if CT 14 is really just the furnace or if it includes other heating
3. Consider smart plugs/timers for overhead lights
4. Run analysis again in summer to see A/C usage patterns
