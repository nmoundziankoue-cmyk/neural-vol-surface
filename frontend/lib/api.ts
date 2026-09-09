import type {
  DataQualityRow,
  EvaluationResponse,
  SnapshotSummary,
  VolSurfaceResponse,
} from "./types";

// Trailing slash stripped so a NEXT_PUBLIC_API_BASE set with one (e.g.
// "https://host/") doesn't produce "//api/..." paths, which the backend 404s.
const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000"
).replace(/\/+$/, "");

export async function fetchSnapshots(): Promise<SnapshotSummary[]> {
  const res = await fetch(`${API_BASE}/api/snapshots`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch snapshots: ${res.status}`);
  return res.json();
}

export async function fetchSurface(id: number): Promise<VolSurfaceResponse> {
  const res = await fetch(`${API_BASE}/api/surfaces/by-id/${id}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch surface ${id}: ${res.status}`);
  return res.json();
}

export async function fetchLatestSurface(): Promise<VolSurfaceResponse> {
  const res = await fetch(`${API_BASE}/api/surfaces/latest`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch latest surface: ${res.status}`);
  return res.json();
}

export async function fetchDataQuality(): Promise<DataQualityRow[]> {
  const res = await fetch(`${API_BASE}/api/data-quality`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch data quality: ${res.status}`);
  return res.json();
}

export async function fetchEvaluation(): Promise<EvaluationResponse> {
  const res = await fetch(`${API_BASE}/api/evaluation`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch evaluation: ${res.status}`);
  return res.json();
}
