"""Pydantic response models for the API. Kept separate from routes so the
controllers stay thin and the wire contract is readable in one place."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SnapshotSummary(BaseModel):
    id: int
    ticker: str
    captured_at: datetime
    spot: float


class SurfaceGrid(BaseModel):
    tte_grid: list[float]
    log_moneyness_grid: list[float]
    iv_grid: list[list[float]]           # shape (len(tte_grid), len(log_moneyness_grid))
    extrapolated_mask: list[list[bool]]  # same shape, True = clamp-extrapolated cell


class VolSurfaceResponse(BaseModel):
    snapshot_id: int
    ticker: str
    captured_at: datetime
    spot: float
    tte_grid: list[float]
    log_moneyness_grid: list[float]
    iv_grid: list[list[float]]
    extrapolated_mask: list[list[bool]]
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


class PairEvalRow(BaseModel):
    snapshot_id_t: int
    snapshot_id_t1: int
    date_t: str
    date_t1: str
    calendar_gap_days: int
    is_next_trading_day: bool
    n_cells_scored: int
    persistence_rmse: float
    model_rmse: float | None


class EvaluationResponse(BaseModel):
    available: bool
    ticker: str | None = None
    generated_on: str | None = None
    n_snapshots: int | None = None
    n_pairs: int | None = None
    grid: str | None = None
    pairs: list[PairEvalRow] = []
    persistence_rmse: float | None = None
    model_rmse: float | None = None
    relative_improvement: float | None = None
    model_beats_persistence: bool | None = None
    statistically_significant: bool = False
    caveats: list[str] = []
    verdict: str = ""


class PredictionResponse(BaseModel):
    target_date: str
    method: str                       # currently always "persistence"
    based_on_snapshot_id: int
    based_on_date: str
    predicted: SurfaceGrid
    realized_available: bool
    realized_snapshot_id: int | None = None
    realized: SurfaceGrid | None = None
    masked_rmse_vs_realized: float | None = None
    calendar_gap_days: int | None = None
    note: str = ""
