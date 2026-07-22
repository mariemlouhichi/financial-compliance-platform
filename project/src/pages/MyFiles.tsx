// ============================================================================
// ESPACE UTILISATEUR — « Mes dossiers » : l'agent bancaire suit les
// transactions QU'IL a soumises (GET /api/transactions?mine=true, filtrées
// par owner côté backend via le token) + notifications dérivées des états
// (justificatifs requis, validation superviseur, blocage).
// ============================================================================
import { useEffect, useMemo, useState } from 'react';
import { RefreshCw, Bell, FileWarning, CheckCircle2, XCircle, Hourglass, Search } from 'lucide-react';
import { api, subscribeEvents } from '../api/client';
import type { TxListItem, TxState } from '../types/api';
import type { Session } from '../auth';
import { PageHeader } from '../components/Layout';
import { Card, StatTile, Spinner, EmptyState, DecisionBadge, RiskBadge, StateBadge } from '../components/ui';
import { AreaChart, Donut, DECISION_COLORS, DECISION_HEX } from '../components/charts';
import { Link } from '../router';

const DECISIONS = ['PASS', 'REVIEW', 'EDD', 'STR', 'BLOCK'] as const;

const fmtDate = (iso?: string) =>
  iso ? new Date(iso).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  }) : '—';

const MONTH_LABELS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin',
                      'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

interface Notif {
  txId: string;
  tone: 'warning' | 'success' | 'danger' | 'info';
  Icon: typeof Bell;
  text: string;
}

/** Dérive les notifications métier depuis l'état workflow de chaque dossier. */
function toNotifs(txs: TxListItem[]): Notif[] {
  const out: Notif[] = [];
  for (const t of txs) {
    const state = t.summary?.state as TxState | undefined;
    if (!state) continue;
    if (state === 'PENDING_DOCUMENTS') {
      out.push({
        txId: t.tx_id, tone: 'warning', Icon: FileWarning,
        text: 'Justificatifs requis — demandez les documents au client puis transmettez-les à la conformité.',
      });
    } else if (state === 'PENDING_REVIEW') {
      out.push({
        txId: t.tx_id, tone: 'info', Icon: Hourglass,
        text: 'Dossier en cours d’examen par le superviseur conformité.',
      });
    } else if (state === 'APPROVED_BY_SUPERVISOR') {
      out.push({
        txId: t.tx_id, tone: 'success', Icon: CheckCircle2,
        text: `Validé par le superviseur${t.supervisor_decision ? ` (${t.supervisor_decision.by})` : ''} — l'opération peut être exécutée.`,
      });
    } else if (state === 'REJECTED_BY_SUPERVISOR' || state === 'BLOCKED') {
      out.push({
        txId: t.tx_id, tone: 'danger', Icon: XCircle,
        text: state === 'BLOCKED'
          ? 'Opération BLOQUÉE par le système (violation réglementaire dure).'
          : 'Opération refusée par le superviseur — informer le client.',
      });
    }
  }
  return out;
}

const NOTIF_STYLES: Record<Notif['tone'], string> = {
  warning: 'border-amber-200 bg-amber-50 text-amber-800',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  danger: 'border-red-200 bg-red-50 text-red-800',
  info: 'border-slate-200 bg-slate-50 text-slate-600',
};

