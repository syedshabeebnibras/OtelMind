"""Email notification channel via SMTP."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def _build_html(
    failure_type: str, confidence: float, trace_id: str, reasoning: str, service_name: str
) -> str:
    return f"""
<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 20px; }}
    .card {{ background: #1e293b; border-radius: 8px; padding: 24px; max-width: 600px; margin: 0 auto; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
    .critical {{ background: #dc2626; color: white; }}
    .warning {{ background: #d97706; color: white; }}
    .field {{ margin: 12px 0; }}
    .label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }}
    .value {{ font-size: 16px; color: #f1f5f9; margin-top: 2px; }}
    .btn {{ display: inline-block; background: #3b82f6; color: white; padding: 10px 20px; border-radius: 6px; text-decoration: none; margin-top: 16px; }}
  </style>
</head>
<body>
  <div class="card">
    <h2 style="margin-top:0">⚠️ OtelMind Alert</h2>
    <div class="field">
      <div class="label">Failure Type</div>
      <div class="value">
        <span class="badge {'critical' if confidence >= 0.9 else 'warning'}">{failure_type.replace('_', ' ').upper()}</span>
      </div>
    </div>
    <div class="field"><div class="label">Service</div><div class="value">{service_name}</div></div>
    <div class="field"><div class="label">Confidence</div><div class="value">{confidence:.0%}</div></div>
    <div class="field"><div class="label">Trace ID</div><div class="value" style="font-family:monospace">{trace_id}</div></div>
    <div class="field"><div class="label">Reasoning</div><div class="value">{reasoning}</div></div>
    <a class="btn" href="https://app.otelmind.dev/traces/{trace_id}">View Trace →</a>
  </div>
</body>
</html>
"""


async def send_email_alert(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    from_addr: str,
    to_addrs: list[str],
    failure_type: str,
    confidence: float,
    trace_id: str,
    reasoning: str,
    service_name: str,
) -> bool:
    """Send alert email via SMTP. Returns True on success."""
    if not smtp_host or not to_addrs:
        logger.debug("Email alerting not configured — skipping")
        return False

    subject = (
        f"[OtelMind] {failure_type.replace('_', ' ').title()} in {service_name} ({confidence:.0%})"
    )
    html = _build_html(failure_type, confidence, trace_id, reasoning, service_name)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            if smtp_port != 465:
                server.starttls()
            if smtp_user:
                server.login(smtp_user, smtp_password)
            server.sendmail(from_addr, to_addrs, msg.as_string())
        return True
    except Exception as exc:
        logger.error("Email alert failed: %s", exc)
        return False
