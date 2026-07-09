"""Synthetic data builders for tests. Uses a dedicated ticker so tests
never touch real SPY snapshots accumulated by the daily cron."""

from __future__ import annotations

from datetime import datetime

import numpy as np
from sqlalchemy.orm import Session

from app.db.models import Snapshot, VolPoint

TEST_TICKER = "TEST_XYZ"


def synthetic_points(spot: float = 100.0) -> list[dict]:
    """3 expiries x 5 OTM strikes each (>= MIN_POINTS_PER_SLICE=2 per
    expiry), spanning both sides of spot so the surface has an actual
    put/call skew to interpolate."""
    expiries = [("2026-08-01", 0.10), ("2026-09-01", 0.20), ("2026-12-01", 0.45)]
    quotes = [
        (80, "put", 0.30), (90, "put", 0.22), (100, "call", 0.18),
        (110, "call", 0.16), (120, "call", 0.15),
    ]
    points = []
    for expiry, tte in expiries:
        for strike, option_type, iv in quotes:
            points.append(
                dict(
                    expiry=expiry, tte=tte, strike=float(strike), option_type=option_type,
                    bid=1.0, ask=1.1, mid=1.05, volume=100, open_interest=100,
                    log_moneyness=float(np.log(strike / spot)), implied_vol=iv,
                )
            )
    return points


def make_snapshot(
    session: Session,
    ticker: str,
    captured_at: datetime,
    spot: float = 100.0,
    points: list[dict] | None = None,
) -> Snapshot:
    snapshot = Snapshot(
        ticker=ticker, captured_at=captured_at, spot=spot,
        risk_free_rate=0.03, dividend_yield=0.01,
    )
    session.add(snapshot)
    session.flush()
    for p in points or []:
        session.add(VolPoint(snapshot_id=snapshot.id, **p))
    session.commit()
    return snapshot
