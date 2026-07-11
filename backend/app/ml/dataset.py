"""Builds (surface_day_J, surface_day_J+1) training pairs from consecutive
Postgres snapshots.

Each snapshot's own log-moneyness/TTE grid bounds are data-derived
(available strikes/expiries shift day to day), so simply flattening each
day's independently-built grid would misalign coordinates: cell (i, j)
would not refer to the same (moneyness, TTE) point across days. We fix
this by computing one common grid — the intersection of every loaded
snapshot's observed range — and rebuilding every day's surface on it via
build_vol_surface's log_moneyness_range/tte_range overrides.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Snapshot, VolPoint
from app.db.session import SessionLocal
from app.vol.surface_builder import build_vol_surface

MIN_SNAPSHOTS_REQUIRED = 2  # need >= 2 consecutive days to form one (J, J+1) pair


class InsufficientSnapshotsError(Exception):
    """Raised when there isn't enough data yet to build training pairs."""


@dataclass
class VolSurfaceDataset:
    X: torch.Tensor  # (n_pairs, n_moneyness * n_tte) — day J, flattened
    y: torch.Tensor  # (n_pairs, n_moneyness * n_tte) — day J+1, flattened
    extrapolated_mask: torch.Tensor  # (n_pairs, n_moneyness * n_tte) bool — day J+1's mask
    snapshot_id_pairs: list[tuple[int, int]]
    log_moneyness_grid: np.ndarray
    tte_grid: np.ndarray


def _snapshot_extent(session: Session, snapshot_id: int) -> tuple[float, float, float, float]:
    row = session.execute(
        select(
            func.min(VolPoint.log_moneyness), func.max(VolPoint.log_moneyness),
            func.min(VolPoint.tte), func.max(VolPoint.tte),
        ).where(VolPoint.snapshot_id == snapshot_id)
    ).one()
    if row[0] is None:
        raise InsufficientSnapshotsError(f"Snapshot {snapshot_id} has no VolPoints")
    return row


def load_training_pairs(
    ticker: str = "SPY",
    n_moneyness: int = 40,
    n_tte: int = 40,
    n_snapshots: int | None = None,
    session: Session | None = None,
) -> VolSurfaceDataset:
    """n_snapshots limits to the most recent N (chronological order
    preserved for pairing); None uses every available snapshot."""
    owns_session = session is None
    if owns_session:
        session = SessionLocal()
    try:
        snapshots = session.execute(
            select(Snapshot).where(Snapshot.ticker == ticker).order_by(Snapshot.captured_at.asc())
        ).scalars().all()

        if n_snapshots is not None and len(snapshots) > n_snapshots:
            snapshots = snapshots[-n_snapshots:]

        if len(snapshots) < MIN_SNAPSHOTS_REQUIRED:
            raise InsufficientSnapshotsError(
                f"Only {len(snapshots)} snapshot(s) available for {ticker}, need at least "
                f"{MIN_SNAPSHOTS_REQUIRED} to build a (day J, day J+1) training pair. "
                f"Let the daily cron accumulate more snapshots before training."
            )

        # Common grid = intersection of every snapshot's observed range, so no
        # snapshot needs to extrapolate beyond its own real data to fill it.
        extents = [_snapshot_extent(session, s.id) for s in snapshots]
        log_m_min = max(e[0] for e in extents)
        log_m_max = min(e[1] for e in extents)
        tte_min = max(e[2] for e in extents)
        tte_max = min(e[3] for e in extents)

        if log_m_min >= log_m_max or tte_min >= tte_max:
            raise InsufficientSnapshotsError(
                "Loaded snapshots have no overlapping (log-moneyness, TTE) range, so no "
                "common grid can be built. Check option chain coverage consistency day to day."
            )

        grids = [
            build_vol_surface(
                s.id,
                n_moneyness=n_moneyness,
                n_tte=n_tte,
                session=session,
                log_moneyness_range=(log_m_min, log_m_max),
                tte_range=(tte_min, tte_max),
            )
            for s in snapshots
        ]

        X_list = [g.iv_grid.flatten() for g in grids[:-1]]
        y_list = [g.iv_grid.flatten() for g in grids[1:]]
        # Mask is keyed on the *target* (day J+1) grid: it tells us whether
        # the ground truth we'd be scoring predictions against is real data
        # or a clamp-extrapolated fill-in.
        mask_list = [g.extrapolated_mask.flatten() for g in grids[1:]]
        id_pairs = [(snapshots[i].id, snapshots[i + 1].id) for i in range(len(snapshots) - 1)]

        return VolSurfaceDataset(
            X=torch.tensor(np.array(X_list), dtype=torch.float32),
            y=torch.tensor(np.array(y_list), dtype=torch.float32),
            extrapolated_mask=torch.tensor(np.array(mask_list), dtype=torch.bool),
            snapshot_id_pairs=id_pairs,
            log_moneyness_grid=grids[0].log_moneyness_grid,
            tte_grid=grids[0].tte_grid,
        )
    finally:
        if owns_session:
            session.close()
