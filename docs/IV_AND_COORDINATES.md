# Implied volatility: coordinates and assumptions

This is the reference for *exactly* what the numbers on the surface mean.
Every approximation below is deliberate and is called out as such — none
of it is presented as exact.

## Surface coordinate system

The reconstructed surface is a dense grid over **(log-moneyness, time-to-expiry)**.

### Log-moneyness — *spot* moneyness, not forward

```
k = ln(K / S)
```

where `K` is the option strike and `S` is the SPY spot at capture time
(`Snapshot.spot`). Computed in `scripts/daily_capture.build_vol_points`
and stored as `VolPoint.log_moneyness`.

This is **spot** log-moneyness. It is *not* forward log-moneyness
`ln(K / F)` and it is *not* a delta coordinate. Consequence: the `k = 0`
column of the grid is struck at spot, not at the forward, so it sits a
small horizontal distance

```
ln(F / S) = (r - q) * T
```

away from the true at-the-money-forward point — on the order of
`(0.037 - 0.010) * 0.5 ≈ 0.014` in `k` at a 6-month tenor, i.e. well
under one grid cell. We accept this offset rather than build a
per-maturity forward. If a future version needs ATM-forward alignment
(e.g. for a SVI/SSVI fit), switch the stored coordinate to `ln(K / F_T)`
with `F_T = S * exp((r - q) * T)`.

The OTM-only stitching boundary (calls kept for `K >= S`, puts for
`K <= S`) is likewise drawn at **spot**, not forward.

### Time to expiry

```
T = (expiry_date - trading_date) / 365
```

Calendar days, ACT/365F. `trading_date` is the capture date in
**US/Eastern** (not UTC — see `build_vol_points`). This is a calendar-time
convention, not a business-day / 252-trading-day one, and it ignores the
intraday fraction (a snapshot captured at 21:00 UTC still gets integer
days). Contracts with `T < MIN_DTE/365` (default 2 calendar days) are
dropped: sub-2-day IV is dominated by microstructure and pins.

## Black-Scholes inversion assumptions

`app/vol/black_scholes.py` inverts the **European** Black-Scholes price
with a continuous dividend yield for `sigma`, using Brent's method
(`scipy.optimize.brentq`) on `[iv_lower_bound, iv_upper_bound]` = `[1e-4, 5.0]`.
There is no closed form for `sigma` given a price, hence the 1-D root find.

| Input | Source | Approximation |
|---|---|---|
| Option price | `mid = (bid + ask) / 2` of the contract's quote | Not last-trade, not liquidity-weighted. Quotes are **end-of-day** (capture runs after the 16:00 ET close), when spreads are widest and quotes stalest — this is the main driver of inversion failures and smile noise. |
| `S` (spot) | `yfinance` `fast_info.last_price` at capture | Single scalar for the whole chain. |
| `r` (risk-free) | Latest close of `^IRX` (13-week US T-bill discount yield) / 100 | **One scalar for all maturities** — no term structure. A 13-week rate is applied to a 2-day option and to an 18-month option alike. |
| `q` (dividend yield) | Trailing ~12-month realized SPY cash dividends / spot, as a continuous yield | Backward-looking and continuous. Not the actual discrete ex-div schedule, not forward-looking. See `fetch_dividend_yield` — this value had a unit bug (was 0.98 = 98%) fixed on 2026-09-07. |
| Exercise style | European BS used for **American** SPY options | SPY is physically settled, American-exercise. Early-exercise premium is unpriced. Bias is largest for deep ITM (mostly puts) and negligible for the OTM wing the surface is actually built from, where early exercise is never/barely optimal. |
| Settlement / borrow / hard-to-borrow | Ignored | SPY is a liquid ETF; assumed frictionless. |

## What "IV" means here, in one sentence

The single `sigma` that, plugged into the **European** Black-Scholes
formula with **spot** `S`, a **flat** rate `r` from the 13-week bill, a
**continuous trailing** dividend yield `q`, and **ACT/365 calendar**
time, reproduces the contract's **end-of-day mid** price — inverted
numerically because BS has no analytic vega inverse.

## Not modelled (by design, for now)

- No-arbitrage constraints (calendar / butterfly) are **not enforced** on
  the surface. They are a manual diagnostic only. The clamp-extrapolation
  in `surface_builder.py` keeps the tails monotone-ish but is not an
  arbitrage-free construction.
- No bid/ask IV band — a single mid IV per contract.
- No weighting of the interpolation by quote quality (spread, open
  interest) — every kept point is equal weight.
