"""Integration tests for the judge calibration REST API."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from otelmind.api import calibration_routes as mod
from otelmind.api.auth import require_api_key
from otelmind.api.main import app
from otelmind.eval.calibration import CalibrationResult, DimensionCalibration
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


def _stub_calibration_result() -> CalibrationResult:
    return CalibrationResult(
        cohens_kappa=0.81,
        agreement_rate=0.88,
        confusion_matrix={(5, 5): 8, (4, 4): 6},
        per_dimension={
            "faithfulness": DimensionCalibration(
                cohens_kappa=0.85,
                agreement_rate=0.92,
                mean_absolute_error=0.12,
                bias=0.02,
                n=14,
            )
        },
        bias=0.04,
        calibration_curve=[{"bin": 5, "predicted": 0.95, "actual": 0.91, "n": 8}],
        case_count=14,
        judge_model="gpt-4o",
        raw_pairs=[],
    )


# ─── POST /api/calibrations/ ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_calibration_returns_summary(auth, stub_session):
    body = {
        "test_cases": [
            {"id": "c1", "question": "q1", "actual": "a1"},
        ],
        "human_labels": [
            {"case_id": "c1", "dimension": "faithfulness", "score": 1.0},
        ],
    }

    # The DB row is built then refreshed; fake the ORM-side fields the route reads back
    refreshed = MagicMock(
        id=42,
        judge_model="gpt-4o",
        cohens_kappa=0.81,
        agreement_rate=0.88,
        bias=0.04,
        per_dimension={
            "faithfulness": {
                "cohens_kappa": 0.85,
                "agreement_rate": 0.92,
                "mean_absolute_error": 0.12,
                "bias": 0.02,
                "n": 14,
            }
        },
        calibration_curve=[{"bin": 5, "predicted": 0.95, "actual": 0.91, "n": 8}],
        case_count=14,
        created_at=datetime.now(UTC),
    )
    stub_session.add = MagicMock(side_effect=lambda r: setattr(r, "id", 42))
    stub_session.refresh = AsyncMock(side_effect=lambda r: r.__dict__.update(refreshed.__dict__))

    with patch.object(mod, "calibrate_judge", AsyncMock(return_value=_stub_calibration_result())):
        async with _client() as client:
            resp = await client.post(
                "/api/calibrations/", json=body, headers={"x-api-key": "test"}
            )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["judge_model"] == "gpt-4o"
    assert data["cohens_kappa"] == pytest.approx(0.81)
    assert data["case_count"] == 14
    assert "per_dimension" in data
    assert "calibration_curve" in data


@pytest.mark.asyncio
async def test_create_calibration_rejects_empty_cases(auth, stub_session):
    body = {"test_cases": [], "human_labels": [{"case_id": "x", "dimension": "y", "score": 0.5}]}
    async with _client() as client:
        resp = await client.post("/api/calibrations/", json=body, headers={"x-api-key": "test"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_calibration_rejects_empty_labels(auth, stub_session):
    body = {"test_cases": [{"id": "c1", "question": "q", "actual": "a"}], "human_labels": []}
    async with _client() as client:
        resp = await client.post("/api/calibrations/", json=body, headers={"x-api-key": "test"})
    assert resp.status_code == 422


# ─── GET /api/calibrations/ ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_calibrations_empty(auth, stub_session):
    async with _client() as client:
        resp = await client.get("/api/calibrations/", headers={"x-api-key": "test"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": [], "total": 0}


# ─── GET /api/calibrations/{id} ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_calibration_404(auth, stub_session):
    async with _client() as client:
        resp = await client.get("/api/calibrations/9999", headers={"x-api-key": "test"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_endpoints_require_auth(stub_session):
    async with _client() as client:
        resp = await client.get("/api/calibrations/")
    assert resp.status_code in (401, 403, 422)
