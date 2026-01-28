#!/usr/bin/env python3
"""
Visualization module for eGauge Energy Analysis Toolkit.
Generates charts and graphs using matplotlib.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

# Check if matplotlib is available
try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend for saving files
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

from config import CHARTS_DIR


def check_matplotlib():
    """Check if matplotlib is available."""
    if not MATPLOTLIB_AVAILABLE:
        print("Warning: matplotlib not installed. Charts will be skipped.")
        print("Install with: pip install matplotlib")
    return MATPLOTLIB_AVAILABLE


def ensure_charts_dir():
    """Ensure the charts directory exists."""
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    return CHARTS_DIR


def dollar_formatter(x, pos):
    """Format y-axis values as dollars."""
    return f'${x:.2f}'


def kwh_formatter(x, pos):
    """Format y-axis values as kWh."""
    return f'{x:.1f}'


def generate_consumption_by_register_chart(
    register_stats: Dict[str, Dict],
    output_path: Optional[Path] = None,
    title: str = "Energy Consumption by Circuit"
) -> Optional[Path]:
    """
    Generate a horizontal bar chart showing consumption by register.

    Args:
        register_stats: Dictionary of register stats from analyze_data()
        output_path: Where to save the chart (default: charts/consumption_by_register.png)
        title: Chart title

    Returns path to saved chart, or None if matplotlib unavailable.
    """
    if not check_matplotlib():
        return None

    ensure_charts_dir()
    if output_path is None:
        output_path = CHARTS_DIR / 'consumption_by_register.png'

    # Sort by cost
    sorted_data = sorted(
        register_stats.items(),
        key=lambda x: x[1]['total_cost'],
        reverse=True
    )

    # Extract data
    names = [r[0].replace(' [kWh]', '') for r in sorted_data]
    costs = [r[1]['total_cost'] for r in sorted_data]
    kwh = [r[1]['total_kwh'] for r in sorted_data]

    # Create figure with two subplots
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, max(6, len(names) * 0.4)))

    # Cost chart
    colors = plt.cm.Reds([0.3 + (i * 0.5 / len(costs)) for i in range(len(costs))])
    bars1 = ax1.barh(names, costs, color=colors)
    ax1.set_xlabel('Cost ($)')
    ax1.set_title('Total Cost by Circuit')
    ax1.xaxis.set_major_formatter(FuncFormatter(dollar_formatter))
    ax1.invert_yaxis()

    # Add value labels
    for bar, cost in zip(bars1, costs):
        ax1.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height()/2,
                f'${cost:.2f}', va='center', fontsize=8)

    # kWh chart
    colors2 = plt.cm.Blues([0.3 + (i * 0.5 / len(kwh)) for i in range(len(kwh))])
    bars2 = ax2.barh(names, kwh, color=colors2)
    ax2.set_xlabel('Energy (kWh)')
    ax2.set_title('Total Consumption by Circuit')
    ax2.invert_yaxis()

    # Add value labels
    for bar, k in zip(bars2, kwh):
        ax2.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height()/2,
                f'{k:.1f}', va='center', fontsize=8)

    plt.suptitle(title, fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    return output_path


def generate_tou_breakdown_chart(
    register_stats: Dict[str, Dict],
    output_path: Optional[Path] = None,
    top_n: int = 6
) -> Optional[Path]:
    """
    Generate a stacked bar chart showing TOU breakdown for top consumers.

    Args:
        register_stats: Dictionary of register stats
        output_path: Where to save the chart
        top_n: Number of top consumers to show

    Returns path to saved chart, or None if matplotlib unavailable.
    """
    if not check_matplotlib():
        return None

    ensure_charts_dir()
    if output_path is None:
        output_path = CHARTS_DIR / 'tou_breakdown.png'

    # Get top N by cost
    sorted_data = sorted(
        register_stats.items(),
        key=lambda x: x[1]['total_cost'],
        reverse=True
    )[:top_n]

    names = [r[0].replace(' [kWh]', '') for r in sorted_data]
    peak = [r[1]['by_tou']['peak']['kwh'] for r in sorted_data]
    part_peak = [r[1]['by_tou']['part_peak']['kwh'] for r in sorted_data]
    off_peak = [r[1]['by_tou']['off_peak']['kwh'] for r in sorted_data]

    fig, ax = plt.subplots(figsize=(12, 6))

    x = range(len(names))
    width = 0.6

    # Create stacked bars
    bars1 = ax.bar(x, off_peak, width, label='Off-Peak (12am-3pm)', color='#28a745')
    bars2 = ax.bar(x, part_peak, width, bottom=off_peak, label='Part-Peak (3-4pm, 9pm-12am)', color='#ffc107')
    bars3 = ax.bar(x, peak, width, bottom=[o+p for o,p in zip(off_peak, part_peak)],
                   label='Peak (4-9pm)', color='#dc3545')

    ax.set_ylabel('Energy (kWh)')
    ax.set_title('Time-of-Use Breakdown by Circuit', fontsize=14, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=45, ha='right')
    ax.legend(loc='upper right')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    return output_path


def generate_daily_trend_chart(
    daily_data: List[Dict],
    output_path: Optional[Path] = None,
    title: str = "Daily Energy Consumption Trend"
) -> Optional[Path]:
    """
    Generate a line chart showing daily consumption over time.

    Args:
        daily_data: List of {date, total_kwh, total_cost} dictionaries
        output_path: Where to save the chart
        title: Chart title

    Returns path to saved chart, or None if matplotlib unavailable.
    """
    if not check_matplotlib():
        return None

    if not daily_data:
        return None

    ensure_charts_dir()
    if output_path is None:
        output_path = CHARTS_DIR / 'daily_trend.png'

    # Parse dates
    dates = [datetime.strptime(d['date'], '%Y-%m-%d') if isinstance(d['date'], str)
             else d['date'] for d in daily_data]
    kwh = [d['total_kwh'] for d in daily_data]
    costs = [d['total_cost'] for d in daily_data]

    fig, ax1 = plt.subplots(figsize=(12, 6))

    # Plot kWh
    color1 = '#1f77b4'
    ax1.set_xlabel('Date')
    ax1.set_ylabel('Energy (kWh)', color=color1)
    line1 = ax1.plot(dates, kwh, color=color1, marker='o', linewidth=2,
                     markersize=6, label='kWh')
    ax1.tick_params(axis='y', labelcolor=color1)
    ax1.fill_between(dates, kwh, alpha=0.2, color=color1)

    # Add average line
    avg_kwh = sum(kwh) / len(kwh)
    ax1.axhline(y=avg_kwh, color=color1, linestyle='--', alpha=0.5,
                label=f'Avg: {avg_kwh:.1f} kWh')

    # Second y-axis for cost
    ax2 = ax1.twinx()
    color2 = '#2ca02c'
    ax2.set_ylabel('Cost ($)', color=color2)
    line2 = ax2.plot(dates, costs, color=color2, marker='s', linewidth=2,
                     markersize=5, label='Cost')
    ax2.tick_params(axis='y', labelcolor=color2)
    ax2.yaxis.set_major_formatter(FuncFormatter(dollar_formatter))

    # Format x-axis
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax1.xaxis.set_major_locator(mdates.DayLocator())
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')

    plt.title(title, fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    return output_path


def generate_comparison_chart(
    current_period: Dict,
    previous_period: Dict,
    output_path: Optional[Path] = None
) -> Optional[Path]:
    """
    Generate a comparison chart between current and previous period.

    Args:
        current_period: Stats for current period {total_kwh, total_cost, ...}
        previous_period: Stats for previous period
        output_path: Where to save the chart

    Returns path to saved chart, or None if matplotlib unavailable.
    """
    if not check_matplotlib():
        return None

    ensure_charts_dir()
    if output_path is None:
        output_path = CHARTS_DIR / 'period_comparison.png'

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 5))

    # kWh comparison
    labels = ['Previous', 'Current']
    kwh_values = [previous_period['total_kwh'], current_period['total_kwh']]
    colors = ['#6c757d', '#007bff']

    bars1 = ax1.bar(labels, kwh_values, color=colors, width=0.5)
    ax1.set_ylabel('Energy (kWh)')
    ax1.set_title('Energy Consumption')

    # Add change indicator
    change_kwh = current_period['total_kwh'] - previous_period['total_kwh']
    pct_change_kwh = (change_kwh / previous_period['total_kwh'] * 100) if previous_period['total_kwh'] > 0 else 0
    ax1.text(0.5, max(kwh_values) * 1.05,
             f'{change_kwh:+.1f} kWh ({pct_change_kwh:+.1f}%)',
             ha='center', fontsize=12, fontweight='bold',
             color='red' if change_kwh > 0 else 'green')

    # Add value labels
    for bar, val in zip(bars1, kwh_values):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                f'{val:.1f}', ha='center', va='bottom', fontsize=10)

    # Cost comparison
    cost_values = [previous_period['total_cost'], current_period['total_cost']]

    bars2 = ax2.bar(labels, cost_values, color=colors, width=0.5)
    ax2.set_ylabel('Cost ($)')
    ax2.set_title('Total Cost')
    ax2.yaxis.set_major_formatter(FuncFormatter(dollar_formatter))

    # Add change indicator
    change_cost = current_period['total_cost'] - previous_period['total_cost']
    pct_change_cost = (change_cost / previous_period['total_cost'] * 100) if previous_period['total_cost'] > 0 else 0
    ax2.text(0.5, max(cost_values) * 1.05,
             f'{change_cost:+.2f} ({pct_change_cost:+.1f}%)',
             ha='center', fontsize=12, fontweight='bold',
             color='red' if change_cost > 0 else 'green')

    # Add value labels
    for bar, val in zip(bars2, cost_values):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                f'${val:.2f}', ha='center', va='bottom', fontsize=10)

    plt.suptitle('Week-over-Week Comparison', fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    return output_path


def generate_register_history_chart(
    register_name: str,
    history: List[Dict],
    output_path: Optional[Path] = None
) -> Optional[Path]:
    """
    Generate a chart showing historical consumption for a specific register.

    Args:
        register_name: Name of the register
        history: List of {date, kwh} dictionaries
        output_path: Where to save the chart

    Returns path to saved chart, or None if matplotlib unavailable.
    """
    if not check_matplotlib():
        return None

    if not history:
        return None

    ensure_charts_dir()
    if output_path is None:
        safe_name = register_name.replace(' ', '_').replace('[kWh]', '').strip('_')
        output_path = CHARTS_DIR / f'{safe_name}_history.png'

    dates = [datetime.strptime(h['date'], '%Y-%m-%d') if isinstance(h['date'], str)
             else h['date'] for h in history]
    values = [h['kwh'] for h in history]

    fig, ax = plt.subplots(figsize=(12, 5))

    ax.plot(dates, values, color='#1f77b4', marker='o', linewidth=2, markersize=4)
    ax.fill_between(dates, values, alpha=0.2, color='#1f77b4')

    # Add average and trend lines
    avg_val = sum(values) / len(values)
    ax.axhline(y=avg_val, color='orange', linestyle='--', alpha=0.7,
               label=f'Average: {avg_val:.2f} kWh/day')

    ax.set_xlabel('Date')
    ax.set_ylabel('Energy (kWh)')
    ax.set_title(f'{register_name} - Daily Consumption History', fontsize=12, fontweight='bold')
    ax.legend()

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    return output_path


def generate_all_charts(
    register_stats: Dict[str, Dict],
    daily_data: List[Dict] = None,
    previous_period: Dict = None,
    days: int = 7
) -> List[Path]:
    """
    Generate all standard charts.

    Returns list of paths to generated charts.
    """
    charts = []

    # Consumption by register
    path = generate_consumption_by_register_chart(
        register_stats,
        title=f"Energy Consumption by Circuit (Last {days} Days)"
    )
    if path:
        charts.append(path)

    # TOU breakdown
    path = generate_tou_breakdown_chart(register_stats)
    if path:
        charts.append(path)

    # Daily trend
    if daily_data:
        path = generate_daily_trend_chart(
            daily_data,
            title=f"Daily Energy Consumption (Last {days} Days)"
        )
        if path:
            charts.append(path)

    # Comparison chart
    if previous_period:
        current = {
            'total_kwh': sum(s['total_kwh'] for s in register_stats.values()),
            'total_cost': sum(s['total_cost'] for s in register_stats.values()),
        }
        path = generate_comparison_chart(current, previous_period)
        if path:
            charts.append(path)

    return charts
