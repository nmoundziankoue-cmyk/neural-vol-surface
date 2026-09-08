from datetime import datetime, timezone

import pytest

from app.vol.quality import RejectionBreakdown, build_data_quality_report
from tests.factories import TEST_TICKER, make_snapshot, synthetic_points


def test_rejection_breakdown_accounting_invariant():
    rej = RejectionBreakdown(n_contracts_raw=100)
    rej.n_otm_side = 40
    rej.n_short_dte = 10
    rej.n_missing_price = 5
    rej.n_wide_spread = 3
    rej.n_inversion_attempted = 42
    rej.n_inversion_failed = 2
    rej.n_kept = 40

    # every raw contract lands in exactly one bucket
    assert rej.accounted() == 100
    assert rej.inversion_failure_rate == pytest.approx(2 / 42)


def test_inversion_failure_rate_none_when_nothing_attempted():
    assert RejectionBreakdown(n_contracts_raw=0).inversion_failure_rate is None


def test_build_report_backfill_from_stored_points(db_session, clean_test_snapshots):
    snap = make_snapshot(db_session, TEST_TICKER, datetime.now(timezone.utc), points=synthetic_points())

    report = build_data_quality_report(db_session, snap.id, rejections=None)

    assert report.backfilled is True
    assert report.n_contracts_raw is None          # raw chain unavailable on backfill
    assert report.n_kept == len(synthetic_points())
    assert report.n_expiries == 3
    assert report.n_strikes == 5
    assert report.min_strike == 80.0 and report.max_strike == 120.0
    # default 40x40 grid, every cell classified as observed or extrapolated
    assert report.n_grid_cells == 1600
    assert report.n_cells_observed + report.n_cells_extrapolated == 1600
    # default grid bounds are data-derived, so with no range override the
    # grid spans exactly the observed range and nothing is clamp-extrapolated
    assert report.n_cells_observed == 1600
    assert report.n_cells_extrapolated == 0


def test_build_report_with_rejections_is_not_flagged_backfilled(db_session, clean_test_snapshots):
    snap = make_snapshot(db_session, TEST_TICKER, datetime.now(timezone.utc), points=synthetic_points())
    rej = RejectionBreakdown(n_contracts_raw=200, n_otm_side=185, n_inversion_attempted=15)

    report = build_data_quality_report(db_session, snap.id, rejections=rej)

    assert report.backfilled is False
    assert report.n_contracts_raw == 200
    assert report.n_kept == len(synthetic_points())  # taken from stored points, not the stale rej.n_kept
