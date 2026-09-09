"use client";

import { useEffect, useState } from "react";
import { fetchDataQuality, fetchEvaluation, fetchSnapshots, fetchSurface } from "@/lib/api";
import type {
  DataQualityRow,
  EvaluationResponse,
  SnapshotSummary,
  VolSurfaceResponse,
} from "@/lib/types";
import VolSurfacePlot from "@/components/VolSurfacePlot";
import DataQualityTable from "@/components/DataQualityTable";
import ModelEvaluation from "@/components/ModelEvaluation";

// Snapshots are labelled by their ET capture instant everywhere in the
// UI, so the dropdown, the data-quality table and the evaluation panel
// all agree on which calendar day a snapshot belongs to.
const etDateTime = (iso: string) =>
  new Date(iso).toLocaleString("en-CA", {
    timeZone: "America/New_York",
    dateStyle: "medium",
    timeStyle: "short",
  }) + " ET";

export default function Home() {
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([]);
  const [snapshotsLoaded, setSnapshotsLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [surface, setSurface] = useState<VolSurfaceResponse | null>(null);
  const [dataQuality, setDataQuality] = useState<DataQualityRow[]>([]);
  const [dqLoaded, setDqLoaded] = useState(false);
  const [evaluation, setEvaluation] = useState<EvaluationResponse | null>(null);
  const [evalLoaded, setEvalLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchDataQuality()
      .then(setDataQuality)
      .catch(() => setDataQuality([]))
      .finally(() => setDqLoaded(true));
    fetchEvaluation()
      .then(setEvaluation)
      .catch(() => setEvaluation(null))
      .finally(() => setEvalLoaded(true));
  }, []);

  useEffect(() => {
    fetchSnapshots()
      .then((data) => {
        setSnapshots(data);
        if (data.length > 0) setSelectedId(data[0].id);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setSnapshotsLoaded(true));
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

      {(!snapshotsLoaded || !dqLoaded || !evalLoaded) && !error && (
        <p className="text-neutral-400 mb-4">
          Loading… the API runs on a free tier and sleeps after ~15 min idle, so the
          first load can take 30–60&nbsp;s while the backend and database wake up.
        </p>
      )}

      {snapshotsLoaded && snapshots.length === 0 && !error && (
        <p className="text-neutral-400 mb-4">
          No snapshot available yet — the capture pipeline runs once per trading day
          after the market close (17:00 ET). Check back a little later.
        </p>
      )}

      {snapshots.length > 0 && (
        <div className="mb-4 flex items-center gap-3">
          <label htmlFor="snapshot-select" className="text-sm text-neutral-400">
            Snapshot:
          </label>
          <select
            id="snapshot-select"
            className="bg-neutral-900 border border-neutral-700 rounded px-3 py-1.5 text-sm"
            value={selectedId ?? ""}
            onChange={(e) => setSelectedId(Number(e.target.value))}
          >
            {snapshots.map((s) => (
              <option key={s.id} value={s.id}>
                #{s.id} — {etDateTime(s.captured_at)} — spot {s.spot.toFixed(2)}
              </option>
            ))}
          </select>
        </div>
      )}

      {error && <p className="text-red-400 mb-4">Error: {error}</p>}
      {loading && <p className="text-neutral-400 mb-4">Loading surface…</p>}

      {surface && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4 text-sm">
            <Stat label="Raw points (K,T,σ)" value={surface.n_raw_points} />
            <Stat label="Expiries used" value={surface.n_expiries_used} />
            <Stat label="Expiries dropped (<2 pts)" value={surface.n_expiries_dropped} />
            <Stat label="Spot at capture" value={surface.spot.toFixed(2)} />
          </div>
          <VolSurfacePlot surface={surface} />
        </>
      )}

      <section className="mt-10">
        <h2 className="text-lg font-semibold mb-1">Model vs persistence</h2>
        <p className="text-neutral-400 text-sm mb-3">
          Question: does a lightweight neural model predict the next-session SPY IV
          surface better than the persistence hypothesis (IV<sub>t+1</sub> = IV<sub>t</sub>)?
          Persistence is the only baseline. <em>Masked RMSE</em> = RMSE over only the
          cells built from real quotes (observed + PCHIP-interpolated); the flat-clamp
          extrapolation in the wings is excluded. <em>LOPO</em> = leave-one-pair-out:
          each pair is predicted by a model trained on the others. Figures are recomputed
          on every backend deploy; the database may hold captures more recent than the
          last recompute.
        </p>
        {evalLoaded ? (
          <ModelEvaluation evaluation={evaluation} />
        ) : (
          <p className="text-neutral-500 text-sm">Loading evaluation…</p>
        )}
      </section>

      <section className="mt-10">
        <h2 className="text-lg font-semibold mb-1">Data quality</h2>
        <p className="text-neutral-400 text-sm mb-3">
          Per snapshot: how much of the raw option chain survives the liquidity filter
          and the Black-Scholes inversion, why the rest is dropped, and how much of the
          reconstructed surface is real vs clamp-extrapolated.
        </p>
        {dqLoaded ? (
          <DataQualityTable rows={dataQuality} />
        ) : (
          <p className="text-neutral-500 text-sm">Loading data-quality report…</p>
        )}
      </section>
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
