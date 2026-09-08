"""Raw data ingestion from yfinance: spot, risk-free rate, dividend yield, option chain."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from app.config import settings
from app.ingestion.retry import with_retry

# Share the "daily_capture" logger so warnings land in the same file/console
# handlers configured by scripts/daily_capture.py.
logger = logging.getLogger("daily_capture")

# Pause between successive per-expiry requests in fetch_raw_chain, to avoid
# tripping Yahoo's rate limiter on chains with many expiries.
INTER_REQUEST_DELAY_SECONDS = 1.5

# No broad equity index pays a dividend yield above this. Used both to
# range-check the final number and to disambiguate yfinance's unit drift
# (see fetch_dividend_yield).
DIVIDEND_YIELD_SANITY_MAX = 0.15  # 15%


@dataclass
class MarketContext:
    ticker: str
    snapshot_ts: datetime
    spot: float
    risk_free_rate: float  # decimal, e.g. 0.0372
    dividend_yield: float  # decimal, annualized


def fetch_spot(ticker: str = settings.ticker) -> float:
    def _call() -> float:
        return float(yf.Ticker(ticker).fast_info.last_price)

    return with_retry(_call, label=f"fetch_spot({ticker})", validate=lambda v: v is not None and v > 0)


def fetch_risk_free_rate(rate_ticker: str = settings.rate_ticker) -> float:
    """13-week T-bill (^IRX) quoted in percent -> decimal."""

    def _call() -> pd.DataFrame:
        return yf.Ticker(rate_ticker).history(period="5d")

    hist = with_retry(_call, label=f"fetch_risk_free_rate({rate_ticker})", validate=lambda df: df is not None and not df.empty)
    return float(hist["Close"].iloc[-1]) / 100.0


def _sanity_check_yield(y: float, *, source: str) -> float:
    """Clamp to a plausible range and log the source, so a bad upstream
    value can't silently poison Black-Scholes (a wrong q shifts every
    forward and inflates the inversion failure rate)."""
    if not 0.0 <= y <= DIVIDEND_YIELD_SANITY_MAX:
        logger.warning(
            "dividend yield %.4f from %s outside [0, %.2f] - clamping",
            y, source, DIVIDEND_YIELD_SANITY_MAX,
        )
        return min(max(y, 0.0), DIVIDEND_YIELD_SANITY_MAX)
    logger.info("dividend yield %.4f (%.2f%%) from %s", y, y * 100.0, source)
    return y


def fetch_dividend_yield(ticker: str = settings.ticker) -> float:
    """Trailing ~12-month dividend yield as a decimal (e.g. 0.0098 for SPY).

    Primary source: actual cash dividends paid over the trailing year
    divided by spot. This is unit-unambiguous, unlike yfinance's `info`
    fields whose scaling has drifted across library versions - the same
    ~1% SPY yield has been returned as 0.0101, 1.01 AND 0.98. `info` is
    only a fallback (used when dividend history is unavailable), and
    every path is range-checked by _sanity_check_yield.
    """
    tk = yf.Ticker(ticker)

    divs = with_retry(
        lambda: tk.dividends,
        label=f"fetch_dividend_yield.dividends({ticker})",
        validate=lambda d: d is not None,
    )
    if divs is not None and not divs.empty:
        cutoff = pd.Timestamp.now(tz=divs.index.tz) - pd.DateOffset(years=1)
        trailing = float(divs[divs.index >= cutoff].sum())
        if trailing > 0:
            return _sanity_check_yield(trailing / fetch_spot(ticker), source="trailing dividends")

    info = with_retry(lambda: tk.info, label=f"fetch_dividend_yield.info({ticker})", validate=lambda d: bool(d))
    for field in ("yield", "trailingAnnualDividendYield", "dividendYield"):
        raw = info.get(field)
        if raw is None or raw <= 0:
            continue
        return _sanity_check_yield(_normalize_info_yield(raw), source=f"info[{field}]")

    logger.warning("dividend yield unavailable for %s - defaulting to 0.0", ticker)
    return 0.0


def _normalize_info_yield(raw: float) -> float:
    """yfinance `info` yield fields have been seen in both decimal (0.0101)
    and percent (1.01, 0.98) scaling for the same ~1% yield. Anything above
    the sanity max cannot be a real decimal yield, so treat it as percent."""
    return raw / 100.0 if raw > DIVIDEND_YIELD_SANITY_MAX else float(raw)


def fetch_market_context(ticker: str = settings.ticker) -> MarketContext:
    return MarketContext(
        ticker=ticker,
        snapshot_ts=datetime.now(timezone.utc),
        spot=fetch_spot(ticker),
        risk_free_rate=fetch_risk_free_rate(),
        dividend_yield=fetch_dividend_yield(ticker),
    )


def fetch_raw_chain(ticker: str = settings.ticker) -> pd.DataFrame:
    """Pull the full option chain (calls + puts, all expiries) into one
    flat DataFrame with a `option_type` and `expiry` column.

    Fires one HTTP request per expiry (yfinance has no bulk endpoint), so
    each request is retried independently and throttled by
    INTER_REQUEST_DELAY_SECONDS to stay under Yahoo's rate limiter.
    """
    tk = yf.Ticker(ticker)

    expiries = with_retry(
        lambda: tk.options,
        label=f"fetch_raw_chain.options({ticker})",
        validate=lambda e: bool(e),
    )

    frames = []
    for i, expiry in enumerate(expiries):
        if i > 0:
            time.sleep(INTER_REQUEST_DELAY_SECONDS)

        chain = with_retry(
            lambda expiry=expiry: tk.option_chain(expiry),
            label=f"fetch_raw_chain.option_chain({ticker}, {expiry})",
            validate=lambda c: c is not None and not c.calls.empty,
        )
        calls = chain.calls.copy()
        calls["option_type"] = "call"
        puts = chain.puts.copy()
        puts["option_type"] = "put"
        combined = pd.concat([calls, puts], ignore_index=True)
        combined["expiry"] = expiry
        frames.append(combined)

    df = pd.concat(frames, ignore_index=True)
    df["volume"] = df["volume"].fillna(0)
    df["openInterest"] = df["openInterest"].fillna(0)
    return df
