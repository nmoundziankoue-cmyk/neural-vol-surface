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
