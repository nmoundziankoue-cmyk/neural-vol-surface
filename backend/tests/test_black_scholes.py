import pytest

from app.vol.black_scholes import bs_price, implied_volatility


@pytest.mark.parametrize(
    "spot,strike,tte,r,q,sigma,option_type",
    [
        (100, 100, 1.0, 0.03, 0.01, 0.20, "call"),   # ATM call
        (100, 110, 0.5, 0.03, 0.01, 0.35, "put"),     # OTM put
        (745, 700, 0.25, 0.04, 0.01, 0.05, "put"),    # low vol
        (745, 900, 1.5, 0.04, 0.01, 1.20, "call"),    # high vol, far OTM
        (100, 100, 0.01, 0.03, 0.01, 0.18, "call"),   # very short TTE
    ],
)
def test_price_then_invert_round_trip(spot, strike, tte, r, q, sigma, option_type):
    """price -> mid quote -> invert should recover the original sigma."""
    price = bs_price(spot, strike, tte, r, q, sigma, option_type)
    recovered = implied_volatility(price, spot, strike, tte, r, q, option_type)
    assert recovered == pytest.approx(sigma, abs=1e-4)


def test_below_intrinsic_returns_none():
    # A call price of 0.0001 on a strike far below spot is below intrinsic
    # value for any sigma >= iv_lower_bound - no root exists.
    iv = implied_volatility(0.0001, spot=100, strike=50, tte=0.5, r=0.03, q=0.01, option_type="call")
    assert iv is None


@pytest.mark.parametrize("bad_price", [0, -1.0])
def test_non_positive_price_returns_none(bad_price):
    iv = implied_volatility(bad_price, spot=100, strike=100, tte=1.0, r=0.03, q=0.01, option_type="call")
    assert iv is None


def test_zero_tte_returns_none():
    iv = implied_volatility(5.0, spot=100, strike=100, tte=0, r=0.03, q=0.01, option_type="call")
    assert iv is None


def test_invalid_option_type_raises():
    with pytest.raises(ValueError):
        bs_price(100, 100, 1.0, 0.03, 0.01, 0.20, "straddle")


# --- mathematical properties, not just "it runs" ---

def test_put_call_parity():
    """C - P = S e^{-qT} - K e^{-rT} for European options."""
    from math import exp

    s, k, t, r, q, sig = 745.0, 760.0, 0.5, 0.04, 0.012, 0.22
    c = bs_price(s, k, t, r, q, sig, "call")
    p = bs_price(s, k, t, r, q, sig, "put")
    assert c - p == pytest.approx(s * exp(-q * t) - k * exp(-r * t), abs=1e-8)


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_price_strictly_increasing_in_vol(option_type):
    """Vega > 0: a higher sigma is a higher option value. This is exactly
    what makes the Brent inversion well-posed."""
    prices = [bs_price(100, 100, 1.0, 0.03, 0.01, sig, option_type) for sig in [0.05, 0.1, 0.2, 0.4, 0.8]]
    assert all(b > a for a, b in zip(prices, prices[1:]))


def test_call_price_within_no_arbitrage_bounds():
    """max(S e^{-qT} - K e^{-rT}, 0) <= C <= S e^{-qT}."""
    from math import exp

    s, k, t, r, q, sig = 100.0, 90.0, 0.75, 0.03, 0.01, 0.3
    c = bs_price(s, k, t, r, q, sig, "call")
    lower = max(s * exp(-q * t) - k * exp(-r * t), 0.0)
    assert lower <= c <= s * exp(-q * t) + 1e-9


def test_inversion_recovers_sigma_across_moneyness():
    true_sigma = 0.25
    for strike in (600, 700, 745, 800, 900):
        price = bs_price(745, strike, 0.5, 0.04, 0.01, true_sigma, "call" if strike >= 745 else "put")
        recovered = implied_volatility(price, 745, strike, 0.5, 0.04, 0.01, "call" if strike >= 745 else "put")
        assert recovered == pytest.approx(true_sigma, abs=1e-4)
