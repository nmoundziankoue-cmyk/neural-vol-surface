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
  const [evaluation, setEvaluation] = useState<EvaluationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchDataQuality()
      .then(setDataQuality)
      .catch(() => setDataQuality([]));
    fetchEvaluation()
      .then(setEvaluation)
      .catch(() => setEvaluation(null));
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

      {!snapshotsLoaded && !error && (
        <p className="text-neutral-400 mb-4">Chargement des snapshots...</p>
      )}

      {snapshotsLoaded && snapshots.length === 0 && !error && (
        <p className="text-neutral-400 mb-4">
          Aucun snapshot disponible pour le moment — le pipeline de capture tourne
          quotidiennement après la clôture du marché (17h ET). Reviens un peu plus tard.
        </p>
      )}

      {snapshots.length > 0 && (
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
                #{s.id} — {etDateTime(s.captured_at)} — spot {s.spot.toFixed(2)}
              </option>
            ))}
          </select>
        </div>
      )}

      {error && <p className="text-red-400 mb-4">Erreur : {error}</p>}
      {loading && <p className="text-neutral-400 mb-4">Chargement de la surface...</p>}

      {surface && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4 text-sm">
            <Stat label="Points bruts (K,T,σ)" value={surface.n_raw_points} />
            <Stat label="Échéances utilisées" value={surface.n_expiries_used} />
            <Stat label="Échéances écartées (<2 pts)" value={surface.n_expiries_dropped} />
            <Stat label="Spot à la capture" value={surface.spot.toFixed(2)} />
          </div>
          <VolSurfacePlot surface={surface} />
        </>
      )}

      <section className="mt-10">
        <h2 className="text-lg font-semibold mb-1">Modèle vs persistence</h2>
        <p className="text-neutral-400 text-sm mb-3">
          Question : un modèle neuronal léger prédit-il la surface IV SPY de la séance
          suivante mieux que l&apos;hypothèse de persistence (IV<sub>t+1</sub> = IV<sub>t</sub>) ?
          Unique baseline. <em>RMSE masquée</em> = RMSE sur les seules cellules issues de
          vraies cotations (observées + interpolées PCHIP), l&apos;extrapolation à plat des
          ailes étant exclue. <em>LOPO</em> = leave-one-pair-out : chaque paire est prédite
          par un modèle entraîné sur les autres. Chiffres recalculés à chaque déploiement du
          backend ; la base peut contenir des captures plus récentes que le dernier recalcul.
        </p>
        <ModelEvaluation evaluation={evaluation} />
      </section>

      <section className="mt-10">
        <h2 className="text-lg font-semibold mb-1">Qualité des données</h2>
        <p className="text-neutral-400 text-sm mb-3">
          Par snapshot : combien de la chaîne d&apos;options brute survit au filtrage de liquidité
          et à l&apos;inversion Black-Scholes, pourquoi le reste est écarté, et quelle part de la
          surface reconstruite est réelle vs extrapolée par clamp.
        </p>
        <DataQualityTable rows={dataQuality} />
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
