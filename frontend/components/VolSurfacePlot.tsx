"use client";

import dynamic from "next/dynamic";
import type { VolSurfaceResponse } from "@/lib/types";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

/**
 * Splits iv_grid into two overlaid surface traces so extrapolated
 * (clamp) cells render at reduced opacity, mirroring the hatching used
 * in the matplotlib sanity-check plot from surface_builder.py.
 *
 * Axes match the stored coordinates exactly: x = SPOT log-moneyness
 * k = ln(K/S) (not forward, not delta), y = ACT/365 calendar TTE in
 * years, z = Black-Scholes implied vol as an annualised decimal
 * (0.15 = 15 vol points). See docs/IV_AND_COORDINATES.md.
 */
export default function VolSurfacePlot({ surface }: { surface: VolSurfaceResponse }) {
  const { tte_grid, log_moneyness_grid, iv_grid, extrapolated_mask } = surface;

  const zInterpolated = iv_grid.map((row, i) =>
    row.map((v, j) => (extrapolated_mask[i][j] ? null : v))
  );
  const zExtrapolated = iv_grid.map((row, i) =>
    row.map((v, j) => (extrapolated_mask[i][j] ? v : null))
  );

  const nCells = extrapolated_mask.length * (extrapolated_mask[0]?.length ?? 0);
  const nExtrap = extrapolated_mask.reduce(
    (acc, row) => acc + row.reduce((a, f) => a + (f ? 1 : 0), 0),
    0
  );
  const pctExtrap = nCells > 0 ? Math.round((100 * nExtrap) / nCells) : 0;

  return (
    <div>
      <Plot
        data={[
          {
            type: "surface",
            x: log_moneyness_grid,
            y: tte_grid,
            z: zInterpolated,
            colorscale: "Viridis",
            opacity: 1,
            showscale: true,
            colorbar: { title: { text: "IV (décimal)" } },
            name: "Interpolé (PCHIP)",
          },
          {
            type: "surface",
            x: log_moneyness_grid,
            y: tte_grid,
            z: zExtrapolated,
            colorscale: "Viridis",
            opacity: 0.35,
            showscale: false,
            name: "Extrapolé (clamp)",
          },
        ]}
        layout={{
          title: {
            text: `Surface de volatilité implicite — ${surface.ticker} (snapshot #${surface.snapshot_id})`,
          },
          autosize: true,
          scene: {
            xaxis: { title: { text: "log-moneyness k = ln(K/S) — spot" } },
            yaxis: { title: { text: "TTE (années, ACT/365)" } },
            zaxis: { title: { text: "IV (décimal annualisé)" } },
          },
          margin: { l: 0, r: 0, b: 0, t: 40 },
          paper_bgcolor: "rgba(0,0,0,0)",
          font: { color: "#e5e5e5" },
        }}
        style={{ width: "100%", height: "640px" }}
        useResizeHandler
        config={{ responsive: true }}
      />
      <p className="text-neutral-500 text-xs mt-1">
        Opacité pleine = cellules construites sur de vraies cotations (points
        observés + interpolation PCHIP entre eux). Opacité réduite ={" "}
        {pctExtrap}% de la grille = extrapolation à plat (clamp) au-delà de la
        dernière cotation, dans l&apos;aile long-dated peu liquide — exclue de
        toutes les métriques d&apos;erreur. Coordonnées : moneyness <em>spot</em>{" "}
        k = ln(K/S), TTE calendaire ACT/365, IV en décimal annualisé (0,15 = 15
        points de vol).
      </p>
    </div>
  );
}