export function MyFilesPage({ session }: { session: Session }) {
  const [txs, setTxs] = useState<TxListItem[] | null>(null);
  const [error, setError] = useState('');
  const [decisionFilter, setDecisionFilter] = useState('');   // '' = toutes
  const [query, setQuery] = useState('');                     // ID / montant / pays
  const [period, setPeriod] = useState('');                   // '' | day | week | month

  const load = async () => {
    try {
      setTxs(await api.transactions({ mine: true }));
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setTxs([]);
    }
  };

  useEffect(() => {
    load();
    // Temps réel : push SSE à chaque changement d'état (analyse/décision).
    const unsub = subscribeEvents(() => load());
    // Filet de sécurité lent (si le flux SSE tombe) — plus le polling 10 s.
    const id = setInterval(load, 45_000);
    return () => { unsub(); clearInterval(id); };
  }, []);

  // Filtre décision + période + recherche (ID, montant, pays) — côté client
  const shown = useMemo(() => {
    if (!txs) return [];
    const q = query.trim().toLowerCase();
    const day = 86_400_000;
    const horizon = period === 'day' ? day : period === 'week' ? 7 * day
      : period === 'month' ? 30 * day : Infinity;
    return txs.filter(t => {
      if (decisionFilter && t.summary?.ca_decision !== decisionFilter) return false;
      if (Date.now() - new Date(t.created_at).getTime() > horizon) return false;
      if (!q) return true;
      const hay = [
        t.tx_id, String(t.transaction.amount ?? ''),
        t.transaction.sender_country ?? '', t.transaction.destination_country ?? '',
        t.transaction.tx_type ?? '', t.transaction.currency ?? '',
      ].join(' ').toLowerCase();
      return hay.includes(q);
    });
  }, [txs, decisionFilter, query]);

  // Données des 2 graphiques (sur TOUS mes dossiers, pas le filtre)
  const monthlyRows = useMemo(() => {
    const by: Record<string, number> = {};
    for (const t of txs ?? []) {
      const d = new Date(t.created_at);
      if (isNaN(d.getTime())) continue;
      by[`${d.getFullYear()}-${String(d.getMonth()).padStart(2, '0')}`] =
        (by[`${d.getFullYear()}-${String(d.getMonth()).padStart(2, '0')}`] ?? 0) + 1;
    }
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b)).slice(-6)
      .map(([k, v]) => {
        const [y, m] = k.split('-');
        return { label: `${MONTH_LABELS[Number(m)]} ${y.slice(2)}`, value: v,
                 color: 'bg-indigo-500' };
      });
  }, [txs]);

  const decisionRows = useMemo(() => {
    const by: Record<string, number> = {};
    for (const t of txs ?? []) {
      const d = t.summary?.ca_decision;
      if (d) by[d] = (by[d] ?? 0) + 1;
    }
    return DECISIONS.filter(d => by[d]).map(d => ({
      label: d, value: by[d], color: DECISION_COLORS[d] ?? 'bg-slate-400',
    }));
  }, [txs]);

  if (!txs) return <Spinner label="Chargement de vos dossiers…" />;

  const notifs = toNotifs(txs);
  const pending = txs.filter(t => (t.summary?.state ?? '').startsWith('PENDING')).length;
  const approved = txs.filter(t =>
    ['APPROVED', 'APPROVED_BY_SUPERVISOR'].includes(t.summary?.state ?? '')).length;

  return (
    <>
      <PageHeader
        title="Mes dossiers"
        subtitle={`Transactions soumises par ${session.name || session.username} — statuts, résultats et notifications`}
        actions={
          <button onClick={load}
            className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
            <RefreshCw size={13} /> Actualiser
          </button>
        }
      />

      {error && (
        <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
          {error}
        </p>
      )}

      {/* ── KPIs personnels ─────────────────────────────────────────── */}
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Dossiers soumis" value={txs.length} />
        <StatTile label="En attente" value={pending}
          tone={pending > 0 ? 'warning' : 'default'} hint="documents ou superviseur" />
        <StatTile label="Approuvés" value={approved} tone="success" />
        <StatTile label="Notifications" value={notifs.length}
          tone={notifs.length > 0 ? 'warning' : 'default'} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ── Notifications ───────────────────────────────────────────── */}
        <div>
          <Card title={<><Bell size={15} className="mr-1.5 inline text-slate-400" />Notifications</>}>
            {notifs.length === 0 ? (
              <EmptyState>Aucune action requise. ✅</EmptyState>
            ) : (
              <ul className="space-y-2">
                {notifs.map((n, i) => (
                  <li key={i}
                    className={`rounded-lg border px-3 py-2.5 text-xs ${NOTIF_STYLES[n.tone]}`}>
                    <div className="mb-1 flex items-center gap-1.5 font-bold">
                      <n.Icon size={13} />
                      <Link to={`pipeline/${n.txId}`} className="font-mono hover:underline">
                        {n.txId}
                      </Link>
                    </div>
                    {n.text}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        {/* ── Table des dossiers ──────────────────────────────────────── */}
        <div className="lg:col-span-2">
          <Card title="Historique de mes soumissions"
                subtitle="Cliquer sur un dossier pour revoir l'analyse complète (8 étapes)">
            {/* ── Barre de filtres (recherche · période · décisions) ──── */}
            <div className="mb-4 rounded-xl border border-slate-200 bg-slate-50/70 p-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative">
                  <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input value={query} onChange={e => setQuery(e.target.value)}
                    placeholder="Rechercher (ID, montant, pays…)"
                    className="w-56 rounded-lg border border-slate-200 bg-white py-1.5 pl-7 pr-2 text-xs focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100" />
                </div>
                <select value={period} onChange={e => setPeriod(e.target.value)}
                  className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 focus:border-indigo-400 focus:outline-none">
                  <option value="">Toute la période</option>
                  <option value="day">Aujourd'hui</option>
                  <option value="week">7 derniers jours</option>
                  <option value="month">30 derniers jours</option>
                </select>
                <span className="ml-auto text-[11px] text-slate-400">
                  {shown.length} / {txs.length} dossier(s)
                </span>
              </div>
              {/* décisions = chips colorées (couleur de statut quand actif) */}
              <div className="mt-2 flex flex-wrap gap-1.5">
                <button onClick={() => setDecisionFilter('')}
                  className={`rounded-full px-2.5 py-1 text-[10px] font-bold transition-colors ${
                    decisionFilter === ''
                      ? 'bg-slate-800 text-white' : 'bg-white text-slate-500 ring-1 ring-slate-200 hover:ring-slate-300'}`}>
                  Toutes
                </button>
                {DECISIONS.map(d => {
                  const on = decisionFilter === d;
                  return (
                    <button key={d} onClick={() => setDecisionFilter(on ? '' : d)}
                      className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-[10px] font-bold transition-colors ${
                        on ? `${DECISION_COLORS[d] ?? 'bg-slate-500'} text-white`
                           : 'bg-white text-slate-500 ring-1 ring-slate-200 hover:ring-slate-300'}`}>
                      <span className={`h-1.5 w-1.5 rounded-full ${on ? 'bg-white/80' : DECISION_COLORS[d] ?? 'bg-slate-400'}`} />
                      {d}
                    </button>
                  );
                })}
              </div>
            </div>

            {shown.length === 0 ? (
              <EmptyState>
                {txs.length === 0
                  ? <>Aucun dossier — soumettez une opération depuis <b>Nouvelle transaction</b>.</>
                  : <>Aucun dossier ne correspond au filtre.</>}
              </EmptyState>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400">
                      <th className="pb-2 pr-4">Dossier</th>
                      <th className="pb-2 pr-4">Soumis le</th>
                      <th className="pb-2 pr-4">Type</th>
                      <th className="pb-2 pr-4">Montant</th>
                      <th className="pb-2 pr-4">Décision</th>
                      <th className="pb-2 pr-4">Risque</th>
                      <th className="pb-2">État</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map(t => (
                      <tr key={t.tx_id} className="border-b border-slate-100 hover:bg-slate-50">
                        <td className="py-2.5 pr-4">
                          <Link to={`pipeline/${t.tx_id}`}
                            className="font-mono text-xs font-semibold text-indigo-700 hover:underline">
                            {t.tx_id}
                          </Link>
                        </td>
                        <td className="py-2.5 pr-4 text-xs tabular-nums text-slate-500">
                          {fmtDate(t.created_at)}
                        </td>
                        <td className="py-2.5 pr-4 text-xs text-slate-600">{t.transaction.tx_type}</td>
                        <td className="py-2.5 pr-4 tabular-nums text-slate-800">
                          {Number(t.transaction.amount ?? 0).toLocaleString('fr-FR')} {t.transaction.currency}
                        </td>
                        <td className="py-2.5 pr-4"><DecisionBadge value={t.summary?.ca_decision} /></td>
                        <td className="py-2.5 pr-4"><RiskBadge value={t.summary?.risk_level} /></td>
                        <td className="py-2.5">
                          {t.status === 'RUNNING'
                            ? <span className="text-xs font-medium text-slate-400">analyse en cours…</span>
                            : <StateBadge value={t.summary?.state} />}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* ── Graphiques : évolution (aire) + répartition (donut) ────────── */}
      {txs.length > 0 && (
        <div className="mt-6 grid gap-6 lg:grid-cols-5">
          <div className="lg:col-span-3">
            <Card title="Évolution de mes soumissions"
                  subtitle="Nombre de dossiers par mois">
              <AreaChart points={monthlyRows.map(r => ({ label: r.label, value: r.value }))} />
            </Card>
          </div>
          <div className="lg:col-span-2">
            <Card title="Répartition des décisions"
                  subtitle="Couleurs de statut réglementaire">
              {decisionRows.length === 0
                ? <EmptyState>Aucune décision.</EmptyState>
                : <Donut centerLabel="dossiers"
                    segments={decisionRows.map(r => ({
                      label: r.label, value: r.value,
                      hex: DECISION_HEX[r.label] ?? '#94a3b8' }))} />}
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
