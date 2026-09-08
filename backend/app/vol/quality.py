"""Per-snapshot data-quality accounting.

Two halves:

* `RejectionBreakdown` - filled in at capture time by
  `scripts/daily_capture.build_vol_points`. Every raw contract from the
  option chain lands in exactly one bucket (kept, or one rejection
  reason); `accounted()` must equal `n_contracts_raw`. Nothing is
  dropped silently.

* `build_data_quality_report` - assembles the persisted
  `DataQualityReport` row. Coverage (strikes / maturities) and surface
  cell counts (observed vs clamp-extrapolated) are derived from the
  stored `VolPoint`s and a rebuilt surface, so they are available even
  for snapshots captured before this module existed (backfill); the
  raw-chain rejection counts are only available when a fresh
  `RejectionBreakdown` is passed in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import DataQualityReport, Snapshot, VolPoint
from app.vol.surface_builder import build_vol_surface


@dataclass
class RejectionBreakdown:
    """Capture-time contract accounting. Buckets are mutually exclusive and
    assigned first-match-wins in build_vol_points' filter order."""

    n_contracts_raw: int = 0
    n_kept: int = 0

    n_short_dte: int = 0            # expiring in < MIN_DTE days
    n_otm_side: int = 0            # ITM side of the OTM-only convention
    n_missing_price: int = 0       # bid or ask is NaN / absent
    n_non_positive_bid: int = 0    # bid <= 0 or ask <= 0
    n_crossed_market: int = 0      # ask < bid
    n_low_open_interest: int = 0   # openInterest < MIN_OPEN_INTEREST
    n_wide_spread: int = 0         # (ask-bid)/mid > MAX_RELATIVE_SPREAD

    n_inversion_attempted: int = 0  # reached brentq (passed every filter above)
    n_inversion_failed: int = 0     # brentq found no root in [iv_lower, iv_upper]

    @property
    def inversion_failure_rate(self) -> float | None:
        if self.n_inversion_attempted == 0:
            return None
        return self.n_inversion_failed / self.n_inversion_attempted

    def accounted(self) -> int:
        return (
            self.n_kept
            + self.n_short_dte
            + self.n_otm_side
            + self.n_missing_price
            + self.n_non_positive_bid
            + self.n_crossed_market
            + self.n_low_open_interest
            + self.n_wide_spread
            + self.n_inversion_failed
        )

    def as_dict(self) -> dict:
        return {
            "n_contracts_raw": self.n_contracts_raw,
            "n_kept": self.n_kept,
            "n_short_dte": self.n_short_dte,
            "n_otm_side": self.n_otm_side,
            "n_missing_price": self.n_missing_price,
            "n_non_positive_bid": self.n_non_positive_bid,
            "n_crossed_market": self.n_crossed_market,
            "n_low_open_interest": self.n_low_open_interest,
            "n_wide_spread": self.n_wide_spread,
            "n_inversion_attempted": self.n_inversion_attempted,
            "n_inversion_failed": self.n_inversion_failed,
            "inversion_failure_rate": self.inversion_failure_rate,
        }


def _coverage(session: Session, snapshot_id: int) -> dict:
    """Strike / maturity coverage from the stored VolPoints."""
    n_expiries, n_strikes, min_k, max_k, min_tte, max_tte = session.execute(
        select(
            func.count(func.distinct(VolPoint.expiry)),
            func.count(func.distinct(VolPoint.strike)),
            func.min(VolPoint.strike),
            func.max(VolPoint.strike),
            func.min(VolPoint.tte),
            func.max(VolPoint.tte),
        ).where(VolPoint.snapshot_id == snapshot_id)
    ).one()
    return {
        "n_expiries": int(n_expiries or 0),
        "n_strikes": int(n_strikes or 0),
        "min_strike": float(min_k) if min_k is not None else None,
        "max_strike": float(max_k) if max_k is not None else None,
        "min_tte": float(min_tte) if min_tte is not None else None,
        "max_tte": float(max_tte) if max_tte is not None else None,
        "min_dte": int(round(min_tte * 365)) if min_tte is not None else None,
        "max_dte": int(round(max_tte * 365)) if max_tte is not None else None,
    }


def _spread_stats(session: Session, snapshot_id: int) -> dict:
    """Relative bid-ask spread distribution over the KEPT points (a wide
    spread on a kept point still signals a low-confidence quote)."""
    rows = session.execute(
        select(VolPoint.bid, VolPoint.ask, VolPoint.mid).where(VolPoint.snapshot_id == snapshot_id)
    ).all()
    if not rows:
        return {"median_rel_spread": None, "p95_rel_spread": None, "max_rel_spread": None}
    rel = np.array([(a - b) / m for b, a, m in rows if m and m > 0])
    if rel.size == 0:
        return {"median_rel_spread": None, "p95_rel_spread": None, "max_rel_spread": None}
    return {
        "median_rel_spread": float(np.median(rel)),
        "p95_rel_spread": float(np.percentile(rel, 95)),
        "max_rel_spread": float(rel.max()),
    }


def _surface_cells(snapshot_id: int, session: Session) -> dict:
    """Observed vs clamp-extrapolated cell counts on the default grid."""
    try:
        grid = build_vol_surface(snapshot_id, session=session)
    except ValueError:
        return {"n_grid_cells": None, "n_cells_observed": None, "n_cells_extrapolated": None}
    n_cells = int(grid.extrapolated_mask.size)
    n_extrap = int(grid.extrapolated_mask.sum())
    return {
        "n_grid_cells": n_cells,
        "n_cells_observed": n_cells - n_extrap,
        "n_cells_extrapolated": n_extrap,
    }


def build_data_quality_report(
    session: Session,
    snapshot_id: int,
    *,
    rejections: RejectionBreakdown | None = None,
) -> DataQualityReport:
    """Compute (do not persist) the quality report for one snapshot.

    `rejections` carries the capture-time raw-chain accounting; omit it to
    build the backfill subset (coverage + surface cells + spread stats)
    for a snapshot whose raw chain is no longer available.
    """
    snapshot = session.get(Snapshot, snapshot_id)
    if snapshot is None:
        raise ValueError(f"Snapshot {snapshot_id} not found")

    n_kept = session.execute(
        select(func.count()).select_from(VolPoint).where(VolPoint.snapshot_id == snapshot_id)
    ).scalar_one()

    fields: dict = {
        "snapshot_id": snapshot_id,
        "spot": snapshot.spot,
        "n_kept": int(n_kept),
        "backfilled": rejections is None,
    }
    fields.update(_coverage(session, snapshot_id))
    fields.update(_spread_stats(session, snapshot_id))
    fields.update(_surface_cells(snapshot_id, session))

    if rejections is not None:
        rejections.n_kept = int(n_kept)
        fields.update(rejections.as_dict())

    return DataQualityReport(**fields)


def upsert_data_quality_report(session: Session, report: DataQualityReport) -> DataQualityReport:
    existing = session.execute(
        select(DataQualityReport).where(DataQualityReport.snapshot_id == report.snapshot_id)
    ).scalar_one_or_none()
    if existing is not None:
        for col in DataQualityReport.__table__.columns.keys():
            if col in ("id", "snapshot_id", "created_at"):
                continue
            setattr(existing, col, getattr(report, col))
        session.commit()
        return existing
    session.add(report)
    session.commit()
    return report
