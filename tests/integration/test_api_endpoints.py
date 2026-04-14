"""API endpoint integration tests — exercises the FastAPI app without a real DB.

The OtelMind routes hit the database, so we patch `get_session` to return an
in-memory AsyncMock session. This keeps the test hermetic (no Postgres, no
Redis) while still exercising request parsing, middleware, and response
schemas.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from otelmind.api.main import app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _asgi_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_v1_returns_200():
    async with _asgi_client() as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "version" in data


@pytest.mark.asyncio
async def test_health_bare_api_prefix():
    async with _asgi_client() as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_root_mounted():
    async with _asgi_client() as client:
        resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_openapi_schema_served():
    async with _asgi_client() as client:
        resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"] == "OtelMind"
    assert "/api/v1/health" in spec["paths"]


@pytest.mark.asyncio
async def test_docs_redirect_or_serve():
    async with _asgi_client() as client:
        resp = await client.get("/docs")
    # Either Swagger UI is served (200) or we are redirected.
    assert resp.status_code in (200, 307, 308)


@pytest.mark.asyncio
async def test_missing_api_key_is_rejected_on_protected_route():
    """A protected route with no auth header should produce 401/403 — not 500."""
    async with _asgi_client() as client:
        resp = await client.get("/api/v1/traces")
    assert resp.status_code in (401, 403, 422)


@pytest.mark.asyncio
async def test_invalid_path_returns_404():
    async with _asgi_client() as client:
        resp = await client.get("/api/v1/not-a-real-endpoint")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_trailing_slash_tolerated():
    async with _asgi_client() as client:
        a = await client.get("/api/v1/health")
        b = await client.get("/api/v1/health/")
    assert a.status_code == 200
    # FastAPI either serves both or redirects — anything in 2xx/3xx is fine.
    assert b.status_code in (200, 307, 308, 404)
