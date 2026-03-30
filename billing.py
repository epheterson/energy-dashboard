"""
Bill estimation and NEM 2.0 true-up tracking.
Uses existing eGauge + solar data to estimate bills and project true-up.
"""

from datetime import date, timedelta
from config import get_billing_config


def estimate_current_month(history_data, solar_data):
    """Estimate the current month's PG&E bill.

    Args:
        history_data: Result from _build_history() -- dict with 'total_cost', 'circuits', 'daily'
        solar_data: Result from _build_solar() -- dict with 'net_cost', 'total_export_credit', etc.

    Returns dict with bill estimate breakdown.
    """
    billing = get_billing_config()
    base_charge = billing.get('base_services_charge', 24.49)

    today = date.today()
    # Calculate days in current month
    if today.month == 12:
        days_in_month = 31
    else:
        days_in_month = (date(today.year, today.month + 1, 1) - timedelta(days=1)).day
    days_elapsed = today.day

    # Grid costs from eGauge (all circuits)
    total_grid_cost = history_data.get('total_cost', 0) if history_data else 0

    # Solar export credits offset
    export_credit = 0
    net_energy_cost = total_grid_cost
    if isinstance(solar_data, dict) and 'error' not in solar_data:
        export_credit = solar_data.get('export_credit', solar_data.get('total_export_credit', 0))
        net_energy_cost = solar_data.get('net_cost', total_grid_cost)

    # Project full month from daily rate
    daily_rate = net_energy_cost / max(days_elapsed, 1)
    projected_energy = daily_rate * days_in_month
    projected_bill = base_charge + projected_energy

    return {
        'period': f"{today.strftime('%Y-%m')}-01 to {today.strftime('%Y-%m-%d')}",
        'days_elapsed': days_elapsed,
        'days_in_month': days_in_month,
        'base_services_charge': base_charge,
        'energy_cost_to_date': round(net_energy_cost, 2),
        'export_credits_to_date': round(export_credit, 2),
        'grid_cost_to_date': round(total_grid_cost, 2),
        'daily_energy_rate': round(daily_rate, 2),
        'projected_energy_cost': round(projected_energy, 2),
        'projected_bill': round(projected_bill, 2),
    }


def estimate_trueup(monthly_snapshots):
    """Project NEM 2.0 true-up balance.

    Args:
        monthly_snapshots: List of monthly billing snapshot dicts from DB

    Returns dict with true-up projection.
    """
    billing = get_billing_config()
    trueup_month = billing.get('trueup_month', 1)

    today = date.today()

    # Calculate months since last true-up anniversary
    if today.month >= trueup_month:
        anniversary_year = today.year
    else:
        anniversary_year = today.year - 1
    months_elapsed = (today.year - anniversary_year) * 12 + (today.month - trueup_month)
    months_remaining = 12 - months_elapsed

    # Sum YTD net energy costs from snapshots
    ytd_net = sum(s.get('net_energy_cost', 0) for s in monthly_snapshots)

    # Project to 12 months
    monthly_avg = ytd_net / max(months_elapsed, 1)
    projected_annual = monthly_avg * 12

    return {
        'anniversary_month': trueup_month,
        'next_trueup': f"{anniversary_year + 1}-{trueup_month:02d}",
        'months_elapsed': months_elapsed,
        'months_remaining': months_remaining,
        'ytd_net_energy_cost': round(ytd_net, 2),
        'monthly_average': round(monthly_avg, 2),
        'projected_annual_trueup': round(projected_annual, 2),
    }
