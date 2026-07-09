"""Ad-hoc visual check for build_vol_surface — not part of the app, just proof."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from app.vol.surface_builder import build_vol_surface

grid = build_vol_surface(snapshot_id=1)

M, T = np.meshgrid(grid.log_moneyness_grid, grid.tte_grid)

fig = plt.figure(figsize=(14, 6))

ax1 = fig.add_subplot(1, 2, 1, projection="3d")
surf = ax1.plot_surface(M, T, grid.iv_grid, cmap="viridis", edgecolor="none", alpha=0.95)
ax1.set_xlabel("log-moneyness log(K/S)")
ax1.set_ylabel("TTE (years)")
ax1.set_zlabel("Implied Vol")
ax1.set_title(f"SPY vol surface — snapshot_id={grid.snapshot_id}\n"
              f"{grid.n_raw_points} raw points, {grid.n_expiries_used} expiries")
fig.colorbar(surf, ax=ax1, shrink=0.5, label="IV")

ax2 = fig.add_subplot(1, 2, 2)
im = ax2.pcolormesh(grid.log_moneyness_grid, grid.tte_grid, grid.iv_grid, cmap="viridis", shading="auto")
# hatch extrapolated cells
ax2.contourf(
    grid.log_moneyness_grid, grid.tte_grid, grid.extrapolated_mask.astype(float),
    levels=[0.5, 1.5], colors="none", hatches=["///"],
)
ax2.set_xlabel("log-moneyness log(K/S)")
ax2.set_ylabel("TTE (years)")
ax2.set_title("Heatmap (hachures = cellules extrapolées)")
fig.colorbar(im, ax=ax2, label="IV")

plt.tight_layout()
out_path = "/tmp/vol_surface_snapshot1.png"
plt.savefig(out_path, dpi=130)
print(f"Saved to {out_path}")
