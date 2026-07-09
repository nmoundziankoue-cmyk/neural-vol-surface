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

from sqlalchemy import select

from app.config import settings
from app.db.models import Snapshot, VolPoint
from app.db.session import SessionLocal, init_db
from app.ingestion.options_fetcher import fetch_market_context, fetch_raw_chain
from app.ingestion.retry import FetchFailure
from app.vol.black_scholes import implied_volatility

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


def _mid_price(bid: float, ask: float) -> float | None:
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2.0


def build_vol_points(ctx, raw_chain) -> list[dict]:
    today = ctx.snapshot_ts.date()
    points: list[dict] = []
    skipped = {"otm_side": 0, "bad_quote": 0, "illiquid": 0, "wide_spread": 0, "no_root": 0, "short_dte": 0}

    for row in raw_chain.itertuples(index=False):
        expiry_date = datetime.strptime(row.expiry, "%Y-%m-%d").date()
        dte = (expiry_date - today).days
        if dte < settings.min_dte:
            skipped["short_dte"] += 1
            continue
        tte = dte / 365.0

        # OTM-only convention: calls above spot, puts below spot (best-priced side)
        if row.option_type == "call" and row.strike < ctx.spot:
            skipped["otm_side"] += 1
            continue
        if row.option_type == "put" and row.strike > ctx.spot:
            skipped["otm_side"] += 1
            continue

        mid = _mid_price(row.bid, row.ask)
        if mid is None:
            skipped["bad_quote"] += 1
            continue

        if row.openInterest < settings.min_open_interest:
            skipped["illiquid"] += 1
            continue

        rel_spread = (row.ask - row.bid) / mid
        if rel_spread > settings.max_relative_spread:
            skipped["wide_spread"] += 1
            continue

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
            skipped["no_root"] += 1
            continue

        points.append(
            dict(
                expiry=row.expiry,
                tte=tte,
                strike=row.strike,
                option_type=row.option_type,
                bid=row.bid,
                ask=row.ask,
                mid=mid,
                volume=row.volume,
                open_interest=row.openInterest,
                log_moneyness=log(row.strike / ctx.spot),
                implied_vol=iv,
            )
        )

    logger.info("kept %d points, skipped: %s", len(points), skipped)
    return points


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

    vol_points = build_vol_points(ctx, raw_chain)
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
