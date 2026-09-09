# Interview guide

How to talk about each concept in this project: **what it is → why it
matters → how this project uses it → likely interview question → good
answer → follow-up they'll ask next.**

The honest framing to keep returning to: *this is a data-engineering +
surface-construction project with a forecasting scaffold that cannot yet
be evaluated. There are 5 snapshots; three landed on a weekend or holiday
and carry the prior Friday's close, so only one of the four adjacent
pairs (#75→#315) is a true next-session move.* Owning that is the point.
Numbers below are the 2026-09-09 recompute; the live figure is
`GET /api/evaluation`. See also
[IV_AND_COORDINATES.md](IV_AND_COORDINATES.md).

---

## 1. Implied volatility and why the Black-Scholes inversion is numerical

**What it is.** Implied volatility is the single σ that, put into the
Black-Scholes formula, makes the model price equal the observed market
price. It's the market's quote *expressed in vol units* instead of price
units.

**Why it matters.** Prices aren't comparable across strikes and
maturities; IVs are. The whole surface, the skew, every greek and every
risk number is computed off IV, not off raw premiums.

**How this project uses it.** `app/vol/black_scholes.py` prices European
BS with a continuous dividend yield and inverts it for σ with Brent's
method (`scipy.optimize.brentq`) on the end-of-day mid price, bracketed
on `[1e-4, 5.0]`.

**Likely question.** "Why do you need a root-finder — why not just solve
for σ?"

**Good answer.** BS price as a function of σ has no closed-form inverse.
It *is* strictly increasing in σ (vega > 0), so a root exists and is
unique whenever the price is inside the no-arbitrage band — which makes
it a well-posed 1-D root-find. I use Brent because it's a bracketing
method: given a sign change on `[lo, hi]` it's guaranteed to converge,
with superlinear speed. Newton-Raphson would need vega and can diverge
for deep ITM/OTM options where vega ≈ 0 and the price is nearly flat in
σ.

**Follow-up.** "What do you do when there's no root?" → The mid is
outside `[bs(σ_lo), bs(σ_hi)]` — typically below intrinsic or an
arbitrage-violating quote. `implied_volatility` returns `None`, the
contract is counted as `inversion_failed` in the data-quality report, and
it never silently becomes a data point.

---

## 2. The volatility smile and skew

**What it is.** IV plotted against strike (or moneyness) for a fixed
expiry is not flat. In equity indices it slopes **down** to the
left — low strikes have higher IV. That monotone shape is the *skew*; a
symmetric U (typical in FX) is the *smile*. Both flatten as maturity
grows.

**Why it matters.** A flat IV would mean BS is literally true and returns
are lognormal. The skew is the market pricing in fat left tails and the
leverage effect (vol rises when the index falls), plus persistent
hedging demand for downside puts.

**How this project uses it.** The per-expiry PCHIP pass in
`surface_builder.py` interpolates exactly this curve, using OTM puts on
the left and OTM calls on the right stitched at spot.

**Likely question.** "Why is the equity skew negative and roughly linear
in log-moneyness, not a smile?"

**Good answer.** Three reinforcing effects: (1) leverage / balance-sheet
— a falling index raises leverage and realised vol, so low strikes carry
higher IV; (2) crash-risk aversion — investors pay up for OTM puts as
tail insurance; (3) supply/demand — structurally more put buyers
(protection) than sellers. Index options are dominated by the downside,
so you get a skew rather than a symmetric smile.

**Follow-up.** "What happens to the skew as T → 0 and as T → ∞?" → Very
short-dated: skew steepens and becomes highly convex (jump/gap risk
dominates, IV can spike near pinning strikes) — which is exactly why this
project drops < 2-DTE contracts. Long-dated: it flattens toward a level,
as the central limit effect averages out path-dependent skew.

---

## 3. PCHIP, and why not a natural cubic spline

**What it is.** PCHIP = Piecewise Cubic Hermite Interpolating Polynomial.
It fits a cubic on each interval with derivatives chosen (Fritsch-Carlson
rule) so the interpolant is **monotone on any interval where the data is
monotone** and never overshoots. It's C¹, not C².

**Why it matters.** A natural cubic spline is C² and minimises global
curvature, but to do that it *overshoots* near kinks and rings
(oscillates) — and it's non-local, so one noisy quote wiggles the curve
far away. On an IV smile an overshoot can produce a local IV bump that
isn't there, or even a negative IV in the wing — both are arbitrage.

**How this project uses it.** Both interpolation passes are PCHIP. Past
the last knot, extrapolation is **flat clamp**, not the boundary cubic,
because PCHIP's shape guarantee only holds *between* knots.

**Likely question.** "Why PCHIP over a cubic spline for a vol surface?"

**Good answer.** For an IV curve I care more about "no spurious
non-monotonicity and no negative values" than about C² smoothness.
PCHIP's shape-preservation gives me that by construction; a natural cubic
spline doesn't — it can overshoot between kinked, noisy end-of-day
quotes and it propagates a bad point globally. The cost is a visible
kink in the second derivative at knots, which is acceptable for a
visualisation and a coarse grid.

**Follow-up.** "PCHIP still isn't arbitrage-free. What would be?" → Right
— PCHIP controls shape locally but doesn't enforce calendar or butterfly
no-arbitrage. An arbitrage-free construction means fitting in total-
variance space with monotonicity in T and convexity in K enforced —
e.g. SVI per slice with SSVI-style constraints, or a constrained global
fit. That's the natural next step once there's enough data to fit
parameters stably.

---

## 4. Log-moneyness / delta as surface coordinates

**What it is.** The x-axis of the surface. Options:
- **raw strike K** — not comparable across spot levels or across days;
- **spot log-moneyness** `k = ln(K/S)` — centred at 0, roughly stationary;
- **forward log-moneyness** `ln(K/F)`, `F = S·e^{(r−q)T}` — same, but ATM
  (k = 0) sits exactly at the forward, removing rate/dividend drift;
- **delta** — maps every option to `(0, 1)`, is how OTC vol is actually
  quoted, but it depends on σ itself (circular) and needs a vol to invert.

**Why it matters.** A model or an interpolation that works in K is
learning the spot level, not the surface shape. Moneyness coordinates
make cell `(i, j)` mean the same economic thing on different days, which
is what lets you difference two days or pair them for training.

**How this project uses it.** Spot log-moneyness `ln(K/S)`, ACT/365
calendar TTE. Stored on every `VolPoint`. The `k = 0` column is therefore
`≈ (r−q)T` away from ATM-forward — under one grid cell at index tenors,
documented as an approximation.

**Likely question.** "You use spot, not forward, moneyness. What does
that cost you?"

**Good answer.** A small horizontal misalignment: the ATM point of each
smile is offset by `ln(F/S) = (r−q)T`, about 0.014 in k at six months.
That's well under my grid spacing (~0.08), so for interpolation and a
coarse forecast it's negligible. It would matter for a parametric slice
fit (SVI), where the ATM location is a fitted parameter — there I'd
switch the stored coordinate to `ln(K/F)`. It's a one-line change.

**Follow-up.** "Why not delta coordinates?" → Delta is the natural
quoting axis and compresses the wings nicely, but it's σ-dependent — you
need a vol to compute the delta you'd index by, which is circular during
construction — and it makes the coordinate non-stationary in a different
way (the same k maps to different deltas as vol moves). For a first
surface, moneyness is cleaner.

---

## 5. Delta (residual) prediction vs full-surface reconstruction

**What it is.** The model is `forward(x) = x + Δ_net(x)` — it predicts the
*change* from today's surface, and the prediction is today's surface plus
that change. The alternative is a plain map `x → y` that outputs
tomorrow's surface directly.

**Why it matters.** A vol surface is highly autocorrelated day to day —
`corr(σₜ, σₜ₊₁)` is very close to 1. The day-over-day *change* has a much
smaller dynamic range than the surface itself. Predicting the residual
means the model starts from a strong, correct prior (the identity map =
persistence) and only has to learn the small corrections. Predicting `y`
directly spends most of the network's capacity re-encoding the surface
shape it was already handed in `x`.

**How this project uses it.** `app/ml/model.py` — a single residual
connection around a `512-256-512` feedforward `Δ_net`. If `Δ_net` learns
to output ≈ 0, the model *is* persistence, so it structurally cannot do
much worse than the baseline — important when you have 4 training pairs.

**Likely question.** "Why residual prediction here specifically?"

**Good answer.** With this little data, the residual formulation is a
regulariser: the model's default behaviour is persistence, and it can
only deviate by learning something from the (tiny) data. A direct `x → y`
model with 1.3M parameters and 4 examples would just memorise, and its
untrained behaviour is arbitrary rather than a sensible baseline.

**Follow-up.** "Isn't a feedforward net on a flattened 1600-vector
throwing away the 2-D structure?" → Yes. A conv or graph structure over
the (moneyness, TTE) grid, or a per-slice parametric model, would respect
locality and need far fewer parameters. I didn't build that because
there's no data to justify tuning it — the honest baseline to beat first
is persistence with a feedforward residual, and that hasn't been cleared.

---

## 6. The persistence baseline and why it's non-negotiable

**What it is.** `IV̂ₜ₊₁ = IVₜ` — predict "no change".

**Why it matters.** Vol surfaces are near-random-walk over one day. "No
change" is a genuinely hard baseline. An RMSE number in isolation is
meaningless — 0.01 could be excellent or terrible. The only interpretable
statement is *relative to persistence*: `(RMSE_persist − RMSE_model) /
RMSE_persist`. If a model can't beat persistence, it has learned nothing
useful, no matter how low its absolute error looks.

**How this project uses it.** It's the *only* baseline (no PCA baseline —
not enough data). `app/ml/evaluate.py` computes persistence masked RMSE
per pair and pooled, and every model number is reported as a relative
improvement over it. Current result: **−51.4%** (model masked RMSE 0.0198
vs persistence 0.0131), i.e. the model is worse.

