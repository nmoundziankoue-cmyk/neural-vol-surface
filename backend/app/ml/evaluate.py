"""Honest model-vs-persistence evaluation.

The only baseline is persistence: IV_hat(t+1) = IV(t). No PCA, no PCA
forecast - there is nowhere near enough history for either to mean
anything.

The model number is produced by leave-one-pair-out (LOPO)
cross-validation: each pair is predicted by a model trained only on the
other pairs, so the model RMSE is genuinely out-of-sample even on a tiny
dataset. Fitting on all pairs and scoring in-sample would just report the
memorization floor (~0), which tells us nothing.

The headline number is the relative improvement of the neural model over
persistence on masked RMSE:

    rel_improvement = (rmse_persistence - rmse_model) / rmse_persistence

`statistically_significant` is a hard gate, not a p-value: it is False
unless there are enough next-trading-day pairs to support a conclusion.
With the current handful of non-consecutive snapshots it is always False,
and `caveats` says exactly why. Nothing here is massaged - if the model
loses to persistence, `model_beats_persistence` is False and the verdict
says so.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import torch
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Snapshot
from app.db.session import SessionLocal
from app.ml.dataset import InsufficientSnapshotsError, load_training_pairs
from app.ml.metrics import masked_rmse
from app.ml.model import VolSurfaceForecaster

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"

# Significance gate. A "pair" only counts as a genuine one-day-ahead
# forecast test if the two snapshots are consecutive trading days.
SIGNIFICANCE_MIN_PAIRS = 20          # ~a month of clean daily data
SIGNIFICANCE_MAX_GAP_DAYS = 4        # Fri->Mon is 3; anything more is a hole

# Fixed so the reported number is reproducible run to run.
_TRAIN_SEED = 0
_TRAIN_EPOCHS = 300
_TRAIN_LR = 1e-3


@dataclass
class PairEval:
    snapshot_id_t: int
    snapshot_id_t1: int
    date_t: str
    date_t1: str
    calendar_gap_days: int
    is_next_trading_day: bool
    n_cells_scored: int
    persistence_rmse: float
    model_rmse: float | None


@dataclass
class EvaluationReport:
    ticker: str
    generated_on: str
    n_snapshots: int
    n_pairs: int
    grid: str
    pairs: list[PairEval] = field(default_factory=list)
    persistence_rmse: float | None = None       # pooled over all scored cells
    model_rmse: float | None = None
    relative_improvement: float | None = None
    model_beats_persistence: bool | None = None
    statistically_significant: bool = False
    caveats: list[str] = field(default_factory=list)
    verdict: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _snapshot_dates(session: Session, ticker: str) -> dict[int, date]:
    rows = session.execute(select(Snapshot).where(Snapshot.ticker == ticker)).scalars().all()
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    return {s.id: s.captured_at.astimezone(et).date() for s in rows}


def _fit(X: torch.Tensor, y: torch.Tensor, grid_size: int) -> VolSurfaceForecaster:
    """Deterministic fit on the given pairs."""
    torch.manual_seed(_TRAIN_SEED)
    model = VolSurfaceForecaster(grid_size=grid_size)
    opt = torch.optim.Adam(model.parameters(), lr=_TRAIN_LR)
    loss_fn = torch.nn.MSELoss()
    model.train()
    for _ in range(_TRAIN_EPOCHS):
        opt.zero_grad()
        loss_fn(model(X), y).backward()
        opt.step()
    model.eval()
    return model


def _lopo_predictions(dataset, grid_size: int) -> torch.Tensor:
    """Leave-one-pair-out: row i is the prediction for pair i from a model
    trained on every pair except i. Out-of-sample even with 2-3 pairs
    (though extremely high variance at that size)."""
    n = dataset.X.shape[0]
    preds = torch.empty_like(dataset.y)
    idx = torch.arange(n)
    for i in range(n):
        keep = idx != i
        model = _fit(dataset.X[keep], dataset.y[keep], grid_size)
        with torch.no_grad():
            preds[i] = model(dataset.X[i : i + 1])[0]
    return preds


def build_evaluation_report(
    ticker: str = "SPY",
    n_moneyness: int = 40,
    n_tte: int = 40,
    session: Session | None = None,
) -> EvaluationReport:
    owns = session is None
    session = session or SessionLocal()
    try:
        n_snap = len(
            session.execute(select(Snapshot).where(Snapshot.ticker == ticker)).scalars().all()
        )
        report = EvaluationReport(
            ticker=ticker,
            generated_on=date.today().isoformat(),
            n_snapshots=n_snap,
            n_pairs=0,
            grid=f"{n_tte}x{n_moneyness} (TTE x log-moneyness)",
        )

        try:
            dataset = load_training_pairs(
                ticker=ticker, n_moneyness=n_moneyness, n_tte=n_tte, session=session
            )
        except InsufficientSnapshotsError as e:
            report.caveats.append(str(e))
            report.verdict = (
                "Not enough snapshots to form a single (day J, day J+1) pair - "
                "no comparison is possible yet."
            )
            return report

        dates = _snapshot_dates(session, ticker)
        grid_size = n_moneyness * n_tte
        model_pred_all = _lopo_predictions(dataset, grid_size)

        pair_evals: list[PairEval] = []
        for i, (id_t, id_t1) in enumerate(dataset.snapshot_id_pairs):
            x = dataset.X[i : i + 1]
            y = dataset.y[i : i + 1]
            m = dataset.extrapolated_mask[i : i + 1]
            gap = abs((dates[id_t1] - dates[id_t]).days)
            pair_evals.append(
                PairEval(
                    snapshot_id_t=id_t,
                    snapshot_id_t1=id_t1,
                    date_t=dates[id_t].isoformat(),
                    date_t1=dates[id_t1].isoformat(),
                    calendar_gap_days=gap,
                    is_next_trading_day=gap <= SIGNIFICANCE_MAX_GAP_DAYS,
                    n_cells_scored=int((~m.bool()).sum()),
                    persistence_rmse=masked_rmse(x, y, m),
                    model_rmse=masked_rmse(model_pred_all[i : i + 1], y, m),
                )
            )

        report.pairs = pair_evals
        report.n_pairs = len(pair_evals)
        report.persistence_rmse = masked_rmse(dataset.X, dataset.y, dataset.extrapolated_mask)
        report.model_rmse = masked_rmse(model_pred_all, dataset.y, dataset.extrapolated_mask)
        if report.persistence_rmse and report.persistence_rmse > 0:
            report.relative_improvement = (
                report.persistence_rmse - report.model_rmse
            ) / report.persistence_rmse
            report.model_beats_persistence = report.model_rmse < report.persistence_rmse

        n_clean_pairs = sum(p.is_next_trading_day for p in pair_evals)
        report.statistically_significant = (
            report.n_pairs >= SIGNIFICANCE_MIN_PAIRS
            and n_clean_pairs == report.n_pairs
        )

        if report.n_pairs < SIGNIFICANCE_MIN_PAIRS:
            report.caveats.append(
                f"Only {report.n_pairs} pair(s); need >= {SIGNIFICANCE_MIN_PAIRS} "
                f"next-trading-day pairs before any RMSE comparison is more than anecdote."
            )
        if n_clean_pairs != report.n_pairs:
            holes = [
                f"{p.date_t}->{p.date_t1} ({p.calendar_gap_days}d)"
                for p in pair_evals
                if not p.is_next_trading_day
            ]
            report.caveats.append(
                "Some pairs span multi-day gaps, so persistence is being asked to "
                "hold over a weekend+ rather than one session: " + ", ".join(holes)
            )
        report.caveats.append(
            f"Model RMSE is leave-one-pair-out cross-validated, but with "
            f"{report.n_pairs} pair(s) each fold trains on only "
            f"{report.n_pairs - 1} pair(s) - the number is out-of-sample but "
            f"has enormous variance and should not be read as a point estimate."
        )

        report.verdict = _verdict(report)
        return report
    finally:
        if owns:
            session.close()


def _verdict(r: EvaluationReport) -> str:
    if r.model_rmse is None or r.persistence_rmse is None:
        return "No model number could be computed."
    if r.persistence_rmse == 0.0:
        return (
            f"On {r.n_pairs} pair(s), persistence has zero masked RMSE (the surface "
            f"did not move on the scored cells), so relative improvement is undefined. "
            f"Model masked RMSE is {r.model_rmse:.4f}."
        )
    direction = "beats" if r.model_beats_persistence else "does not beat"
    line = (
        f"On {r.n_pairs} pair(s), the model {direction} persistence "
        f"(model masked RMSE {r.model_rmse:.4f} vs persistence {r.persistence_rmse:.4f}, "
        f"relative improvement {r.relative_improvement:+.1%})."
    )
    if not r.statistically_significant:
        line += (
            " This is NOT statistically meaningful: too few pairs, some spanning "
            "multi-day gaps, and each cross-validation fold trains on almost "
            "nothing. Treat it as a pipeline smoke test, not evidence about the "
            "model either way."
        )
    return line


def write_evaluation_report(report: EvaluationReport, ticker: str | None = None) -> Path:
    MODEL_DIR.mkdir(exist_ok=True)
    path = MODEL_DIR / f"evaluation_{ticker or report.ticker}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2))
    return path


def read_evaluation_report(ticker: str = "SPY") -> dict | None:
    path = MODEL_DIR / f"evaluation_{ticker}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())
