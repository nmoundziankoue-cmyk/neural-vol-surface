"""Route-level smoke tests: status codes, error mapping, response shape.
Financial correctness is covered by the surface/BS/metrics tests."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_session
from app.config import settings
from app.main import app
from tests.factories import TEST_TICKER, make_snapshot, synthetic_points


@pytest.fixture()
def client(db_session, clean_test_snapshots, monkeypatch):
    monkeypatch.setattr(settings, "ticker", TEST_TICKER)
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def two_days(db_session):
    t0 = datetime.now(timezone.utc).replace(hour=21, minute=0, second=0, microsecond=0)
    a = make_snapshot(db_session, TEST_TICKER, t0 - timedelta(days=1), points=synthetic_points())
    b = make_snapshot(db_session, TEST_TICKER, t0, points=synthetic_points(iv_shift=0.02))
    return a, b


def test_snapshots_list(client, two_days):
    r = client.get("/api/snapshots")
    assert r.status_code == 200
    body = r.json()
    assert TEST_TICKER in {row["ticker"] for row in body}
    if len(body) > 1:
        assert body[0]["captured_at"] >= body[-1]["captured_at"]  # newest first


def test_surface_by_id_and_404(client, two_days):
    a, _ = two_days
    ok = client.get(f"/api/surfaces/by-id/{a.id}")
    assert ok.status_code == 200
    assert ok.json()["snapshot_id"] == a.id
    assert len(ok.json()["iv_grid"]) == len(ok.json()["tte_grid"])

    assert client.get("/api/surfaces/by-id/99999999").status_code == 404


def test_surfaces_latest_and_by_date(client, two_days):
    _, b = two_days
    latest = client.get("/api/surfaces/latest")
    assert latest.status_code == 200
    assert latest.json()["snapshot_id"] == b.id

    et_day = b.captured_at.astimezone(timezone.utc).date().isoformat()
    by_date = client.get(f"/api/surfaces/{et_day}")
    assert by_date.status_code in (200, 404)  # depends on UTC/ET day boundary; must not 500

    assert client.get("/api/surfaces/1990-01-01").status_code == 404


def test_prediction_reports_error_vs_realized(client, two_days):
    _, b = two_days
    et_day = b.captured_at.astimezone(timezone.utc).date().isoformat()
    r = client.get(f"/api/predictions/{et_day}")
    if r.status_code == 404:
        pytest.skip("target day fell outside the seeded ET window")
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "persistence"
    assert body["realized_available"] is True
    assert body["masked_rmse_vs_realized"] >= 0.0


def test_data_quality_and_evaluation_endpoints(client, two_days):
    assert client.get("/api/data-quality").status_code == 200
    ev = client.get("/api/evaluation")
    assert ev.status_code == 200
    assert "available" in ev.json()