**Likely question.** "Your model loses to persistence. Why is that in the
README?"

**Good answer.** Because the deliverable is the honest measurement
harness, not a winning model. Hiding a negative result would be the
actual red flag. And the −51.4% isn't "the model is 51% worse" as a
stable fact — it's a leave-one-pair-out number from 4 folds each trained
on 3 non-independent pairs, on a sample where only one pair is a real
next-session move. It's almost pure variance: the previous recompute, one
snapshot ago, read −82.9%. The correct statement is "there is not enough
data to evaluate the model", and the API's `statistically_significant`
flag encodes exactly that.

**Follow-up.** "When would a PCA / factor baseline be worth adding?" →
Once there are enough snapshots to estimate a covariance across grid
cells stably — very roughly 5-10× as many observation days as retained
factors, so ~50+ days for a 3-5 factor model. Below that the loadings are
noise and a 'PCA forecast' is theatre.

---

## 7. Masked RMSE and why extrapolated cells are excluded

**What it is.** RMSE computed only over grid cells where
`extrapolated_mask` is `False` — cells built from real quotes, i.e.
observed points and the PCHIP interpolation strictly between them, but
**not** the flat clamp fill past the last knot in the wings. Interpolated
cells count; extrapolated ones don't.

**Why it matters.** ~60% of the default 40×40 grid is clamp-extrapolated
(a constant value copied from the boundary). Both persistence *and* any
model reproduce a constant region for free. Include those cells and you
dilute every RMSE toward zero and inflate apparent skill — the model
looks good because it "got right" a bunch of cells that were trivially
constant.

