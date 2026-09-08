"use client";

import type { DataQualityRow } from "@/lib/types";

/**
 * Plain per-snapshot data-quality table. One row per capture: how much of
 * the raw option chain survived filtering + Black-Scholes inversion, why
 * the rest was dropped, and how much of the reconstructed surface is real
 * vs clamp-extrapolated. Nothing is dropped silently upstream, so these
 * columns sum back to the raw contract count.
 */

const pct = (x: number | null) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
const num = (x: number | null) => (x == null ? "—" : x.toLocaleString());

export default function DataQualityTable({ rows }: { rows: DataQualityRow[] }) {
  if (rows.length === 0) {
    return <p className="text-neutral-400">Aucun rapport de qualité de données pour le moment.</p>;
  }

  const anyBackfilled = rows.some((r) => r.backfilled);

  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse w-full">
        <thead>
          <tr className="text-neutral-400 text-left border-b border-neutral-700">
            <th className="py-2 pr-4">Snapshot</th>
            <th className="py-2 pr-4">Date</th>
            <th className="py-2 pr-4">Spot</th>
            <th className="py-2 pr-4">Contrats bruts</th>
            <th className="py-2 pr-4">IV valides</th>
            <th className="py-2 pr-4" title="échecs d'inversion / contrats ayant atteint Brent">
              Échec inversion BS
            </th>
            <th className="py-2 pr-4" title="prix manquant + bid/ask ≤ 0 + marché croisé">
              Quotes rejetées
            </th>
            <th className="py-2 pr-4">Spread large</th>
            <th className="py-2 pr-4">Échéances</th>
            <th className="py-2 pr-4">Strikes</th>
            <th className="py-2 pr-4" title="médiane / p95 du spread bid-ask relatif sur les points gardés">
              Spread méd / p95
            </th>
            <th className="py-2 pr-4" title="cellules de grille interpolées à partir de vraies données vs clampées">
              Cellules obs / extrap
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const quotesRejected =
              r.n_missing_price == null
                ? null
                : (r.n_missing_price ?? 0) + (r.n_non_positive_bid ?? 0) + (r.n_crossed_market ?? 0);
            return (
              <tr key={r.snapshot_id} className="border-b border-neutral-800">
                <td className="py-1.5 pr-4">
                  #{r.snapshot_id}
                  {r.backfilled && <span className="text-amber-500" title="rétro-rempli : chaîne brute indisponible"> *</span>}
                </td>
                <td className="py-1.5 pr-4">{new Date(r.captured_at).toISOString().slice(0, 10)}</td>
                <td className="py-1.5 pr-4">{r.spot.toFixed(2)}</td>
                <td className="py-1.5 pr-4">{num(r.n_contracts_raw)}</td>
                <td className="py-1.5 pr-4">{num(r.n_kept)}</td>
                <td className="py-1.5 pr-4">
                  {r.inversion_failure_rate == null
                    ? "—"
                    : `${num(r.n_inversion_failed)} (${pct(r.inversion_failure_rate)})`}
                </td>
                <td className="py-1.5 pr-4">{num(quotesRejected)}</td>
                <td className="py-1.5 pr-4">{num(r.n_wide_spread)}</td>
                <td className="py-1.5 pr-4">
                  {r.n_expiries}
                  {r.min_dte != null && (
                    <span className="text-neutral-500"> ({r.min_dte}–{r.max_dte}j)</span>
                  )}
                </td>
                <td className="py-1.5 pr-4">{r.n_strikes}</td>
                <td className="py-1.5 pr-4">
                  {pct(r.median_rel_spread)} / {pct(r.p95_rel_spread)}
                </td>
                <td className="py-1.5 pr-4">
                  {num(r.n_cells_observed)} / {num(r.n_cells_extrapolated)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {anyBackfilled && (
        <p className="text-neutral-500 mt-2">
          <span className="text-amber-500">*</span> rétro-rempli : capturé avant l&apos;instrumentation
          data-quality, la chaîne brute n&apos;est plus disponible — seules les métriques dérivées des
          points stockés sont renseignées.
        </p>
      )}
    </div>
  );
}
