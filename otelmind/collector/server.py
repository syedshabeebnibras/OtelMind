"""Collector server — receives OTLP HTTP spans and writes them to PostgreSQL.

This is a standalone FastAPI app that acts as an OTLP-compatible receiver.
Instrumented LangGraph apps send spans here via HTTP POST /v1/traces.
The server processes each span and queues it for batch writing.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from otelmind.collector.processor import process_span
from otelmind.collector.writer import BatchWriter
from otelmind.config import settings

_writer: BatchWriter | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the asyncpg pool and batch writer on startup; stop on shutdown."""
    global _writer

    pool = await asyncpg.create_pool(
        host=settings.db.host,
        port=settings.db.port,
        database=settings.db.database,
        user=settings.db.user,
        password=settings.db.password,
        min_size=5,
        max_size=20,
    )

    _writer = BatchWriter(pool)
    await _writer.start()

    yield

    if _writer:
        await _writer.stop()
    await pool.close()


app = FastAPI(
    title="OtelMind Collector",
    description="OTLP HTTP span receiver",
    lifespan=lifespan,
)


@app.post("/v1/traces")
async def receive_traces(request: Request) -> JSONResponse:
    """Receive OTLP HTTP trace data.

    Accepts a JSON payload with a list of resource spans (simplified format).
    Each span is processed and queued for batch writing to PostgreSQL.
    """
    global _writer

    if _writer is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Collector not ready"},
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON payload"},
        )

    # Handle both OTLP format and simplified format
    spans_data = _extract_spans(body)

    for span_data in spans_data:
        processed = process_span(span_data)
        await _writer.write(processed)

    return JSONResponse(
        status_code=200,
        content={"accepted": len(spans_data)},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "service": "collector"}


def _extract_spans(body: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    """Extract span records from either OTLP or simplified payload format."""
    # Simplified format: list of span dicts
    if isinstance(body, list):
        return body

    # OTLP format: {"resourceSpans": [{"scopeSpans": [{"spans": [...]}]}]}
    spans: list[dict[str, Any]] = []
    for resource_span in body.get("resourceSpans", body.get("resource_spans", [])):
        for scope_span in resource_span.get("scopeSpans", resource_span.get("scope_spans", [])):
            for span in scope_span.get("spans", []):
                # Flatten OTLP attributes into a simple dict
                attributes: dict[str, Any] = {}
                for attr in span.get("attributes", []):
                    key = attr.get("key", "")
                    value = attr.get("value", {})
                    # OTel attributes have typed values
                    for val_type in ("stringValue", "intValue", "doubleValue", "boolValue"):
                        if val_type in value:
                            attributes[key] = value[val_type]
                            break

                spans.append(
                    {
                        "span_id": span.get("spanId", span.get("span_id")),
                        "trace_id": span.get("traceId", span.get("trace_id")),
                        "parent_span_id": span.get("parentSpanId", span.get("parent_span_id")),
                        "name": span.get("name", "unknown"),
                        "status": span.get("status", {}),
                        "attributes": attributes,
                    }
                )

    # Fallback: treat body as single-span dict or {"spans": [...]}
    if not spans and "spans" in body:
        spans = body["spans"]
    elif not spans and "span_id" in body:
        spans = [body]

    return spans