**How this project uses it.** `app/ml/metrics.py::masked_rmse`. The mask
is keyed on the **target** day (t+1): it asks "is the ground truth we're
scoring against real data or fill?". `dataset.py` propagates the
smile-pass mask into the term-pass mask so a cell that's interpolated on
one axis but extrapolated on the other still counts as extrapolated.

**Likely question.** "Why mask on the target day and not the input day?"

**Good answer.** The RMSE measures error against ground truth. If the
ground-truth cell is itself a fabricated clamp value, scoring against it
is meaningless regardless of what the input looked like. So the mask that
matters is the target's.

**Follow-up.** "Doesn't masking make the metric non-comparable across
days, since the mask changes?" → Yes, slightly — different days expose
different numbers of real cells. I report the pooled RMSE over all real
cells across all pairs (`sqrt(mean(squared error))` over the union, not
the mean of per-pair RMSEs), plus per-pair numbers with the cell count,
so the weighting is visible. There's a second-order effect too: the
evaluation grid is the *intersection* of every loaded snapshot's observed
range, so adding one snapshot can shift every pair's scored region and
move all the RMSEs a little — which is part of why the pooled figure
jumped from −82.9% to −51.4% when snapshot #315 arrived. With more data
I'd fix a common evaluation region (e.g. |k| < 0.15, T < 1y) observed on
every day and score only inside it.

