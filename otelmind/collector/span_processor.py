"""Span processor that persists OTel span records into PostgreSQL."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from otelmind.storage.telemetry_service import TelemetryService


class SpanProcessor:
    """Processes raw span records and persists them via TelemetryService."""

    def __init__(self, session: AsyncSession) -> None:
        self._svc = TelemetryService(session)

    async def process_spans(self, tenant_id: uuid.UUID, span_records: list[dict[str, Any]]) -> int:
        """Ingest a batch of span records for one tenant. Returns count of spans persisted."""
        if not span_records:
            return 0

        # Group spans by trace_id
        traces: dict[str, list[dict[str, Any]]] = {}
        for record in span_records:
            tid = record["trace_id"]
            traces.setdefault(tid, []).append(record)

        persisted = 0
        for trace_id, spans in traces.items():
            await self._ensure_trace(tenant_id, trace_id, spans)
            for span_rec in spans:
                await self._persist_span(tenant_id, span_rec)
                persisted += 1

        logger.info("Processed {} spans across {} traces tenant={}", persisted, len(traces), tenant_id)
        return persisted

    async def _ensure_trace(
        self, tenant_id: uuid.UUID, trace_id: str, spans: list[dict[str, Any]]
    ) -> None:
        """Create trace record if it doesn't already exist."""
        existing = await self._svc.get_trace(tenant_id, trace_id)
        if existing:
            return

        start_times = [_parse_dt(s["start_time"]) for s in spans if s.get("start_time")]
        end_times = [_parse_dt(s["end_time"]) for s in spans if s.get("end_time")]

        start_time = min(start_times) if start_times else datetime.now(UTC)
        end_time = max(end_times) if end_times else None

        duration_ms = None
        if end_time and start_time:
            duration_ms = (end_time - start_time).total_seconds() * 1000

        has_error = any(s.get("status_code") == "ERROR" for s in spans)
        service_name = _infer_service_name(spans)

        await self._svc.create_trace(
            tenant_id,
            trace_id=trace_id,
            service_name=service_name,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            status="error" if has_error else "ok",
        )

    async def _persist_span(self, tenant_id: uuid.UUID, record: dict[str, Any]) -> None:
        """Persist a single span record and extract token counts if present."""
        inputs = _try_parse_json(record.get("inputs"))
        outputs = _try_parse_json(record.get("outputs"))

        await self._svc.create_span(
            tenant_id,
            span_id=record["span_id"],
            trace_id=record["trace_id"],
            name=record["name"],
            start_time=_parse_dt(record["start_time"]),
            parent_span_id=record.get("parent_span_id"),
            kind=record.get("kind", "INTERNAL"),
            status_code=record.get("status_code", "OK"),
            end_time=_parse_dt(record["end_time"]) if record.get("end_time") else None,
            duration_ms=record.get("duration_ms"),
            attributes=record.get("attributes"),
            inputs=inputs,
            outputs=outputs,
            error_message=record.get("error_message"),
        )

        # Extract and persist token counts from attributes
        attrs = record.get("attributes") or {}
        prompt_tokens = attrs.get("llm.token.prompt_tokens", 0)
        completion_tokens = attrs.get("llm.token.completion_tokens", 0)
        if prompt_tokens or completion_tokens:
            model_name = attrs.get("llm.model", "unknown")
            await self._svc.record_token_usage(
                tenant_id,
                trace_id=record["trace_id"],
                model_name=model_name,
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                span_id=record["span_id"],
            )


def _infer_service_name(spans: list[dict[str, Any]]) -> str:
    for s in spans:
        if s.get("service_name"):
            return str(s["service_name"])
        attrs = s.get("attributes") or {}
        if attrs.get("service.name"):
            return str(attrs["service.name"])
    return "otelmind"


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _try_parse_json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except (json.JSONDecodeError, TypeError):
            return {"raw": value}
    return {"raw": str(value)}
