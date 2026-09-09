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

// ET trading date, so this column matches the evaluation panel (which
// keys every pair on the America/New_York capture date). Formatting the
// UTC ISO string directly would put weekend/holiday captures on the
// wrong calendar day.
const etDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-CA", { timeZone: "America/New_York" });

// Short ET weekday (Sat/Sun get flagged: a capture on a non-trading day
// holds the prior session's close - see the evaluation panel caveats).
const etWeekday = (iso: string) =>
  new Date(iso).toLocaleDateString("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
  });
const isWeekendET = (iso: string) => {
  const d = etWeekday(iso);
  return d === "Sat" || d === "Sun";
};

export default function DataQualityTable({ rows }: { rows: DataQualityRow[] }) {
  if (rows.length === 0) {
    return <p className="text-neutral-400">No data-quality report yet.</p>;
  }

  const anyBackfilled = rows.some((r) => r.backfilled);
  const anyWeekend = rows.some((r) => isWeekendET(r.captured_at));

  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse w-full">
        <thead>
          <tr className="text-neutral-400 text-left border-b border-neutral-700">
            <th className="py-2 pr-4">Snapshot</th>
            <th className="py-2 pr-4" title="trading-session date in New York time (ET)">Date (ET)</th>
            <th className="py-2 pr-4">Spot</th>
            <th className="py-2 pr-4">Raw contracts</th>
            <th className="py-2 pr-4">Valid IVs</th>
            <th className="py-2 pr-4" title="inversion failures / contracts that reached Brent">
              BS inversion failures
            </th>
            <th className="py-2 pr-4" title="missing price + bid/ask ≤ 0 + crossed market">
              Quotes rejected
            </th>
            <th className="py-2 pr-4">Wide spread</th>
            <th className="py-2 pr-4">Expiries</th>
            <th className="py-2 pr-4">Strikes</th>
            <th className="py-2 pr-4" title="median / p95 of the relative bid-ask spread over kept points">
              Spread med / p95
            </th>
            <th className="py-2 pr-4" title="grid cells interpolated from real data vs clamp-extrapolated">
              Cells obs / extrap
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
                  {r.backfilled && <span className="text-amber-500" title="backfilled: raw chain unavailable"> *</span>}
                </td>
                <td className="py-1.5 pr-4 whitespace-nowrap">
                  {etDate(r.captured_at)}{" "}
                  <span className="text-neutral-500">({etWeekday(r.captured_at)})</span>
                  {isWeekendET(r.captured_at) && (
                    <span
                      className="text-amber-500"
                      title="captured on a non-trading day (ET): holds the previous Friday's close"
                    >
                      {" "}⚠
                    </span>
                  )}
                </td>
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
                    <span className="text-neutral-500"> ({r.min_dte}–{r.max_dte}d)</span>
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
          <span className="text-amber-500">*</span> backfilled: captured before the data-quality
          instrumentation existed; the raw chain is no longer available, so only metrics derivable
          from the stored points are filled in.
        </p>
      )}
      {anyWeekend && (
        <p className="text-neutral-500 mt-2">
          <span className="text-amber-500">⚠</span> captured on a Saturday/Sunday (ET): the daily
          capture ran outside a trading session, so the option chain reflects the previous Friday&apos;s
          close. Exchange holidays have the same effect but are not detected here. See also the
          caveats in the &ldquo;Model vs persistence&rdquo; panel.
        </p>
      )}
    </div>
  );
}