---

## 8. Why few snapshots caps the statistical validity

**What it is.** 5 snapshots → 4 chronologically-adjacent pairs; two span
22 and 36 calendar days, and three of the five captures landed on a
weekend/holiday so they hold the prior Friday's close. Only #75→#315 is a
real one-session move. Any RMSE comparison is one draw from a distribution
with enormous variance.

**Why it matters.** You cannot separate skill from luck. There's no
held-out test set. There's no coverage of different vol regimes (the
whole sample is one summer). And the multi-day gaps mean "persistence" is
being asked to hold a surface for weeks — a different, weaker baseline
than the one-day-ahead question.

**How this project uses it.** `evaluate.py` has a **hard gate**:
`statistically_significant` is `False` unless there are ≥ 20 pairs *and*
every pair is a next-trading-day pair. It's not a p-value — it's a
refusal to call anything significant until the data exists. The verdict
string and `caveats` list say why in plain language (including which
snapshots were weekend captures), and the frontend shows a "not
significant" banner.

**Likely question.** "How many snapshots would you actually need?"

**Good answer.** A floor of ~20-30 consecutive-day pairs to get a
persistence-relative RMSE with a usable confidence interval — and that's
just for a point estimate. To *trust* a model that beats persistence I'd
want several hundred days spanning at least one high-vol and one low-vol
regime, evaluated walk-forward. Right now I have one usable pair.

**Follow-up.** "Why leave-one-pair-out rather than a single holdout, with
so few pairs?" → A single holdout would spend a quarter of an already
tiny sample on one test point, and the estimate would swing entirely on
*which* pair I held out — the pair-to-pair RMSE spread here is roughly 3×.
LOPO uses every pair as the test exactly once and averages, which is the
lowest-variance almost-unbiased estimator available at this n; k-fold
with k < n just trains on even less with no offsetting benefit. It's
still barely worth computing — each fold trains on three pairs — and it
isn't clean: adjacent pairs share a snapshot (pair *i*'s *t+1* is pair
*i+1*'s *t*), so a fold's test pair overlaps its training pairs by one
surface and the LOPO number is mildly optimistic. Leave-one-*snapshot*-out
would be stricter; with four pairs it collapses to almost nothing, so I
report LOPO and flag the leakage in the caveats rather than pretend it
away.

**Follow-up.** "So why build the model at all now?" → To make the
pipeline end-to-end and the evaluation honest *before* the data arrives,
so that when it does, the only thing that changes is the numbers — not
the methodology, and not the incentives to fudge them.

---

## 9. Detecting overfitting with almost no data

**What it is.** Knowing whether the model has memorised its 4 pairs
rather than learned anything.

**Why it matters.** A `512-256-512` net has ~1.3M parameters. Four
1600-dim training pairs. In-sample it will hit ≈ 0 RMSE — that number is
meaningless.

**How this project uses it.**
- **Leave-one-pair-out cross-validation** in `evaluate.py`: each pair is
  predicted by a model trained only on the others. The reported model
  RMSE is that out-of-sample number, not the in-sample one.
