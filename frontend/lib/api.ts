import type { DataQualityRow, SnapshotSummary, VolSurfaceResponse } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export async function fetchSnapshots(): Promise<SnapshotSummary[]> {
  const res = await fetch(`${API_BASE}/api/snapshots`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch snapshots: ${res.status}`);
  return res.json();
}

export async function fetchSurface(id: number): Promise<VolSurfaceResponse> {
  const res = await fetch(`${API_BASE}/api/surface/${id}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch surface ${id}: ${res.status}`);
  return res.json();
}

export async function fetchDataQuality(): Promise<DataQualityRow[]> {
  const res = await fetch(`${API_BASE}/api/data-quality`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch data quality: ${res.status}`);
  return res.json();
}
