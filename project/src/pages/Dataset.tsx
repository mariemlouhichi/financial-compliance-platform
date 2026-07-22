// ============================================================================
// ESPACE ADMIN — Dataset IBM AML HI-Small : agrégats pré-calculés OFFLINE
// (compute_dataset_stats.py → output/dataset_stats.json → /api/dataset-stats).
// C'est le dataset d'entraînement du ML Fraud Detector (LightGBM + LSTM-AE).
// ============================================================================
import { useEffect, useState } from 'react';
import { Database } from 'lucide-react';
import { api } from '../api/client';
import type { DatasetStats } from '../types/api';
import { PageHeader } from '../components/Layout';
import { Card, StatTile, Spinner, EmptyState } from '../components/ui';
import { BarList } from '../components/charts';

// Identité des types de paiement : ordre fixe, jamais recyclé (dataviz)
const FORMAT_COLORS: Record<string, string> = {
  'Cheque': 'bg-indigo-500',
  'Credit Card': 'bg-sky-500',
  'ACH': 'bg-teal-500',
  'Cash': 'bg-amber-500',
  'Wire': 'bg-violet-500',
  'Bitcoin': 'bg-rose-400',
  'Reinvestment': 'bg-slate-400',
};

/** Mini-graphe 24 h : fraudes par heure (séquentiel, une seule teinte). */
function HourStrip({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <div>
      <div className="flex h-24 items-end gap-[3px]">
        {entries.map(([h, v]) => (
          <div key={h} className="group relative flex-1"
            title={`${h} — ${v.toLocaleString('fr-FR')} fraudes`}>
            <div
              className="w-full rounded-t-[3px] bg-indigo-500 transition-colors group-hover:bg-indigo-700"
              style={{ height: `${Math.max(3, (v / max) * 96)}px` }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-slate-400">
        <span>00h</span><span>06h</span><span>12h</span><span>18h</span><span>23h</span>
      </div>
    </div>
  );
}

export function DatasetPage() {
  const [stats, setStats] = useState<DatasetStats | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.datasetStats()
      .then(setStats)
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner label="Chargement des statistiques du dataset…" />;

  if (error || !stats) {
    return (
      <>
        <PageHeader title="Dataset IBM AML" />
        <EmptyState>
          Statistiques non disponibles — lance une fois dans le terminal :{' '}
          <code className="rounded bg-slate-200 px-1.5 py-0.5">python compute_dataset_stats.py</code>
          <span className="mt-2 block text-xs text-slate-400">{error}</span>
        </EmptyState>
      </>
    );
  }

  const fmt = (n: number) => n.toLocaleString('fr-FR');

  // Taux de fraude par type de paiement (fraudes / total du type)
  const fraudRateRows = Object.entries(stats.by_payment_format)
    .filter(([, total]) => total > 0)
    .map(([k, total]) => ({
      label: k,
      value: Math.round(10000 * (stats.fraud_by_payment_format[k] ?? 0) / total) / 100,
      color: FORMAT_COLORS[k] ?? 'bg-slate-400',
    }))
    .sort((a, b) => b.value - a.value);

  return (
    <>
      <PageHeader
        title={<><Database size={22} className="mr-2 inline text-slate-400" />Dataset IBM AML — {stats.dataset}</>}
        subtitle={`Corpus d'entraînement du détecteur ML (LightGBM + LSTM-AE) · agrégats pré-calculés le ${new Date(stats.computed_at).toLocaleString('fr-FR')}`}
      />

      {/* ── KPIs ────────────────────────────────────────────────────── */}
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Transactions" value={fmt(stats.total_transactions)} />
        <StatTile label="Fraudes étiquetées" value={fmt(stats.fraud_count)} tone="warning" />
        <StatTile label="Taux de fraude" value={`${stats.fraud_rate_pct} %`}
          hint="déséquilibre extrême → PR-AUC + seuil F2" />
        {stats.total_amount_usd_billion != null && (
          <StatTile label="Volume total" value={`${fmt(stats.total_amount_usd_billion)} Md$`} />
        )}
      </div>

      {/* ── Distributions ───────────────────────────────────────────── */}
      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        <Card title="Transactions par type de paiement"
              subtitle="Volume brut (identité : ordre et couleurs fixes)">
          <BarList rows={Object.entries(stats.by_payment_format).map(([k, v]) => ({
            label: k, value: v, color: FORMAT_COLORS[k] ?? 'bg-slate-400',
          }))} />
        </Card>
        <Card title="Taux de fraude par type de paiement (%)"
              subtitle="Fraudes du type ÷ volume du type — les canaux les plus risqués">
          <BarList rows={fraudRateRows} unit=" %" />
        </Card>
      </div>

      <div className="mb-6 grid gap-4 lg:grid-cols-3">
        <Card title="Devises (top 10)" subtitle="Devise de paiement">
          <BarList rows={Object.entries(stats.by_currency).map(([k, v]) => ({
            label: k, value: v, color: 'bg-slate-500',
          }))} />
        </Card>
        <Card title="Montants (USD)" subtitle="Tranches en échelle logarithmique">
          <BarList rows={Object.entries(stats.by_amount_bucket).map(([k, v]) => ({
            label: k, value: v, color: 'bg-teal-600',
          }))} />
        </Card>
        <Card title="Fraudes par heure de la journée"
              subtitle="Concentration nocturne → signal NIGHT_BURST du Level 1">
          <HourStrip data={stats.fraud_by_hour} />
        </Card>
      </div>

      <p className="text-xs text-slate-400">
        💡 Ce déséquilibre ({stats.fraud_rate_pct} % de fraudes) justifie les choix ML du projet :
        métrique PR-AUC plutôt qu'accuracy, scale_pos_weight, seuil optimisé F2-beta, et retrait
        des identifiants de banque (From_Bank/To_Bank) pour éliminer la fuite d'identité.
      </p>
    </>
  );
}
