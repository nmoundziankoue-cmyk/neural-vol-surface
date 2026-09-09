# Neural Volatility Surface Forecaster — SPY

**▶ Live demo: https://neural-vol-surface.vercel.app**
&nbsp;·&nbsp; API: `https://neural-vol-surface-backend.onrender.com` (`/api/evaluation`, `/api/data-quality`, `/api/surfaces/latest`)

> Hosted on free tiers (Render web service + Neon Postgres). The backend
> cold-starts after ~15 min idle — the first request can take 30–60 s, so
> if the page loads empty, wait and reload once. Neon also suspends an
> idle database, adding a few seconds to the first query after a pause.

## Research question

> Can a lightweight neural model predict tomorrow's SPY implied-volatility
> surface better than the persistence hypothesis (IV̂ₜ₊₁ = IVₜ)?

**Current answer: no, and — more importantly — the dataset is far too
small to answer it either way.** As of 2026-09-09 the database holds
**5 snapshots**, forming **4 chronologically-adjacent pairs**, of which
**exactly one is a genuine single-session move** (three of the five
captures ran on a weekend or holiday and carry the previous Friday's
close — see [§8](#8-baseline-and-results)). Pooled over those pairs, a
leave-one-pair-out–validated model has a masked RMSE of **0.0198 vs
persistence's 0.0131** (relative improvement **−51.4%**). That number is
not evidence about the model; it is a pipeline smoke test. Live figures:
`GET /api/evaluation`.

The engineering that *is* real and finished: end-to-end daily capture of
a data series that exists nowhere for free, in-house Black-Scholes
inversion, a defensible surface reconstruction, and a data-quality layer
that accounts for every contract that enters and leaves the pipeline.

---

## 2. Why volatility surfaces matter

An option chain gives you discrete `(strike, expiry, price)` quotes. The
implied-volatility surface is the 2-D function `σ(K, T)` those quotes
imply through Black-Scholes. It matters because:

- **It is the market's forward-looking risk map.** The skew (σ higher for
  low strikes) prices crash risk; the term structure prices
  known events and mean reversion.
- **Everything downstream consumes it.** Pricing exotics, computing
  greeks, VaR, and margin all interpolate off a surface, not off raw
  quotes.
- **It moves as an object.** Level, skew and curvature shift together in
  a low-dimensional way — which is *why* a forecasting question is
  reasonable to ask, even if this project can't yet answer it.

---

## 3. System architecture

```
                         ┌──────────────────────────┐
   yfinance (SPY chain)  │  GitHub Actions (cron)    │  once per trading day
   ─ spot, ^IRX, divs ──▶│  scripts/daily_capture.py │  17:00 ET, after close
                         └────────────┬─────────────┘
                                      │
             ┌────────────────────────┼─────────────────────────┐
             ▼                        ▼                         ▼
   ingestion/options_fetcher   vol/black_scholes           vol/quality
   ─ retry+backoff             ─ mid = (bid+ask)/2         ─ RejectionBreakdown
   ─ OTM-only stitching        ─ Brent inversion → σ       ─ every contract
                                                              accounted for
             └────────────────────────┬─────────────────────────┘
                                      ▼
                         Postgres:  snapshots
                                    vol_points   (raw (K,T,σ) points, not a grid)
                                    data_quality_reports
                                      │
             ┌────────────────────────┼─────────────────────────┐
             ▼                        ▼                         ▼
   vol/surface_builder        ml/dataset + ml/evaluate    api/ (FastAPI)
   ─ separable PCHIP          ─ (surfaceₜ, surfaceₜ₊₁)     ─ /surfaces/*
   ─ flat clamp + mask        ─ persistence baseline       ─ /predictions/*
                              ─ LOPO model vs baseline     ─ /data-quality
                                                           ─ /evaluation
                                      │
                                      ▼
                         Next.js + Plotly  (3-D surface, DQ table, eval panel)
```

Each stage runs and is tested independently — `surface_builder.py`
doesn't care whether its input came from today's cron run or from six
months of accumulated history.

---

## 4. Data pipeline

| Step | File | What it does |
|---|---|---|
| Market context | `ingestion/options_fetcher.py` | spot (`fast_info.last_price`), `r` (`^IRX` 13-week bill), `q` (trailing 12-month realized dividends / spot) |
| Raw chain | `ingestion/options_fetcher.py` | one HTTP request per expiry (no bulk endpoint), each retried with 30/60/120 s backoff, throttled 1.5 s apart |
| Filter + invert | `scripts/daily_capture.py::build_vol_points` | see rejection ladder below |
| Persist | `scripts/daily_capture.py::run` | `Snapshot` + `VolPoint` rows, then a `DataQualityReport`, then a refreshed evaluation — each in its own transaction so a late-stage failure never rolls back the snapshot |
| Schedule | `.github/workflows/daily_capture.yml` | GitHub Actions, not a laptop cron (see [Deployment](#11-deployment)) |

**Rejection ladder** (first match wins; every raw contract lands in
exactly one bucket, `RejectionBreakdown.accounted() == n_contracts_raw`):

1. `short_dte` — expiring in < 2 calendar days (0-2 DTE IV is microstructure)
2. `otm_side` — ITM side of the OTM-only convention (calls kept for K ≥ S, puts for K ≤ S)
3. `missing_price` — bid or ask is NaN
4. `non_positive_bid` — bid ≤ 0 or ask ≤ 0
5. `crossed_market` — ask < bid
6. `low_open_interest` — OI < 1
7. `wide_spread` — (ask − bid)/mid > 0.5
8. `inversion_failed` — Brent found no root in [1e-4, 5.0] (arbitrage-violating quote)

Observed on snapshot #315: 9 860 raw contracts → 4 990 `VolPoint` rows.
The `otm_side` bucket (3 869) is ~40% of the raw chain and ~80% of all
rejections — it's the deliberate OTM-only de-duplication (keep the OTM
call *or* the OTM put at each strike, never both), not a data problem.
`short_dte` (512), `wide_spread` (195) and non-positive/crossed quotes
(285) account for essentially all of the rest; `inversion_failed` was 0.

We store **raw points, not a pre-built grid**, so the PCHIP grid choice
can be revisited at read/train time.

---

## 5. IV computation and assumptions

Full detail in **[docs/IV_AND_COORDINATES.md](docs/IV_AND_COORDINATES.md)**.
Every item below is a deliberate approximation and is labelled as one —
none is presented as exact.

- **European Black-Scholes** with continuous dividend yield, inverted for
  σ by Brent's method (`scipy.optimize.brentq`) on the **end-of-day mid**
  price. There is no closed form for σ given a price, hence the 1-D root
  find.
- **Applied to American-style SPY options.** Early-exercise premium is
  unpriced; the bias is concentrated deep ITM and is negligible for the
  OTM wing the surface is built from.
- **`r`** = latest `^IRX` close (13-week T-bill), a **single scalar for
  every maturity** — no term structure.
- **`q`** = trailing ~12-month realized cash dividends / spot, continuous
  — not the discrete ex-div schedule. (This value had a unit bug — it was
  0.98, i.e. 98% — fixed 2026-09-07; it had pushed the inversion failure
  rate to ~39%.)
- **Coordinates**: the surface grid is **(spot log-moneyness `k = ln(K/S)`,
  ACT/365 calendar TTE)**. It is *not* forward moneyness `ln(K/F)` and
  *not* delta. The `k = 0` column sits `≈ (r−q)T` from ATM-forward — under
  one grid cell at index tenors.

---

## 6. Surface reconstruction — separable PCHIP

SciPy has no 2-D scattered-data PCHIP, so the surface is built in two
1-D passes (`vol/surface_builder.py`):

1. **Per-expiry smile**: PCHIP over log-moneyness using only that
   expiry's observed points, evaluated on a shared moneyness grid.
2. **Per-moneyness term structure**: PCHIP over TTE across the actual
   expiries, using the pass-1 outputs.

**Why PCHIP, not a natural cubic spline:** PCHIP is *shape-preserving* —
it never overshoots between monotone data points, so it cannot
manufacture a spurious dip or a negative IV between two adjacent quotes.
A natural cubic spline minimises curvature globally and will ring
(oscillate) on the kinked, noisy smile of a real EOD chain. For an IV
surface, "monotone in, monotone out" matters more than C² smoothness.

**Extrapolation is flat (clamp to the boundary value), never
polynomial.** PCHIP is only shape-preserving *between* knots; past the
last knot SciPy just extends the boundary cubic, which is not guaranteed
positive or bounded. Every clamped cell is flagged in
`extrapolated_mask` — the dashboard renders it at reduced opacity and the
evaluation excludes it from RMSE. On the default 40×40 grid, ~60% of
cells are clamp-extrapolated, mostly at long TTE where few expiries are
liquid.

No-arbitrage (calendar / butterfly) is **not enforced** — it is a manual
diagnostic only (see [Interview guide](docs/INTERVIEW_GUIDE.md), concept 10).

---

## 7. Prediction methodology

**Delta / residual prediction.** The model is
`forward(x) = x + Δ_net(x)`: it predicts the *change* to the surface, not
the surface itself. A vol surface moves little day to day, so this gives
the network a built-in easy path to at least match persistence
(`Δ_net(x) = 0`) instead of having to relearn the whole surface shape
from a handful of examples. With this little data, a plain `x → y` map
has no such prior and would not even reach the persistence floor.

The architecture is a **feedforward net** (`512-256-512`), deliberately —
no LSTM/Transformer is justified until a feedforward residual model
beats persistence on real accumulated data, which has not been tested.

**Grids must share a fixed axis to be comparable across days.** Each
snapshot's own grid bounds are data-derived (observed strike/expiry
coverage shifts daily), so `dataset.py` and the `/predictions` endpoint
rebuild every surface on the *intersection* of the relevant snapshots'
observed ranges before pairing or scoring.

---

## 8. Baseline and results

**The only baseline is persistence: IV̂ₜ₊₁ = IVₜ.** No PCA baseline, no
PCA forecast — there is nowhere near enough history for either to be
anything but noise.

"Masked RMSE" here means: RMSE over the grid cells built from real quotes
— observed points and the PCHIP interpolation between them — with the
~60% of cells that are flat-clamp-extrapolated in the wings excluded (see
[§6](#6-surface-reconstruction--separable-pchip) and
`docs/INTERVIEW_GUIDE.md` §7). "Pooled" means one RMSE over every scored
cell of every pair — `sqrt(mean(squared error))` across the union —
**not** the mean of the per-pair RMSEs, which is why the pooled
persistence figure (0.0131) sits above the simple average of the four
per-pair values.

The model number is **leave-one-pair-out (LOPO) cross-validated**: each
pair is predicted by a model trained only on the *other* pairs, so it is
out-of-sample even at this size. (Fitting on all pairs and scoring
in-sample just reports the ≈0 memorisation floor and tells us nothing.)
Caveat: adjacent pairs share a snapshot — pair *i*'s day *t+1* is pair
*i+1*'s day *t* — so the folds are not fully independent and the LOPO
number is, if anything, optimistic.

Live numbers, refreshed on each backend deploy (`scripts/evaluate.py`,
served at `GET /api/evaluation`) — values below are the 2026-09-09
recompute:

| | value |
|---|---|
| Snapshots | 5 — ET capture dates 2026-07-08 (Wed), 07-11 (Sat*), 08-16 (Sun*), 09-07 (Mon, Labor Day*), 09-08 (Tue) |
| Pairs | 4 — calendar gaps **3, 36, 22, 1 days**; **only the last (#75→#315) is a true single-session move** |
| Persistence masked RMSE (pooled) | **0.0131** |
| Model masked RMSE (LOPO, pooled) | **0.0198** |
| Relative improvement | **−51.4%** (model is worse) |
| Beats persistence? | **No** |
| Statistically significant? | **No** — hard gate: ≥ 20 pairs *and* every pair a next-trading-day pair |

<sub>* Captured on a non-trading day (ET), so the snapshot holds the prior
Friday's close: #23 → 2026-07-10, #54 → 2026-08-14, #75 → 2026-09-04.
`calendar_gap_days` is measured between capture timestamps, not trading
sessions, so pair #1→#23 is tagged `is_next_trading_day` despite its
content spanning Wed→Fri (two sessions). The one clean pair is #75→#315:
Fri 09-04 close → Tue 09-08 close, one session across the Labor Day
weekend — and it is where the model does worst (0.0263 vs 0.0123).</sub>

**Honest reading:** four pairs, only one a real next-session forecast (and
`n = 1` there); two spanning multi-week gaps where "persistence" is asked
to hold a surface for 3–5 weeks; LOPO folds that train on three
non-independent pairs. Nothing here supports a conclusion in either
direction. The `statistically_significant` flag in the API is a hard gate
that stays `false` until ≥ 20 clean next-trading-day pairs exist. The
pipeline is validated end to end; the model is not.

---

## 9. Data quality

Every snapshot gets a persisted `DataQualityReport` (`vol/quality.py`,
table `data_quality_reports`, `GET /api/data-quality`, table in the
frontend). It records, per snapshot:

- raw contract count and kept IV-observation count
- every rejection reason count (the ladder in §4) and the **BS inversion
  failure rate** over contracts that reached Brent
- missing prices, non-positive bids, crossed markets — broken out, not
  lumped
- bid-ask relative-spread distribution (median / p95 / max) over kept points
- strike and maturity coverage (count, min/max, DTE range)
- reconstructed-surface cells: observed vs clamp-extrapolated
- spot at capture

**Nothing is rejected silently** — `RejectionBreakdown.accounted()` must
equal the raw contract count or the capture logs a warning.

Snapshots captured before this layer existed (the four backfilled ones —
#1, #23, #54, #75) carry a `backfilled = true` flag: the derivable half
(coverage, spreads, surface cells) is filled from the stored points; the
raw-chain rejection counts are `null` because the raw chain isn't kept.
Only #315 onward (captured by the GitHub Actions job) has the full
raw-chain accounting.

---

## 10. Tests

`cd backend && PYTHONPATH=. pytest` — **54 tests, ~3 s.** DB-backed tests
run against the same local Postgres as the app, isolated by a dedicated
`TEST_XYZ` ticker cleaned before/after each test.

High-value coverage, not coverage for its own sake:

- **Black-Scholes**: put-call parity, price strictly increasing in σ
  (what makes the inversion well-posed), no-arbitrage price bounds, σ
  round-trip recovered across a moneyness range, below-intrinsic → `None`.
- **Surface builder**: PCHIP reproduces the observed IV exactly at a
  knot; clamp extrapolation is piecewise-flat and never leaves the
  observed IV band; mask set on extrapolated cells only; explicit range
  overrides respected.
- **Persistence baseline**: zero error on an unchanged surface, matches a
  hand calc, masked RMSE == plain RMSE when nothing is masked.
- **Data quality**: `accounted()` invariant, inversion-failure-rate,
  backfill report from stored points.
- **Evaluation**: insufficient-data path, comparison without claiming
  significance, multi-day-gap flagging.
- **API**: status codes and error mapping (404 / 422) via `TestClient`.

Not covered: `options_fetcher` network paths and `daily_capture`
end-to-end (would need a yfinance mock).

---

## 11. Deployment

- **Database** → Neon (external free-tier Postgres). `render.yaml`
  declares no managed database; the web service takes `DATABASE_URL` as a
  manual env var (`sync: false`). `config.py::_normalize_database_url`
  rewrites Neon's `postgresql://…?sslmode=require&channel_binding=require`
  to the `postgresql+psycopg2://` form SQLAlchemy needs, query string
  intact.
- **Backend** → Render, from `render.yaml` (Blueprint = Docker web
  service only). `backend/Dockerfile` is CPU-only torch; `entrypoint.sh`
  runs `alembic upgrade head` then uvicorn on `$PORT`. Env vars, both set
  by hand in the dashboard: `DATABASE_URL` (Neon connection string) and
  `FRONTEND_ORIGIN` (the Vercel Production URL, else CORS blocks the
  browser). Health check: `/health`.
- **Frontend** → Vercel, root directory `frontend/`, one env var
  `NEXT_PUBLIC_API_BASE = https://<backend>.onrender.com` (baked at build
  time — set it before the first build, redeploy if you change it).
- **Seeding the hosted DB**: Neon starts empty. After the backend's first
  deploy has run `alembic upgrade head`, load the local snapshots once:
  `pg_dump --data-only --no-owner -t snapshots -t vol_points -t data_quality_reports "$LOCAL_URL" | psql "$NEON_URL"`.
- **Evaluation report staleness (known)**: `/api/evaluation` serves
  `backend/models/evaluation_SPY.json` **as baked into the Docker image**,
  not a live recompute. `/api/data-quality`, `/api/snapshots` and the
  surface endpoints all read Neon live, so between deploys the evaluation
  panel can lag the rest of the UI by a snapshot or two. The daily capture
  regenerates the file on the GitHub runner (writing to the DB, not to
  Render), so the served copy only moves when the image rebuilds. To
  refresh it deliberately: `PYTHONPATH=. python scripts/evaluate.py`
  against the Neon URL, commit the new JSON, let Render redeploy. A live
  recompute on each request is avoided on purpose — LOPO retrains the
  model 4+ times and would make every `/api/evaluation` hit multi-second
  on the free tier.
- **Daily capture**: `.github/workflows/daily_capture.yml`, two UTC cron
  triggers (21:00 and 22:00) so the capture lands at 17:00 ET year-round
  regardless of DST; the second run of the day is a harmless dedup no-op.

  ⚠️ **The workflow needs a `DATABASE_URL` repo secret** pointing at the
  same persistent Postgres the backend uses (the Neon connection string).
  Without it, the job falls back to a disposable CI Postgres and the
  snapshot is discarded when the container is torn down — which is exactly
  what happened to ~20 "successful" scheduled runs before this was caught.
  Scheduled runs now **hard-fail** when the secret is missing instead of
  silently losing data; `workflow_dispatch` self-tests still use the
  ephemeral DB.

### Run it locally

```bash
# 1. deps  (Python 3.11+; developed on 3.14)
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

# 2. Postgres
docker compose up -d

# 3. schema
cd backend && PYTHONPATH=. alembic upgrade head

# 4. tests
PYTHONPATH=. pytest -q

# 5. capture a snapshot  (needs 2+ on different days before ML code runs)
PYTHONPATH=. python3 scripts/daily_capture.py

# 6. backfill data-quality for snapshots captured before the DQ layer
PYTHONPATH=. python3 scripts/backfill_data_quality.py

# 7. recompute the honest model-vs-persistence report
PYTHONPATH=. python3 scripts/evaluate.py

# 8. API
PYTHONPATH=. uvicorn app.main:app --reload --port 8000

# 9. frontend  (separate terminal)
cd ../frontend && npm install && npm run dev   # http://localhost:3000
```

`backend/app/config.py` defaults already match `docker-compose.yml`
(`vol_user` / `vol_pass` / `vol_surface` on `localhost:5432`); a repo-root
`.env` overrides them (see `.env.example`).

---

## 12. Known limitations

- **Dataset size — the binding constraint.** 5 snapshots, 4 adjacent
  pairs, one genuine single-session move. No model conclusion is possible;
  nothing that needs history (PCA, walk-forward validation, regime
  detection, drift monitoring, ensembles) is implemented, on purpose —
  with this much data it would produce impressive-looking noise. See
  [§13](#13-what-more-data-would-unlock).
- **Data accumulation stalled 2026-08-16 → 2026-09-07.** Local cron
  failed silently (Postgres container down); GitHub Actions ran but wrote
  to an ephemeral DB (no `DATABASE_URL` secret). Both are fixed/guarded
  now, but the lost month is lost.
- **Non-consecutive pairs and non-trading-day captures.** The 4 pairs
  span 3, 36, 22 and 1 calendar days. Three of the five snapshots were
  captured on a weekend or holiday (ET) and hold the prior Friday's close,
  so the only clean next-session pair is #75→#315. The evaluation flags
  every multi-day gap and lists the weekend captures; `calendar_gap_days`
  is measured between capture timestamps, not trading sessions.
- **European BS on American options** — accepted, uncorrected.
- **Flat `r`, continuous trailing `q`, spot (not forward) moneyness** —
  see §5 / `docs/IV_AND_COORDINATES.md`.
- **No-arbitrage not enforced** on the surface — manual diagnostic only.
- **Post-close inversion failures.** Quotes are end-of-day, when spreads
  are widest and stalest; a handful of contracts per snapshot fail
  inversion outright and are counted (`inversion_failed`), not hidden.
- **yfinance is an unofficial API.** Retry/backoff covers transient rate
  limiting; a sustained block loses a day. No secondary source. Field
  units drift between library versions (the `q` bug).
- **Free-tier hosting**: the Render web service cold-starts after 15 min
  idle (first request ~30–60 s); the Neon free-tier database auto-suspends
  when idle, adding a few seconds to the first query after a pause.
- **Model is retrained inside the evaluation**, not loaded from a
  registry — fine at this scale, not a pattern to keep once training
  costs anything.

---

## 13. What more data would unlock

None of this is implemented — with 5 snapshots each item would add
precision it hasn't earned. It is the intended order of work once the
daily capture has run unbroken for a few months.

- **Walk-forward, expanding-window evaluation.** Replace LOPO with a
  causal split: train on pairs 1..k, test on pair k+1, step forward. A
  random split leaks — adjacent surfaces are ~0.99 autocorrelated, so a
  shuffled test pair is almost in the training set. Report the
  distribution of per-step (model − persistence) loss differentials, not
  just a pooled number, and run a sign test / Diebold-Mariano on it.
- **A statistical baseline between persistence and the net.** EWMA or
  AR(1) on ATM total variance, and a random-walk-with-drift on the first
  two or three surface PCs. If the neural model can't beat *these* it has
  no reason to exist; persistence alone is too low a bar to be
  interesting.
- **Arbitrage-aware construction.** Move surface fitting into
  total-variance space with calendar monotonicity (`w(k, T)` ↑ in `T`)
  and butterfly convexity enforced — SVI per slice with SSVI-style
  constraints — instead of separable PCHIP. Then the ~60% extrapolated
  wing can be a constrained fit rather than a flat clamp, and a
  no-arbitrage penalty can be added to the model loss.
- **Factor structure.** Once ~50+ observation days exist (roughly 5–10×
  the retained factor count), a PCA on the grid gives 3–4 stable
  factors — level, skew, curvature, term slope — and the forecasting
  problem shrinks to predicting a handful of factor scores, which is both
  more tractable and more interpretable than a 1600-cell map.
- **Regime stratification.** Bucket pairs by a VIX (or realised-vol)
  regime and check whether any model edge is uniform or comes entirely
  from one calm stretch. The current sample is a single low-vol summer.
- **Cheaper cuts at the inputs.** An intraday snapshot (e.g. 15:45 ET) to
  escape the widest, stalest end-of-day spreads; a short-rate curve
  bootstrapped from the bill/note ladder instead of one 13-week scalar;
  forward log-moneyness `ln(K/F_T)` as the stored coordinate so each
  smile's ATM point sits exactly at the forward. Each is a small,
  well-understood change deferred only because the dataset is the
  bottleneck.
