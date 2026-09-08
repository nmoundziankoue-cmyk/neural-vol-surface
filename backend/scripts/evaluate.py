"""Recompute the honest model-vs-persistence report and cache it to
models/evaluation_<ticker>.json for the API to serve.

    PYTHONPATH=. python scripts/evaluate.py

Safe to run any time; with too few snapshots it just writes a report
whose verdict says so.
"""

from __future__ import annotations

import sys

from app.config import settings
from app.ml.evaluate import build_evaluation_report, write_evaluation_report


def main(ticker: str = settings.ticker) -> None:
    report = build_evaluation_report(ticker=ticker)
    path = write_evaluation_report(report, ticker=ticker)

    print(f"ticker              {report.ticker}")
    print(f"snapshots           {report.n_snapshots}")
    print(f"pairs               {report.n_pairs}")
    if report.persistence_rmse is not None:
        print(f"persistence RMSE    {report.persistence_rmse:.6f}")
    if report.model_rmse is not None:
        print(f"model RMSE          {report.model_rmse:.6f}")
    if report.relative_improvement is not None:
        print(f"rel. improvement    {report.relative_improvement:+.2%}")
    print(f"significant         {report.statistically_significant}")
    for c in report.caveats:
        print(f"  caveat: {c}")
    print()
    print(report.verdict)
    print(f"\nwritten to {path}")


if __name__ == "__main__":
    main(*(sys.argv[1:2] or []))
