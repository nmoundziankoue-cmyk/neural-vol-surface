export interface SnapshotSummary {
  id: number;
  ticker: string;
  captured_at: string;
  spot: number;
}

export interface VolSurfaceResponse {
  snapshot_id: number;
  ticker: string;
  captured_at: string;
  spot: number;
  tte_grid: number[];
  log_moneyness_grid: number[];
  iv_grid: number[][];
  extrapolated_mask: boolean[][];
  n_raw_points: number;
  n_expiries_used: number;
  n_expiries_dropped: number;
}
