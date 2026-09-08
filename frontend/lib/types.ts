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

export interface DataQualityRow {
  snapshot_id: number;
  captured_at: string;
  ticker: string;
  spot: number;
  backfilled: boolean;

  n_contracts_raw: number | null;
  n_kept: number;
  n_short_dte: number | null;
  n_otm_side: number | null;
  n_missing_price: number | null;
  n_non_positive_bid: number | null;
  n_crossed_market: number | null;
  n_low_open_interest: number | null;
  n_wide_spread: number | null;
  n_inversion_attempted: number | null;
  n_inversion_failed: number | null;
  inversion_failure_rate: number | null;

  n_expiries: number;
  n_strikes: number;
  min_strike: number | null;
  max_strike: number | null;
  min_dte: number | null;
  max_dte: number | null;

  median_rel_spread: number | null;
  p95_rel_spread: number | null;
  max_rel_spread: number | null;

  n_grid_cells: number | null;
  n_cells_observed: number | null;
  n_cells_extrapolated: number | null;
}
