# 60–90 second demo script

Say this while clicking. It front-loads the honesty, which is the point.

---

**[0:00 — the question]**
"The question is narrow: can a small neural net predict tomorrow's SPY
implied-vol surface better than assuming it doesn't change. That last
part — persistence — is a hard baseline, because vol surfaces are nearly
a random walk day to day."

**[0:12 — the surface]** *(open the frontend, rotate the 3-D plot)*
"This is one snapshot. I pull the full SPY option chain after close,
invert Black-Scholes per contract with Brent's method to get implied
vol — yfinance's own IV field is a broken floor value — then build this
surface with shape-preserving PCHIP: separable, smile then term
structure. The faded cells are flat-clamp extrapolated; about 60% of the
default grid, all in the illiquid long-dated wing. They're marked so they
can be excluded downstream."

**[0:35 — data quality]** *(scroll to the data-quality table)*
"Every contract that enters the pipeline is accounted for in exactly one
bucket — kept, or a specific rejection reason, with the Black-Scholes
inversion failure rate broken out. This caught a live bug: yfinance
changed its dividend-yield units and my normaliser was passing through a
98% yield, which had pushed the inversion failure rate to about 40%.
Fixed; it's back to zero."

**[0:55 — the honest result]** *(scroll to the model-vs-persistence panel)*
"And here's the result, stated the only honest way: I have **4 snapshots
on non-consecutive days**, so **3 pairs**, two of which span three to five
weeks. On those, the model — leave-one-pair-out cross-validated — **loses
to persistence by 83%**. That number is basically variance. The API has a
hard 'not statistically significant' gate that won't flip until there are
20-plus clean next-day pairs. The pipeline is done and validated
end-to-end; the model is not, and I'm not going to pretend otherwise."

**[1:20 — close]**
"So what's actually finished is the hard part: a self-sustaining data
series that doesn't exist for free, defensible surface construction, and
an evaluation that can't be gamed. The model is a placeholder waiting for
data."

---

## If they want to see it run

```bash
cd backend
PYTHONPATH=. pytest -q                        # 54 tests, ~3s
PYTHONPATH=. python3 scripts/evaluate.py      # prints the honest verdict
PYTHONPATH=. uvicorn app.main:app --port 8000 # then hit /api/evaluation
```
