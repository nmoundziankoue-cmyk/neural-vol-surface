"""Backfill DataQualityReport rows for snapshots captured before the
data-quality layer existed.

The raw option chain isn't stored, so the capture-time rejection counts
(n_contracts_raw, n_inversion_failed, ...) can't be reconstructed - those
stay NULL and the row is flagged backfilled=True. Everything derivable
from the stored VolPoints (strike/maturity coverage, bid-ask spread
distribution, observed-vs-extrapolated surface cells) is filled in.

    PYTHONPATH=. python scripts/backfill_data_quality.py [--force]
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.db.models import DataQualityReport, Snapshot
from app.db.session import SessionLocal
from app.vol.quality import build_data_quality_report, upsert_data_quality_report


def main(force: bool = False) -> None:
    with SessionLocal() as session:
        snapshots = session.execute(select(Snapshot).order_by(Snapshot.captured_at)).scalars().all()
        existing = {
            r.snapshot_id
            for r in session.execute(select(DataQualityReport)).scalars().all()
        }

        done = 0
        for snap in snapshots:
            if snap.id in existing and not force:
                print(f"snapshot {snap.id}: report already exists, skipping")
                continue
            report = build_data_quality_report(session, snap.id, rejections=None)
            upsert_data_quality_report(session, report)
            print(
                f"snapshot {snap.id} ({snap.captured_at:%Y-%m-%d}): "
                f"n_kept={report.n_kept} expiries={report.n_expiries} "
                f"strikes={report.n_strikes} "
                f"cells observed/total={report.n_cells_observed}/{report.n_grid_cells}"
            )
            done += 1

        print(f"\nbackfilled {done} report(s)")


if __name__ == "__main__":
    main(force="--force" in sys.argv[1:])
