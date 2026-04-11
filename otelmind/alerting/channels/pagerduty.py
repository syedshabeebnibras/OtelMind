"""PagerDuty Events API v2 notification channel."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_PAGERDUTY_URL = "https://events.pagerduty.com/v2/enqueue"

_SEVERITY_MAP = {
    "hallucination": "warning",
    "tool_timeout": "warning",
    "infinite_loop": "critical",
    "context_overflow": "warning",
    "tool_misuse": "error",
}


async def send_pagerduty_alert(
    routing_key: str,
    failure_type: str,
    confidence: float,
    trace_id: str,
    reasoning: str,
    service_name: str,
) -> bool:
    """Trigger a PagerDuty incident via Events API v2. Returns True on success."""
    severity = _SEVERITY_MAP.get(failure_type, "warning")
    # Scale up to critical if very high confidence
    if confidence >= 0.95:
        severity = "critical"

    payload = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": f"otelmind:{trace_id}:{failure_type}",
        "payload": {
            "summary": f"OtelMind: {failure_type.replace('_', ' ').title()} in {service_name} ({confidence:.0%} confidence)",
            "source": service_name,
            "severity": severity,
            "custom_details": {
                "trace_id": trace_id,
                "failure_type": failure_type,
                "confidence": confidence,
                "reasoning": reasoning,
            },
        },
        "links": [
            {
                "href": f"https://app.otelmind.dev/traces/{trace_id}",
                "text": "View trace in OtelMind",
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_PAGERDUTY_URL, json=payload)
            resp.raise_for_status()
            return True
    except httpx.HTTPError as exc:
        logger.error("PagerDuty alert failed: %s", exc)
        return False
