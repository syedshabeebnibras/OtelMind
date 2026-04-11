"""Slack notification channel."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_FAILURE_EMOJI = {
    "hallucination": "🔮",
    "tool_timeout": "⏱️",
    "infinite_loop": "🔄",
    "context_overflow": "📚",
    "tool_misuse": "🔧",
}


async def send_slack_alert(
    webhook_url: str,
    failure_type: str,
    confidence: float,
    trace_id: str,
    reasoning: str,
    service_name: str,
    app_base_url: str = "https://app.otelmind.dev",
) -> bool:
    """POST a formatted Slack message. Returns True on success."""
    emoji = _FAILURE_EMOJI.get(failure_type, "⚠️")
    severity = "Critical" if confidence >= 0.9 else "Warning" if confidence >= 0.7 else "Info"

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} OtelMind — {failure_type.replace('_', ' ').title()} Detected",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Service:*\n{service_name}"},
                {"type": "mrkdwn", "text": f"*Severity:*\n{severity}"},
                {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence:.0%}"},
                {"type": "mrkdwn", "text": f"*Trace ID:*\n`{trace_id[:16]}...`"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Reasoning:*\n{reasoning}"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "View Trace"},
                    "url": f"{app_base_url}/traces/{trace_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Failures"},
                    "url": f"{app_base_url}/failures",
                },
            ],
        },
        {"type": "divider"},
    ]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={"blocks": blocks})
            resp.raise_for_status()
            return True
    except httpx.HTTPError as exc:
        logger.error("Slack alert failed: %s", exc)
        return False
