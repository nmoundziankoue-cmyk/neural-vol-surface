"""Daily snapshot capture: pull SPY option chain, invert Black-Scholes IV
per contract, filter for liquidity, and persist raw (strike, expiry, IV)
points to Postgres.

Run once per trading day (cron or manual):
    PYTHONPATH=. python scripts/daily_capture.py

We store raw points rather than a pre-built grid so that log-moneyness x
TTE interpolation (PCHIP) can be redone at read/train time without losing
information baked into an earlier grid choice.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from math import log
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select

from app.config import settings
from app.db.models import Snapshot, VolPoint
from app.db.session import SessionLocal, init_db
from app.ingestion.options_fetcher import fetch_market_context, fetch_raw_chain
from app.ingestion.retry import FetchFailure
from app.vol.black_scholes import implied_volatility
from app.vol.quality import (
    RejectionBreakdown,
    build_data_quality_report,
    upsert_data_quality_report,
)

MARKET_TZ = ZoneInfo("America/New_York")

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("daily_capture")
logger.setLevel(logging.INFO)
_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

_file_handler = logging.FileHandler(LOG_DIR / "daily_capture.log")
_file_handler.setFormatter(_formatter)
logger.addHandler(_file_handler)

_console_handler = logging.StreamHandler(sys.stderr)
_console_handler.setFormatter(_formatter)
logger.addHandler(_console_handler)


def build_vol_points(ctx, raw_chain) -> tuple[list[dict], RejectionBreakdown]:
    """Filter the raw chain and invert Black-Scholes per surviving contract.

    Returns the kept points plus a RejectionBreakdown in which every raw
    contract is accounted for in exactly one bucket - no silent drops.
    """
    # Trading day / days-to-expiry must be measured in exchange-local time
    # (ET), not UTC: a capture that lands after ~20:00 ET is already the
    # next calendar day in UTC, which would shorten every TTE by one day
    # and mis-count DTE against MIN_DTE. _find_todays_snapshot already uses
    # ET for its dedup check - this keeps the two consistent.
    today = ctx.snapshot_ts.astimezone(MARKET_TZ).date()
    points: list[dict] = []
    rej = RejectionBreakdown(n_contracts_raw=len(raw_chain))

    for row in raw_chain.itertuples(index=False):
        expiry_date = datetime.strptime(row.expiry, "%Y-%m-%d").date()
        dte = (expiry_date - today).days
        if dte < settings.min_dte:
            rej.n_short_dte += 1
            continue
        tte = dte / 365.0

        # OTM-only convention: calls above spot, puts below spot (best-priced side)
        if row.option_type == "call" and row.strike < ctx.spot:
            rej.n_otm_side += 1
            continue
        if row.option_type == "put" and row.strike > ctx.spot:
            rej.n_otm_side += 1
            continue

        # Quote validity, split into distinct reasons rather than one
        # "bad quote" bucket. A NaN bid/ask must be caught here explicitly;
        # left alone it flows through as mid=NaN and gets mis-attributed to
        # an inversion failure downstream.
        bid, ask = row.bid, row.ask
        if pd.isna(bid) or pd.isna(ask):
            rej.n_missing_price += 1
            continue
        if bid <= 0 or ask <= 0:
            rej.n_non_positive_bid += 1
            continue
        if ask < bid:
            rej.n_crossed_market += 1
            continue
        mid = (bid + ask) / 2.0

        if row.openInterest < settings.min_open_interest:
            rej.n_low_open_interest += 1
            continue

        rel_spread = (ask - bid) / mid
        if rel_spread > settings.max_relative_spread:
            rej.n_wide_spread += 1
            continue

        rej.n_inversion_attempted += 1
        iv = implied_volatility(
            mid_price=mid,
            spot=ctx.spot,
            strike=row.strike,
            tte=tte,
            r=ctx.risk_free_rate,
            q=ctx.dividend_yield,
            option_type=row.option_type,
        )
        if iv is None:
            rej.n_inversion_failed += 1
            continue

        points.append(
            dict(
                expiry=row.expiry,
                tte=tte,
                strike=row.strike,
                option_type=row.option_type,
                bid=bid,
                ask=ask,
                mid=mid,
                volume=row.volume,
                open_interest=row.openInterest,
                log_moneyness=log(row.strike / ctx.spot),
                implied_vol=iv,
            )
        )

    rej.n_kept = len(points)
    gap = rej.n_contracts_raw - rej.accounted()
    logger.info(
        "kept %d / %d contracts (inversion failure rate %s); breakdown: %s",
        rej.n_kept, rej.n_contracts_raw,
        f"{rej.inversion_failure_rate:.1%}" if rej.inversion_failure_rate is not None else "n/a",
        rej.as_dict(),
    )
    if gap != 0:
        logger.warning("contract accounting gap: %d contracts unaccounted for", gap)
    return points, rej


def _find_todays_snapshot(session, ticker: str) -> Snapshot | None:
    """Trading day is defined in exchange local time (ET), not UTC, so a
    cron job anchored to UTC can't accidentally see 'yesterday' as today."""
    today_et = datetime.now(MARKET_TZ).date()
    latest = session.execute(
        select(Snapshot).where(Snapshot.ticker == ticker).order_by(Snapshot.captured_at.desc()).limit(1)
    ).scalar_one_or_none()
    if latest is not None and latest.captured_at.astimezone(MARKET_TZ).date() == today_et:
        return latest
    return None


