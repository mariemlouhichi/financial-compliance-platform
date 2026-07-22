// ============================================================================
// ESPACE ADMIN — « Flux bancaire en direct » : la banque qui vit.
//   1. FLUX CONTINU  : générateur Core Banking (1 tx / intervalle, clients
//      répétés → historique Redis → LSTM/smurfing fonctionnent réellement)
//   2. SCÉNARIO RÉALISTE : 8 opérations de VRAIS clients (emails réels →
//      « Mes dossiers » + emails de décision), documents joints → docling
//   3. ARRIVÉES EN DIRECT : le fil des transactions qui tombent, en continu
// ============================================================================
import { useEffect, useState } from 'react';
import { Radio, Play, Square, Users, Bot } from 'lucide-react';
import { api } from '../api/client';
import type { SimulatorStatus, TxListItem, Decision } from '../types/api';
import { PageHeader } from '../components/Layout';
import { Card, Spinner, EmptyState, DecisionBadge, RiskBadge, StateBadge } from '../components/ui';
import { Link } from '../router';

// Cohérence attendu → obtenu (tolérance : décision plus stricte acceptée)
function verdictOk(expected: string, got: Decision | null): boolean | null {
  if (!got) return null;
  if (got === expected) return true;
  if (expected === 'PASS') return ['RECORD', 'KYC', 'REVIEW'].includes(got);
  if (expected === 'EDD') return got === 'STR';
  if (expected === 'STR') return got === 'BLOCK';
  return false;
}

