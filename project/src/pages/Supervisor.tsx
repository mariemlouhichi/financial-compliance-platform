import { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, XCircle, MessageSquarePlus, ShieldCheck, Clock,
         Play, Loader2, TrendingUp, TrendingDown } from 'lucide-react';
import { api, subscribeEvents, type RlhfSummary, type RlhfCycleReport } from '../api/client';
import type { TxListItem, QueueItem, JobResult } from '../types/api';
import type { Session } from '../auth';
import { PageHeader } from '../components/Layout';
import { Card, KV, DecisionBadge, RiskBadge, StateBadge, EmptyState, Spinner } from '../components/ui';
import { Link } from '../router';

type Action = 'APPROVE' | 'REJECT' | 'ANNOTATE';

const PRIORITY_STYLE: Record<string, string> = {
  HAUTE: 'bg-red-100 text-red-700 ring-red-200',
  MOYENNE: 'bg-amber-100 text-amber-700 ring-amber-200',
  NORMALE: 'bg-slate-100 text-slate-600 ring-slate-200',
};

/** Ancienneté lisible pour le tri d'urgence (min / h / j). */
function fmtAge(h: number | null): string {
  if (h == null) return '';
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `${Math.round(h)} h`;
  return `${Math.round(h / 24)} j`;
}

const fmtDate = (iso?: string) =>
  iso ? new Date(iso).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  }) : '—';

