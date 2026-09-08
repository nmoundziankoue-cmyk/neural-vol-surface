from datetime import datetime, timezone

import numpy as np
import pytest

from app.vol.surface_builder import build_vol_surface
from tests.factories import TEST_TICKER, make_snapshot, synthetic_points


def test_grid_shape_and_no_nan(db_session, clean_test_snapshots):
    snapshot = make_snapshot(db_session, TEST_TICKER, datetime.now(timezone.utc), points=synthetic_points())

    grid = build_vol_surface(snapshot.id, n_moneyness=15, n_tte=10, session=db_session)

    assert grid.iv_grid.shape == (10, 15)
    assert grid.extrapolated_mask.shape == (10, 15)
    assert not np.isnan(grid.iv_grid).any()
    assert grid.n_expiries_used == 3
    assert grid.n_expiries_dropped == 0


def test_iv_values_stay_in_a_sane_range(db_session, clean_test_snapshots):
    snapshot = make_snapshot(db_session, TEST_TICKER, datetime.now(timezone.utc), points=synthetic_points())

    grid = build_vol_surface(snapshot.id, session=db_session)

    # Synthetic IVs are all in [0.15, 0.30]; clamp extrapolation must not
    # produce values outside the observed range.
    assert grid.iv_grid.min() >= 0.15 - 1e-6
    assert grid.iv_grid.max() <= 0.30 + 1e-6


def test_explicit_range_overrides_are_respected(db_session, clean_test_snapshots):
    snapshot = make_snapshot(db_session, TEST_TICKER, datetime.now(timezone.utc), points=synthetic_points())

    grid = build_vol_surface(
        snapshot.id, n_moneyness=10, n_tte=8, session=db_session,
        log_moneyness_range=(-0.5, 0.3), tte_range=(0.1, 0.3),
    )

    assert grid.log_moneyness_grid[0] == pytest.approx(-0.5)
    assert grid.log_moneyness_grid[-1] == pytest.approx(0.3)
    assert grid.tte_grid[0] == pytest.approx(0.1)
    assert grid.tte_grid[-1] == pytest.approx(0.3)


def test_missing_snapshot_raises_value_error(db_session):
    with pytest.raises(ValueError):
        build_vol_surface(snapshot_id=-999, session=db_session)


# --- PCHIP interpolation behaviour ---

def test_pchip_reproduces_observed_iv_at_a_knot(db_session, clean_test_snapshots):
    """A grid column placed exactly on an observed log-moneyness must
    return that expiry's observed IV there - interpolation, not smoothing."""
    snapshot = make_snapshot(db_session, TEST_TICKER, datetime.now(timezone.utc), points=synthetic_points())

    # synthetic_points has a quote at strike 100, spot 100 -> log-moneyness 0.0,
    # IV 0.18, present in every expiry. Force a grid line through k = 0 and the
    # shortest expiry's TTE (0.10).
    grid = build_vol_surface(
        snapshot.id, n_moneyness=9, n_tte=5, session=db_session,
        log_moneyness_range=(-0.4, 0.4), tte_range=(0.10, 0.45),
    )
    j0 = int(np.argmin(np.abs(grid.log_moneyness_grid - 0.0)))
    i0 = int(np.argmin(np.abs(grid.tte_grid - 0.10)))
    assert grid.log_moneyness_grid[j0] == pytest.approx(0.0)
    assert grid.tte_grid[i0] == pytest.approx(0.10)
    assert not grid.extrapolated_mask[i0, j0]
    assert grid.iv_grid[i0, j0] == pytest.approx(0.18, abs=1e-6)


def test_clamp_extrapolation_is_flat_and_masked(db_session, clean_test_snapshots):
    snapshot = make_snapshot(db_session, TEST_TICKER, datetime.now(timezone.utc), points=synthetic_points())

    # observed log-moneyness spans ln(80/100)..ln(120/100) = -0.223..0.182;
    # ask for a wider grid so the ends must be clamp-extrapolated.
    grid = build_vol_surface(
        snapshot.id, n_moneyness=21, n_tte=6, session=db_session,
        log_moneyness_range=(-0.6, 0.6), tte_range=(0.10, 0.45),
    )
    row = grid.iv_grid[0]
    mask_row = grid.extrapolated_mask[0]
    assert mask_row[0] and mask_row[-1]                       # ends flagged
    assert not mask_row[len(mask_row) // 2]                   # middle not flagged

    # flat clamp: every extrapolated cell on a side holds one constant value
    mid = len(mask_row) // 2
    left = np.where(mask_row[:mid])[0]
    right = np.where(mask_row[mid:])[0] + mid
    assert np.allclose(row[left], row[left[0]])
    assert np.allclose(row[right], row[right[0]])
    # and the clamp never invents values outside the observed IV band [0.15, 0.30]
    assert 0.15 - 1e-9 <= row.min() and row.max() <= 0.30 + 1e-9
