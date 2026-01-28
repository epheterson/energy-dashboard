#!/usr/bin/env python3
"""
HTML Report Generator for eGauge Energy Analysis Toolkit.
Generates rich HTML reports with colors, trends, and styling.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config import (
    FURNACE_DAILY_THRESHOLD_KWH, HIGH_PEAK_USAGE_PERCENT,
    get_rate, get_tou_period
)


# Color scheme
COLORS = {
    'primary': '#2563eb',      # Blue
    'success': '#16a34a',      # Green
    'warning': '#ca8a04',      # Yellow/Orange
    'danger': '#dc2626',       # Red
    'muted': '#6b7280',        # Gray
    'light': '#f3f4f6',        # Light gray
    'dark': '#1f2937',         # Dark gray
    'white': '#ffffff',
    'peak': '#dc2626',         # Red for peak
    'part_peak': '#ca8a04',    # Yellow for part-peak
    'off_peak': '#16a34a',     # Green for off-peak
}


def format_currency(value: float) -> str:
    """Format value as currency."""
    return f"${value:,.2f}"


def format_kwh(value: float) -> str:
    """Format value as kWh."""
    return f"{value:.2f}"


def get_trend_indicator(change_pct: float) -> Tuple[str, str, str]:
    """
    Get trend arrow, color, and label based on percentage change.
    Returns (arrow_html, color, label).
    For energy/cost, increase is bad (red), decrease is good (green).
    """
    if change_pct > 10:
        return ('&#9650;', COLORS['danger'], f'+{change_pct:.1f}%')  # Up arrow, red
    elif change_pct > 0:
        return ('&#9650;', COLORS['warning'], f'+{change_pct:.1f}%')  # Up arrow, yellow
    elif change_pct < -10:
        return ('&#9660;', COLORS['success'], f'{change_pct:.1f}%')  # Down arrow, green
    elif change_pct < 0:
        return ('&#9660;', COLORS['success'], f'{change_pct:.1f}%')  # Down arrow, green
    else:
        return ('&#9644;', COLORS['muted'], '0%')  # Flat line, gray


def get_usage_status(value: float, threshold: float, invert: bool = False) -> Tuple[str, str]:
    """
    Get status indicator and color based on value vs threshold.
    Returns (status_text, color).
    """
    if invert:
        # Lower is bad (e.g., off-peak percentage)
        if value >= threshold:
            return ('Good', COLORS['success'])
        elif value >= threshold * 0.7:
            return ('OK', COLORS['warning'])
        else:
            return ('Low', COLORS['danger'])
    else:
        # Higher is bad (e.g., peak usage)
        if value <= threshold * 0.5:
            return ('Good', COLORS['success'])
        elif value <= threshold:
            return ('OK', COLORS['warning'])
        else:
            return ('High', COLORS['danger'])


def generate_html_report(
    register_stats: Dict[str, Dict],
    days: int,
    previous_week: Optional[Dict] = None,
    historical_avg: Optional[Dict] = None,
    daily_data: Optional[List[Dict]] = None
) -> str:
    """Generate a complete HTML email report."""

    # Sort registers by cost
    sorted_registers = sorted(
        register_stats.items(),
        key=lambda x: x[1]['total_cost'],
        reverse=True
    )

    total_kwh = sum(s['total_kwh'] for s in register_stats.values())
    total_cost = sum(s['total_cost'] for s in register_stats.values())
    avg_daily_kwh = total_kwh / days
    avg_daily_cost = total_cost / days

    # Calculate trends
    trend_html = ""
    if previous_week:
        prev_kwh = previous_week['total_kwh']
        prev_cost = previous_week['total_cost']
        kwh_change_pct = ((total_kwh - prev_kwh) / prev_kwh * 100) if prev_kwh > 0 else 0
        cost_change_pct = ((total_cost - prev_cost) / prev_cost * 100) if prev_cost > 0 else 0

        kwh_arrow, kwh_color, kwh_label = get_trend_indicator(kwh_change_pct)
        cost_arrow, cost_color, cost_label = get_trend_indicator(cost_change_pct)

        trend_html = f"""
        <div style="background: {COLORS['light']}; border-radius: 8px; padding: 16px; margin: 16px 0;">
            <h3 style="margin: 0 0 12px 0; color: {COLORS['dark']};">Week-over-Week Trend</h3>
            <table style="width: 100%;">
                <tr>
                    <td style="text-align: center; padding: 8px;">
                        <div style="font-size: 24px; color: {kwh_color};">{kwh_arrow}</div>
                        <div style="font-size: 18px; font-weight: bold; color: {kwh_color};">{kwh_label}</div>
                        <div style="color: {COLORS['muted']}; font-size: 12px;">Energy</div>
                        <div style="font-size: 11px; color: {COLORS['muted']};">{format_kwh(prev_kwh)} → {format_kwh(total_kwh)} kWh</div>
                    </td>
                    <td style="text-align: center; padding: 8px;">
                        <div style="font-size: 24px; color: {cost_color};">{cost_arrow}</div>
                        <div style="font-size: 18px; font-weight: bold; color: {cost_color};">{cost_label}</div>
                        <div style="color: {COLORS['muted']}; font-size: 12px;">Cost</div>
                        <div style="font-size: 11px; color: {COLORS['muted']};">{format_currency(prev_cost)} → {format_currency(total_cost)}</div>
                    </td>
                </tr>
            </table>
        </div>
        """

    # Historical comparison
    historical_html = ""
    if historical_avg and historical_avg.get('days_analyzed', 0) > 7:
        hist_daily_kwh = historical_avg['avg_daily_kwh']
        hist_daily_cost = historical_avg['avg_daily_cost']

        kwh_vs_avg = ((avg_daily_kwh - hist_daily_kwh) / hist_daily_kwh * 100) if hist_daily_kwh > 0 else 0
        cost_vs_avg = ((avg_daily_cost - hist_daily_cost) / hist_daily_cost * 100) if hist_daily_cost > 0 else 0

        _, kwh_color, _ = get_trend_indicator(kwh_vs_avg)
        _, cost_color, _ = get_trend_indicator(cost_vs_avg)

        historical_html = f"""
        <div style="background: {COLORS['white']}; border: 1px solid {COLORS['light']}; border-radius: 8px; padding: 12px; margin: 16px 0;">
            <div style="font-size: 12px; color: {COLORS['muted']};">vs 30-Day Average</div>
            <div style="display: flex; justify-content: space-around; margin-top: 8px;">
                <div style="text-align: center;">
                    <span style="font-weight: bold; color: {kwh_color};">{kwh_vs_avg:+.1f}%</span>
                    <span style="color: {COLORS['muted']}; font-size: 12px;"> energy</span>
                </div>
                <div style="text-align: center;">
                    <span style="font-weight: bold; color: {cost_color};">{cost_vs_avg:+.1f}%</span>
                    <span style="color: {COLORS['muted']}; font-size: 12px;"> cost</span>
                </div>
            </div>
        </div>
        """

    # Build register rows
    register_rows = ""
    for i, (register, stats) in enumerate(sorted_registers):
        name = register.replace(' [kWh]', '')

        # Determine row color based on position
        row_bg = COLORS['white'] if i % 2 == 0 else COLORS['light']

        # Calculate cost percentage of total
        cost_pct = (stats['total_cost'] / total_cost * 100) if total_cost > 0 else 0

        # Get peak usage status
        peak_pct = stats['by_tou']['peak']['percent']
        off_peak_pct = stats['by_tou']['off_peak']['percent']

        # Color coding for peak usage
        if peak_pct > HIGH_PEAK_USAGE_PERCENT:
            peak_color = COLORS['danger']
            peak_status = '⚠️'
        elif peak_pct > HIGH_PEAK_USAGE_PERCENT * 0.7:
            peak_color = COLORS['warning']
            peak_status = ''
        else:
            peak_color = COLORS['success']
            peak_status = '✓'

        # Progress bar for cost
        bar_width = min(cost_pct * 2, 100)  # Scale for visibility
        bar_color = COLORS['danger'] if cost_pct > 25 else COLORS['warning'] if cost_pct > 10 else COLORS['primary']

        register_rows += f"""
        <tr style="background: {row_bg};">
            <td style="padding: 12px; border-bottom: 1px solid {COLORS['light']};">
                <div style="font-weight: 600; color: {COLORS['dark']};">{name}</div>
                <div style="height: 4px; background: {COLORS['light']}; border-radius: 2px; margin-top: 4px;">
                    <div style="height: 4px; background: {bar_color}; border-radius: 2px; width: {bar_width}%;"></div>
                </div>
            </td>
            <td style="padding: 12px; text-align: right; border-bottom: 1px solid {COLORS['light']};">
                <div style="font-weight: 600;">{format_currency(stats['total_cost'])}</div>
                <div style="font-size: 11px; color: {COLORS['muted']};">{cost_pct:.1f}% of total</div>
            </td>
            <td style="padding: 12px; text-align: right; border-bottom: 1px solid {COLORS['light']};">
                <div>{format_kwh(stats['total_kwh'])} kWh</div>
                <div style="font-size: 11px; color: {COLORS['muted']};">{format_kwh(stats['avg_daily_kwh'])}/day</div>
            </td>
            <td style="padding: 12px; text-align: center; border-bottom: 1px solid {COLORS['light']};">
                <span style="color: {peak_color}; font-weight: 600;">{peak_pct:.0f}%</span>
                <span style="font-size: 12px;">{peak_status}</span>
            </td>
        </tr>
        """

    # Build alerts section
    alerts_html = ""
    alerts = []

    # Check furnace
    furnace_key = 'CT 14 - Furnace [kWh]'
    if furnace_key in register_stats:
        furnace_daily = register_stats[furnace_key]['avg_daily_kwh']
        if furnace_daily > FURNACE_DAILY_THRESHOLD_KWH:
            alerts.append({
                'type': 'danger',
                'icon': '🔥',
                'title': 'Furnace High Usage',
                'message': f'{furnace_daily:.1f} kWh/day (threshold: {FURNACE_DAILY_THRESHOLD_KWH})',
            })

    # Check peak usage
    for register, stats in sorted_registers[:5]:
        peak_pct = stats['by_tou']['peak']['percent']
        if peak_pct > HIGH_PEAK_USAGE_PERCENT:
            name = register.replace(' [kWh]', '')
            alerts.append({
                'type': 'warning',
                'icon': '⚡',
                'title': f'{name}: High Peak Usage',
                'message': f'{peak_pct:.1f}% during peak hours ({format_currency(stats["by_tou"]["peak"]["cost"])})',
            })

    if alerts:
        alerts_items = ""
        for alert in alerts:
            bg_color = COLORS['danger'] if alert['type'] == 'danger' else COLORS['warning']
            alerts_items += f"""
            <div style="background: {bg_color}15; border-left: 4px solid {bg_color}; padding: 12px; margin: 8px 0; border-radius: 0 4px 4px 0;">
                <div style="font-weight: 600; color: {bg_color};">{alert['icon']} {alert['title']}</div>
                <div style="color: {COLORS['dark']}; font-size: 13px;">{alert['message']}</div>
            </div>
            """

        alerts_html = f"""
        <div style="margin: 24px 0;">
            <h3 style="color: {COLORS['dark']}; margin-bottom: 12px;">⚠️ Alerts</h3>
            {alerts_items}
        </div>
        """
    else:
        alerts_html = f"""
        <div style="background: {COLORS['success']}15; border-left: 4px solid {COLORS['success']}; padding: 12px; margin: 24px 0; border-radius: 0 4px 4px 0;">
            <div style="font-weight: 600; color: {COLORS['success']};">✓ All Clear</div>
            <div style="color: {COLORS['dark']}; font-size: 13px;">No alerts - consumption within normal parameters</div>
        </div>
        """

    # TOU breakdown summary
    total_peak_kwh = sum(s['by_tou']['peak']['kwh'] for s in register_stats.values())
    total_part_peak_kwh = sum(s['by_tou']['part_peak']['kwh'] for s in register_stats.values())
    total_off_peak_kwh = sum(s['by_tou']['off_peak']['kwh'] for s in register_stats.values())

    total_peak_cost = sum(s['by_tou']['peak']['cost'] for s in register_stats.values())
    total_part_peak_cost = sum(s['by_tou']['part_peak']['cost'] for s in register_stats.values())
    total_off_peak_cost = sum(s['by_tou']['off_peak']['cost'] for s in register_stats.values())

    peak_pct = (total_peak_kwh / total_kwh * 100) if total_kwh > 0 else 0
    part_peak_pct = (total_part_peak_kwh / total_kwh * 100) if total_kwh > 0 else 0
    off_peak_pct = (total_off_peak_kwh / total_kwh * 100) if total_kwh > 0 else 0

    # Determine if TOU distribution is good
    off_peak_status_color = COLORS['success'] if off_peak_pct > 60 else COLORS['warning'] if off_peak_pct > 40 else COLORS['danger']

    tou_html = f"""
    <div style="margin: 24px 0;">
        <h3 style="color: {COLORS['dark']}; margin-bottom: 12px;">Time-of-Use Distribution</h3>
        <div style="display: flex; height: 24px; border-radius: 4px; overflow: hidden; margin-bottom: 8px;">
            <div style="width: {off_peak_pct}%; background: {COLORS['off_peak']};" title="Off-Peak"></div>
            <div style="width: {part_peak_pct}%; background: {COLORS['part_peak']};" title="Part-Peak"></div>
            <div style="width: {peak_pct}%; background: {COLORS['peak']};" title="Peak"></div>
        </div>
        <table style="width: 100%; font-size: 13px;">
            <tr>
                <td style="padding: 4px;">
                    <span style="display: inline-block; width: 12px; height: 12px; background: {COLORS['off_peak']}; border-radius: 2px; margin-right: 4px;"></span>
                    Off-Peak <span style="color: {COLORS['muted']};">(12am-3pm)</span>
                </td>
                <td style="text-align: right; color: {off_peak_status_color}; font-weight: 600;">{off_peak_pct:.1f}%</td>
                <td style="text-align: right;">{format_kwh(total_off_peak_kwh)} kWh</td>
                <td style="text-align: right;">{format_currency(total_off_peak_cost)}</td>
            </tr>
            <tr>
                <td style="padding: 4px;">
                    <span style="display: inline-block; width: 12px; height: 12px; background: {COLORS['part_peak']}; border-radius: 2px; margin-right: 4px;"></span>
                    Part-Peak <span style="color: {COLORS['muted']};">(3-4pm, 9pm-12am)</span>
                </td>
                <td style="text-align: right;">{part_peak_pct:.1f}%</td>
                <td style="text-align: right;">{format_kwh(total_part_peak_kwh)} kWh</td>
                <td style="text-align: right;">{format_currency(total_part_peak_cost)}</td>
            </tr>
            <tr>
                <td style="padding: 4px;">
                    <span style="display: inline-block; width: 12px; height: 12px; background: {COLORS['peak']}; border-radius: 2px; margin-right: 4px;"></span>
                    Peak <span style="color: {COLORS['muted']};">(4-9pm)</span>
                </td>
                <td style="text-align: right; color: {COLORS['peak'] if peak_pct > 30 else COLORS['dark']};">{peak_pct:.1f}%</td>
                <td style="text-align: right;">{format_kwh(total_peak_kwh)} kWh</td>
                <td style="text-align: right;">{format_currency(total_peak_cost)}</td>
            </tr>
        </table>
    </div>
    """

    # Build complete HTML
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>eGauge Energy Report</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.5; color: {COLORS['dark']}; background: {COLORS['light']}; margin: 0; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background: {COLORS['white']}; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); overflow: hidden;">

        <!-- Header -->
        <div style="background: linear-gradient(135deg, {COLORS['primary']}, #1d4ed8); color: white; padding: 24px; text-align: center;">
            <h1 style="margin: 0; font-size: 24px;">⚡ Energy Report</h1>
            <div style="opacity: 0.9; font-size: 14px; margin-top: 4px;">Last {days} Days</div>
            <div style="opacity: 0.7; font-size: 12px; margin-top: 2px;">{datetime.now().strftime('%B %d, %Y')}</div>
        </div>

        <!-- Summary Cards -->
        <div style="padding: 20px; display: flex; gap: 12px;">
            <div style="flex: 1; background: {COLORS['light']}; border-radius: 8px; padding: 16px; text-align: center;">
                <div style="font-size: 28px; font-weight: 700; color: {COLORS['primary']};">{format_currency(total_cost)}</div>
                <div style="color: {COLORS['muted']}; font-size: 12px;">Total Cost</div>
                <div style="font-size: 11px; color: {COLORS['muted']}; margin-top: 4px;">{format_currency(avg_daily_cost)}/day</div>
            </div>
            <div style="flex: 1; background: {COLORS['light']}; border-radius: 8px; padding: 16px; text-align: center;">
                <div style="font-size: 28px; font-weight: 700; color: {COLORS['dark']};">{format_kwh(total_kwh)}</div>
                <div style="color: {COLORS['muted']}; font-size: 12px;">Total kWh</div>
                <div style="font-size: 11px; color: {COLORS['muted']}; margin-top: 4px;">{format_kwh(avg_daily_kwh)}/day</div>
            </div>
        </div>

        <!-- Trend Section -->
        <div style="padding: 0 20px;">
            {trend_html}
            {historical_html}
        </div>

        <!-- Alerts -->
        <div style="padding: 0 20px;">
            {alerts_html}
        </div>

        <!-- Register Table -->
        <div style="padding: 20px;">
            <h3 style="color: {COLORS['dark']}; margin-bottom: 12px;">Consumption by Circuit</h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                <thead>
                    <tr style="background: {COLORS['dark']}; color: white;">
                        <th style="padding: 10px; text-align: left; border-radius: 4px 0 0 0;">Circuit</th>
                        <th style="padding: 10px; text-align: right;">Cost</th>
                        <th style="padding: 10px; text-align: right;">Energy</th>
                        <th style="padding: 10px; text-align: center; border-radius: 0 4px 0 0;">Peak %</th>
                    </tr>
                </thead>
                <tbody>
                    {register_rows}
                </tbody>
            </table>
        </div>

        <!-- TOU Breakdown -->
        <div style="padding: 0 20px 20px 20px;">
            {tou_html}
        </div>

        <!-- Footer -->
        <div style="background: {COLORS['light']}; padding: 16px 20px; text-align: center; font-size: 11px; color: {COLORS['muted']};">
            <div>Generated by eGauge Energy Analysis Toolkit</div>
            <div style="margin-top: 4px;">Peak: 4-9pm | Part-Peak: 3-4pm, 9pm-12am | Off-Peak: 12am-3pm</div>
        </div>
    </div>
</body>
</html>
"""

    return html


