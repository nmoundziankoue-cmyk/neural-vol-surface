from datetime import datetime, timedelta, timezone

import pytest

from app.ml.dataset import InsufficientSnapshotsError, load_training_pairs
from tests.factories import TEST_TICKER, make_snapshot, synthetic_points


def test_raises_with_zero_snapshots(db_session, clean_test_snapshots):
    with pytest.raises(InsufficientSnapshotsError, match="need at least"):
        load_training_pairs(ticker=TEST_TICKER, session=db_session)


def test_raises_with_only_one_snapshot(db_session, clean_test_snapshots):
    make_snapshot(db_session, TEST_TICKER, datetime.now(timezone.utc), points=synthetic_points())

    with pytest.raises(InsufficientSnapshotsError, match="need at least"):
        load_training_pairs(ticker=TEST_TICKER, session=db_session)


def test_builds_one_pair_from_two_snapshots(db_session, clean_test_snapshots):
    t0 = datetime.now(timezone.utc)
    make_snapshot(db_session, TEST_TICKER, t0, points=synthetic_points())
    make_snapshot(db_session, TEST_TICKER, t0 + timedelta(days=1), points=synthetic_points())

    dataset = load_training_pairs(ticker=TEST_TICKER, n_moneyness=10, n_tte=8, session=db_session)

    assert dataset.X.shape == (1, 80)
    assert dataset.y.shape == (1, 80)
    assert len(dataset.snapshot_id_pairs) == 1


def test_n_snapshots_limits_to_most_recent(db_session, clean_test_snapshots):
    t0 = datetime.now(timezone.utc)
    for i in range(4):
        make_snapshot(db_session, TEST_TICKER, t0 + timedelta(days=i), points=synthetic_points())

    dataset = load_training_pairs(ticker=TEST_TICKER, n_snapshots=2, n_moneyness=10, n_tte=8, session=db_session)

    # 2 most recent snapshots -> exactly 1 pair, not 3
    assert dataset.X.shape[0] == 1
