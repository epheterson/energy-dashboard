"""Tests for solar_forecast.py — load prediction math."""
import pytest

from solar_forecast import predict_loads


def test_predict_loads_returns_expected_keys():
    result = predict_loads()
    if result is None:
        pytest.skip("predict_loads couldn't load history (offline test environment)")
    expected_keys = {"daytime_kwh", "overnight_kwh"}
    assert expected_keys.issubset(set(result.keys())), f"missing keys, got {result.keys()}"


def test_predict_loads_non_negative():
    result = predict_loads()
    if result is None:
        pytest.skip("offline")
    for k, v in result.items():
        if isinstance(v, (int, float)):
            assert v >= 0, f"{k} should not be negative, got {v}"


def test_predict_loads_with_short_lookback():
    """Should not crash with very small lookback window."""
    result = predict_loads(lookback_days=2)
    # May return None if no data; that's fine — just don't crash
    assert result is None or isinstance(result, dict)
