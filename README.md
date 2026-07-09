# Neural Volatility Surface Forecaster

A pipeline that captures SPY's implied volatility surface daily, stores it, and (once enough days have accumulated) trains a neural network to forecast how the surface will look the next trading day.

## Problem

An option chain gives you discrete (strike, expiry, price) quotes. Turning that into a smooth implied volatility surface — and then predicting how that surface moves one day forward — requires: inverting Black-Scholes on noisy market data, interpolating a sparse and irregular grid, accumulating a time series that doesn't exist anywhere for free (Yahoo Finance has no options history endpoint), and only then training a model. Most of the actual work is in the first three steps.

## Pipeline

```
yfinance (SPY chain)
    -> options_fetcher.py       raw calls/puts, all expiries, retried with backoff
    -> black_scholes.py         mid price -> implied vol via Brent's method
    -> daily_capture.py         liquidity filters, persists to Postgres (cron, once/day)
    -> surface_builder.py       scattered points -> dense (log-moneyness x TTE) grid, PCHIP
    -> app/ml/{dataset,model,train}.py   (surface_t, surface_t+1) pairs -> feedforward net
    -> FastAPI (routes.py)      /api/snapshots, /api/surface/{id}
    -> Next.js + Plotly         3D surface viewer
```

Each stage is runnable and testable independently — `surface_builder.py` doesn't care whether its input came from today's cron run or from six months of accumulated history.

## Stack

