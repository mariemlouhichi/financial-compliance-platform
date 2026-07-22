// ============================================================================
// ESPACE ADMIN — Dashboard ANALYTIQUE : pilotage et tendances.
// (Le contrôle du flux simulé et le scénario réaliste vivent désormais sur
//  la page « Flux bancaire en direct » — séparation analyse / opérations.)
// Plusieurs types de visualisation : KPIs, donut, courbe d'aire, barres.
// ============================================================================
import { useEffect, useMemo, useState } from 'react';
import { RefreshCw, Radio } from 'lucide-react';
import { api, subscribeEvents } from '../api/client';
import type { Stats, TxListItem } from '../types/api';
import { PageHeader } from '../components/Layout';
import { Card, StatTile, Spinner, EmptyState, DecisionBadge, RiskBadge, StateBadge } from '../components/ui';
import { AreaChart, Donut, DECISION_HEX } from '../components/charts';
import { Link } from '../router';

const DECISION_ORDER = ['PASS', 'REVIEW', 'KYC', 'RECORD', 'EDD', 'STR', 'BLOCK'];
const RISK_ORDER = ['FAIBLE', 'MOYEN', 'ÉLEVÉ', 'CRITIQUE'];

// Tuiles de risque : couleur + description métier
const RISK_TILE: Record<string, string> = {
  FAIBLE: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  MOYEN: 'border-amber-200 bg-amber-50 text-amber-800',
  'ÉLEVÉ': 'border-orange-200 bg-orange-50 text-orange-800',
  CRITIQUE: 'border-red-200 bg-red-50 text-red-800',
};
const RISK_DESC: Record<string, string> = {
  FAIBLE: 'exécution automatique', MOYEN: 'vigilance',
  'ÉLEVÉ': 'diligence renforcée', CRITIQUE: 'blocage / déclaration',
};
const PATTERN_EMOJI: Record<string, string> = {
  FAN_OUT: '🌐', FAN_IN: '🎯', LAYERING: '🌀', STRUCTURING: '✂️',
  HIGH_RISK_ROUTE: '🌍', NIGHT_BURST: '🌙', NORMAL: '✅',
};
const PATTERN_DESC: Record<string, string> = {
  FAN_OUT: 'un émetteur → nombreux destinataires',
  FAN_IN: 'nombreux émetteurs → un destinataire',
  LAYERING: 'empilement d’opérations (dissimulation)',
  STRUCTURING: 'fractionnement sous le seuil',
  HIGH_RISK_ROUTE: 'route géographique à risque',
  NIGHT_BURST: 'rafale d’opérations nocturnes',
  NORMAL: 'aucun schéma suspect',
};

