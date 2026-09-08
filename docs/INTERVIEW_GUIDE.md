# Interview guide

How to talk about each concept in this project: **what it is → why it
matters → how this project uses it → likely interview question → good
answer → follow-up they'll ask next.**

The honest framing to keep returning to: *this is a data-engineering +
surface-construction project with a forecasting scaffold that cannot yet
be evaluated, because there are 4 snapshots on non-consecutive days.*
Owning that is the point. See also
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
much worse than the baseline — important when you have 3 training pairs.

**Likely question.** "Why residual prediction here specifically?"

**Good answer.** With this little data, the residual formulation is a
regulariser: the model's default behaviour is persistence, and it can
only deviate by learning something from the (tiny) data. A direct `x → y`
model with 1.3M parameters and 3 examples would just memorise, and its
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
improvement over it. Current result: **−82.9%**, i.e. the model is worse.

**Likely question.** "Your model loses to persistence. Why is that in the
README?"

**Good answer.** Because the deliverable is the honest measurement
harness, not a winning model. Hiding a negative result would be the
actual red flag. And the −82.9% isn't "the model is 83% worse" — it's a
leave-one-pair-out number from 3 folds each trained on 2 pairs, so it's
almost pure variance. The correct statement is "there is not enough data
to evaluate the model", and the API's `statistically_significant` flag
encodes exactly that.

**Follow-up.** "When would a PCA / factor baseline be worth adding?" →
Once there are enough snapshots to estimate a covariance across grid
cells stably — very roughly 5-10× as many observation days as retained
factors, so ~50+ days for a 3-5 factor model. Below that the loadings are
noise and a 'PCA forecast' is theatre.

---

## 7. Masked RMSE and why extrapolated cells are excluded

**What it is.** RMSE computed only over grid cells where
`extrapolated_mask` is `False` — the cells built from real interpolated
quotes, not the flat clamp fill in the wings.

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
cells across all pairs, plus per-pair numbers with the cell count, so the
weighting is visible. With more data I'd fix a common evaluation region
(e.g. |k| < 0.15, T < 1y) that's observed on every day.

---

## 8. Why few snapshots caps the statistical validity

**What it is.** With 4 snapshots there are 3 consecutive pairs; two of
them span 22 and 36 calendar days. Any RMSE comparison is one draw from a
distribution with enormous variance.

**Why it matters.** You cannot separate skill from luck. There's no
held-out test set. There's no coverage of different vol regimes (the
whole sample is one summer). And the multi-day gaps mean "persistence" is
being asked to hold a surface for weeks — a different, weaker baseline
than the one-day-ahead question.

**How this project uses it.** `evaluate.py` has a **hard gate**:
`statistically_significant` is `False` unless there are ≥ 20 pairs *and*
every pair is a next-trading-day pair. It's not a p-value — it's a
refusal to call anything significant until the data exists. The verdict
string and `caveats` list say why in plain language, and the frontend
shows a "not significant" banner.

**Likely question.** "How many snapshots would you actually need?"

**Good answer.** A floor of ~20-30 consecutive-day pairs to get a
persistence-relative RMSE with a usable confidence interval — and that's
just for a point estimate. To *trust* a model that beats persistence I'd
want several hundred days spanning at least one high-vol and one low-vol
regime, evaluated walk-forward. Right now I have effectively one usable
pair.

**Follow-up.** "So why build the model at all now?" → To make the
pipeline end-to-end and the evaluation honest *before* the data arrives,
so that when it does, the only thing that changes is the numbers — not
the methodology, and not the incentives to fudge them.

---

## 9. Detecting overfitting with almost no data

**What it is.** Knowing whether the model has memorised its 3 pairs
rather than learned anything.

**Why it matters.** A `512-256-512` net has ~1.3M parameters. Three
1600-dim training pairs. In-sample it will hit ≈ 0 RMSE — that number is
meaningless.

**How this project uses it.**
- **Leave-one-pair-out cross-validation** in `evaluate.py`: each pair is
  predicted by a model trained only on the others. The reported model
  RMSE is that out-of-sample number, not the in-sample one.
- **The comparison to persistence is the overfitting test.** Out of
  sample the model scores −82.9% vs persistence → it is overfitting /
  underdetermined, full stop.
- **The residual architecture** bounds the damage: worst case it drifts
  from persistence rather than from zero.
- **Parameter-to-sample ratio** is stated openly as a caveat.

**Likely question.** "How do you know it's overfitting and not just a bad
architecture?"

**Good answer.** I can't fully separate those two with 3 pairs — that's
the point. But the LOPO error being ~2× persistence, while in-sample
error is ~0, is the classic memorisation signature. With more data the
diagnostic would be a learning curve (train vs walk-forward error vs
number of pairs) — if the gap doesn't close as data grows, it's capacity;
if it does, it was data starvation.

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

**1. "Your model loses to persistence by 83%. Why should I take this
project seriously?"**
Because the project's contribution is the honest pipeline and evaluation,
not the model. The −83% is a leave-one-pair-out figure from 3 folds
trained on 2 pairs each — it's variance, not a measurement. The correct
statement, which the API and README both make, is "insufficient data to
evaluate". A candidate who showed a model *beating* persistence on 3
non-consecutive pairs and presented it as a result would be the concern.

**2. "Two of your three pairs span 22 and 36 days. Persistence over a
month is a straw man — and you still lose to it."**
Correct on both counts, and I say so in the evaluation output. Only the
one 3-day pair is a fair one-session test, and n = 1 there. The multi-day
pairs are in the dataset because they're the only data that exists; the
evaluation flags every gap and the significance gate rejects the whole
set.

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
the binding constraint when the dataset is 4 points.

**5. "Quantify the European-vs-American pricing error for SPY."**
Early exercise is essentially never optimal for OTM options, so on the
OTM-only points the surface is built from, the premium is a bp or two of
IV. It grows for ITM puts (forgone interest + dividend capture) and would
be worst for deep ITM puts — which are exactly what the OTM-side filter
removes. So the bias on kept points is small; the filter is doing double
duty.

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
I refuse it *now* because with 4 snapshots any drift statistic is noise
and would add false precision. With ~60+ snapshots: track the rolling
persistence RMSE (a spike = the surface moved a lot, i.e. a regime
event) and the rolling model-minus-persistence delta (widening = model
decaying). Both are one line on top of the evaluation I already compute.
Adding them today would be exactly the "impressive-looking noise" the
project is trying not to ship.

**9. "The evaluation retrains the model on every run. Non-deterministic
and slow — why is that acceptable?"**
It's deterministic — fixed seed, fixed epoch count, full-batch. It's a
few seconds, so it's computed by the capture job and cached to
`models/evaluation_SPY.json`; the API just serves the file. At any real
scale, training becomes a separate job producing a versioned artifact,
and the evaluation loads that artifact rather than refitting. At 3 pairs,
refitting in-process is simpler and the cost is zero.

**10. "You get 2 years of clean daily data tomorrow. What do you check
before believing a model that beats persistence?"**
Walk-forward, expanding-window evaluation — never a random split, because
day-to-day autocorrelation leaks the test set into training. Then: does
it beat persistence in *every* sub-period or just on average? Does it
also beat a cheap statistical baseline (EWMA / AR(1) on ATM total
variance), not just naive persistence? Is the RMSE edge larger than the
bid-ask-implied noise floor? And does the edge survive stratification by
VIX regime, or is it all coming from one calm stretch? Only if it clears
all of those would I call it a result.
