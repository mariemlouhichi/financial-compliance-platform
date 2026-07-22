import { useEffect, useState } from 'react';
import { CheckCircle2, XCircle, MessageSquarePlus, ShieldCheck, Clock } from 'lucide-react';
import { api, subscribeEvents } from '../api/client';
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

  return (
    <>
      <PageHeader
        title="File superviseur"
        subtitle="Transactions « en cours » (REVIEW / EDD / STR) en attente de décision humaine"
      />

      {/* Séparation des devoirs (SoD) — rendue VISIBLE : seul ce rôle valide. */}
      <div className="mb-4 flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
        <ShieldCheck size={15} className="shrink-0" />
        <span>
          <b>Séparation des devoirs</b> — la validation d'un dossier réglementaire
          est réservée au <b>Compliance Officer</b>. Un administrateur technique ne
          peut jamais approuver ou refuser (contrôlé aussi côté API : 403).
        </span>
      </div>

      {flash && (
        <p className="mb-4 rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-700 shadow-sm">
          {flash}
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        {/* ── Liste ──────────────────────────────────────────────────── */}
        <div className="lg:col-span-2">
          <Card title={`En attente (${pending.length})`}
                subtitle="Triée par urgence : priorité (STR/BLOCK ▸ HAUTE) puis ancienneté">
            {loading ? <Spinner /> : pending.length === 0 ? (
              <EmptyState>Aucune transaction en attente — la file est vide. ✅</EmptyState>
            ) : (
              <ul className="space-y-2">
                {pending.map(t => (
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
          title={<><Clock size={15} className="mr-1.5 inline text-slate-400" />Historique des décisions</>}
          subtitle="Décisions passées — traçabilité complète (qui, quoi, quand, pourquoi)">
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
    </>
  );
}