def run(ticker: str = settings.ticker) -> int:
    init_db()

    with SessionLocal() as session:
        existing = _find_todays_snapshot(session, ticker)
        if existing is not None:
            logger.info(
                "snapshot already exists for %s today (id=%d, captured_at=%s) — skipping, no duplicate run",
                ticker, existing.id, existing.captured_at,
            )
            return existing.id

    logger.info("fetching market context for %s...", ticker)
    ctx = fetch_market_context(ticker)
    logger.info("spot=%.2f r=%.4f q=%.4f", ctx.spot, ctx.risk_free_rate, ctx.dividend_yield)

    logger.info("fetching raw option chain...")
    raw_chain = fetch_raw_chain(ticker)
    logger.info("%d raw contracts across all expiries", len(raw_chain))

    vol_points, rejections = build_vol_points(ctx, raw_chain)
    if not vol_points:
        raise RuntimeError("No valid vol points produced — check liquidity filters / market hours")

    with SessionLocal() as session:
        snapshot = Snapshot(
            ticker=ticker,
            captured_at=ctx.snapshot_ts,
            spot=ctx.spot,
            risk_free_rate=ctx.risk_free_rate,
            dividend_yield=ctx.dividend_yield,
        )
        session.add(snapshot)
        session.flush()  # assign snapshot.id

        session.add_all(VolPoint(snapshot_id=snapshot.id, **p) for p in vol_points)
        session.commit()
        snapshot_id = snapshot.id

    logger.info("stored snapshot id=%d with %d vol points", snapshot_id, len(vol_points))

    # Persist the data-quality report in its own session so a failure here
    # (e.g. a degenerate surface) never rolls back the snapshot itself.
    try:
        with SessionLocal() as session:
            report = build_data_quality_report(session, snapshot_id, rejections=rejections)
            upsert_data_quality_report(session, report)
        logger.info(
            "data-quality report stored for snapshot id=%d (%d/%d cells observed, inversion failure rate %s)",
            snapshot_id, report.n_cells_observed or 0, report.n_grid_cells or 0,
            f"{rejections.inversion_failure_rate:.1%}" if rejections.inversion_failure_rate is not None else "n/a",
        )
    except Exception:  # noqa: BLE001 - report is secondary to the snapshot
        logger.exception("failed to build/store data-quality report for snapshot id=%d", snapshot_id)

    return snapshot_id


if __name__ == "__main__":
    try:
        run()
    except FetchFailure as e:
        logger.error("=" * 60)
        logger.error("CAPTURE FAILED — Yahoo Finance unavailable after all retries")
        logger.error("Reason: %s", e)
        logger.error("ACTION REQUIRED: rerun manually — a day of data will be lost otherwise:")
        logger.error("    cd backend && PYTHONPATH=. python3 scripts/daily_capture.py")
        logger.error("=" * 60)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 - top-level catch-all so cron never gets a silent failure
        logger.error("=" * 60)
        logger.error("CAPTURE FAILED — unexpected error: %s", e, exc_info=True)
        logger.error("ACTION REQUIRED: rerun manually — a day of data will be lost otherwise:")
        logger.error("    cd backend && PYTHONPATH=. python3 scripts/daily_capture.py")
        logger.error("=" * 60)
        sys.exit(1)
