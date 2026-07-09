"use client";

import dynamic from "next/dynamic";
import type { VolSurfaceResponse } from "@/lib/types";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

/**
 * Splits iv_grid into two overlaid surface traces so extrapolated
 * (clamp) cells render at reduced opacity, mirroring the hatching used
 * in the matplotlib sanity-check plot from surface_builder.py.
 */
export default function VolSurfacePlot({ surface }: { surface: VolSurfaceResponse }) {
  const { tte_grid, log_moneyness_grid, iv_grid, extrapolated_mask } = surface;

  const zInterpolated = iv_grid.map((row, i) =>
    row.map((v, j) => (extrapolated_mask[i][j] ? null : v))
  );
  const zExtrapolated = iv_grid.map((row, i) =>
    row.map((v, j) => (extrapolated_mask[i][j] ? v : null))
  );

  return (
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
          colorbar: { title: { text: "IV" } },
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
          xaxis: { title: { text: "log-moneyness log(K/S)" } },
          yaxis: { title: { text: "TTE (années)" } },
          zaxis: { title: { text: "IV" } },
        },
        margin: { l: 0, r: 0, b: 0, t: 40 },
        paper_bgcolor: "rgba(0,0,0,0)",
        font: { color: "#e5e5e5" },
      }}
      style={{ width: "100%", height: "640px" }}
      useResizeHandler
      config={{ responsive: true }}
    />
  );
}
