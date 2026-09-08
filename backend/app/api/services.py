"""Assembly logic between the DB / surface builder and the API schemas.

Controllers call these; no interpolation or metric maths lives in
routes.py. Raises ValueError / LookupError, which the router maps to HTTP
status codes.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Snapshot
from app.ml.metrics import masked_rmse
from app.vol.surface_builder import build_vol_surface
from app.api.schemas import SurfaceGrid, VolSurfaceResponse

MARKET_TZ = ZoneInfo("America/New_York")


class SnapshotNotFound(LookupError):
    pass


def et_date(snapshot: Snapshot) -> date:
    return snapshot.captured_at.astimezone(MARKET_TZ).date()


def latest_snapshot(session: Session, ticker: str) -> Snapshot:
    snap = session.execute(
        select(Snapshot).where(Snapshot.ticker == ticker).order_by(Snapshot.captured_at.desc()).limit(1)
    ).scalar_one_or_none()
    if snap is None:
        raise SnapshotNotFound(f"No snapshots for {ticker}")
    return snap


def snapshot_on_date(session: Session, ticker: str, on: date) -> Snapshot:
    """The snapshot whose ET trading date is `on`. Compared in Python
    (not SQL) because captured_at is stored UTC and the trading day is ET."""
    day_snaps = session.execute(
        select(Snapshot).where(Snapshot.ticker == ticker).order_by(Snapshot.captured_at)
    ).scalars().all()
    for s in day_snaps:
        if et_date(s) == on:
            return s
    raise SnapshotNotFound(f"No {ticker} snapshot for trading date {on.isoformat()}")


def snapshot_before_date(session: Session, ticker: str, before: date) -> Snapshot:
    day_snaps = session.execute(
        select(Snapshot).where(Snapshot.ticker == ticker).order_by(Snapshot.captured_at.desc())
    ).scalars().all()
    for s in day_snaps:
        if et_date(s) < before:
            return s
    raise SnapshotNotFound(f"No {ticker} snapshot before trading date {before.isoformat()}")


def surface_response(session: Session, snapshot: Snapshot) -> VolSurfaceResponse:
    grid = build_vol_surface(snapshot.id, session=session)  # raises ValueError on degenerate input
    return VolSurfaceResponse(
        snapshot_id=grid.snapshot_id,
        ticker=snapshot.ticker,
        captured_at=snapshot.captured_at,
        spot=snapshot.spot,
        tte_grid=grid.tte_grid.tolist(),
        log_moneyness_grid=grid.log_moneyness_grid.tolist(),
        iv_grid=grid.iv_grid.tolist(),
        extrapolated_mask=grid.extrapolated_mask.tolist(),
        n_raw_points=grid.n_raw_points,
        n_expiries_used=grid.n_expiries_used,
        n_expiries_dropped=grid.n_expiries_dropped,
    )


def _extent(session: Session, snapshot_id: int) -> tuple[float, float, float, float]:
    from app.db.models import VolPoint

    row = session.execute(
        select(
            func.min(VolPoint.log_moneyness), func.max(VolPoint.log_moneyness),
            func.min(VolPoint.tte), func.max(VolPoint.tte),
        ).where(VolPoint.snapshot_id == snapshot_id)
    ).one()
    if row[0] is None:
        raise ValueError(f"Snapshot {snapshot_id} has no VolPoints")
    return row


def _as_grid(g) -> SurfaceGrid:
    return SurfaceGrid(
        tte_grid=g.tte_grid.tolist(),
        log_moneyness_grid=g.log_moneyness_grid.tolist(),
        iv_grid=g.iv_grid.tolist(),
        extrapolated_mask=g.extrapolated_mask.tolist(),
    )


def persistence_prediction(
    session: Session,
    ticker: str,
    target: date,
    n_moneyness: int = 40,
    n_tte: int = 40,
) -> dict:
    """Persistence forecast for `target`: the surface carried forward from
    the most recent snapshot strictly before `target`. If a snapshot for
    `target` also exists, both are rebuilt on their common grid and the
    masked RMSE of the forecast against the realized surface is returned.
    """
    prior = snapshot_before_date(session, ticker, target)

    try:
        realized = snapshot_on_date(session, ticker, target)
    except SnapshotNotFound:
        realized = None

    result: dict = {
        "target_date": target.isoformat(),
        "method": "persistence",
        "based_on_snapshot_id": prior.id,
        "based_on_date": et_date(prior).isoformat(),
        "realized_available": realized is not None,
    }

    if realized is None:
        pred_grid = build_vol_surface(prior.id, session=session, n_moneyness=n_moneyness, n_tte=n_tte)
        result["predicted"] = _as_grid(pred_grid)
        result["note"] = (
            "Realized surface for the target date is not in the database yet, "
            "so no error can be reported - this is the forecast only."
        )
        return result

    # common grid = intersection of both snapshots' observed ranges
    e_prior, e_real = _extent(session, prior.id), _extent(session, realized.id)
    lm = (max(e_prior[0], e_real[0]), min(e_prior[1], e_real[1]))
    tt = (max(e_prior[2], e_real[2]), min(e_prior[3], e_real[3]))
    if lm[0] >= lm[1] or tt[0] >= tt[1]:
        raise ValueError("prior and realized snapshots have no overlapping (log-moneyness, TTE) range")

    pred_grid = build_vol_surface(
        prior.id, session=session, n_moneyness=n_moneyness, n_tte=n_tte,
        log_moneyness_range=lm, tte_range=tt,
    )
    real_grid = build_vol_surface(
        realized.id, session=session, n_moneyness=n_moneyness, n_tte=n_tte,
        log_moneyness_range=lm, tte_range=tt,
    )

    import torch

    mask = pred_grid.extrapolated_mask | real_grid.extrapolated_mask
    rmse = masked_rmse(
        torch.tensor(pred_grid.iv_grid, dtype=torch.float32),
        torch.tensor(real_grid.iv_grid, dtype=torch.float32),
        torch.tensor(mask, dtype=torch.bool),
    )
    gap = abs((et_date(realized) - et_date(prior)).days)

    result["predicted"] = _as_grid(pred_grid)
    result["realized_snapshot_id"] = realized.id
    result["realized"] = _as_grid(real_grid)
    result["masked_rmse_vs_realized"] = rmse
    result["calendar_gap_days"] = gap
    result["note"] = (
        "Persistence carried the surface forward "
        f"{gap} calendar day(s); RMSE is over non-extrapolated cells of the common grid."
        + ("" if gap <= 4 else " NOTE: multi-day gap - not a single-session forecast.")
    )
    return result
