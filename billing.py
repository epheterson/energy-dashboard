"""
Bill estimation and NEM 2.0 true-up tracking.

Supports PG&E-bundled and CCA (Community Choice Aggregation) customers.
Calculates three cost streams:
  1. NEM charges -- PG&E delivery, deferred to annual true-up
  2. Generation charges -- CCA (e.g. MCE) or PG&E, paid monthly
  3. Fixed charges -- Base Services Charge, paid monthly

The monthly bill = generation + fixed charges (NEM is deferred).
The true-up = accumulated NEM charges - delivery credits.
"""

from datetime import date, timedelta
from config import (
    get_billing_config, get_billing_delivery_rate, get_billing_generation_rate,
    get_billing_fixed_daily, get_nem_adjustment, is_cca_enabled, get_cca_storage_credit,
)


def calculate_billing_from_solar(solar_data, days_in_period):
    """Calculate all three billing streams from solar integration data.

    Args:
        solar_data: Dict from _build_solar() with 'by_tou' breakdown.
        days_in_period: Number of billing days.

    Returns dict with all billing components, or None if no solar data.
    """
    if not isinstance(solar_data, dict) or 'error' in solar_data:
        return None

    today = date.today()
    by_tou = solar_data.get('by_tou', {})
    nem_adjustment_rate = get_nem_adjustment()
    daily_fixed = get_billing_fixed_daily()
    cca_enabled = is_cca_enabled()
    storage_credit = get_cca_storage_credit() if cca_enabled else 0

    total_delivery_cost = 0
    total_generation_cost = 0
    total_export_credit = 0
    total_net_kwh = 0
    tou_breakdown = {}

    for period in ['peak', 'part_peak', 'off_peak']:
        tou_data = by_tou.get(period, {})
        grid_import = tou_data.get('grid_import', 0)
        grid_export = tou_data.get('grid_export', 0)
        net_kwh = grid_import - grid_export

        delivery_rate = get_billing_delivery_rate(today, period)
        generation_rate = get_billing_generation_rate(today, period)

        delivery_cost = grid_import * delivery_rate
        gen_cost = grid_import * generation_rate if cca_enabled else 0
        export_credit = tou_data.get('export_credit', 0)

        total_delivery_cost += delivery_cost
        total_generation_cost += gen_cost
        total_export_credit += export_credit
        total_net_kwh += net_kwh

        tou_breakdown[period] = {
            'grid_import_kwh': round(grid_import, 1),
            'grid_export_kwh': round(grid_export, 1),
            'net_kwh': round(net_kwh, 1),
            'delivery_cost': round(delivery_cost, 2),
            'generation_cost': round(gen_cost, 2),
            'export_credit': round(export_credit, 2),
        }

    nem_adjustments = total_net_kwh * nem_adjustment_rate
    nem_charges = total_delivery_cost + nem_adjustments - total_export_credit
    generation_charges = max(0, total_generation_cost - storage_credit)
    fixed_charges = daily_fixed * days_in_period
    monthly_electric_bill = generation_charges + fixed_charges

    return {
        'nem_charges': round(nem_charges, 2),
        'generation_charges': round(generation_charges, 2),
        'fixed_charges': round(fixed_charges, 2),
        'monthly_electric_bill': round(monthly_electric_bill, 2),
        'nem_adjustments': round(nem_adjustments, 2),
        'export_credits': round(total_export_credit, 2),
        'delivery_cost_gross': round(total_delivery_cost, 2),
        'generation_cost_gross': round(total_generation_cost, 2),
        'storage_credit': round(storage_credit, 2),
        'net_kwh': round(total_net_kwh, 1),
        'grid_import_kwh': round(solar_data.get('grid_import_kwh', solar_data.get('total_grid_import_kwh', 0)), 1),
        'grid_export_kwh': round(solar_data.get('grid_export_kwh', solar_data.get('total_grid_export_kwh', 0)), 1),
        'solar_kwh': round(solar_data.get('solar_kwh', solar_data.get('total_solar_kwh', 0)), 1),
        'cca_provider': get_billing_config().get('cca', {}).get('provider', 'PG&E'),
        'by_tou': tou_breakdown,
        # Data coverage: what fraction of consumption is source-attributed
        'consumption_kwh': round(solar_data.get('consumption_kwh', 0), 1),
        'coverage_pct': round(
            (solar_data.get('grid_import_kwh', 0) + solar_data.get('solar_kwh', 0))
            / max(solar_data.get('consumption_kwh', 1), 1) * 100, 1
        ) if solar_data.get('consumption_kwh', 0) > 0 else 0,
    }


