"""Tests for config.py — TOU classification and rate lookup."""
from datetime import date

from config import get_rate, get_tou_period, is_summer, SUMMER_RATES, WINTER_RATES


def test_tou_period_off_peak_morning():
    # 0–14 covers off-peak in PG&E E-TOU-C-like schedules
    for h in (0, 6, 9, 13):
        assert get_tou_period(h) == "off_peak", f"hour {h} should be off_peak"


def test_tou_period_peak_evening():
    for h in (16, 17, 18, 19, 20):
        assert get_tou_period(h) == "peak", f"hour {h} should be peak"


def test_tou_period_partition():
    """Every hour 0–23 must classify as exactly one period."""
    valid = {"off_peak", "part_peak", "peak"}
    for h in range(24):
        assert get_tou_period(h) in valid


def test_get_rate_returns_positive_value():
    summer_day = date(2026, 7, 15)
    winter_day = date(2026, 1, 15)
    for d in (summer_day, winter_day):
        for tp in ("peak", "part_peak", "off_peak"):
            r = get_rate(d, tp)
            assert r > 0, f"rate for {d} {tp} should be positive, got {r}"


def test_summer_winter_split():
    assert is_summer(date(2026, 7, 1)) is True
    assert is_summer(date(2026, 1, 15)) is False


def test_rate_table_complete():
    """Both summer and winter must define all three TOU periods."""
    for tbl, label in [(SUMMER_RATES, "summer"), (WINTER_RATES, "winter")]:
        for tp in ("peak", "part_peak", "off_peak"):
            assert tp in tbl, f"{label} missing {tp}"
            assert tbl[tp] > 0
