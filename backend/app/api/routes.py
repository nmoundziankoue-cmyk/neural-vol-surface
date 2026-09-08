from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import services
from app.api.schemas import (
    DataQualityRow,
    EvaluationResponse,
    PredictionResponse,
    SnapshotSummary,
    VolSurfaceResponse,
)
from app.api.services import SnapshotNotFound
from app.config import settings
from app.db.models import DataQualityReport, Snapshot
from app.db.session import SessionLocal
from app.ml.evaluate import read_evaluation_report

router = APIRouter(prefix="/api")


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------
@router.get("/snapshots", response_model=list[SnapshotSummary])
def list_snapshots(session: Session = Depends(get_session)) -> list[SnapshotSummary]:
    rows = session.execute(select(Snapshot).order_by(Snapshot.captured_at.desc())).scalars().all()
    return [SnapshotSummary(id=r.id, ticker=r.ticker, captured_at=r.captured_at, spot=r.spot) for r in rows]


# --------------------------------------------------------------------------
# surfaces
# --------------------------------------------------------------------------
@router.get("/surfaces/latest", response_model=VolSurfaceResponse)
def latest_surface(session: Session = Depends(get_session)) -> VolSurfaceResponse:
    try:
        snap = services.latest_snapshot(session, settings.ticker)
        return services.surface_response(session, snap)
    except SnapshotNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/surfaces/by-id/{snapshot_id}", response_model=VolSurfaceResponse)
def surface_by_id(snapshot_id: int, session: Session = Depends(get_session)) -> VolSurfaceResponse:
    snap = session.get(Snapshot, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id} not found")
    try:
        return services.surface_response(session, snap)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/surfaces/{trading_date}", response_model=VolSurfaceResponse)
def surface_on_date(trading_date: date, session: Session = Depends(get_session)) -> VolSurfaceResponse:
    """Surface for the snapshot whose ET trading date is `trading_date` (YYYY-MM-DD)."""
    try:
        snap = services.snapshot_on_date(session, settings.ticker, trading_date)
        return services.surface_response(session, snap)
    except SnapshotNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# --------------------------------------------------------------------------
# predictions (persistence baseline only)
# --------------------------------------------------------------------------
@router.get("/predictions/latest", response_model=PredictionResponse)
def latest_prediction(session: Session = Depends(get_session)) -> PredictionResponse:
    try:
        latest = services.latest_snapshot(session, settings.ticker)
        return PredictionResponse(
            **services.persistence_prediction(session, settings.ticker, services.et_date(latest))
        )
    except SnapshotNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/predictions/{trading_date}", response_model=PredictionResponse)
def prediction_for_date(trading_date: date, session: Session = Depends(get_session)) -> PredictionResponse:
    """Persistence forecast for `trading_date`: the surface carried forward
    from the most recent snapshot before it. If the realized surface for
    that date exists, the masked RMSE of the forecast against it is
    included."""
    try:
        return PredictionResponse(
            **services.persistence_prediction(session, settings.ticker, trading_date)
        )
    except SnapshotNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# --------------------------------------------------------------------------
# data quality
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# model vs persistence evaluation
# --------------------------------------------------------------------------
@router.get("/evaluation", response_model=EvaluationResponse)
def get_evaluation() -> EvaluationResponse:
    """Honest persistence-baseline comparison. The only baseline is
    IV_hat(t+1) = IV(t); the model number is leave-one-pair-out
    cross-validated. `statistically_significant` is a hard gate that is
    currently always False - `caveats` and `verdict` say why. Recomputed
    by scripts/evaluate.py (also at the end of each daily capture)."""
    data = read_evaluation_report(settings.ticker)
    if data is None:
        return EvaluationResponse(
            available=False,
            verdict="No evaluation has been computed yet (run scripts/evaluate.py).",
        )
    return EvaluationResponse(available=True, **data)