- **The comparison to persistence is the overfitting test.** Out of
  sample the model scores −51.4% vs persistence → it is overfitting /
  underdetermined, full stop.
- **The residual architecture** bounds the damage: worst case it drifts
  from persistence rather than from zero.
- **Parameter-to-sample ratio** is stated openly as a caveat.

**Likely question.** "How do you know it's overfitting and not just a bad
architecture?"

**Good answer.** I can't fully separate those two with 4 pairs — that's
the point. But the LOPO error being ~1.5× persistence, while in-sample
error is ~0, is the classic memorisation signature. With more data the
diagnostic would be a learning curve (train vs walk-forward error vs
number of pairs) — if the gap doesn't close as data grows, it's capacity;
if it does, it was data starvation.

**Follow-up.** "Once you have 20+ pairs, what tells a model that *works*
apart from a variance artifact?" → No single test — a stack that has to
clear together: (1) it beats persistence out-of-sample in a large
majority of walk-forward steps, not just on pooled RMSE — a sign test or
Diebold-Mariano on the per-step loss differential; (2) the RMSE edge
exceeds the bid-ask noise floor (compare it to the median half-spread
expressed in vol points — an edge smaller than the quote noise isn't
real); (3) it also beats a cheap statistical baseline — EWMA / AR(1) on
ATM total variance, random-walk-with-drift on the top PCA factors — not
just naive persistence; (4) the edge survives stratification by VIX
regime instead of coming entirely from one calm stretch; (5) the
learning curve shows the train/test gap closing as pairs accumulate
(data starvation) rather than plateauing (capacity/misspecification);
(6) the predicted daily change has sane sign and magnitude around known
catalysts (Fed days, CPI). Any one of those can be luck; the bar is
clearing most of them at once, on data the model never touched in
training.

**Follow-up.** "What single change would most reduce overfitting risk
here?" → Fewer parameters that respect the grid structure — a small
conv/graph model or a per-slice SVI fit with a handful of parameters per
maturity — plus early stopping against a walk-forward split. But none of
that is worth tuning until persistence is beaten by *something* simple.

---

## 10. No-arbitrage: calendar and butterfly

**What it is.** Two static-arbitrage constraints on a surface:
- **Calendar**: total implied variance `w(k, T) = σ²(k, T)·T` must be
  non-decreasing in `T` at fixed log-moneyness. If a shorter option has
  more total variance than a longer one, you can sell the near and buy
  the far for a locked-in profit.
- **Butterfly**: the undiscounted call price must be convex and
  decreasing in strike, so the implied risk-neutral density
  `∂²C/∂K² ≥ 0`. In IV space this is a bound on the smile's slope and
  curvature (the "g(k) ≥ 0" condition in SVI).

**Why it matters.** A surface that violates either lets someone construct
a portfolio with negative cost and non-negative payoff. Any model or
interpolation that isn't constrained can produce one, especially in the
extrapolated wings.

**How this project uses it.** It **doesn't enforce it** — this is stated
as a limitation. The PCHIP flat-clamp keeps the wings monotone-ish and
non-negative, which avoids the crudest butterfly violations, but there's
no calendar check and no density-positivity check. It's a manual
diagnostic: you can eyeball `w(k, ·)` monotonicity on the grid.

**Likely question.** "Your surface can contain arbitrage. Does that
matter for a forecasting project?"

**Good answer.** For the forecasting *question* — beat persistence on
RMSE — it matters less, because both the input and target surfaces are
built the same way, so a small consistent bias partly cancels. It would
matter a lot if the surface were used to price anything. The right fix is
to move construction into total-variance space with calendar
monotonicity and butterfly convexity enforced — SVI/SSVI per slice — and
optionally add an arbitrage penalty to the model's loss so its
predictions stay in the no-arbitrage set. That's deferred until there's
data to fit SVI parameters stably.

**Follow-up.** "How would you *measure* how arbitrageable your current
surfaces are?" → Count grid cells where `w(k, Tⱼ₊₁) < w(k, Tⱼ)` (calendar
violations) and where the discrete second difference of call price in K
goes negative (butterfly violations), per snapshot, and put it in the
data-quality report. Cheap to add, and it would quantify the problem
instead of hand-waving it.

