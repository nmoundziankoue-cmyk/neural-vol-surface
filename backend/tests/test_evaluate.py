from datetime import datetime, timedelta, timezone

from app.ml.evaluate import build_evaluation_report
from tests.factories import TEST_TICKER, make_snapshot, synthetic_points

# small grid -> tiny net -> fast LOPO refit in the test
GRID = dict(n_moneyness=8, n_tte=6)


def test_report_says_insufficient_with_one_snapshot(db_session, clean_test_snapshots):
    make_snapshot(db_session, TEST_TICKER, datetime.now(timezone.utc), points=synthetic_points())

    report = build_evaluation_report(ticker=TEST_TICKER, session=db_session, **GRID)

    assert report.n_pairs == 0
    assert report.persistence_rmse is None
    assert report.model_rmse is None
    assert report.statistically_significant is False
    assert "pair" in report.verdict.lower()


def test_report_compares_but_never_claims_significance(db_session, clean_test_snapshots):
    t0 = datetime.now(timezone.utc)
    for i in range(3):
        make_snapshot(
            db_session, TEST_TICKER, t0 + timedelta(days=i),
            points=synthetic_points(iv_shift=0.01 * i),  # surface moves day to day
        )

    report = build_evaluation_report(ticker=TEST_TICKER, session=db_session, **GRID)

    assert report.n_pairs == 2
    assert report.persistence_rmse is not None
    assert report.model_rmse is not None
    assert report.relative_improvement is not None
    assert report.model_beats_persistence == (report.model_rmse < report.persistence_rmse)
    # 2 consecutive-day pairs is nowhere near the significance gate
    assert report.statistically_significant is False
    assert any("pair(s)" in c for c in report.caveats)
    # consecutive days -> no multi-day-gap caveat
    assert all(p.is_next_trading_day for p in report.pairs)


def test_multi_day_gap_is_flagged(db_session, clean_test_snapshots):
    t0 = datetime.now(timezone.utc)
    make_snapshot(db_session, TEST_TICKER, t0, points=synthetic_points())
    make_snapshot(db_session, TEST_TICKER, t0 + timedelta(days=30), points=synthetic_points(iv_shift=0.02))

    report = build_evaluation_report(ticker=TEST_TICKER, session=db_session, **GRID)

    assert report.n_pairs == 1
    assert report.pairs[0].calendar_gap_days == 30
    assert report.pairs[0].is_next_trading_day is False
    assert any("multi-day gap" in c for c in report.caveats)
