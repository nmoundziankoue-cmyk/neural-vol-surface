"""Raw data ingestion from yfinance: spot, risk-free rate, dividend yield, option chain."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from app.config import settings
from app.ingestion.retry import with_retry

# Pause between successive per-expiry requests in fetch_raw_chain, to avoid
# tripping Yahoo's rate limiter on chains with many expiries.
INTER_REQUEST_DELAY_SECONDS = 1.5


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


def fetch_dividend_yield(ticker: str = settings.ticker) -> float:
    """Trailing annualized dividend yield, decimal. Falls back to summing
    the last 4 dividend payments / spot if `info` lacks the field."""
    tk = yf.Ticker(ticker)

    info = with_retry(lambda: tk.info, label=f"fetch_dividend_yield.info({ticker})", validate=lambda d: bool(d))
    y = info.get("dividendYield")
    if y is not None:
        # yfinance has changed units across versions (0.0101 vs 1.01); normalize to decimal.
        return y / 100.0 if y > 1 else y

    divs = with_retry(lambda: tk.dividends, label=f"fetch_dividend_yield.dividends({ticker})", validate=lambda d: d is not None)
    if divs.empty:
        return 0.0
    trailing = divs.tail(4).sum()
    spot = fetch_spot(ticker)
    return float(trailing / spot)


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
