"""Shared pytest fixtures for OtelMind tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from otelmind.watchdog.failure_detection import FailureDetector


@pytest.fixture
def failure_detector() -> FailureDetector:
    return FailureDetector()


def make_span(
    *,
    name: str = "langgraph.node.test",
    span_id: str | None = None,
    trace_id: str = "abc123",
    duration_ms: float | None = 100.0,
    status_code: str = "OK",
    error_message: str | None = None,
    attributes: dict | None = None,
    inputs: dict | None = None,
    outputs: dict | None = None,
) -> MagicMock:
    """Create a mock Span object for testing."""
    span = MagicMock()
    span.span_id = span_id or uuid.uuid4().hex
    span.trace_id = trace_id
    span.name = name
    span.duration_ms = duration_ms
    span.status_code = status_code
    span.error_message = error_message
    span.attributes = attributes or {}
    span.inputs = inputs
    span.outputs = outputs
    span.start_time = datetime.now(timezone.utc)
    span.end_time = datetime.now(timezone.utc)
    return span