---

# The 10 hardest questions on *this* project

Honest answers that own the limitations rather than hide them.

**1. "Your model loses to persistence by 51%. Why should I take this
project seriously?"**
Because the project's contribution is the honest pipeline and evaluation,
not the model. The −51% is a leave-one-pair-out figure from 4 folds
trained on 3 non-independent pairs each — it's variance, not a
measurement, and it read −83% one snapshot ago. The correct statement,
which the API and README both make, is "insufficient data to evaluate". A
candidate who showed a model *beating* persistence on four
non-consecutive pairs and presented it as a result would be the concern.

**2. "Two of your four pairs span 22 and 36 days, and three of your five
snapshots were captured on non-trading days. Persistence over a month is
a straw man — and you still lose to it."**
Correct on every count, and the evaluation output says so. The weekend
and holiday captures (#23 Sat, #54 Sun, #75 Labor Day) hold the previous
Friday's close, so `calendar_gap_days` — which is measured between capture
timestamps — understates the real session gap; pair #1→#23 is tagged
`is_next_trading_day` even though its content is Wed→Fri. The only clean
one-session pair is #75→#315 (Fri 09-04 close → Tue 09-08 close across
Labor Day), n = 1, and it's the pair the model does *worst* on (0.0263 vs
0.0123). The multi-day pairs are in the dataset because they're the only
data that exists; the evaluation flags every gap, lists the weekend
captures, and the significance gate rejects the whole set.

**3. "You invert Black-Scholes on end-of-day mid prices. Isn't that the
worst possible time — stale quotes, widest spreads?"**
Yes. EOD is when bid-ask is widest and quotes are most stale, and it's
the main driver of the handful of inversion failures per snapshot and of
smile noise. It's a consequence of a free-data pipeline that can only run
after close. The data-quality report surfaces the inversion failure rate
and the spread distribution so the noise is visible, not buried. A
better version would snapshot intraday (e.g. 15:45 ET) or use a
liquidity-weighted price.

**4. "A single 13-week T-bill rate for a 2-day option and an 18-month
option. Defend that."**
It's indefensible as *exact* and I label it an approximation. The impact:
the discount factor is wrong for long maturities, biasing their IV by a
fraction of a vol point (the rate error at 18M might be 50-100bp, and IV
sensitivity to r is small for near-ATM options). The fix is bootstrapping
a short-rate curve from the bill/note ladder — straightforward, just not
the binding constraint when the dataset is 5 points.

**5. "Quantify the European-vs-American pricing error for SPY."**
Direction first: the American price ≥ the European price, so treating an
American quote as European makes `bs_price(σ)` too cheap at the true σ,
and the solver pushes σ **up** to compensate — the inversion
over-estimates IV, by more where the early-exercise premium is larger.

Bound it by wing:
- **OTM calls (K > S).** Early exercise of a call is only rational to
  capture a dividend that exceeds the interest earned by deferring the
  strike payment. SPY pays ~1.2%/yr in four discrete dividends (~0.3% of
  spot each); with `r ≈ 4%` the carry never favours early exercise for an
  out-of-the-money call. Premium ≈ 0; IV bias sub-bp.
- **OTM puts (K < S).** American puts carry an early-exercise premium
  (forgone interest on the strike), but early exercise is only optimal
  below a critical price well *under* K — the option has to go
  substantially ITM first. For a contract that is currently OTM the
  premium is second-order: the value of a right you can only use after
  the surface has moved a long way against you. On a ~1.2%-yield
  underlying at `r ≈ 4%`, that's a few bps of IV for short-dated OTM
  puts, growing with maturity (more time to reach the exercise region)
  toward maybe 20–40 bps for `T ≈ 1y` puts near the money. The crude
  `K(1 − e^{−rT})` bound (~1% of K at 3 months) is far too loose to be
  useful here — it's near the whole premium of a short-dated OTM put.
