"""Span Processor — transforms raw OTel spans into database records.

When a span arrives from the OTel SDK, it's a protobuf object with nested
attributes. This module flattens it into simple dictionaries that map
directly to our PostgreSQL tables.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any


def process_span(span_data: dict[str, Any]) -> dict[str, Any]:
    """Process a single span into database-ready records.

    Returns dict with keys: span, trace, tokens (or None), error (or None).
    """
    attributes = span_data.get("attributes", {})

    span_record = {
        "span_id": span_data.get("span_id", str(uuid.uuid4())),
        "trace_id": span_data.get("trace_id"),
        "parent_span_id": span_data.get("parent_span_id"),
        "span_name": span_data.get("name", "unknown"),
        "step_index": attributes.get("otelmind.step_index"),
        "duration_ms": attributes.get("otelmind.duration_ms"),
        "status_code": span_data.get("status", {}).get("status_code", "UNSET"),
        "input_preview": str(attributes.get("otelmind.input_preview", ""))[:500],
        "output_preview": str(attributes.get("otelmind.output_preview", ""))[:500],
        "created_at": datetime.now(UTC),
    }

    trace_record = {
        "trace_id": span_data.get("trace_id"),
        "service_name": attributes.get("otelmind.service_name", "unknown"),
        "started_at": datetime.now(UTC),
    }

    # Extract token counts (only if this span involved an LLM call)
    token_record = None
    prompt_tokens = attributes.get("otelmind.prompt_tokens")
    if prompt_tokens is not None:
        token_record = {
            "span_id": span_record["span_id"],
            "trace_id": span_record["trace_id"],
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(attributes.get("otelmind.completion_tokens", 0)),
            "model": attributes.get("otelmind.model", "unknown"),
            "created_at": datetime.now(UTC),
        }

    # Extract error info
    error_record = None
    error_type = attributes.get("otelmind.error_type")
    if error_type:
        error_record = {
            "span_id": span_record["span_id"],
            "trace_id": span_record["trace_id"],
            "tool_name": span_record["span_name"],
            "error_type": error_type,
            "error_message": attributes.get("otelmind.error_message", ""),
            "created_at": datetime.now(UTC),
        }

    return {
        "span": span_record,
        "trace": trace_record,
        "tokens": token_record,
        "error": error_record,
    }