const fmt = (iso?: string) =>
  iso ? new Date(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';

export function SimulatorPage() {
  const [status, setStatus] = useState<SimulatorStatus | null>(null);
  const [txs, setTxs] = useState<TxListItem[]>([]);
  const [interval_, setInterval_] = useState(30);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const refresh = async () => {
    try {
      const [s, t] = await Promise.all([api.simulatorStatus(), api.transactions()]);
      setStatus(s);
      setTxs(t.slice(0, 12));
    } catch { /* backend down */ }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 4_000);
    return () => clearInterval(id);
  }, []);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError('');
    try { await fn(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const run = status?.speedrun ?? [];
  const doneCount = run.filter(r => r.status !== 'RUNNING').length;

  return (
    <>
      <PageHeader
        title="Flux bancaire en direct"
        subtitle="Deux sources d'opérations : le générateur continu (Core Banking simulé) et le scénario clients réels"
      />

      {error && (
        <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        {/* ── 1. FLUX CONTINU (générateur) ─────────────────────────────── */}
        <Card
          title={<>
            <Bot size={15} className={`mr-1.5 inline ${
              status?.running && status.mode !== 'speedrun'
                ? 'animate-pulse text-emerald-500' : 'text-slate-400'}`} />
            Flux continu — générateur
          </>}
          subtitle="1 transaction / intervalle · clients répétés → l'historique Redis se construit → smurfing & LSTM">
          <div className="flex flex-wrap items-center gap-3">
            <select value={interval_} onChange={e => setInterval_(Number(e.target.value))}
              disabled={status?.running}
              className="rounded-lg border border-slate-200 bg-slate-50/70 px-2.5 py-2 text-xs disabled:opacity-50">
              {[15, 30, 60, 120].map(s => <option key={s} value={s}>toutes les {s} s</option>)}
            </select>
            <button
              onClick={() => act(() => status?.running ? api.simulatorStop() : api.simulatorStart(interval_))}
              disabled={busy || status === null || (!!status?.running && status.mode === 'speedrun')}
              className={`flex items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-bold text-white disabled:opacity-50 ${
                status?.running && status.mode !== 'speedrun'
                  ? 'bg-red-600 hover:bg-red-700' : 'bg-emerald-600 hover:bg-emerald-700'}`}>
              {status?.running && status.mode !== 'speedrun'
                ? <><Square size={13} /> Arrêter</>
                : <><Play size={13} /> Démarrer</>}
            </button>
            <span className="text-xs text-slate-500">
              {status?.generated ?? 0} générée(s)
              {status?.last_tx_id && <> · <Link to={`pipeline/${status.last_tx_id}`}
                className="font-mono font-semibold text-indigo-600 hover:underline">
                {status.last_tx_id}</Link></>}
            </span>
          </div>
        </Card>

        {/* ── 2. SCÉNARIO RÉALISTE (clients réels) ─────────────────────── */}
        <Card
          title={<>
            <Users size={15} className={`mr-1.5 inline ${
              status?.mode === 'speedrun' ? 'animate-pulse text-indigo-500' : 'text-slate-400'}`} />
            Scénario clients réels
          </>}
          subtitle="8 opérations de vrais comptes utilisateurs — documents joints (docling), dépôts répétés (smurfing), emails de décision">
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => act(() => status?.mode === 'speedrun' ? api.simulatorStop() : api.speedrunStart())}
              disabled={busy || status === null || (!!status?.running && status.mode !== 'speedrun')}
              className={`flex items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-bold text-white disabled:opacity-50 ${
                status?.mode === 'speedrun'
                  ? 'bg-red-600 hover:bg-red-700' : 'bg-indigo-600 hover:bg-indigo-500'}`}>
              {status?.mode === 'speedrun'
                ? <><Square size={13} /> Arrêter</>
                : <><Play size={13} /> Lancer le scénario</>}
            </button>
            {run.length > 0 && (
              <span className="text-xs text-slate-500">
                {doneCount}/{run.length} opération(s)
                {status?.mode === 'speedrun' && (
                  <span className="ml-1 animate-pulse text-indigo-500">· en cours…</span>
                )}
              </span>
            )}
          </div>
        </Card>
      </div>

      {/* ── Résultats du scénario (attendu → obtenu) ────────────────────── */}
      {run.length > 0 && (
        <div className="mb-6">
          <Card title="Déroulé du scénario"
                subtitle="Chaque opération est rattachée au compte du client — visible dans son espace « Mes dossiers »">
            <div className="grid gap-1.5 md:grid-cols-2">
              {run.map(r => {
                const v = verdictOk(r.expected, r.got);
                return (
                  <Link key={r.tx_id} to={`pipeline/${r.tx_id}`}
                    className="flex items-center gap-2.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs transition-colors hover:border-indigo-300">
                    <span className="w-5 text-center text-sm">
                      {r.status === 'RUNNING' ? '⏳' : v === null ? '—' : v ? '✅' : '❌'}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-slate-600">{r.label}</span>
                    <span className="flex shrink-0 items-center gap-1.5">
                      <span className="text-[10px] text-slate-400">attendu</span>
                      <DecisionBadge value={r.expected as Decision} />
                      <span className="text-slate-300">→</span>
                      <DecisionBadge value={r.got ?? undefined} />
                    </span>
                  </Link>
                );
              })}
            </div>
          </Card>
        </div>
      )}

      {/* ── 3. ARRIVÉES EN DIRECT ───────────────────────────────────────── */}
      <Card
        title={<>
          <Radio size={15} className={`mr-1.5 inline ${
            status?.running ? 'animate-pulse text-emerald-500' : 'text-slate-400'}`} />
          Arrivées en direct
        </>}
        subtitle="Les dernières opérations reçues, toutes sources confondues — rafraîchi toutes les 4 s">
        {txs.length === 0 ? (
          status === null ? <Spinner label="Connexion au flux…" /> :
          <EmptyState>Aucune opération pour l'instant — démarre un flux ci-dessus.</EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400">
                  <th className="pb-2 pr-4">Heure</th>
                  <th className="pb-2 pr-4">Transaction</th>
                  <th className="pb-2 pr-4">Client</th>
                  <th className="pb-2 pr-4">Type</th>
                  <th className="pb-2 pr-4">Montant</th>
                  <th className="pb-2 pr-4">Décision</th>
                  <th className="pb-2 pr-4">Risque</th>
                  <th className="pb-2">État</th>
                </tr>
              </thead>
              <tbody>
                {txs.map(t => (
                  <tr key={t.tx_id} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="py-2.5 pr-4 text-xs tabular-nums text-slate-400">
                      {fmt(t.created_at)}
                    </td>
                    <td className="py-2.5 pr-4">
                      <Link to={`pipeline/${t.tx_id}`}
                        className="font-mono text-xs font-semibold text-indigo-600 hover:underline">
                        {t.tx_id}
                      </Link>
                    </td>
                    <td className="py-2.5 pr-4 text-xs text-slate-600">
                      {t.source === 'SIMULATOR' ? '🤖 générateur'
                        : `👤 ${(t.owner ?? '').split('@')[0] || 'client'}`}
                    </td>
                    <td className="py-2.5 pr-4 text-xs text-slate-600">{t.transaction.tx_type}</td>
                    <td className="py-2.5 pr-4 tabular-nums text-slate-800">
                      {Number(t.transaction.amount ?? 0).toLocaleString('fr-FR')} {t.transaction.currency}
                    </td>
                    <td className="py-2.5 pr-4">
                      {t.status === 'RUNNING'
                        ? <span className="text-xs font-medium text-indigo-500">analyse…</span>
                        : <DecisionBadge value={t.summary?.ca_decision} />}
                    </td>
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