export function SupervisorPage({ session }: { session: Session }) {
  const [pending, setPending] = useState<QueueItem[]>([]);
  const [history, setHistory] = useState<TxListItem[]>([]);
  const [selected, setSelected] = useState<JobResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [annotation, setAnnotation] = useState('');
  const [labelTag, setLabelTag] = useState('CORRECT');
  const [sending, setSending] = useState(false);
  const [flash, setFlash] = useState('');
  const [period, setPeriod] = useState('');   // '' | day | week | month

  const load = async () => {
    setLoading(true);
    try {
      // File enrichie (priorité + ancienneté, triée par urgence) + historique,
      // servis côté serveur depuis les dossiers persistés (PostgreSQL).
      const [queue, hist] = await Promise.all([
        api.supervisorQueue(), api.supervisorHistory(),
      ]);
      setPending(queue);
      setHistory(hist);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // Temps réel : la file et l'historique se rafraîchissent dès qu'une
    // analyse se termine ou qu'une décision est prise (push SSE).
    const unsub = subscribeEvents(() => load());
    return unsub;
  }, []);

  const open = async (txId: string) => {
    setSelected(await api.result(txId));
    setAnnotation('');
  };

  const decide = async (action: Action) => {
    if (!selected) return;
    if (!annotation.trim()) { setFlash('⚠️ Annotation obligatoire pour toute décision.'); return; }
    setSending(true);
    try {
      await api.validate(selected.tx_id, {
        action, label: labelTag, annotation: annotation.trim(),
        supervisor_id: session.username,
      });
      setFlash(`✅ ${selected.tx_id} — décision « ${action} » enregistrée (feedback RLHF).`);
      setSelected(null);
      await load();
    } catch (e) {
      setFlash(`Erreur : ${e}`);
    } finally {
      setSending(false);
    }
  };

  // Filtre par periode + tri du plus RECENT au plus ancien : un dossier
  // qui vient d'etre soumis doit apparaitre en tete de file.
  const shownPending = useMemo(() => {
    const day = 86_400_000;
    const horizon = period === 'day' ? day : period === 'week' ? 7 * day
      : period === 'month' ? 30 * day : Infinity;
    return pending
      .filter(t => Date.now() - new Date(t.created_at).getTime() <= horizon)
      .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  }, [pending, period]);

  return (
    <>
      <PageHeader
        title="File superviseur"
      />

      {flash && (
        <p className="mb-4 rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-700 shadow-sm">
          {flash}
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        {/* ── Liste ──────────────────────────────────────────────────── */}
        <div className="lg:col-span-2">
          <Card title={`En attente (${shownPending.length}${
                  shownPending.length !== pending.length ? ` / ${pending.length}` : ''})`}
                actions={
                  <select value={period} onChange={e => setPeriod(e.target.value)}
                    className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 focus:border-indigo-400 focus:outline-none">
                    <option value="">Toute la période</option>
                    <option value="day">Aujourd'hui</option>
                    <option value="week">7 derniers jours</option>
                    <option value="month">30 derniers jours</option>
                  </select>}>
            {loading ? <Spinner /> : shownPending.length === 0 ? (
              <EmptyState>Aucune transaction en attente — la file est vide. ✅</EmptyState>
            ) : (
              <ul className="space-y-2">
                {shownPending.map(t => (
                  <li key={t.tx_id}>
                    <button onClick={() => open(t.tx_id)}
                      className={`w-full rounded-lg border px-3 py-2.5 text-left transition-colors ${
                        selected?.tx_id === t.tx_id
                          ? 'border-slate-900 bg-slate-50'
                          : 'border-slate-200 hover:border-slate-400'
                      }`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-xs font-semibold text-slate-800">{t.tx_id}</span>
                        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ring-1 ${PRIORITY_STYLE[t.priority] ?? PRIORITY_STYLE.NORMALE}`}>
                          {t.priority}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center justify-between gap-2">
                        <DecisionBadge value={t.summary?.ca_decision} />
                        <span className="flex shrink-0 items-center gap-1 text-[10px] text-slate-400"
                          title="Ancienneté dans la file">
                          <Clock size={10} /> en attente {fmtAge(t.age_hours)}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        {t.transaction.tx_type} · {Number(t.transaction.amount ?? 0).toLocaleString('fr-FR')}{' '}
                        {t.transaction.currency} · {t.transaction.sender_country} → {t.transaction.destination_country}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        {/* ── Détail + décision ──────────────────────────────────────── */}
        <div className="lg:col-span-3">
          {!selected ? (
            <Card title="Détail">
              <EmptyState>Sélectionne une transaction dans la file pour instruire le dossier.</EmptyState>
            </Card>
          ) : (
            <Card
              title={<>Dossier <span className="font-mono">{selected.tx_id}</span></>}
              actions={
                <Link to={`pipeline/${selected.tx_id}`}
                  className="text-xs font-semibold text-indigo-700 hover:underline">
                  Pipeline complet →
                </Link>
              }>
              <div className="grid gap-x-8 sm:grid-cols-2">
                <KV k="Décision système" v={<DecisionBadge value={selected.summary?.ca_decision} />} />
                <KV k="Niveau de risque" v={<RiskBadge value={selected.summary?.risk_level} />} />
                <KV k="État" v={<StateBadge value={selected.summary?.state} />} />
                <KV k="Pattern AML" v={selected.steps.sar?.aml_pattern ?? '—'} />
                <KV k="Divergence RAG↔ML" v={selected.steps.sar?.divergence_flag ?? '—'} />
                <KV k="ML (indicatif)" v={selected.summary?.ml_decision ?? '—'} />
              </div>

              {selected.steps.sar?.divergence_action && (
                <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  {selected.steps.sar.divergence_action}
                </p>
              )}

              {(selected.steps.compliance?.violated_rules?.length ?? 0) > 0 && (
                <div className="mt-3">
                  <p className="mb-1 text-xs font-semibold text-slate-600">Obligations violées :</p>
                  {selected.steps.compliance!.violated_rules.slice(0, 4).map((r, i) => (
                    <p key={i} className="text-xs text-red-700">
                      • [{r.severity}] {r.source_doc} {r.rule_ref} (réf. {r.fatf_rec})
                    </p>
                  ))}
                </div>
              )}

              {(selected.steps.sar?.required_documents?.length ?? 0) > 0 && (
                <div className="mt-3">
                  <p className="mb-1 text-xs font-semibold text-slate-600">Documents requis :</p>
                  {selected.steps.sar!.required_documents.map((d, i) => (
                    <p key={i} className="text-xs text-slate-600">{i + 1}. {d}</p>
                  ))}
                </div>
              )}

              {/* ── Bloc décision ──────────────────────────────────── */}
              <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Décision du superviseur
                </p>
                <div className="mb-3 grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="mb-1 block text-xs font-semibold text-slate-600">
                      Votre verdict sur la décision du système
                    </label>
                    <select value={labelTag} onChange={e => setLabelTag(e.target.value)}
                      className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
                      <option value="CORRECT">✅ La décision est correcte — le système a bien jugé</option>
                      <option value="FALSE_POSITIVE">🟡 Fausse alerte — le système a signalé à tort une opération légitime</option>
                      <option value="TRUE_FRAUD">🔴 Fraude confirmée — l'alerte était justifiée</option>
                    </select>
                    <p className="mt-1 text-[10px] text-slate-400">
                      Ce verdict entraîne le système (apprentissage par feedback / RLHF).
                    </p>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-slate-500">
                      Annotation (obligatoire, tracée dans l'audit)
                    </label>
                    <textarea value={annotation} onChange={e => setAnnotation(e.target.value)}
                      rows={2} placeholder="Justification de la décision…"
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => decide('APPROVE')} disabled={sending}
                    className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-xs font-bold text-white hover:bg-emerald-700 disabled:opacity-50">
                    <CheckCircle2 size={14} /> Approuver
                  </button>
                  <button onClick={() => decide('REJECT')} disabled={sending}
                    className="flex items-center gap-1.5 rounded-lg bg-red-600 px-4 py-2 text-xs font-bold text-white hover:bg-red-700 disabled:opacity-50">
                    <XCircle size={14} /> Refuser
                  </button>
                  <button onClick={() => decide('ANNOTATE')} disabled={sending}
                    className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-4 py-2 text-xs font-bold text-slate-700 hover:bg-slate-100 disabled:opacity-50">
                    <MessageSquarePlus size={14} /> Annoter seulement
                  </button>
                </div>
                <p className="mt-2 text-[11px] text-slate-400">
                  Chaque décision émet un FeedbackEvent (output/supervisor_feedback.jsonl) —
                  graine des boucles d'amélioration continue.
                </p>
              </div>
            </Card>
          )}
        </div>
      </div>

      {/* ── Historique des décisions superviseur ───────────────────────── */}
      <div className="mt-6">
        <Card
          title={<><Clock size={15} className="mr-1.5 inline text-slate-400" />Historique des décisions</>}>
          {history.length === 0 ? (
            <EmptyState>Aucune décision enregistrée pour l'instant.</EmptyState>
          ) : (
            <ul className="space-y-2">
              {history.map(t => {
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
                        par <b>{d.by}</b> · {fmtDate(d.at)}
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

      {/* ── Amélioration continue (RLHF) ────────────────────────────────── */}
      <div className="mt-6">
        <RlhfPanel />
      </div>
    </>
  );
}

// ── Panneau RLHF : boucle d'amélioration continue à partir du feedback ───
//  Montre l'accord système/expert + le taux de fausses alertes, et permet de
//  LANCER un cycle (warm-start garde-fou) et d'en voir le verdict avant/après.
const fmtPct = (v?: number) => (v == null ? '—' : `${(v * 100).toFixed(1)} %`);

function RlhfPanel() {
  const [sum, setSum] = useState<RlhfSummary | null>(null);
  const [report, setReport] = useState<RlhfCycleReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const load = () => api.rlhfSummary().then(setSum).catch(e => setErr(String(e)));
  useEffect(() => { load(); }, []);

  const run = async (force: boolean) => {
    setErr(''); setBusy(true); setReport(null);
    try {
      const r = await api.rlhfRun(force);
      setReport(r);
      await load();   // rafraîchit les métriques du modèle
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  const s = sum?.stats;
  const t = report?.training;
  const before = t?.metrics_before, after = t?.metrics_after;

  return (
    <Card
      title={<><ShieldCheck size={15} className="mr-1.5 inline text-slate-400" />Amélioration continue (RLHF)</>}>
      {/* Stats de feedback */}
      <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <KV k="Étiquettes" v={<b>{s?.labelled ?? '—'}</b>} />
        <KV k="Accord système/expert" v={fmtPct(s?.agreement_rate)} />
        <KV k="Taux de fausses alertes"
          v={<span className={s && s.false_positive_rate > (sum?.thresholds.fp_alert ?? 0.3) ? 'font-bold text-red-600' : ''}>
            {fmtPct(s?.false_positive_rate)}</span>} />
        <KV k="Seuil modèle actuel"
          v={sum?.model.threshold != null ? sum.model.threshold.toFixed(4) : '—'} />
      </div>

      <p className="mb-3 text-[11px] text-slate-500">
        🛡️ Garde-fous : volume minimum <b>{sum?.thresholds.min_labels ?? 20}</b> étiquettes ·
        déploiement <b>seulement si F1 et PR-AUC ne régressent pas</b> · trace auditable dans{' '}
        <code className="rounded bg-slate-100 px-1">output/retraining/</code>.
        {sum?.model.last_rlhf && (
          <span className="ml-1 text-emerald-600">
            Dernier warm-start : {sum.model.last_rlhf.feedback_used} feedback(s).
          </span>
        )}
      </p>

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        <button onClick={() => run(false)} disabled={busy}
          className="flex items-center gap-1.5 rounded-lg bg-slate-900 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          Lancer un cycle RLHF
        </button>
        <button onClick={() => run(true)} disabled={busy}
          className="rounded-lg border border-slate-300 px-4 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40"
          title="Ignore le seuil de volume — pour la démonstration">
          Forcer (démo)
        </button>
      </div>

      {err && <p className="mt-2 text-xs text-red-600">{err}</p>}

      {/* Rapport du cycle */}
      {report && (
        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50/60 p-3">
          <div className="mb-2 flex items-center gap-2">
            <span className={`rounded px-2 py-0.5 text-[11px] font-bold ${
              t?.deployed ? 'bg-emerald-100 text-emerald-800'
              : t?.status === 'VOLUME_INSUFFISANT' ? 'bg-amber-100 text-amber-800'
              : 'bg-slate-200 text-slate-700'}`}>
              {t?.deployed ? '✓ MODÈLE DÉPLOYÉ' : `⟲ ${t?.status ?? 'NON DÉPLOYÉ'}`}
            </span>
            {t?.duration_s != null && (
              <span className="text-[11px] text-slate-400">en {t.duration_s}s</span>
            )}
          </div>
          <p className="mb-2 text-xs text-slate-600">{t?.reason ?? t?.detail ?? report.decision.reason}</p>

          {before && after && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wide text-slate-400">
                    <th className="pb-1 pr-4">Métrique</th>
                    <th className="pb-1 pr-4">Avant</th>
                    <th className="pb-1 pr-4">Après</th>
                    <th className="pb-1">Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {(['pr_auc', 'f1', 'precision', 'recall', 'threshold'] as const).map(k => {
                    const b = before[k], a = after[k];
                    const delta = (a != null && b != null) ? a - b : null;
                    return (
                      <tr key={k} className="border-t border-slate-100">
                        <td className="py-1 pr-4 font-medium text-slate-600">{k}</td>
                        <td className="py-1 pr-4 tabular-nums text-slate-500">{b?.toFixed(4) ?? '—'}</td>
                        <td className="py-1 pr-4 tabular-nums font-semibold text-slate-800">{a?.toFixed(4) ?? '—'}</td>
                        <td className="py-1 tabular-nums">
                          {delta == null ? '—' : (
                            <span className={`inline-flex items-center gap-0.5 ${
                              delta > 0 ? 'text-emerald-600' : delta < 0 ? 'text-red-600' : 'text-slate-400'}`}>
                              {delta > 0 ? <TrendingUp size={11} /> : delta < 0 ? <TrendingDown size={11} /> : null}
                              {delta > 0 ? '+' : ''}{delta.toFixed(4)}
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {t?.feedback && (
            <p className="mt-2 text-[11px] text-slate-500">
              Dataset feedback : {t.feedback.usable} exemple(s)
              ({t.feedback.positives} fraude / {t.feedback.negatives} normal)
              {t.feedback.missing_features > 0 &&
                ` · ${t.feedback.missing_features} feedback(s) sans features (transactions antérieures)`}
            </p>
          )}
        </div>
      )}
    </Card>
  );
}