- **ITM (both sides).** The premium is largest here — tens of bps to a
  vol point for deep ITM puts — but the OTM-side filter drops every ITM
  contract before inversion, so none of these reach the surface. That
  filter is doing double duty: de-duplication *and* removing the
  contracts where the European approximation is worst.

How I'd actually put a number on it rather than argue bounds: take a
sample of kept contracts, reprice each with a Cox-Ross-Rubinstein
American binomial tree (~500–1000 steps) at the σ the European inversion
produced, take the price gap, and re-invert the European formula on
`european_price + gap` to get ΔIV per contract. Aggregate ΔIV by
moneyness/TTE bucket and add it to the data-quality report. Expected
result for the OTM wing the surface is built from: a few bps of IV,
i.e. well inside the EOD quote noise — which is why it's accepted
uncorrected.

**6. "Your PCHIP is separable — smile then term structure. What does the
non-jointness cost you?"**
The term-structure pass interpolates each moneyness column independently,
so it ignores that different expiries cover different moneyness ranges. A
cell can be 'interpolated' along T but built from a value that was
smile-*extrapolated* at the near expiry. I mitigate this by OR-ing the
smile mask into the term mask, so such a cell is still marked
extrapolated and excluded from RMSE. A joint 2-D fit (thin-plate spline,
GP, or arb-free SVI term structure) would be principled but loses the
shape-preservation guarantee.

**7. "60% of your grid is extrapolated. In what sense is this a
'surface'?"**
The honest surface is the ~40% observed region — roughly |k| < 0.2 and
T < 1y, where SPY options are liquid. The rest is a flat-clamped visual
completion, drawn at low opacity in the dashboard and excluded from every
metric. I'd rather show a small honest surface with the fill visibly
marked than a full one that pretends the wings are data.

**8. "You explicitly refuse to add drift/regime monitoring. How would you
know your model has gone stale in production?"**
I refuse it *now* because with 5 snapshots any drift statistic is noise
and would add false precision. With ~60+ snapshots: track the rolling
persistence RMSE (a spike = the surface moved a lot, i.e. a regime
event) and the rolling model-minus-persistence delta (widening = model
decaying). Both are one line on top of the evaluation I already compute.
Adding them today would be exactly the "impressive-looking noise" the
project is trying not to ship.

**9. "The evaluation retrains the model on every run. Non-deterministic
and slow — why is that acceptable?"**
It's deterministic — fixed seed, fixed epoch count, full-batch, a few
seconds — and it is *not* on the request path. `scripts/evaluate.py`
writes `models/evaluation_SPY.json`, and `GET /api/evaluation` just
serves that file. Trade-off I made explicitly: on the deployed backend
the file is the copy **baked into the Docker image**, so it only refreshes
when the image rebuilds (a deploy), not when the daily capture writes a
new snapshot to the DB — the other endpoints read the DB live, so the
evaluation panel can lag them by a snapshot or two. I chose that over a
live recompute because LOPO retrains the model N times and would make
every `/api/evaluation` hit multi-second on a free-tier box with no
caching. At real scale, training is a separate job emitting a versioned
artifact and the evaluation loads it; at four pairs, a committed JSON
refreshed on deploy is the honest minimum.

**10. "You get 2 years of clean daily data tomorrow. What do you check
before believing a model that beats persistence?"**
Walk-forward, expanding-window evaluation — never a random split, because
day-to-day autocorrelation leaks the test set into training. Then: is the
per-step (model − persistence) loss differential negative in a large
majority of steps, not just on the pooled mean (sign test /
Diebold-Mariano, since one big week can carry a pooled RMSE)? Does it also
beat a cheap statistical baseline (EWMA / AR(1) on ATM total variance,
RW-with-drift on the top PCs), not just naive persistence? Is the RMSE
edge larger than the bid-ask-implied noise floor? Does a learning curve
show the train/walk-forward gap *closing* as data accumulates rather than
plateauing? And does the edge survive stratification by VIX regime, or is
it all from one calm stretch? Only if it clears all of those would I call
it a result. (Same checklist, condensed, in §9's follow-up.)
