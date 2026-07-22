// ============================================================================
// ESPACE AUDIT — Investigation : l'auditeur ne se contente pas d'une lecture
// seule, il dispose de :
//   1. une RECHERCHE TRANSVERSALE (client, période, décision, source)
//   2. la PISTE D'AUDIT des décisions superviseur (qui a fait quoi, quand)
//   3. des STATISTIQUES DE QUALITÉ (faux positifs annotés, temps de
//      traitement, dossiers en attente)
// ============================================================================
import { useEffect, useMemo, useState } from 'react';
import { SearchCheck, ClipboardList, Search } from 'lucide-react';
import { api } from '../api/client';
import type { TxListItem, Stats } from '../types/api';
import { PageHeader } from '../components/Layout';
import { Card, StatTile, Spinner, EmptyState, DecisionBadge, StateBadge } from '../components/ui';
import { Link } from '../router';

const DECISIONS = ['PASS', 'REVIEW', 'EDD', 'STR', 'BLOCK'];
const PERIODS = [
  { key: '', label: 'Toute la période' },
  { key: 'day', label: "Aujourd'hui" },
  { key: 'week', label: '7 jours' },
  { key: 'month', label: '30 jours' },
] as const;

const fmt = (iso?: string) =>
  iso ? new Date(iso).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  }) : '—';

/** Convertit un préréglage de période en borne date_from ISO (SQL). */
function periodToDateFrom(period: string): string | undefined {
  if (!period) return undefined;
  if (period === 'day') {
    const d = new Date(); d.setHours(0, 0, 0, 0); return d.toISOString();
  }
  const days = period === 'week' ? 7 : 30;
  return new Date(Date.now() - days * 86_400_000).toISOString();
}

type QueryRes = { count: number; backend: string; results: TxListItem[] };

