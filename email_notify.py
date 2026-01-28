#!/usr/bin/env python3
"""
Email notification module for eGauge Energy Analysis Toolkit.
Sends reports via SMTP with HTML formatting and optional attachments.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import List, Optional

from config import (
    EMAIL_ENABLED, EMAIL_TO, EMAIL_FROM,
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_USE_TLS
)


def is_email_configured() -> bool:
    """Check if email is properly configured."""
    if not EMAIL_ENABLED:
        return False
    if not EMAIL_TO:
        print("Warning: EMAIL_TO not configured")
        return False
    return True


def send_report(
    subject: str,
    body: str,
    attachments: Optional[List[Path]] = None,
    to_address: Optional[str] = None,
    html_body: Optional[str] = None
) -> bool:
    """
    Send an email report.

    Args:
        subject: Email subject line
        body: Plain text body of the email (fallback)
        attachments: List of file paths to attach (e.g., charts)
        to_address: Override recipient address
        html_body: HTML version of the email body

    Returns True if sent successfully, False otherwise.
    """
    if not is_email_configured():
        print("Email not configured. Skipping email delivery.")
        return False

    recipient = to_address or EMAIL_TO
    sender = EMAIL_FROM or f"egauge-reports@localhost"

    # Create message
    if html_body:
        # Multipart message with both HTML and plain text
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = recipient

        # Plain text version (fallback)
        text_part = MIMEText(body, 'plain', 'utf-8')
        msg.attach(text_part)

        # HTML version (preferred)
        html_part = MIMEText(html_body, 'html', 'utf-8')
        msg.attach(html_part)

        # If we have attachments, we need to wrap in a mixed container
        if attachments:
            outer = MIMEMultipart('mixed')
            outer['Subject'] = subject
            outer['From'] = sender
            outer['To'] = recipient
            outer.attach(msg)

            for filepath in attachments:
                if not filepath.exists():
                    continue
                with open(filepath, 'rb') as f:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename="{filepath.name}"'
                    )
                    outer.attach(part)
            msg = outer
    else:
        # Plain text only
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = recipient
        msg.attach(MIMEText(body, 'plain'))

        if attachments:
            for filepath in attachments:
                if not filepath.exists():
                    continue
                with open(filepath, 'rb') as f:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename="{filepath.name}"'
                    )
                    msg.attach(part)

    # Send email
    try:
        if SMTP_USE_TLS:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
            server.starttls()
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)

        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)

        server.sendmail(sender, [recipient], msg.as_string())
        server.quit()

        print(f"Email sent successfully to {recipient}")
        return True

    except smtplib.SMTPAuthenticationError:
        print("Error: SMTP authentication failed. Check SMTP_USER and SMTP_PASSWORD.")
        return False
    except smtplib.SMTPConnectError:
        print(f"Error: Could not connect to SMTP server {SMTP_HOST}:{SMTP_PORT}")
        return False
    except smtplib.SMTPException as e:
        print(f"Error sending email: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error sending email: {e}")
        return False


def send_html_report(
    subject: str,
    html_body: str,
    plain_text_fallback: str,
    attachments: Optional[List[Path]] = None,
    to_address: Optional[str] = None
) -> bool:
    """
    Send an HTML email report with plain text fallback.

    Args:
        subject: Email subject line
        html_body: HTML content
        plain_text_fallback: Plain text version for email clients that don't support HTML
        attachments: Optional file attachments
        to_address: Override recipient

    Returns True if sent successfully.
    """
    return send_report(
        subject=subject,
        body=plain_text_fallback,
        attachments=attachments,
        to_address=to_address,
        html_body=html_body
    )


def send_alert(
    subject: str,
    message: str,
    to_address: Optional[str] = None,
    alert_type: str = 'warning',
    details: Optional[dict] = None
) -> bool:
    """
    Send an alert email with HTML formatting.

    Args:
        subject: Email subject line
        message: Alert message
        to_address: Override recipient address
        alert_type: 'warning', 'danger', or 'info'
        details: Optional dict of additional details to display

    Returns True if sent successfully, False otherwise.
    """
    try:
        from html_report import generate_html_alert
        html = generate_html_alert(subject, message, alert_type, details)

        return send_report(
            subject=f"[ALERT] {subject}",
            body=message,
            attachments=None,
            to_address=to_address,
            html_body=html
        )
    except ImportError:
        return send_report(
            subject=f"[ALERT] {subject}",
            body=message,
            attachments=None,
            to_address=to_address
        )


def send_weekly_report(
    report_text: str,
    chart_paths: Optional[List[Path]] = None,
    week_date: Optional[str] = None,
    register_stats: Optional[dict] = None,
    days: int = 7,
    previous_week: Optional[dict] = None,
    historical_avg: Optional[dict] = None,
    daily_data: Optional[list] = None
) -> bool:
    """
    Send the weekly energy report with HTML formatting.

    Args:
        report_text: The full plain text report
        chart_paths: Paths to chart images to attach
        week_date: Date string for subject line
        register_stats: Stats dict for HTML generation
        days: Number of days in report
        previous_week: Previous week stats for trends
        historical_avg: Historical average data
        daily_data: Daily totals data

    Returns True if sent successfully, False otherwise.
    """
    from datetime import datetime
    date_str = week_date or datetime.now().strftime('%Y-%m-%d')

    html_body = None
    if register_stats:
        try:
            from html_report import generate_html_report
            html_body = generate_html_report(
                register_stats,
                days,
                previous_week,
                historical_avg,
                daily_data
            )
        except ImportError:
            print("Warning: Could not import html_report module. Sending plain text.")

    return send_report(
        subject=f"⚡ Weekly Energy Report - {date_str}",
        body=report_text,
        attachments=chart_paths,
        html_body=html_body
    )
