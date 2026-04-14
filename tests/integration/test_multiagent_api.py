"""Integration tests for the multi-agent REST API.

Background `_execute_group_run` is patched out so we don't make real Claude
calls — these tests only verify the HTTP layer, request validation, tenant
scoping, and DB persistence (with a mocked session).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from otelmind.api import multiagent_routes as mod
from otelmind.api.auth import require_api_key
from otelmind.api.main import app
from tests.integration._api_helpers import make_auth_override


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _empty_session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            scalar_one_or_none=MagicMock(return_value=None),
        )
    )
    session.scalar = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def stub_session(monkeypatch):
    session = _empty_session()

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(mod, "get_session", factory)
    return session


@pytest.fixture
def auth(monkeypatch):
    tenant = MagicMock(id=uuid.uuid4(), slug="t", is_active=True, name="t")
    fake_api_key = MagicMock(scopes=["admin"], id=uuid.uuid4())
    override = make_auth_override(tenant, fake_api_key)
    app.dependency_overrides[require_api_key] = override

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(mod, "enforce_tenant_rate_limit", noop)
    yield tenant
    app.dependency_overrides.pop(require_api_key, None)


@pytest.fixture
def stub_background(monkeypatch):
    """Replace the asyncio.create_task call so no real LLM call ever happens."""
    spawned = []

    def fake_create_task(coro):
        spawned.append(coro)
        coro.close()  # immediately discard
        return MagicMock()

    monkeypatch.setattr(mod.asyncio, "create_task", fake_create_task)
    return spawned


# ─── POST /api/v1/multiagent/runs ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_run_returns_id_immediately(auth, stub_session, stub_background):
    body = {
        "problem": "What is 2 + 2?",
        "roles": [{"name": "coder", "system_prompt": "Be brief."}],
        "protocol": "round_robin",
        "max_rounds": 1,
    }
    async with _client() as client:
        resp = await client.post(
            "/api/v1/multiagent/runs", json=body, headers={"x-api-key": "test"}
        )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert "id" in data
    assert data["status"] == "pending"
    assert data["protocol"] == "round_robin"
    assert len(stub_background) == 1


@pytest.mark.asyncio
async def test_create_run_rejects_empty_roles(auth, stub_session, stub_background):
    body = {"problem": "x", "roles": []}
    async with _client() as client:
        resp = await client.post(
            "/api/v1/multiagent/runs", json=body, headers={"x-api-key": "test"}
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_run_rejects_unknown_protocol(auth, stub_session, stub_background):
    body = {
        "problem": "x",
        "roles": [{"name": "a", "system_prompt": "p"}],
        "protocol": "telepathy",
    }
    async with _client() as client:
        resp = await client.post(
            "/api/v1/multiagent/runs", json=body, headers={"x-api-key": "test"}
        )
    assert resp.status_code == 422
    assert "telepathy" in resp.text


@pytest.mark.asyncio
async def test_create_run_requires_auth(stub_session, stub_background):
    body = {"problem": "x", "roles": [{"name": "a", "system_prompt": "p"}]}
    async with _client() as client:
        resp = await client.post("/api/v1/multiagent/runs", json=body)
    assert resp.status_code in (401, 403, 422)


# ─── GET /api/v1/multiagent/runs ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_runs_empty(auth, stub_session):
    async with _client() as client:
        resp = await client.get("/api/v1/multiagent/runs", headers={"x-api-key": "test"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_list_runs_passes_status_filter(auth, stub_session):
    async with _client() as client:
        resp = await client.get(
            "/api/v1/multiagent/runs?status=completed&limit=5", headers={"x-api-key": "test"}
        )
    assert resp.status_code == 200


# ─── GET /api/v1/multiagent/runs/{id} ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_run_invalid_uuid_422(auth, stub_session):
    async with _client() as client:
        resp = await client.get("/api/v1/multiagent/runs/not-a-uuid", headers={"x-api-key": "test"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_run_missing_returns_404(auth, stub_session):
    async with _client() as client:
        resp = await client.get(
            f"/api/v1/multiagent/runs/{uuid.uuid4()}", headers={"x-api-key": "test"}
        )
    assert resp.status_code == 404


# ─── GET /api/v1/multiagent/runs/{id}/messages ────────────────────────────────


@pytest.mark.asyncio
async def test_get_messages_missing_run_returns_404(auth, stub_session):
    async with _client() as client:
        resp = await client.get(
            f"/api/v1/multiagent/runs/{uuid.uuid4()}/messages", headers={"x-api-key": "test"}
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_messages_invalid_uuid_422(auth, stub_session):
    async with _client() as client:
        resp = await client.get(
            "/api/v1/multiagent/runs/abc/messages", headers={"x-api-key": "test"}
        )
    assert resp.status_code == 422


# ─── Startup recovery for stuck group runs ─────────────────────────────────


@pytest.mark.asyncio
async def test_stuck_run_recovery_marks_old_running_rows_as_failed(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from otelmind.api import main as main_module

    now = datetime.now(UTC)
    stuck_1 = MagicMock(
        id=uuid.uuid4(),
        status="running",
        created_at=now - timedelta(minutes=15),
        result=None,
    )
    stuck_2 = MagicMock(
        id=uuid.uuid4(),
        status="pending",
        created_at=now - timedelta(minutes=20),
        result={"old": "data"},
    )

    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(
                return_value=MagicMock(all=MagicMock(return_value=[stuck_1, stuck_2]))
            )
        )
    )

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(main_module, "get_session", factory)

    n = await main_module._recover_stuck_group_runs(stuck_after_minutes=10)

    assert n == 2
    assert stuck_1.status == "failed"
    assert stuck_2.status == "failed"
    assert "Process restarted" in stuck_1.result["error"]
    assert stuck_2.result["old"] == "data"  # pre-existing keys preserved
    assert stuck_1.completed_at is not None


@pytest.mark.asyncio
async def test_stuck_run_recovery_no_stuck_rows_returns_zero(monkeypatch):
    from otelmind.api import main as main_module

    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(main_module, "get_session", factory)

    assert await main_module._recover_stuck_group_runs(stuck_after_minutes=10) == 0


@pytest.mark.asyncio
async def test_stuck_run_recovery_swallows_db_errors(monkeypatch):
    """If the DB is unreachable, recovery must not block startup."""
    from otelmind.api import main as main_module

    @asynccontextmanager
    async def factory():
        raise RuntimeError("DB unreachable")
        yield  # pragma: no cover

    monkeypatch.setattr(main_module, "get_session", factory)
    assert await main_module._recover_stuck_group_runs(stuck_after_minutes=10) == 0