export function AuditPage() {
  const [txs, setTxs] = useState<TxListItem[] | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState('');

  // Filtres de la requête SQL (côté serveur)
  const [client, setClient] = useState('');
  const [minAmount, setMinAmount] = useState('');
  const [decision, setDecision] = useState('');
  const [period, setPeriod] = useState('');
  const [queryRes, setQueryRes] = useState<QueryRes | null>(null);
  const [querying, setQuerying] = useState(false);

  // Chargement complet (piste d'audit + indicateurs de qualité)
  useEffect(() => {
    Promise.all([api.transactions(), api.stats()])
      .then(([t, s]) => { setTxs(t); setStats(s); })
      .catch(e => { setError(String(e)); setTxs([]); });
  }, []);

  // Requête SQL côté serveur — relancée à chaque changement de filtre (debounce)
  useEffect(() => {
    const t = setTimeout(async () => {
      setQuerying(true);
      try {
        const res = await api.transactionsQuery({
          minAmount: minAmount ? Number(minAmount) : undefined,
          decision: decision || undefined,
          client: client.trim() || undefined,
          dateFrom: periodToDateFrom(period),
        });
        setQueryRes(res);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
      setQuerying(false);
    }, 300);
    return () => clearTimeout(t);
  }, [minAmount, decision, client, period]);

  const shown = queryRes?.results ?? [];

  // Piste d'audit = tous les dossiers ayant une décision superviseur
  const decided = useMemo(
    () => (txs ?? []).filter(t => t.supervisor_decision)
      .sort((a, b) => (b.supervisor_decision!.at ?? '').localeCompare(
        a.supervisor_decision!.at ?? '')),
    [txs]);

  // Statistiques de qualité (dérivées des annotations superviseur)
  const quality = useMemo(() => {
    const fp = decided.filter(t => t.supervisor_decision!.label === 'FALSE_POSITIVE').length;
    const fraud = decided.filter(t => t.supervisor_decision!.label === 'TRUE_FRAUD').length;
    return { decided: decided.length, fp, fraud };
  }, [decided]);

  if (!txs) return <Spinner label="Chargement des données d'audit…" />;

  return (
    <>
      <PageHeader
        title={<><SearchCheck size={22} className="mr-2 inline text-slate-400" />Investigation</>}
        subtitle="Recherche transversale, piste d'audit des décisions et indicateurs de qualité — lecture seule"
      />

      {error && (
        <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
          {error}
        </p>
      )}

      {/* ── Indicateurs de qualité ─────────────────────────────────────── */}
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatTile label="Dossiers analysés" value={stats?.total_analyzed ?? txs.length} />
        <StatTile label="Décisions superviseur" value={quality.decided}
          hint="piste d'audit ci-dessous" />
        <StatTile label="Faux positifs annotés" value={quality.fp}
          tone={quality.fp > 0 ? 'warning' : 'default'}
          hint="alimente le ré-entraînement ML" />
        <StatTile label="Vraies fraudes annotées" value={quality.fraud}
          tone={quality.fraud > 0 ? 'danger' : 'default'} />
        <StatTile label="Temps moyen pipeline"
          value={`${((stats?.avg_pipeline_ms ?? 0) / 1000).toFixed(1)}s`} />
      </div>

      {/* ── Recherche transversale ─────────────────────────────────────── */}
      <Card
        title="Recherche transversale (SQL)"
        subtitle="Requête exécutée côté serveur — filtres montant, période, décision, client">
        <div className="mb-3 flex flex-wrap items-end gap-2">
          <div>
            <label className="mb-0.5 block text-[10px] font-semibold uppercase tracking-wide text-slate-400">Client</label>
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input value={client} onChange={e => setClient(e.target.value)}
                placeholder="email / compte…"
                className="w-52 rounded-lg border border-slate-300 py-1.5 pl-7 pr-2 text-xs focus:border-slate-900 focus:outline-none" />
            </div>
          </div>
          <div>
            <label className="mb-0.5 block text-[10px] font-semibold uppercase tracking-wide text-slate-400">Montant min.</label>
            <input type="number" min={0} value={minAmount}
              onChange={e => setMinAmount(e.target.value)} placeholder="ex. 10000"
              className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-xs tabular-nums focus:border-slate-900 focus:outline-none" />
          </div>
          <div>
            <label className="mb-0.5 block text-[10px] font-semibold uppercase tracking-wide text-slate-400">Période</label>
            <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5">
              {PERIODS.map(p => (
                <button key={p.key} onClick={() => setPeriod(p.key)}
                  className={`rounded-md px-2.5 py-1 text-[11px] font-semibold ${
                    period === p.key ? 'bg-white text-slate-900 shadow-sm'
                                     : 'text-slate-500 hover:text-slate-700'}`}>
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="mb-0.5 block text-[10px] font-semibold uppercase tracking-wide text-slate-400">Décision</label>
            <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5">
              {['', ...DECISIONS].map(d => (
                <button key={d || 'all'} onClick={() => setDecision(d)}
                  className={`rounded-md px-2.5 py-1 text-[11px] font-semibold ${
                    decision === d ? 'bg-white text-slate-900 shadow-sm'
                                   : 'text-slate-500 hover:text-slate-700'}`}>
                  {d || 'Toutes'}
                </button>
              ))}
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2 self-end pb-1">
            {querying && <span className="text-[11px] text-slate-400">recherche…</span>}
            {queryRes && (
              <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-[11px] font-semibold text-indigo-700 ring-1 ring-indigo-100"
                title="Backend de persistance ayant traité la requête">
                {queryRes.count} résultat(s) · moteur : {queryRes.backend}
              </span>
            )}
          </div>
        </div>

        {shown.length === 0 ? (
          <EmptyState>Aucun dossier ne correspond aux critères.</EmptyState>
        ) : (
          <div className="max-h-[380px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-white">
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400">
                  <th className="pb-2 pr-3">Dossier</th>
                  <th className="pb-2 pr-3">Date</th>
                  <th className="pb-2 pr-3">Client / source</th>
                  <th className="pb-2 pr-3">Montant</th>
                  <th className="pb-2 pr-3">Décision</th>
                  <th className="pb-2">État</th>
                </tr>
              </thead>
              <tbody>
                {shown.map(t => (
                  <tr key={t.tx_id} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="py-2 pr-3">
                      <Link to={`pipeline/${t.tx_id}`}
                        className="font-mono text-xs font-semibold text-indigo-700 hover:underline">
                        {t.tx_id}
                      </Link>
                    </td>
                    <td className="py-2 pr-3 text-xs tabular-nums text-slate-500">{fmt(t.created_at)}</td>
                    <td className="py-2 pr-3 text-xs text-slate-600">
                      {t.owner ?? (t.source === 'SIMULATOR' ? '🤖 flux simulé' : '—')}
                    </td>
                    <td className="py-2 pr-3 text-xs tabular-nums text-slate-800">
                      {Number(t.transaction.amount ?? 0).toLocaleString('fr-FR')} {t.transaction.currency}
                    </td>
                    <td className="py-2 pr-3"><DecisionBadge value={t.summary?.ca_decision} /></td>
                    <td className="py-2"><StateBadge value={t.summary?.state} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── Piste d'audit des décisions superviseur ────────────────────── */}
      <div className="mt-6">
        <Card
          title={<><ClipboardList size={15} className="mr-1.5 inline text-slate-400" />Piste d'audit — décisions superviseur</>}
          subtitle="Qui a décidé quoi, quand, avec quelle justification (source : feedback RLHF)">
          {decided.length === 0 ? (
            <EmptyState>Aucune décision superviseur enregistrée pour l'instant.</EmptyState>
          ) : (
            <ul className="space-y-2">
              {decided.map(t => {
                const d = t.supervisor_decision!;
                const tone = d.action === 'APPROVE'
                  ? 'border-emerald-200 bg-emerald-50'
                  : d.action === 'REJECT' ? 'border-red-200 bg-red-50'
                  : 'border-slate-200 bg-slate-50';
                return (
                  <li key={t.tx_id} className={`rounded-lg border px-3 py-2 text-xs ${tone}`}>
                    <div className="flex flex-wrap items-center gap-2">
                      <Link to={`pipeline/${t.tx_id}`}
                        className="font-mono font-bold text-indigo-700 hover:underline">
                        {t.tx_id}
                      </Link>
                      <b>{d.action}</b>
                      {d.label && (
                        <span className="rounded bg-white/70 px-1.5 py-0.5 font-mono text-[10px]">
                          {d.label}
                        </span>
                      )}
                      <span className="ml-auto text-slate-500">
                        par <b>{d.by}</b> · {fmt(d.at)}
                      </span>
                    </div>
                    {d.annotation && (
                      <p className="mt-1 italic text-slate-600">« {d.annotation} »</p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
