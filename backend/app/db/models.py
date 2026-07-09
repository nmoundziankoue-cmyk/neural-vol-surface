from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
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