- **Data**: yfinance (free, no API key, no options history — hence the daily capture requirement)
- **Backend**: Python 3.14, FastAPI, SQLAlchemy 2.0
- **Storage**: PostgreSQL (Docker), raw `(strike, expiry, IV)` points rather than pre-built grids
- **Surface construction**: SciPy (PCHIP interpolation, Brent's method for IV inversion)
- **ML**: PyTorch, feedforward network (no RNN/Transformer — not justified until a simple baseline works)
- **Frontend**: Next.js 16 (App Router), Plotly.js for the 3D surface
- **Scheduling**: cron (macOS)

## Technical decisions

**yfinance's `impliedVolatility` field is not used.** On a live 0DTE chain, every ITM contract reported `impliedVolatility = 0.000010` — a floor value, not a real number. IV is instead computed in-house by inverting Black-Scholes (`scipy.optimize.brentq`) against the mid price `(bid+ask)/2`.

**Liquidity filtering removes about half the raw chain, and that's mostly by design.** On the snapshot used during development: 10,622 raw contracts in, 5,390 `VolPoint` rows out (~51%). Breakdown of what got dropped: 4,119 dropped by the OTM-only convention (calls kept only for strike ≥ spot, puts only for strike ≤ spot — this is deliberate de-duplication, not a data problem), 517 for expiring in under 2 days (0DTE/1DTE IV is too noisy to be useful), 318 for a missing/crossed bid-ask, 268 for a relative spread above 50%, 10 for zero open interest, and — notably — **0** contracts where Black-Scholes inversion itself failed to find a root. The upstream filters already catch what would otherwise break the inversion.

**PCHIP has no native 2D scattered-data form in SciPy**, so the surface is built in two passes: per-expiry PCHIP over log-moneyness (evaluated on a shared moneyness grid), then per-moneyness PCHIP over TTE across the actual expiries. Outside each pass's observed range, values are **clamped flat** rather than left to SciPy's default polynomial extrapolation, which is only shape-preserving *between* knots and can produce unstable or negative IV past the edges. Every clamped cell is flagged in a returned `extrapolated_mask` rather than silently blended in — the dashboard renders it at reduced opacity, and the ML dataset can use it to exclude low-confidence cells later.

**Grids must share a fixed axis before they can be used as training pairs.** `build_vol_surface`'s default behavior derives grid bounds from each snapshot's own observed strikes/expiries — fine for a single-day view, but two different days have different bounds, so flattening each grid independently would silently misalign coordinate `(i, j)` across days. `dataset.py` computes the intersection of every loaded snapshot's observed range and rebuilds each surface on that common grid via explicit `log_moneyness_range`/`tte_range` overrides before pairing. This was caught and fixed before any training code ran.

**Black-Scholes (European) is used to price American-style SPY options.** This is a known, accepted approximation — the bias is largest for deep ITM puts (unpriced early-exercise premium) and negligible for the OTM contracts the surface is actually built from, since OTM American and European option prices are close (early exercise is never optimal, or only marginally so, for options with no or low intrinsic value).

## Running locally

Requires Docker (for Postgres) and Python 3.11+ (developed and tested on 3.14).

```bash
# 1. Backend environment
cd neural-vol-surface
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# 2. Postgres
docker compose up -d

# 3. Capture a snapshot (needs 2+ snapshots on different days before ML code will run)
cd backend
PYTHONPATH=. python3 scripts/daily_capture.py

# 4. API
PYTHONPATH=. uvicorn app.main:app --reload --port 8000

# 5. Frontend (separate terminal)
cd ../frontend
npm install
npm run dev   # http://localhost:3000
```

Config defaults in `backend/app/config.py` already match `docker-compose.yml` (`vol_user`/`vol_pass`/`vol_surface` on `localhost:5432`) — no `.env` file is required to run locally. See `.env.example` for the overridable variables (ticker, rate ticker, liquidity thresholds).

### Daily capture via cron

```cron
TZ=America/New_York
0 17 * * 1-5 cd /path/to/neural-vol-surface/backend && PYTHONPATH=. /path/to/neural-vol-surface/.venv/bin/python3 scripts/daily_capture.py >> logs/cron_stdout.log 2>&1
```

Runs weekdays at 17:00 ET (after market close), using an absolute path to the venv's Python rather than `source activate` — cron's environment doesn't source shell rc files. `daily_capture.py` skips cleanly (no duplicate row) if a snapshot already exists for the current trading day, and retries yfinance calls with exponential backoff (30s/60s/120s) before giving up and logging a `CAPTURE FAILED` banner to `backend/logs/daily_capture.log`.

On macOS, cron also needs Full Disk Access (System Settings → Privacy & Security) and Docker Desktop needs "start at login" enabled, or the job will fail silently after a reboot.

## Repo structure

```
neural-vol-surface/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI app + CORS
│   │   ├── config.py                # env-driven settings, defaults match docker-compose
│   │   ├── ingestion/
│   │   │   ├── options_fetcher.py   # yfinance wrapper (spot, ^IRX, dividend yield, chain)
│   │   │   └── retry.py             # generic retry-with-backoff for flaky yfinance calls
│   │   ├── vol/
│   │   │   ├── black_scholes.py     # BS pricing + IV inversion (Brent's method)
│   │   │   └── surface_builder.py   # scattered points -> dense grid, separable PCHIP
│   │   ├── db/
│   │   │   ├── models.py            # Snapshot, VolPoint (SQLAlchemy 2.0)
│   │   │   └── session.py
│   │   ├── ml/
│   │   │   ├── dataset.py           # (day J, day J+1) pairs on a common grid
│   │   │   ├── model.py             # feedforward forecaster
│   │   │   └── train.py             # train/val loop, checkpoint to backend/models/
│   │   └── api/
│   │       └── routes.py            # GET /api/snapshots, GET /api/surface/{id}
│   ├── scripts/
│   │   └── daily_capture.py         # cron entrypoint
│   ├── logs/                        # daily_capture.log (persistent, gitignored)
│   └── requirements.txt
├── frontend/
│   ├── app/page.tsx                 # snapshot selector + surface viewer
│   ├── components/VolSurfacePlot.tsx  # Plotly 3D surface, extrapolated cells at reduced opacity
│   └── lib/{api,types}.ts
├── docker-compose.yml                # Postgres only
└── .env.example
```

## Known limitations / next steps

- **Dataset size**: as of writing, 1 snapshot exists. The ML code (`dataset.py`, `model.py`, `train.py`) is written and was verified end-to-end against a throwaway synthetic second snapshot, then deliberately not trained on real data — there isn't enough of it yet. Needs at least 5-7 daily snapshots before a training run means anything.
- **Model**: feedforward only, predicts the full next-day grid (not a residual/delta). No temporal architecture (LSTM/Transformer) until this baseline is validated against real accumulated data.
- **Single ticker**: SPY only; `VOL_SURFACE_TICKER` is configurable but the pipeline hasn't been run against anything else.
- **European BS on American options**: accepted approximation, see above — not corrected for early exercise.
- **`alembic` and `python-dotenv` are in `requirements.txt` but not wired up**: schema is created via `Base.metadata.create_all()`, no migrations exist yet; environment variables are read through `os.getenv` defaults in `config.py`, not an actual `.env` loader. Fine for a single-developer local project, would need fixing before this touches a second environment.
- **No automated tests**: `backend/tests/` exists but is empty. Correctness so far has been verified with targeted manual scripts run against real and synthetic data during development, not a test suite.
- **Yahoo Finance is an unofficial API**: retry-with-backoff handles transient rate-limiting, but a sustained block would still lose a day of data — there's no secondary data source.
- **Version control**: the repo root is not yet a git repository; `frontend/` has its own nested `.git` from `create-next-app`'s default init. Needs a single root-level `git init` (and removal of the nested one) before this is pushed anywhere.
