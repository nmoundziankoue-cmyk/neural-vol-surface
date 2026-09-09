"use client";

import type { EvaluationResponse } from "@/lib/types";

/**
 * Honest model-vs-persistence panel. The only baseline is persistence
 * (IV_hat(t+1) = IV(t)); the model number is leave-one-pair-out
 * cross-validated. The "not statistically significant" banner is shown
 * whenever the hard gate fails, which - with the current handful of
 * non-consecutive snapshots - is always.
 */

const fmt = (x: number | null, d = 4) => (x == null ? "—" : x.toFixed(d));
const pct = (x: number | null) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);

export default function ModelEvaluation({ evaluation }: { evaluation: EvaluationResponse | null }) {
  if (!evaluation) return null;

  if (!evaluation.available) {
    return (
      <p className="text-neutral-400 text-sm">
        No evaluation has been computed yet. {evaluation.verdict}
      </p>
    );
  }

  const e = evaluation;

  return (
    <div className="text-sm">
      {!e.statistically_significant && (
        <div className="mb-3 rounded border border-amber-700/60 bg-amber-950/40 px-3 py-2 text-amber-200">
          Result <strong>not statistically significant</strong> — insufficient data.
          Read this as an end-to-end test of the pipeline, not as evidence that the model
          works (or doesn&apos;t).
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
        <Stat label="Adjacent pairs" value={String(e.n_pairs ?? "—")} />
        <Stat label="Persistence RMSE (pooled)" value={fmt(e.persistence_rmse)} />
        <Stat label="Model RMSE (LOPO, pooled)" value={fmt(e.model_rmse)} />
        <Stat
          label="Relative improvement"
          value={pct(e.relative_improvement)}
          tone={
            e.model_beats_persistence == null
              ? undefined
              : e.model_beats_persistence
              ? "good"
              : "bad"
          }
        />
      </div>

      <p className="text-neutral-300 mb-3">{e.verdict}</p>

      {e.pairs.length > 0 && (
        <div className="overflow-x-auto mb-3">
          <table className="text-xs border-collapse">
            <thead>
              <tr className="text-neutral-400 text-left border-b border-neutral-700">
                <th className="py-1.5 pr-4">Pair (ET dates)</th>
                <th className="py-1.5 pr-4" title="calendar days between the two capture timestamps — not the number of trading sessions">
                  Gap
                </th>
                <th className="py-1.5 pr-4">Cells scored</th>
                <th className="py-1.5 pr-4">Persistence RMSE</th>
                <th className="py-1.5 pr-4">Model RMSE</th>
              </tr>
            </thead>
            <tbody>
              {e.pairs.map((p, i) => (
                <tr key={i} className="border-b border-neutral-800">
                  <td className="py-1.5 pr-4">
                    {p.date_t} → {p.date_t1}
                  </td>
                  <td className="py-1.5 pr-4">
                    {p.calendar_gap_days}d
                    {!p.is_next_trading_day && (
                      <span className="text-amber-500" title="not a consecutive trading day">
                        {" "}⚠
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 pr-4">{p.n_cells_scored.toLocaleString()}</td>
                  <td className="py-1.5 pr-4">{fmt(p.persistence_rmse)}</td>
                  <td className="py-1.5 pr-4">{fmt(p.model_rmse)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {e.caveats.length > 0 && (
        <ul className="text-neutral-500 text-xs list-disc pl-5 space-y-1">
          {e.caveats.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  const toneClass =
    tone === "good" ? "text-emerald-400" : tone === "bad" ? "text-red-400" : "text-neutral-100";
  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded p-3">
      <div className="text-neutral-500 text-xs">{label}</div>
      <div className={`text-base font-medium ${toneClass}`}>{value}</div>
    </div>
  );
}
