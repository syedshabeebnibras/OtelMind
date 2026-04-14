"""API endpoint integration tests — exercises the FastAPI app without a real DB.

We override FastAPI dependencies to bypass auth and the tenant rate limiter,
and patch the SQLAlchemy session factory to return a mocked session. This
lets us exercise route logic, request parsing, Pydantic response validation,
and middleware without standing up Postgres or Redis.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from otelmind.api import routes as routes_module
from otelmind.api.auth import require_api_key
from otelmind.api.main import app
from tests.integration._api_helpers import make_auth_override as _make_auth_override


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _fake_tenant() -> MagicMock:
    """Build a Tenant stand-in the routes can treat like the real ORM object."""
    tenant = MagicMock()
    tenant.id = uuid.uuid4()
    tenant.slug = "test-tenant"
    tenant.is_active = True
    tenant.name = "Test"
    return tenant


def _empty_session() -> MagicMock:
    """Mocked async SQLAlchemy session whose queries all return empty results."""
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            all=MagicMock(return_value=[]),
            scalar_one_or_none=MagicMock(return_value=None),
        )
    )
    session.scalar = AsyncMock(return_value=0)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.fixture
def mock_session_factory(monkeypatch):
    """Patch get_session to yield a session with empty results everywhere."""
    session = _empty_session()

    @asynccontextmanager
    async def fake_get_session():
        yield session

    # Patch the symbol everywhere routes import from
    monkeypatch.setattr(routes_module, "get_session", fake_get_session)
    return session


@pytest.fixture
def request_state_bypass():
    """Override require_api_key and stamp an admin-scope api_key on request.state.

    The file uses `from __future__ import annotations`, which turns every
    annotation into a string forward-ref. FastAPI's dependency analyzer tries
    to resolve `'Request'` at call time and — when that string isn't a name in
    the override's surrounding scope — falls back to treating `request` as a
    query parameter (hence the 422 "query.request missing" responses).
    We work around this by calling the helper defined at module scope below,
    whose annotation lives in a module with `Request` resolvable.
    """
    tenant = _fake_tenant()
    fake_api_key = MagicMock(scopes=["admin"], id=uuid.uuid4())
    _override = _make_auth_override(tenant, fake_api_key)
    app.dependency_overrides[require_api_key] = _override
    yield fake_api_key
    app.dependency_overrides.pop(require_api_key, None)


@pytest.fixture
def rate_limit_bypass(monkeypatch):
    """No-op the per-tenant rate limit check so tests don't need Redis."""

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(routes_module, "enforce_tenant_rate_limit", _noop)


@pytest.fixture
def telemetry_service_stub(monkeypatch):
    """Stub TelemetryService so /traces and /dashboard/stats return clean empties."""
    svc = MagicMock()
    svc.count_traces = AsyncMock(return_value=0)
    svc.list_traces = AsyncMock(return_value=[])
    svc.get_metrics = AsyncMock(
        return_value={
            "total_traces": 0,
            "total_spans": 0,
            "total_failures": 0,
            "total_tool_errors": 0,
            "avg_trace_duration_ms": 0.0,
            "total_tokens_consumed": 0,
        }
    )

    monkeypatch.setattr(routes_module, "TelemetryService", MagicMock(return_value=svc))
    return svc


def _client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ─── Basic reachability ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_v1_returns_200():
    async with _client() as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "version" in data


@pytest.mark.asyncio
async def test_health_bare_api_prefix():
    async with _client() as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_root_mounted():
    async with _client() as client:
        resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_openapi_schema_served():
    async with _client() as client:
        resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"] == "OtelMind"
    assert "/api/v1/health" in spec["paths"]
    assert "/api/v1/traces" in spec["paths"]
    assert "/api/v1/failures" in spec["paths"]


@pytest.mark.asyncio
async def test_missing_api_key_is_rejected():
    async with _client() as client:
        resp = await client.get("/api/v1/traces")
    assert resp.status_code in (401, 403, 422)


@pytest.mark.asyncio
async def test_invalid_path_returns_404():
    async with _client() as client:
        resp = await client.get("/api/v1/not-a-real-endpoint")
    assert resp.status_code == 404


# ─── Traces list with mocked DB ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_traces_list_empty_database(
    request_state_bypass, rate_limit_bypass, telemetry_service_stub, mock_session_factory
):
    async with _client() as client:
        resp = await client.get("/api/v1/traces", headers={"x-api-key": "test"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_traces_list_pagination_params(
    request_state_bypass, rate_limit_bypass, telemetry_service_stub, mock_session_factory
):
    async with _client() as client:
        resp = await client.get(
            "/api/v1/traces?limit=25&status=error", headers={"x-api-key": "test"}
        )
    assert resp.status_code == 200
    telemetry_service_stub.list_traces.assert_awaited_once()
    call_kwargs = telemetry_service_stub.list_traces.await_args.kwargs
    assert call_kwargs["limit"] == 25
    assert call_kwargs["ui_status"] == "error"


@pytest.mark.asyncio
async def test_traces_list_rejects_bad_limit(
    request_state_bypass, rate_limit_bypass, telemetry_service_stub, mock_session_factory
):
    async with _client() as client:
        resp = await client.get("/api/v1/traces?limit=9999", headers={"x-api-key": "test"})
    assert resp.status_code == 422  # FastAPI Query(le=500) violation


# ─── Failures list with mocked DB ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_failures_list_empty_database(
    request_state_bypass, rate_limit_bypass, mock_session_factory
):
    async with _client() as client:
        resp = await client.get("/api/v1/failures", headers={"x-api-key": "test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_failures_list_with_type_filter(
    request_state_bypass, rate_limit_bypass, mock_session_factory
):
    async with _client() as client:
        resp = await client.get(
            "/api/v1/failures?failure_type=hallucination", headers={"x-api-key": "test"}
        )
    assert resp.status_code == 200


# ─── Dashboard stats with mocked DB ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_dashboard_stats_shape(
    request_state_bypass, rate_limit_bypass, telemetry_service_stub, mock_session_factory
):
    async with _client() as client:
        resp = await client.get("/api/v1/dashboard/stats", headers={"x-api-key": "test"})
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "total_traces",
        "total_failures",
        "failure_rate",
        "avg_duration_ms",
        "total_cost_usd",
        "active_services",
        "failures_by_type",
        "traces_by_status",
    ):
        assert key in body
    # With empty metrics, failure_rate should be 0.0 (guard against divide-by-zero)
    assert body["failure_rate"] == 0.0
    assert body["total_traces"] == 0


@pytest.mark.asyncio
async def test_dashboard_stats_calls_metrics(
    request_state_bypass, rate_limit_bypass, telemetry_service_stub, mock_session_factory
):
    async with _client() as client:
        await client.get("/api/v1/dashboard/stats", headers={"x-api-key": "test"})
    telemetry_service_stub.get_metrics.assert_awaited_once()
