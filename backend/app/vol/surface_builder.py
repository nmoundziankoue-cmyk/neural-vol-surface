"""Build a dense (log-moneyness x TTE) implied vol surface from the
scattered VolPoints of one snapshot, via separable PCHIP interpolation.

Coordinates (see docs/IV_AND_COORDINATES.md):
  * log-moneyness is SPOT moneyness k = ln(K / S), not forward ln(K / F)
    and not delta. The k = 0 column is therefore struck at spot, ~(r-q)T
    away from ATM-forward (< 1 grid cell at index tenors).
  * TTE is ACT/365 calendar time to expiry.

scipy has no scattered-data 2D PCHIP, so we use the standard two-pass
construction:
  1. Per-expiry smile: PCHIP over log-moneyness, using only that expiry's
     observed (call+put, OTM-stitched) points. Evaluated on a common
     log-moneyness grid.
  2. Per-moneyness term structure: PCHIP over TTE across the actual
     expiries, using the step-1 outputs as input. Evaluated on the final
     TTE grid.

Extrapolation: flat (clamp to the nearest observed boundary) in both
passes. PCHIP is only shape-preserving *between* knots; scipy's default
extrapolation just extends the boundary cubic piece, which is not
guaranteed to stay positive or bounded outside the fitted range - a
correctness risk for an IV surface. Flat extrapolation is the standard,
safe choice for vol surface tails. Every extrapolated cell (from either
pass) is flagged in `extrapolated_mask` rather than silently blended in,
so callers (dashboard, training set) can decide whether to trust it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.interpolate import PchipInterpolator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import VolPoint
from app.db.session import SessionLocal

MIN_POINTS_PER_SLICE = 2  # PchipInterpolator needs >= 2 points


@dataclass
class VolSurfaceGrid:
    snapshot_id: int
    tte_grid: np.ndarray            # shape (n_tte,) years
    log_moneyness_grid: np.ndarray  # shape (n_m,)
    iv_grid: np.ndarray             # shape (n_tte, n_m)
    extrapolated_mask: np.ndarray   # shape (n_tte, n_m), True = clamp-extrapolated cell
    n_raw_points: int
    n_expiries_used: int
    n_expiries_dropped: int


def _load_vol_points(session: Session, snapshot_id: int) -> list[VolPoint]:
    rows = session.execute(select(VolPoint).where(VolPoint.snapshot_id == snapshot_id)).scalars().all()
    if not rows:
        raise ValueError(f"No VolPoints found for snapshot_id={snapshot_id}")
    return list(rows)


def _dedupe_average(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """PCHIP requires strictly increasing x. Duplicates happen at
    log-moneyness=0, where both an ATM call and an ATM put pass the
    OTM-side filter (strike == spot satisfies both sides)."""
    order = np.argsort(x)
    x, y = x[order], y[order]
    unique_x, inverse = np.unique(x, return_inverse=True)
    if len(unique_x) == len(x):
        return x, y
    summed = np.zeros(len(unique_x))
    counts = np.zeros(len(unique_x))
    np.add.at(summed, inverse, y)
    np.add.at(counts, inverse, 1)
    return unique_x, summed / counts


def _clamped_pchip(x: np.ndarray, y: np.ndarray) -> Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]:
    """Returns evaluate(query) -> (values, extrapolated_flags)."""
    interpolator = PchipInterpolator(x, y, extrapolate=False)
    x_min, x_max = x[0], x[-1]

    def evaluate(query: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        clipped = np.clip(query, x_min, x_max)
        values = interpolator(clipped)
        extrapolated = (query < x_min) | (query > x_max)
        return values, extrapolated

    return evaluate


def build_vol_surface(
    snapshot_id: int,
    n_moneyness: int = 40,
    n_tte: int = 40,
    session: Session | None = None,
    log_moneyness_range: tuple[float, float] | None = None,
    tte_range: tuple[float, float] | None = None,
) -> VolSurfaceGrid:
    """log_moneyness_range / tte_range override the default per-snapshot
    data-derived bounds. Needed when comparing grids across snapshots
    (e.g. ML training pairs): each day's observed strike/expiry coverage
    differs, so leaving bounds auto-derived would make grid cell (i,j)
    refer to a different (moneyness, TTE) point on different days."""
    owns_session = session is None
    if owns_session:
        session = SessionLocal()
    try:
        rows = _load_vol_points(session, snapshot_id)
    finally:
        if owns_session:
            session.close()

    log_m_all = np.array([r.log_moneyness for r in rows])
    tte_all = np.array([r.tte for r in rows])
    iv_all = np.array([r.implied_vol for r in rows])
    expiry_all = np.array([r.expiry for r in rows])

    if log_moneyness_range is not None:
        log_moneyness_grid = np.linspace(log_moneyness_range[0], log_moneyness_range[1], n_moneyness)
    else:
        # Grid bounds come from the observed data for this snapshot, not hardcoded.
        log_moneyness_grid = np.linspace(log_m_all.min(), log_m_all.max(), n_moneyness)

    unique_expiries = sorted(set(expiry_all.tolist()), key=lambda e: tte_all[expiry_all == e][0])

    slice_tte: list[float] = []
    slice_values: list[np.ndarray] = []
    slice_extrap: list[np.ndarray] = []
    n_dropped = 0

    for expiry in unique_expiries:
        mask = expiry_all == expiry
        x, y = _dedupe_average(log_m_all[mask], iv_all[mask])
        if len(x) < MIN_POINTS_PER_SLICE:
            n_dropped += 1
            continue
        evaluate = _clamped_pchip(x, y)
        values, extrap = evaluate(log_moneyness_grid)
        slice_tte.append(float(tte_all[mask][0]))
        slice_values.append(values)
        slice_extrap.append(extrap)

    if len(slice_tte) < MIN_POINTS_PER_SLICE:
        raise ValueError(
            f"Only {len(slice_tte)} usable expiry slices (need >= {MIN_POINTS_PER_SLICE}) "
            f"to build a term structure for snapshot_id={snapshot_id}"
        )

    order = np.argsort(slice_tte)
    slice_tte_sorted = np.array(slice_tte)[order]
    slice_values_sorted = np.array(slice_values)[order]     # (n_expiries_used, n_moneyness)
    slice_extrap_sorted = np.array(slice_extrap)[order]     # (n_expiries_used, n_moneyness)

    if tte_range is not None:
        tte_grid = np.linspace(tte_range[0], tte_range[1], n_tte)
    else:
        tte_grid = np.linspace(slice_tte_sorted.min(), slice_tte_sorted.max(), n_tte)
    iv_grid = np.empty((n_tte, n_moneyness))
    extrapolated_mask = np.zeros((n_tte, n_moneyness), dtype=bool)

    for j in range(n_moneyness):
        evaluate = _clamped_pchip(slice_tte_sorted, slice_values_sorted[:, j])
        values, extrap_tte = evaluate(tte_grid)
        iv_grid[:, j] = values
        extrapolated_mask[:, j] = extrap_tte

    # Propagate pass-1 (moneyness-axis) extrapolation: a grid cell is only as
    # trustworthy as the expiry slices PCHIP drew on to build it. Approximate
    # via the two bracketing actual-expiry knots for each tte_grid point.
    for i, t in enumerate(tte_grid):
        idx = np.searchsorted(slice_tte_sorted, t)
        lo = max(idx - 1, 0)
        hi = min(idx, len(slice_tte_sorted) - 1)
        neighbor_extrap = slice_extrap_sorted[lo] | slice_extrap_sorted[hi]
        extrapolated_mask[i, :] |= neighbor_extrap

    return VolSurfaceGrid(
        snapshot_id=snapshot_id,
        tte_grid=tte_grid,
        log_moneyness_grid=log_moneyness_grid,
        iv_grid=iv_grid,
        extrapolated_mask=extrapolated_mask,
        n_raw_points=len(rows),
        n_expiries_used=len(slice_tte),
        n_expiries_dropped=n_dropped,
    )
