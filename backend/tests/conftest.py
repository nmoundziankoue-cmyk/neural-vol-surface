"""DB-backed tests share the real local Postgres instance (see
docker-compose.yml) rather than a separate test database — pragmatic
for a single-developer local project. Isolation from real SPY data
comes from using a dedicated TEST_TICKER and cleaning up before/after
every test, not from a separate DB."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import Snapshot
from app.db.session import SessionLocal, init_db
from tests.factories import TEST_TICKER


@pytest.fixture(scope="session", autouse=True)
def _init_schema():
    init_db()


@pytest.fixture()
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def clean_test_snapshots(db_session):
    def _delete_all():
        rows = db_session.execute(select(Snapshot).where(Snapshot.ticker == TEST_TICKER)).scalars().all()
        for row in rows:
            db_session.delete(row)  # cascades to VolPoint via relationship
        db_session.commit()

    _delete_all()
    yield
    _delete_all()