def estimate_current_month(solar_data):
    """Estimate the current month's electrical bill and NEM charges.

    Args:
        solar_data: Dict from _build_solar() for days elapsed this month.

    Returns dict with bill estimate and projection.
    """
    today = date.today()
    if today.month == 12:
        days_in_month = 31
    else:
        days_in_month = (date(today.year, today.month + 1, 1) - timedelta(days=1)).day
    days_elapsed = today.day

    billing = calculate_billing_from_solar(solar_data, days_elapsed)
    if not billing:
        daily_fixed = get_billing_fixed_daily()
        return {
            'period': f"{today.strftime('%Y-%m')}-01 to {today.strftime('%Y-%m-%d')}",
            'days_elapsed': days_elapsed,
            'days_in_month': days_in_month,
            'error': 'Solar data unavailable',
            'fixed_charges_to_date': round(daily_fixed * days_elapsed, 2),
        }

    nem_daily = billing['nem_charges'] / max(days_elapsed, 1)
    gen_daily = billing['generation_charges'] / max(days_elapsed, 1)

    projected_nem = nem_daily * days_in_month
    projected_gen = gen_daily * days_in_month
    projected_fixed = get_billing_fixed_daily() * days_in_month
    projected_monthly_bill = projected_gen + projected_fixed

    return {
        'period': f"{today.strftime('%Y-%m')}-01 to {today.strftime('%Y-%m-%d')}",
        'days_elapsed': days_elapsed,
        'days_in_month': days_in_month,
        'nem_charges_to_date': billing['nem_charges'],
        'generation_charges_to_date': billing['generation_charges'],
        'fixed_charges_to_date': billing['fixed_charges'],
        'monthly_electric_bill_to_date': billing['monthly_electric_bill'],
        'export_credits_to_date': billing['export_credits'],
        'grid_import_kwh': billing['grid_import_kwh'],
        'grid_export_kwh': billing['grid_export_kwh'],
        'solar_kwh': billing['solar_kwh'],
        'net_kwh': billing['net_kwh'],
        'projected_nem': round(projected_nem, 2),
        'projected_generation': round(projected_gen, 2),
        'projected_fixed': round(projected_fixed, 2),
        'projected_monthly_bill': round(projected_monthly_bill, 2),
        'projected_total_electric': round(projected_nem + projected_monthly_bill, 2),
        'cca_provider': billing['cca_provider'],
        'by_tou': billing['by_tou'],
    }


def estimate_trueup(monthly_snapshots, current_month_nem=0):
    """Project NEM 2.0 true-up balance.

    Args:
        monthly_snapshots: List of monthly billing dicts from DB.
        current_month_nem: NEM charges for current (incomplete) month.

    Returns dict with true-up projection.
    """
    billing_cfg = get_billing_config()
    trueup_month = billing_cfg.get('trueup_month', 1)

    today = date.today()
    if today.month >= trueup_month:
        anniversary_year = today.year
    else:
        anniversary_year = today.year - 1
    months_elapsed = (today.year - anniversary_year) * 12 + (today.month - trueup_month)
    months_remaining = 12 - months_elapsed

    ytd_nem = sum(s.get('nem_charges', s.get('net_energy_cost', 0)) for s in monthly_snapshots)
    ytd_nem += current_month_nem

    daily_fixed = get_billing_fixed_daily()
    ytd_delivery_credits = daily_fixed * 30.5 * months_elapsed

    months_for_avg = max(months_elapsed, 1)
    monthly_avg_nem = ytd_nem / months_for_avg
    projected_annual_nem = monthly_avg_nem * 12
    projected_delivery_credits = daily_fixed * 365
    projected_trueup = max(0, projected_annual_nem - projected_delivery_credits)

    return {
        'anniversary_month': trueup_month,
        'next_trueup': f"{anniversary_year + 1}-{trueup_month:02d}",
        'months_elapsed': months_elapsed,
        'months_remaining': months_remaining,
        'ytd_nem_charges': round(ytd_nem, 2),
        'ytd_delivery_credits': round(ytd_delivery_credits, 2),
        'monthly_avg_nem': round(monthly_avg_nem, 2),
        'projected_annual_nem': round(projected_annual_nem, 2),
        'projected_delivery_credits': round(projected_delivery_credits, 2),
        'projected_trueup': round(projected_trueup, 2),
    }
