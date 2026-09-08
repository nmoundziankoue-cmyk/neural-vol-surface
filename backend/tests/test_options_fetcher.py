"""Unit tests for the pure helpers in options_fetcher (no network).

The dividend-yield path is the one with a history of silently wrong
output: yfinance 1.5.1 started returning `dividendYield = 0.98` (meaning
0.98%) where older versions returned `1.01` (meaning 1.01%), and the old
`y/100 if y > 1 else y` heuristic passed 0.98 straight through as a 98%
yield - which shifted every Black-Scholes forward and pushed the IV
inversion failure rate from ~0 to ~40%.
"""

import pytest

from app.ingestion.options_fetcher import (
    DIVIDEND_YIELD_SANITY_MAX,
    _normalize_info_yield,
    _sanity_check_yield,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.0101, 0.0101),   # already decimal (older yfinance)
        (1.01, 0.0101),     # percent, > 1 (older yfinance)
        (0.98, 0.0098),     # percent, < 1 (yfinance 1.5.1) - the regression case
        (7.5, 0.075),       # percent, high-yield name
        (0.001, 0.001),     # genuinely low decimal yield, left alone
    ],
)
def test_normalize_info_yield(raw, expected):
    assert _normalize_info_yield(raw) == pytest.approx(expected, rel=1e-9)


def test_sanity_check_passes_plausible_value():
    assert _sanity_check_yield(0.0098, source="test") == pytest.approx(0.0098)


def test_sanity_check_clamps_absurd_value():
    # 0.98 (98%) is what the old bug produced; it must never reach Black-Scholes.
    assert _sanity_check_yield(0.98, source="test") == pytest.approx(DIVIDEND_YIELD_SANITY_MAX)


def test_sanity_check_clamps_negative_to_zero():
    assert _sanity_check_yield(-0.01, source="test") == 0.0
