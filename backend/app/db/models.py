from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Snapshot(Base):
    """One daily capture run: market context at the time of the pull."""

    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("ticker", "captured_at", name="uq_snapshot_ticker_ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    spot: Mapped[float] = mapped_column(Float)
    risk_free_rate: Mapped[float] = mapped_column(Float)
    dividend_yield: Mapped[float] = mapped_column(Float)

    points: Mapped[list["VolPoint"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")


class VolPoint(Base):
    """A single (strike, expiry) IV observation belonging to a snapshot,
    after liquidity filtering and BS inversion. This is the raw material
    for building the log-moneyness x TTE grid downstream."""

    __tablename__ = "vol_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"), index=True)

    expiry: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    tte: Mapped[float] = mapped_column(Float)  # years, calendar/365
    strike: Mapped[float] = mapped_column(Float)
    option_type: Mapped[str] = mapped_column(String(4))  # 'call' | 'put'

    bid: Mapped[float] = mapped_column(Float)
    ask: Mapped[float] = mapped_column(Float)
    mid: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    open_interest: Mapped[float] = mapped_column(Float)

    log_moneyness: Mapped[float] = mapped_column(Float)  # log(K/S)
    implied_vol: Mapped[float] = mapped_column(Float)

    snapshot: Mapped[Snapshot] = relationship(back_populates="points")


class DataQualityReport(Base):
    """One row per Snapshot: how much of the raw option chain survived to
    become IV observations, why the rest was dropped, and how much of the
    reconstructed surface is real vs clamp-extrapolated.

    Capture-time columns (n_contracts_raw and every rejection count) are
    nullable: they are only known when the report is built from the live
    raw chain. Coverage, spread and surface-cell columns are derived from
    the stored VolPoints and can be backfilled for older snapshots -
    `backfilled=True` marks those.
    """

    __tablename__ = "data_quality_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    backfilled: Mapped[bool] = mapped_column(Boolean, default=False)

    spot: Mapped[float] = mapped_column(Float)

    # --- capture-time raw-chain accounting (nullable: unknown on backfill) ---
    n_contracts_raw: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_kept: Mapped[int] = mapped_column(Integer)
    n_short_dte: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_otm_side: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_missing_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_non_positive_bid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_crossed_market: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_low_open_interest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_wide_spread: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_inversion_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_inversion_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inversion_failure_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- derived from stored VolPoints (always available) ---
    n_expiries: Mapped[int] = mapped_column(Integer)
    n_strikes: Mapped[int] = mapped_column(Integer)
    min_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_tte: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_tte: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_dte: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_dte: Mapped[int | None] = mapped_column(Integer, nullable=True)

    median_rel_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95_rel_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_rel_spread: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- reconstructed-surface cell accounting ---
    n_grid_cells: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_cells_observed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_cells_extrapolated: Mapped[int | None] = mapped_column(Integer, nullable=True)

    snapshot: Mapped[Snapshot] = relationship()
