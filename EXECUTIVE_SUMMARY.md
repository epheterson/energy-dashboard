# eGauge Energy Analysis - Executive Summary

**Report Date:** January 27, 2026
**Analysis Period:** Last 7 days

---

## 📊 Overall Usage

- **Total Energy:** 282.40 kWh/week
- **Total Cost:** $106.17/week ($5,521/year)
- **Average Daily:** $15.17/day

---

## 🔥 PRIORITY ISSUE: Furnace (CT 14)

### Current Status
- **Usage:** 14.90 kWh/day ($2,091/year)
- **Expected for gas furnace blower:** 2-4 kWh/day ($420/year)
- **⚠️ POTENTIAL SAVINGS: $1,670/year**

### What to Do NOW
1. **Check air filter** (most common cause)
2. **Verify thermostat fan is on "AUTO" not "ON"**
3. **Run diagnostic test:**
   ```bash
   # Turn off furnace breaker, then:
   ~/Projects/egauge/quick_check.sh
   # If CT 14 still shows power, it's measuring more than the furnace
   ```
4. **Schedule HVAC inspection** if filter replacement doesn't help

### Why This Matters
If your furnace blower is using 15 kWh/day instead of 3 kWh/day, that's:
- **$139/month** wasted
- **$1,670/year** wasted
- An ECM blower motor upgrade ($400-800) would pay for itself in **6 months**

---

## ✅ What's Working Well

### 1. EV Charging (CT 5 & 6) - EXCELLENT!
- **77% off-peak charging** 🎉
- Saving ~$10-12/week vs. peak charging
- Annual cost: $969/year (could be $2,200 if charged during peak!)

### 2. Kegerator (CT 16) - NORMAL
- Usage: 2.52 kWh/day ($354/year)
- Normal for a kegerator
- Minor optimizations available (~$50-100/year savings)

### 3. Hot Water/Dishwasher (CT 11) - GOOD
- Only 15.4% peak usage
- 74% off-peak (well optimized)

---

## 💡 Quick Wins (Beyond Furnace)

### 1. Overhead Lights (CT 15)
- **Current:** 27% peak + 27% part-peak
- **Potential:** Use more daytime/off-peak lighting
- **Savings:** ~$60/year

### 2. Kegerator Temperature
- **Current:** Likely 32-34°F
- **Recommendation:** Raise to 38°F (still great for beer)
- **Savings:** ~$100/year

---

## 📈 Total Potential Savings

| Improvement | Annual Savings |
|-------------|----------------|
| **Fix furnace blower** | **$1,670** |
| Optimize lighting schedule | $60 |
| Kegerator temperature | $100 |
| **TOTAL** | **$1,830/year** |

---

## 🛠️ Your Analysis Tools

All tools are in `~/Projects/egauge/`

### Quick Daily Check
```bash
~/Projects/egauge/quick_check.sh
```
Shows current power usage and cost estimate.

### Weekly Analysis
```bash
python3 ~/Projects/egauge/egauge_weekly_analysis.py
```
Full breakdown by circuit and TOU period.

### Specialized Analysis
```bash
python3 ~/Projects/egauge/furnace_savings_analysis.py
python3 ~/Projects/egauge/kegerator_analysis.py
```

### Automate Weekly Reports (Optional)
```bash
# Edit to add your email
nano ~/Projects/egauge/weekly_cron.sh

# Add to crontab (runs every Monday at 6am)
crontab -e
# Add: 0 6 * * 1 /Users/twfarley/Projects/egauge/weekly_cron.sh
```

---

## 📋 Action Plan

### This Week
- [ ] Check/replace furnace air filter
- [ ] Verify thermostat fan setting (AUTO not ON)
- [ ] Test CT 14 with furnace breaker off
- [ ] Check kegerator temperature setting

### This Month
- [ ] Schedule HVAC inspection if furnace still high
- [ ] Clean kegerator condenser coils
- [ ] Review monthly energy report
- [ ] Compare to this week's baseline

### Long Term
- [ ] Consider ECM blower motor upgrade if needed
- [ ] Track seasonal usage changes
- [ ] Review rates when PG&E/SJCE announces changes

---

## 📞 Resources

- **eGauge Web Interface:** https://egauge83159.egaug.es
- **Analysis Directory:** `~/Projects/egauge/`
- **Full Weekly Report:** `~/Projects/egauge/report_20260127.txt`
- **Detailed Findings:** `~/Projects/egauge/FINDINGS.md`

---

## 🎯 Focus Areas

**Priority 1:** Furnace (potential $1,670/year savings)
**Priority 2:** Everything else (potential $160/year savings)

The furnace is **10x more important** than all other optimizations combined!
