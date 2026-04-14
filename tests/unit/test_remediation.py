"""Tests for otelmind.remediation.{retry,escalate,swap_tool}."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import yaml

from otelmind.remediation.escalate import EscalateStrategy
from otelmind.remediation.retry import RetryStrategy
from otelmind.remediation.swap_tool import SwapToolStrategy


@pytest.mark.asyncio
async def test_retry_strategy_succeeds_on_third_attempt():
    call_count = 0

    async def fn():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient")
        return "done"

    strategy = RetryStrategy()
    result = await strategy.execute(
        {"trace_id": "t"},
        {"callable": fn, "max_attempts": 5, "backoff_base": 0.001},
    )
    assert result["status"] == "success"
    assert result["attempts"] == 3
    assert result["result"] == "done"


@pytest.mark.asyncio
async def test_retry_strategy_exhausts_and_fails():
    async def fn():
        raise RuntimeError("always fails")

    strategy = RetryStrategy()
    result = await strategy.execute(
        {"trace_id": "t"},
        {"callable": fn, "max_attempts": 2, "backoff_base": 0.001},
    )
    assert result["status"] == "failed"
    assert "always fails" in result["error"]


@pytest.mark.asyncio
async def test_retry_strategy_skips_without_callable():
    strategy = RetryStrategy()
    result = await strategy.execute({"trace_id": "t"}, {})
    assert result["status"] == "skipped"
    assert result["reason"] == "no_callable_provided"


@pytest.mark.asyncio
async def test_escalate_strategy_sends_webhook():
    strategy = EscalateStrategy()

    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()

    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=client):
        result = await strategy.execute(
            {"trace_id": "t", "failure_type": "tool_timeout", "confidence": 0.9, "evidence": {}},
            {"webhook_url": "https://example.com/hook"},
        )
    assert result["status"] == "success"
    assert result["webhook_status_code"] == 200
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_escalate_strategy_handles_http_error():
    strategy = EscalateStrategy()

    error_response = MagicMock()
    error_response.status_code = 500
    error_response.text = "internal"

    client = MagicMock()
    client.post = AsyncMock(
        side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=error_response)
    )
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=client):
        result = await strategy.execute(
            {"trace_id": "t"},
            {"webhook_url": "https://example.com/hook"},
        )
    assert result["status"] == "failed"
    assert result["webhook_status_code"] == 500


@pytest.mark.asyncio
async def test_escalate_strategy_skipped_without_webhook(monkeypatch):
    from otelmind.config import settings

    monkeypatch.setattr(settings.remediation, "escalation_webhook_url", "")
    strategy = EscalateStrategy()
    result = await strategy.execute({"trace_id": "t"}, {})
    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_swap_tool_returns_fallback(tmp_path: Path):
    mapping = {
        "search_web": {"fallback": "search_web_backup", "description": "backup"},
    }
    config_path = tmp_path / "fallback.yaml"
    config_path.write_text(yaml.safe_dump(mapping))
    strategy = SwapToolStrategy(fallback_path=config_path)

    result = await strategy.execute(
        {"trace_id": "t", "evidence": {"failed_tool": "search_web"}},
        {},
    )
    assert result["status"] == "success"
    assert result["fallback_tool"] == "search_web_backup"


@pytest.mark.asyncio
async def test_swap_tool_no_fallback_for_unknown(tmp_path: Path):
    config_path = tmp_path / "fallback.yaml"
    config_path.write_text(yaml.safe_dump({}))
    strategy = SwapToolStrategy(fallback_path=config_path)

    result = await strategy.execute(
        {"trace_id": "t"},
        {"failed_tool": "unknown_tool"},
    )
    assert result["status"] == "no_fallback_available"


@pytest.mark.asyncio
async def test_swap_tool_skipped_without_failed_tool(tmp_path: Path):
    config_path = tmp_path / "fallback.yaml"
    config_path.write_text(yaml.safe_dump({"a": {"fallback": "b"}}))
    strategy = SwapToolStrategy(fallback_path=config_path)

    result = await strategy.execute({"trace_id": "t"}, {})
    assert result["status"] == "skipped"
