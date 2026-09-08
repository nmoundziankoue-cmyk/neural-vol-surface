# Neural Volatility Surface Forecaster — SPY

**▶ Live demo: https://REPLACE-ME.vercel.app**
&nbsp;·&nbsp; API: `https://REPLACE-ME.onrender.com` (`/api/evaluation`, `/api/data-quality`, `/api/surfaces/latest`)

> Hosted on free tiers (Render web service + Neon Postgres). The backend
> cold-starts after ~15 min idle — the first request can take 30–60 s, so
> if the page loads empty, wait and reload once. Neon also suspends an
> idle database, adding a few seconds to the first query after a pause.

## Research question

> Can a lightweight neural model predict tomorrow's SPY implied-volatility
> surface better than the persistence hypothesis (IV̂ₜ₊₁ = IVₜ)?

**Current answer: no, and — more importantly — the dataset is far too
small to answer it either way.** As of this writing the database holds
**4 snapshots on non-consecutive days**. On the 3 pairs that form, a
leave-one-pair-out–validated model has a masked RMSE of **0.0246 vs
persistence's 0.0135** (relative improvement **−82.9%**). That number is
not evidence about the model; it is a pipeline smoke test. See
[Baseline and results](#8-baseline-and-results).

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

Typical yield: ~9 800 raw contracts → ~5 000 `VolPoint` rows. `otm_side`
is ~40% of the drop and is deliberate de-duplication, not a data problem.

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

The model number is **leave-one-pair-out cross-validated**: each pair is
predicted by a model trained only on the *other* pairs, so it is
genuinely out-of-sample even on 3 pairs. (Fitting on all pairs and
scoring in-sample just reports the ~0 memorisation floor and tells us
nothing.)

Live numbers, recomputed after every capture (`scripts/evaluate.py`,
served at `GET /api/evaluation`):

| | value |
|---|---|
| Snapshots | 4 — ET dates 2026-07-08, 07-11, 08-16, 09-07* |
| Pairs | 3 — calendar gaps **3, 36 and 22 days** |
| Persistence masked RMSE (pooled) | **0.0135** |
| Model masked RMSE (LOPO, pooled) | **0.0246** |
| Relative improvement | **−82.9%** |
| Beats persistence? | **No** |
| Statistically significant? | **No** (hard gate: ≥ 20 next-trading-day pairs) |

<sub>* snapshot #75 was captured on the 2026-09-07 Labor Day holiday and
reflects the 2026-09-04 close.</sub>

**Honest reading:** with 3 pairs — two of which span multi-week gaps, so
"persistence" is being asked to hold a surface across 3–5 weeks rather
than one session — and cross-validation folds that train on 2 pairs,
nothing here supports a conclusion. The `statistically_significant` flag
in the API is a hard gate that will stay `false` until ≥ 20 clean
next-trading-day pairs exist. The pipeline is validated end to end; the
model is not.

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

Snapshots captured before this layer existed (all 4 current ones) carry a
`backfilled = true` flag: the derivable half (coverage, spreads, surface
cells) is filled from the stored points; the raw-chain rejection counts
are `null` because the raw chain isn't kept.

---

## 10. Tests

`cd backend && PYTHONPATH=. pytest` — **54 tests, ~2 s.** DB-backed tests
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
  `models/evaluation_SPY.json` is committed as a seed so `/api/evaluation`
  renders immediately; refresh it with `python scripts/evaluate.py` +
  redeploy.
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

- **Dataset size — the binding constraint.** 4 snapshots, 3
  non-consecutive pairs. No model conclusion is possible; nothing that
  needs history (PCA, walk-forward validation, regime detection, drift
  monitoring, ensembles) is implemented, on purpose — with this much data
  it would produce impressive-looking noise.
- **Data accumulation stalled 2026-08-16 → 2026-09-07.** Local cron
  failed silently (Postgres container down); GitHub Actions ran but wrote
  to an ephemeral DB (no `DATABASE_URL` secret). Both are fixed/guarded
  now, but the lost month is lost.
- **Non-consecutive pairs.** The 3 existing pairs span 3, 36 and 22
  calendar days. The evaluation flags every multi-day gap; a 36-day
  "persistence" step is not a next-session forecast.
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