def generate_html_alert(
    subject: str,
    message: str,
    alert_type: str = 'warning',
    details: Optional[Dict] = None
) -> str:
    """Generate a simple HTML alert email."""

    color = COLORS.get(alert_type, COLORS['warning'])
    icon = '🔥' if alert_type == 'danger' else '⚠️' if alert_type == 'warning' else 'ℹ️'

    details_html = ""
    if details:
        details_rows = ""
        for key, value in details.items():
            details_rows += f"<tr><td style='padding: 4px 8px; color: {COLORS['muted']};'>{key}</td><td style='padding: 4px 8px; font-weight: 600;'>{value}</td></tr>"
        details_html = f"""
        <table style="margin-top: 12px; font-size: 13px;">
            {details_rows}
        </table>
        """

    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: {COLORS['light']};">
    <div style="max-width: 500px; margin: 0 auto; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
        <div style="background: {color}; color: white; padding: 16px; text-align: center;">
            <div style="font-size: 32px;">{icon}</div>
            <h2 style="margin: 8px 0 0 0;">{subject}</h2>
        </div>
        <div style="padding: 20px;">
            <p style="margin: 0; color: {COLORS['dark']};">{message}</p>
            {details_html}
        </div>
        <div style="background: {COLORS['light']}; padding: 12px; text-align: center; font-size: 11px; color: {COLORS['muted']};">
            eGauge Energy Analysis Toolkit
        </div>
    </div>
</body>
</html>
"""

    return html
