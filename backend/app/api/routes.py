from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Snapshot
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


@router.get("/snapshots", response_model=list[SnapshotSummary])
def list_snapshots(session: Session = Depends(get_session)) -> list[SnapshotSummary]:
    rows = session.execute(select(Snapshot).order_by(Snapshot.captured_at.desc())).scalars().all()
    return [SnapshotSummary(id=r.id, ticker=r.ticker, captured_at=r.captured_at, spot=r.spot) for r in rows]


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
