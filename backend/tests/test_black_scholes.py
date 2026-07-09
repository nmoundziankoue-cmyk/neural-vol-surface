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
