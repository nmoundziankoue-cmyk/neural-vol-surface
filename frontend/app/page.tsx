"use client";

import { useEffect, useState } from "react";
import { fetchSnapshots, fetchSurface } from "@/lib/api";
import type { SnapshotSummary, VolSurfaceResponse } from "@/lib/types";
import VolSurfacePlot from "@/components/VolSurfacePlot";

export default function Home() {
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [surface, setSurface] = useState<VolSurfaceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchSnapshots()
      .then((data) => {
        setSnapshots(data);
        if (data.length > 0) setSelectedId(data[0].id);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (selectedId === null) return;
    setLoading(true);
    setError(null);
    fetchSurface(selectedId)
      .then(setSurface)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [selectedId]);

  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100 p-6">
      <h1 className="text-2xl font-semibold mb-4">Neural Volatility Surface — SPY</h1>

      <div className="mb-4 flex items-center gap-3">
        <label htmlFor="snapshot-select" className="text-sm text-neutral-400">
          Snapshot :
        </label>
        <select
          id="snapshot-select"
          className="bg-neutral-900 border border-neutral-700 rounded px-3 py-1.5 text-sm"
          value={selectedId ?? ""}
          onChange={(e) => setSelectedId(Number(e.target.value))}
        >
          {snapshots.map((s) => (
            <option key={s.id} value={s.id}>
              #{s.id} — {new Date(s.captured_at).toLocaleString()} — spot {s.spot.toFixed(2)}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="text-red-400 mb-4">Erreur : {error}</p>}
      {loading && <p className="text-neutral-400 mb-4">Chargement...</p>}

      {surface && (
        <>
          <div className="grid grid-cols-4 gap-4 mb-4 text-sm">
            <Stat label="Points bruts" value={surface.n_raw_points} />
            <Stat label="Échéances utilisées" value={surface.n_expiries_used} />
            <Stat label="Échéances droppées" value={surface.n_expiries_dropped} />
            <Stat label="Spot" value={surface.spot.toFixed(2)} />
          </div>
          <VolSurfacePlot surface={surface} />
        </>
      )}
    </main>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded p-3">
      <div className="text-neutral-500">{label}</div>
      <div className="text-lg font-medium">{value}</div>
    </div>
  );
}
