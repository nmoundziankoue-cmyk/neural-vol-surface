from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DataQualityReport, Snapshot
from app.db.session import SessionLocal
from app.vol.surface_builder import build_vol_surface

router = APIRouter(prefix="/api")


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


class SnapshotSummary(BaseModel):
    id: int
    ticker: str
    captured_at: datetime
    spot: float


class VolSurfaceResponse(BaseModel):
    snapshot_id: int
    ticker: str
    captured_at: datetime
    spot: float
    tte_grid: list[float]
    log_moneyness_grid: list[float]
    iv_grid: list[list[float]]           # shape (len(tte_grid), len(log_moneyness_grid))
    extrapolated_mask: list[list[bool]]  # same shape, True = clamp-extrapolated cell
    n_raw_points: int
    n_expiries_used: int
    n_expiries_dropped: int


class DataQualityRow(BaseModel):
    snapshot_id: int
    captured_at: datetime
    ticker: str
    spot: float
    backfilled: bool  # True = raw-chain rejection counts unavailable (pre-instrumentation snapshot)

    # capture-time raw-chain accounting (null on backfilled rows)
    n_contracts_raw: int | None
    n_kept: int
    n_short_dte: int | None
    n_otm_side: int | None
    n_missing_price: int | None
    n_non_positive_bid: int | None
    n_crossed_market: int | None
    n_low_open_interest: int | None
    n_wide_spread: int | None
    n_inversion_attempted: int | None
    n_inversion_failed: int | None
    inversion_failure_rate: float | None

    # coverage
    n_expiries: int
    n_strikes: int
    min_strike: float | None
    max_strike: float | None
    min_dte: int | None
    max_dte: int | None

    # bid-ask spread distribution over kept points
    median_rel_spread: float | None
    p95_rel_spread: float | None
    max_rel_spread: float | None

    # reconstructed-surface cell accounting
    n_grid_cells: int | None
    n_cells_observed: int | None
    n_cells_extrapolated: int | None


@router.get("/snapshots", response_model=list[SnapshotSummary])
def list_snapshots(session: Session = Depends(get_session)) -> list[SnapshotSummary]:
    rows = session.execute(select(Snapshot).order_by(Snapshot.captured_at.desc())).scalars().all()
    return [SnapshotSummary(id=r.id, ticker=r.ticker, captured_at=r.captured_at, spot=r.spot) for r in rows]


@router.get("/data-quality", response_model=list[DataQualityRow])
def list_data_quality(session: Session = Depends(get_session)) -> list[DataQualityRow]:
    """One row per snapshot: how much of the raw chain became IV
    observations, why the rest was dropped, and how much of the
    reconstructed surface is real vs clamp-extrapolated. Newest first."""
    rows = session.execute(
        select(DataQualityReport, Snapshot)
        .join(Snapshot, DataQualityReport.snapshot_id == Snapshot.id)
        .order_by(Snapshot.captured_at.desc())
    ).all()
    return [
        DataQualityRow(
            snapshot_id=r.snapshot_id,
            captured_at=s.captured_at,
            ticker=s.ticker,
            spot=r.spot,
            backfilled=r.backfilled,
            n_contracts_raw=r.n_contracts_raw,
            n_kept=r.n_kept,
            n_short_dte=r.n_short_dte,
            n_otm_side=r.n_otm_side,
            n_missing_price=r.n_missing_price,
            n_non_positive_bid=r.n_non_positive_bid,
            n_crossed_market=r.n_crossed_market,
            n_low_open_interest=r.n_low_open_interest,
            n_wide_spread=r.n_wide_spread,
            n_inversion_attempted=r.n_inversion_attempted,
            n_inversion_failed=r.n_inversion_failed,
            inversion_failure_rate=r.inversion_failure_rate,
            n_expiries=r.n_expiries,
            n_strikes=r.n_strikes,
            min_strike=r.min_strike,
            max_strike=r.max_strike,
            min_dte=r.min_dte,
            max_dte=r.max_dte,
            median_rel_spread=r.median_rel_spread,
            p95_rel_spread=r.p95_rel_spread,
            max_rel_spread=r.max_rel_spread,
            n_grid_cells=r.n_grid_cells,
            n_cells_observed=r.n_cells_observed,
            n_cells_extrapolated=r.n_cells_extrapolated,
        )
        for r, s in rows
    ]


@router.get("/surface/{snapshot_id}", response_model=VolSurfaceResponse)
def get_surface(snapshot_id: int, session: Session = Depends(get_session)) -> VolSurfaceResponse:
    snapshot = session.get(Snapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id} not found")

    try:
        grid = build_vol_surface(snapshot_id, session=session)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

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
