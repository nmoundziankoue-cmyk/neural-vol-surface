"""Black-Scholes pricing (with continuous dividend yield) and implied
volatility inversion from an observed mid price.

Note: SPY options are American-style; this uses the European BS model as
a standard approximation for surface construction. Bias is largest for
deep ITM puts (early exercise premium) - acceptable for v1.
"""

from __future__ import annotations

from math import exp, log, sqrt

from scipy.optimize import brentq
from scipy.stats import norm

from app.config import settings


def bs_price(
    spot: float,
    strike: float,
    tte: float,
    r: float,
    q: float,
    sigma: float,
    option_type: str,
) -> float:
    """European BS price with continuous dividend yield q. tte in years."""
    if tte <= 0 or sigma <= 0:
        intrinsic = (spot - strike) if option_type == "call" else (strike - spot)
        return max(intrinsic, 0.0)

    d1 = (log(spot / strike) + (r - q + 0.5 * sigma**2) * tte) / (sigma * sqrt(tte))
    d2 = d1 - sigma * sqrt(tte)

    if option_type == "call":
        return spot * exp(-q * tte) * norm.cdf(d1) - strike * exp(-r * tte) * norm.cdf(d2)
    elif option_type == "put":
        return strike * exp(-r * tte) * norm.cdf(-d2) - spot * exp(-q * tte) * norm.cdf(-d1)
    else:
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")


def implied_volatility(
    mid_price: float,
    spot: float,
    strike: float,
    tte: float,
    r: float,
    q: float,
    option_type: str,
) -> float | None:
    """Invert BS for sigma via Brent's method. Returns None if no root
    exists in bounds (arbitrage-violating quote) or price is non-positive."""
    if mid_price <= 0 or tte <= 0:
        return None

    lo, hi = settings.iv_lower_bound, settings.iv_upper_bound

    def objective(sigma: float) -> float:
        return bs_price(spot, strike, tte, r, q, sigma, option_type) - mid_price

    f_lo, f_hi = objective(lo), objective(hi)
    if f_lo * f_hi > 0:
        # mid price outside the range spanned by the model at bound vols
        # (e.g. below intrinsic, or above what even sigma=5.0 can produce)
        return None

    try:
        return brentq(objective, lo, hi, xtol=1e-6, maxiter=100)
    except ValueError:
        return None