export function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [recent, setRecent] = useState<TxListItem[]>([]);
  const [allTxs, setAllTxs] = useState<TxListItem[]>([]);
  const [period, setPeriod] = useState<'day' | 'week' | 'month'>('week');
  const [apiUp, setApiUp] = useState<boolean | null>(null);

  const load = async () => {
    try {
      const [s, txs] = await Promise.all([api.stats(), api.transactions()]);
      setStats(s);
      setRecent(txs.slice(0, 8));
      setAllTxs(txs);
      setApiUp(true);
    } catch {
      setApiUp(false);
    }
  };

  useEffect(() => {
    load();
    const unsub = subscribeEvents(() => load());   // push SSE temps réel
    const id = setInterval(load, 45_000);           // filet de sécurité lent
    return () => { unsub(); clearInterval(id); };
  }, []);

  // ── Courbe d'activité (historique persisté sur disque) ──────────────
  const activityPoints = useMemo(() => {
    const now = Date.now();
    const day = 86_400_000;
    if (period === 'day') {
      // 24 dernières heures, un point par heure (série continue)
      const buckets = new Array(24).fill(0);
      for (const t of allTxs) {
        const d = new Date(t.created_at);
        if (isNaN(d.getTime()) || now - d.getTime() > day) continue;
        const hoursAgo = Math.floor((now - d.getTime()) / 3_600_000);
        buckets[23 - hoursAgo] += 1;
      }
      return buckets.map((v, i) => {
        const h = new Date(now - (23 - i) * 3_600_000).getHours();
        return { label: `${String(h).padStart(2, '0')}h`, value: v };
      });
    }
    const n = period === 'week' ? 7 : 30;
    const buckets = new Array(n).fill(0);
    for (const t of allTxs) {
      const d = new Date(t.created_at);
      if (isNaN(d.getTime()) || now - d.getTime() > n * day) continue;
      const daysAgo = Math.floor((now - d.getTime()) / day);
      if (daysAgo < n) buckets[n - 1 - daysAgo] += 1;
    }
    return buckets.map((v, i) => {
      const d = new Date(now - (n - 1 - i) * day);
      return {
        label: d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' }),
        value: v,
      };
    });
  }, [allTxs, period]);

  if (apiUp === false) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <EmptyState>
          Backend injoignable. Lance-le : <code className="rounded bg-slate-200 px-1.5 py-0.5">
          python -m uvicorn api.main:app --port 8001</code>
        </EmptyState>
      </>
    );
  }

  if (!stats) return <Spinner label="Chargement des indicateurs…" />;

  const donutSegments = DECISION_ORDER
    .filter(k => stats.by_decision[k])
    .map(k => ({ label: k, value: stats.by_decision[k],
                 hex: DECISION_HEX[k] ?? '#94a3b8' }));

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle="Pilotage — tendances, répartitions et dernières analyses"
        actions={
          <div className="flex items-center gap-2">
            <Link to="simulator"
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500">
              <Radio size={13} /> Flux en direct
            </Link>
            <button onClick={load}
              className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
              <RefreshCw size={13} /> Actualiser
            </button>
          </div>
        }
      />

      {/* ── KPIs ────────────────────────────────────────────────────── */}
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Transactions analysées" value={stats.total_analyzed} />
        <StatTile label="En cours d'analyse" value={stats.running}
          tone={stats.running > 0 ? 'warning' : 'default'} />
        <StatTile label="En attente superviseur" value={stats.pending_supervisor.length}
          tone={stats.pending_supervisor.length > 0 ? 'warning' : 'success'}
          hint="REVIEW / EDD / STR non validées" />
        <StatTile label="Durée moyenne pipeline"
          value={`${(stats.avg_pipeline_ms / 1000).toFixed(1)}s`}
          hint="8 étapes, retrieval inclus" />
      </div>

      {/* ── Rangée 1 : courbe d'activité + donut des décisions ───────── */}
      <div className="mb-6 grid gap-4 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <Card
            title="Évolution de l'activité"
            subtitle={period === 'day' ? 'Par heure — 24 dernières heures'
              : period === 'week' ? 'Par jour — 7 derniers jours'
              : 'Par jour — 30 derniers jours'}
            actions={
              <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5">
                {([['day', "24 h"], ['week', '7 j'], ['month', '30 j']] as const)
                  .map(([k, lbl]) => (
                  <button key={k} onClick={() => setPeriod(k)}
                    className={`rounded-md px-2.5 py-1 text-[11px] font-semibold ${
                      period === k ? 'bg-white text-slate-900 shadow-sm'
                                   : 'text-slate-500 hover:text-slate-700'}`}>
                    {lbl}
                  </button>
                ))}
              </div>
            }>
            <AreaChart points={activityPoints} />
          </Card>
        </div>
        <div className="lg:col-span-2">
          <Card title="Répartition des décisions"
                subtitle="Compliance Agent — palette de statut">
            {donutSegments.length === 0
              ? <EmptyState>Aucune analyse pour l'instant.</EmptyState>
              : <Donut segments={donutSegments} centerLabel="analyses" />}
          </Card>
        </div>
      </div>

      {/* ── Rangée 2 : risque (tuiles) + patterns (cartes explicitées) ── */}
      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        <Card title="Niveaux de risque"
              subtitle="Score combiné RAG + garde-fous ML">
          <div className="grid grid-cols-2 gap-3">
            {RISK_ORDER.map(lvl => {
              const v = stats.by_risk_level[lvl] ?? 0;
              const tot = Object.values(stats.by_risk_level).reduce((a, b) => a + b, 0);
              return (
                <div key={lvl} className={`rounded-xl border p-3 ${RISK_TILE[lvl] ?? 'border-slate-200'}`}>
                  <div className="flex items-baseline justify-between">
                    <span className="text-xs font-bold uppercase tracking-wide">{lvl}</span>
                    <span className="text-2xl font-black tabular-nums">{v}</span>
                  </div>
                  <p className="mt-0.5 text-[10px] opacity-70">
                    {tot ? Math.round(v / tot * 100) : 0} % · {RISK_DESC[lvl]}
                  </p>
                </div>
              );
            })}
          </div>
        </Card>
        <Card title="Patterns AML détectés"
              subtitle="Schémas de blanchiment identifiés (langage métier)">
          {Object.values(stats.by_pattern).every(v => !v) ? (
            <EmptyState>Aucun pattern pour l'instant.</EmptyState>
          ) : (
            <div className="space-y-1.5">
              {Object.entries(stats.by_pattern)
                .filter(([k]) => k !== '?')
                .sort((a, b) => b[1] - a[1])
                .map(([k, v]) => (
                  <div key={k} className="flex items-center gap-3 rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2">
                    <span className="text-base">{PATTERN_EMOJI[k] ?? '•'}</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-semibold text-slate-800">{k}</p>
                      <p className="truncate text-[10px] text-slate-500">{PATTERN_DESC[k] ?? 'schéma détecté'}</p>
                    </div>
                    <span className="rounded-full bg-white px-2 py-0.5 text-xs font-bold tabular-nums text-slate-700 ring-1 ring-slate-200">
                      {v}
                    </span>
                  </div>
                ))}
            </div>
          )}
        </Card>
      </div>

      {/* ── Dernières analyses ───────────────────────────────────────── */}
      <Card title="Dernières analyses" subtitle="Cliquer sur une ligne pour ouvrir le Pipeline Live">
        {recent.length === 0 ? (
          <EmptyState>
            Aucune analyse pour l'instant — lance-en une depuis <b>Nouvelle transaction</b> ou le <b>Flux en direct</b>.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400">
                  <th className="pb-2 pr-4">Transaction</th>
                  <th className="pb-2 pr-4">Source</th>
                  <th className="pb-2 pr-4">Type</th>
                  <th className="pb-2 pr-4">Montant</th>
                  <th className="pb-2 pr-4">Décision</th>
                  <th className="pb-2 pr-4">Risque</th>
                  <th className="pb-2">État</th>
                </tr>
              </thead>
              <tbody>
                {recent.map(t => (
                  <tr key={t.tx_id} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="py-2.5 pr-4">
                      <Link to={`pipeline/${t.tx_id}`}
                        className="font-mono text-xs font-semibold text-indigo-700 hover:underline">
                        {t.tx_id}
                      </Link>
                    </td>
                    <td className="py-2.5 pr-4 text-xs text-slate-600"
                        title={t.source === 'SIMULATOR' ? 'Flux bancaire simulé' : `Soumise par ${t.owner ?? 'un agent'}`}>
                      {t.source === 'SIMULATOR' ? '🤖 Flux' : `👤 ${(t.owner ?? 'Agent').split('@')[0]}`}
                    </td>
                    <td className="py-2.5 pr-4 text-xs text-slate-600">{t.transaction.tx_type}</td>
                    <td className="py-2.5 pr-4 tabular-nums text-slate-800">
                      {Number(t.transaction.amount ?? 0).toLocaleString('fr-FR')} {t.transaction.currency}
                    </td>
                    <td className="py-2.5 pr-4"><DecisionBadge value={t.summary?.ca_decision} /></td>
                    <td className="py-2.5 pr-4"><RiskBadge value={t.summary?.risk_level} /></td>
                    <td className="py-2.5"><StateBadge value={t.summary?.state} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
