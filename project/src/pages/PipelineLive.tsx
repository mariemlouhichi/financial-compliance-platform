import { useEffect, useState, useRef } from 'react';
import {
  Gauge, UserRound, ListTodo, DatabaseZap, Scale, BrainCircuit,
  FileText, GitBranch, CheckCircle2, Loader2, X, Upload,
} from 'lucide-react';
import { api, pollResult } from '../api/client';
import type { JobResult, StepName, JustificatifResult } from '../types/api';
import type { Session } from '../auth';
import { PageHeader } from '../components/Layout';
import { Card, KV, DecisionBadge, RiskBadge, StateBadge, EmptyState, Spinner } from '../components/ui';
import { ScoreGauge } from '../components/charts';
import { MarkdownView, printMarkdown } from '../components/Markdown';
import { SarView, printSar } from '../components/SarReport';

const STEPS: { key: StepName; label: string; desc: string; Icon: typeof Gauge }[] = [
  { key: 'level1',         label: 'Level 1 — Règles fixes',   desc: 'Sanctions ONU, seuils BCT, pays FATF (0% LLM)', Icon: Gauge },
  { key: 'client_scoring', label: 'Client Scoring',           desc: 'Profil structurel : PEP, KYC, FATCA',           Icon: UserRound },
  { key: 'planning',       label: 'Planning Agent',           desc: 'Questions réglementaires (Reason-Act-Reflect)', Icon: ListTodo },
  { key: 'retrieval',      label: 'Retrieval Agent',          desc: 'BM25 + vecteurs + RRF + reranker (Weaviate/Neo4j)', Icon: DatabaseZap },
  { key: 'compliance',     label: 'Compliance Agent',         desc: 'Neuro-symbolique : lois violées / respectées',  Icon: Scale },
  { key: 'ml',             label: 'ML Fraud Detector',        desc: 'LightGBM + LSTM-AE + SHAP (signal indicatif)',  Icon: BrainCircuit },
  { key: 'sar',            label: 'SAR Generator',            desc: 'Rapport de soupçon complet (déterministe)',     Icon: FileText },
  { key: 'workflow',       label: 'Decision Workflow',        desc: 'État final + file superviseur',                 Icon: GitBranch },
];

export function PipelineLivePage({ txId, session }: { txId?: string; session?: Session }) {
  const [job, setJob] = useState<JobResult | null>(null);
  const [sarMd, setSarMd] = useState<string | null>(null);
  const [showSar, setShowSar] = useState(false);
  // Le rapport SAR est un document INTERNE conformité (déclaration CTAF) —
  // il n'est pas exposé au rôle Utilisateur (il voit décision/état/notifications).
  const canSeeSar = session?.role !== 'USER';

  useEffect(() => {
    if (!txId) return;
    setJob(null);
    const p = pollResult(txId, setJob);
    return () => p.stop();
  }, [txId]);

  if (!txId) {
    return (
      <>
        <PageHeader title="Pipeline Live" />
        <EmptyState>
          Aucune transaction sélectionnée — lance une analyse depuis
          «&nbsp;Nouvelle transaction&nbsp;» ou le «&nbsp;Simulateur&nbsp;».
        </EmptyState>
      </>
    );
  }

  if (!job) return <Spinner label={`Connexion au pipeline ${txId}…`} />;

  const doneKeys = new Set(Object.keys(job.steps));
  const activeKey = job.status === 'RUNNING'
    ? STEPS.find(s => !doneKeys.has(s.key))?.key
    : undefined;

  const openSar = async () => {
    // le rendu structuré utilise sar_json ; le markdown n'est qu'un repli
    if (!sarMd && !job?.sar_json) {
      try { setSarMd(await api.sarMarkdown(txId)); } catch { setSarMd('SAR indisponible.'); }
    }
    setShowSar(true);
  };

  const tx = job.transaction;

  return (
    <>
      <PageHeader
        title={`Pipeline Live — ${txId}`}
        subtitle={`${tx.tx_type} · ${Number(tx.amount).toLocaleString('fr-FR')} ${tx.currency} · ${tx.sender_country} → ${tx.destination_country}`}
        // pas de rapport de soupcon sur une operation conforme :
        // un SAR est une DECLARATION DE SOUPCON, pas un compte rendu d'analyse
        actions={job.status === 'DONE' && canSeeSar
          && job.steps.compliance?.decision !== 'PASS' && (
          <button onClick={openSar}
            className="rounded-lg bg-slate-900 px-4 py-2 text-xs font-semibold text-white hover:bg-slate-700">
            📄 Ouvrir le rapport SAR
          </button>
        )}
      />

      {/* ── Barre de progression du pipeline ─────────────────────────── */}
      {job.status === 'RUNNING' && (
        <div className="mb-5 rounded-xl border border-indigo-200 bg-indigo-50/50 px-4 py-3">
          <div className="mb-1.5 flex items-baseline justify-between text-xs">
            <span className="font-semibold text-indigo-800">
              Étape {doneKeys.size + 1}/8 — {STEPS.find(s => s.key === activeKey)?.label ?? '…'}
            </span>
            <span className="tabular-nums text-indigo-600">
              {Math.round((doneKeys.size / STEPS.length) * 100)} % ·
              temps restant estimé ~{Math.max(5, (STEPS.length - doneKeys.size) * 6)} s
            </span>
          </div>
          <div className="h-2 w-full rounded-full bg-indigo-100">
            <div className="h-2 rounded-full bg-indigo-500 transition-all duration-700"
              style={{ width: `${(doneKeys.size / STEPS.length) * 100}%` }} />
          </div>
        </div>
      )}

      {job.status === 'ERROR' && (
        <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Erreur pipeline : {job.error}
        </p>
      )}

      {/* ── Bandeau résumé (quand terminé) ───────────────────────────── */}
      {job.summary && (
        <div className="mb-6 grid gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm lg:grid-cols-4">
          <div>
            <p className="mb-1 text-xs text-slate-500">Décision finale</p>
            <DecisionBadge value={job.summary.ca_decision} />
          </div>
          <div>
            <p className="mb-1 text-xs text-slate-500">État workflow</p>
            <StateBadge value={job.summary.state} />
          </div>
          <div>
            <p className="mb-1 text-xs text-slate-500">Niveau de risque</p>
            <RiskBadge value={job.summary.risk_level} />
          </div>
          <ScoreGauge score={job.summary.combined_score} level={job.summary.risk_level} />
        </div>
      )}

      {/* ── Les 8 étapes — TIMELINE verticale ────────────────────────── */}
      <ol className="relative space-y-4 pl-14">
        {/* rail vertical reliant les pastilles */}
        <span aria-hidden
          className="absolute bottom-6 left-[23px] top-6 w-0.5 rounded bg-slate-200" />
        {STEPS.map(({ key, label, desc, Icon }, idx) => {
          const data = job.steps[key];
          const state = data ? 'done' : key === activeKey ? 'running' : 'pending';
          return (
            <li key={key} className="relative">
              {/* pastille d'état sur le rail (anneau « ping » sur l'étape en cours) */}
              <span className={`absolute -left-14 top-2.5 flex h-12 w-12 items-center justify-center rounded-full border-2 bg-white transition-colors ${
                state === 'done' ? 'border-emerald-400 text-emerald-600'
                : state === 'running' ? 'border-indigo-500 text-indigo-600 shadow-lg shadow-indigo-500/30'
                : 'border-slate-200 text-slate-300'}`}>
                {state === 'running' && (
                  <span className="absolute inset-0 animate-ping rounded-full bg-indigo-400/40" />
                )}
                <Icon size={18} strokeWidth={2} className="relative" />
              </span>

              <div className={`rounded-2xl border bg-white shadow-sm transition-all ${
                state === 'running' ? 'border-indigo-300 ring-2 ring-indigo-100'
                : state === 'done' ? 'border-slate-200'
                : 'border-slate-100 opacity-50'}`}>
                <div className="flex items-center gap-2.5 px-5 py-3" title={desc}>
                  <span className="text-[10px] font-bold text-slate-300">{idx + 1}</span>
                  <p className="text-sm font-semibold text-slate-800">{label}</p>
                  {state === 'done' && <CheckCircle2 size={15} className="text-emerald-500" />}
                  {state === 'running' && (
                    <span className="flex items-center gap-1 text-xs font-medium text-indigo-600">
                      <Loader2 size={13} className="animate-spin" /> en cours
                    </span>
                  )}
                  {data && (
                    <span className="ml-auto rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium tabular-nums text-slate-500">
                      {(data.duration_ms / 1000).toFixed(data.duration_ms > 5000 ? 1 : 2)} s
                    </span>
                  )}
                </div>
                {data && <StepDetail step={key} data={data as never} />}
              </div>
            </li>
          );
        })}
      </ol>

      {/* ── Justificatifs (attente de documents OU déjà fournis) ───────── */}
      {(job.steps.workflow?.state === 'PENDING_DOCUMENTS'
        || (job.justificatifs?.length ?? 0) > 0 || job.justificatif) && (
        <div className="mt-6">
          <JustificatifCard txId={txId}
            existing={job.justificatifs ?? (job.justificatif ? [job.justificatif] : [])} />
        </div>
      )}

      {/* ── Modal SAR ────────────────────────────────────────────────── */}
      {showSar && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
             onClick={() => setShowSar(false)}>
          <div className="flex max-h-full w-full max-w-3xl flex-col rounded-xl bg-white shadow-2xl"
               onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
              <h2 className="text-sm font-bold text-slate-800">Rapport SAR — {txId}</h2>
              <div className="flex items-center gap-2">
                {(job.sar_json || sarMd) && (
                  <button
                    onClick={() => job.sar_json
                      ? printSar(job.sar_json, `SAR — ${txId}`)
                      : printMarkdown(sarMd!, `SAR — ${txId}`)}
                    className="rounded-lg bg-slate-900 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-slate-700"
                    title="Ouvre la fenêtre d'impression — choisir « Enregistrer au format PDF »">
                    ⬇️ Télécharger PDF
                  </button>
                )}
                <button onClick={() => setShowSar(false)}
                  className="rounded p-1 text-slate-400 hover:bg-slate-100"><X size={18} /></button>
              </div>
            </div>
            <div className="flex-1 overflow-auto px-5 py-4">
              {job.sar_json ? <SarView sar={job.sar_json} />
                : sarMd ? <MarkdownView md={sarMd} />
                : <Spinner label="Chargement…" />}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ── Détail par étape (rendu spécifique à chaque agent) ───────────────────
function StepDetail({ step, data }: { step: StepName; data: Record<string, unknown> }) {
  const box = 'border-t border-slate-100 px-5 py-3 text-sm';
  const chip = 'inline-block rounded bg-slate-100 px-2 py-0.5 text-[11px] font-mono text-slate-600';

  if (step === 'level1') {
    const d = data as { decision: string; risk_signals: string[]; violations: string[] };
    return (
      <div className={box}>
        <div className="mb-1.5"><DecisionBadge value={d.decision} /></div>
        {d.risk_signals.length > 0 && (
          <p className="flex flex-wrap gap-1.5">
            {d.risk_signals.map(s => <span key={s} className={chip}>{s}</span>)}
          </p>
        )}
        {d.violations.slice(0, 3).map((v, i) => (
          <p key={i} className="mt-1 text-xs text-red-700">• {v}</p>
        ))}
      </div>
    );
  }

  if (step === 'client_scoring') {
    const d = data as { client_id: string; known: boolean; level?: string; score?: number;
                        is_pep?: boolean; kyc_complete?: boolean; fatca?: string };
    return (
      <div className={box}>
        <div className="grid grid-cols-2 gap-x-6 sm:grid-cols-4">
          <KV k="Client" v={<span className="font-mono text-xs">{d.client_id}</span>} />
          <KV k="Risque structurel" v={<RiskBadge value={d.level} />} />
          <KV k="PEP" v={d.is_pep ? '🚨 OUI' : 'non'} />
          <KV k="KYC" v={d.kyc_complete ? 'complet' : '❌ incomplet'} />
        </div>
        {d.fatca && d.fatca !== 'NONE' && (
          <p className="mt-1 text-xs text-indigo-700">⚑ FATCA (fiscal ≠ AML) : {d.fatca}</p>
        )}
      </div>
    );
  }

  if (step === 'planning') {
    const d = data as {
      questions: string[]; llm_used: boolean;
      questions_detail?: { question: string; priority: number; source: string; explanation: string }[];
    };
    const prioStyle = (p: number) =>
      p >= 5 ? 'bg-red-100 text-red-800' : p >= 4 ? 'bg-orange-100 text-orange-800'
      : p >= 3 ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600';
    if (!d.questions_detail?.length) {
      return (
        <div className={box}>
          {d.questions.map((q, i) => (
            <p key={i} className="truncate text-xs text-slate-700">Q{i + 1} · {q}</p>
          ))}
        </div>
      );
    }
    return (
      <div className={box}>
        <p className="mb-2 text-xs text-slate-500">
          {d.questions_detail.length} questions réglementaires
        </p>
        <ol className="space-y-2">
          {d.questions_detail.map((q, i) => (
            <li key={i} className="rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2">
              <div className="mb-0.5 flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] font-bold text-slate-500">Q{i + 1}</span>
                <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${prioStyle(q.priority)}`}>
                  priorité {q.priority}/5
                </span>
              </div>
              <p className="text-xs font-medium text-slate-800">{q.question}</p>
              <p className="mt-0.5 text-[11px] text-slate-500">💡 {q.explanation}</p>
            </li>
          ))}
        </ol>
      </div>
    );
  }

  if (step === 'retrieval') {
    const d = data as {
      chunks_unique: number; sources_count: number;
      graph_answered?: number; corpus_answered?: number;
      by_question?: { question: string; path: string; graph_count?: number;
                      corpus_count?: number; synthesis?: string; sources?: string[];
                      answers: { source_doc: string; article_ref: string; text: string;
                                 score: number; from_graph?: boolean }[] }[];
      top_chunks: { source_doc: string; article_ref: string; score: number }[];
    };
    return (
      <div className={box}>
        <p className="mb-3 text-xs text-slate-500">
          {d.by_question?.length ?? 0} question(s) instruite(s) ·{' '}
          {d.chunks_unique} extrait(s) reglementaire(s) · {d.sources_count} source(s)
        </p>

        {d.by_question?.length ? (
          <div className="space-y-3">
            {d.by_question.map((b, i) => (
              <div key={i} className="overflow-hidden rounded-xl border border-slate-200 bg-white">

                {/* ── La QUESTION ─────────────────────────────────── */}
                <div className="flex items-start gap-2.5 border-b border-slate-100 bg-slate-50/70 px-3.5 py-2.5">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-[10px] font-bold text-white">
                    {i + 1}
                  </span>
                  <p className="text-xs font-semibold leading-relaxed text-slate-800">
                    {b.question}
                  </p>
                  {b.path === 'ERREUR' && (
                    <span className="ml-auto rounded bg-red-100 px-1.5 py-0.5 text-[9px] font-bold uppercase text-red-700">
                      erreur
                    </span>
                  )}
                </div>

                {/* ── La REPONSE : extraits + source sous chacun ───── */}
                <div className="px-3.5 py-2.5">
                  {b.synthesis && (
                    <p className="mb-2.5 rounded-lg bg-emerald-50/70 px-3 py-2 text-xs leading-relaxed text-slate-800">
                      {b.synthesis}
                    </p>
                  )}

                  {b.answers.length === 0 ? (
                    <p className="text-[11px] italic text-slate-400">
                      Aucun texte reglementaire pertinent trouve pour cette question.
                    </p>
                  ) : (
                    <>
                      {b.answers.slice(0, 2).map((a, j) => (
                        <Extrait key={j} a={a} />
                      ))}
                      {b.answers.length > 2 && (
                        <details className="mt-1">
                          <summary className="cursor-pointer text-[11px] font-semibold text-indigo-600 hover:text-indigo-800">
                            Voir les {b.answers.length - 2} autre(s) source(s)
                          </summary>
                          <div className="mt-2">
                            {b.answers.slice(2).map((a, j) => (
                              <Extrait key={j} a={a} />
                            ))}
                          </div>
                        </details>
                      )}
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {d.top_chunks.slice(0, 5).map((c, i) => (
              <span key={i} className={chip} title={`score ${c.score}`}>
                {prettyDoc(c.source_doc)} · {c.article_ref}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (step === 'compliance') {
    const d = data as { decision: string; risk_score: number; applicable_count: number;
                        symbolic_rules?: number; semantic_rules?: number; rules_persisted?: number;
                        semantic_rejected?: number; gap_dims?: number;
                        violated_rules: Rule[]; respected_rules?: Rule[] };
    const violated = d.violated_rules ?? [];
    const respected = d.respected_rules ?? [];
    return (
      <div className={box}>
        {/* En-tête : décision + score */}
        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
          <DecisionBadge value={d.decision} />
          <span className="text-xs text-slate-500">
            risque résiduel <b className="text-slate-700">{d.risk_score}/100</b>
          </span>
        </div>

        {/* Deux compteurs : ce qui est violé, ce qui est respecté */}
        <div className="mb-3 grid grid-cols-2 gap-3">
          <div className={`rounded-xl border px-3 py-2.5 text-center ${
            violated.length > 0
              ? 'border-red-200 bg-red-50'
              : 'border-slate-200 bg-slate-50'}`}>
            <p className={`text-2xl font-bold leading-none ${
              violated.length > 0 ? 'text-red-600' : 'text-slate-400'}`}>
              {violated.length}
            </p>
            <p className="mt-1 text-[11px] font-medium text-slate-600">
              règle(s) violée(s)
            </p>
          </div>
          <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-center">
            <p className="text-2xl font-bold leading-none text-emerald-600">
              {respected.length}
            </p>
            <p className="mt-1 text-[11px] font-medium text-slate-600">
              obligation(s) respectée(s)
            </p>
          </div>
        </div>

        {/* Verdict en une phrase : lève l'ambiguïté « 0 violation mais EDD ? » */}
        <p className="mb-3 rounded-lg bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-700">
          {violated.length > 0
            ? `${violated.length} obligation(s) réglementaire(s) ne sont pas satisfaites : c'est ce manquement qui fonde la décision.`
            : d.decision === 'PASS'
              ? 'Toutes les obligations applicables sont satisfaites : aucune réserve.'
              : "Aucune obligation n'est violée. Le niveau de vigilance provient des contrôles fixes du Niveau 1 (seuil, pays, sanctions), non d'un manquement constaté."}
        </p>

        {/* Gate anti-hallucination : ce qui a été confié au sémantique + rejeté */}
        {(d.semantic_rejected ?? 0) > 0 && (
          <p className="mb-2.5 text-[11px] text-slate-500">
            {(d.semantic_rejected ?? 0) > 0 && (
              <span className="ml-1 rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700"
                title="Règles proposées par le LLM mais REJETÉES car leur citation n'a pas été retrouvée mot-à-mot dans le corpus → garantie anti-hallucination">
                🛡️ {d.semantic_rejected} règle(s) LLM rejetée(s) (citation non ancrée)
              </span>
            )}
          </p>
        )}

        {/* Section VIOLATIONS */}
        {violated.length > 0 && (
          <div className="mb-3">
            <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-red-600">
              <span className="inline-block h-2 w-2 rounded-full bg-red-500" />
              {violated.length} obligation(s) violée(s)
            </p>
            <div className="space-y-2">
              {violated.map((r, i) => <RuleCard key={i} r={r} violated />)}
            </div>
          </div>
        )}

        {/* Section OBLIGATIONS RESPECTÉES — repliées : seules les violations
            changent la décision, la preuve reste à un clic. */}
        {respected.length > 0 && <RespectedRules rules={respected} />}

        {violated.length === 0 && respected.length === 0 && (
          <p className="text-[11px] italic text-slate-400">aucune obligation applicable retenue</p>
        )}
      </div>
    );
  }

  if (step === 'ml') {
    const d = data as { ml_score: number; ml_decision: string; profile_status: string;
                        used_lstm_ae: boolean; isolation_score?: number;
                        shap_top: { feature: string; pushes: string; shap: number }[] };
    const isNew = d.profile_status === 'PROFIL_NOUVEAU';
    // Interprétation métier des features SHAP (langage non technique)
    const FEAT_FR: Record<string, string> = {
      hour: 'heure de l’opération', day_of_week: 'jour de la semaine',
      reconstruction_error: 'séquence temporelle anormale (LSTM)',
      sender_fanout_ratio: 'émetteur → nombreux destinataires',
      receiver_fanin_ratio: 'destinataire ← nombreux émetteurs',
      amount_vs_sender_mean: 'montant vs habitude du client',
      is_night: 'opération de nuit', just_below_10k: 'juste sous 10 000',
      currency_mismatch: 'conversion de devise',
      is_unique_pair: 'première fois entre ces deux comptes',
    };
    return (
      <div className={box}>
        <p className="mb-1 text-xs text-slate-500">
          proba {(d.ml_score * 100).toFixed(1)}% brut → <b>{d.ml_decision}</b> ·{' '}
          {d.profile_status} · LSTM-AE {d.used_lstm_ae ? 'actif' : 'inactif'}
        </p>
        {d.isolation_score != null && (
          <p className="mb-1.5 text-xs">
            <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-semibold ${
              d.isolation_score >= 0.70 ? 'bg-red-50 text-red-700'
              : d.isolation_score >= 0.40 ? 'bg-amber-50 text-amber-700'
              : 'bg-emerald-50 text-emerald-700'}`}>
              🔍 Anomalie (Isolation Forest) : {(d.isolation_score * 100).toFixed(0)}%
              {d.isolation_score >= 0.70 ? ' · atypique'
                : d.isolation_score >= 0.40 ? ' · à surveiller' : ' · dans la norme'}
            </span>
            {isNew && (
              <span className="ml-2 text-[11px] text-slate-500">
                signal non supervisé — fiable même sans historique
              </span>
            )}
          </p>
        )}
        {isNew ? (
          <p className="mb-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-800"
             title="Les variables comportementales sont estimées par leurs médianes : la probabilité n'est pas calibrée sur ce cas. Le signal est affiché mais ignoré dans le score combiné et dans la divergence — la décision repose sur les règles L1 + RAG. Le ML s'activera dès que l'historique sera suffisant (≥ 5 transactions sur 30 jours).">
            ⚠️ <b>Client sans historique</b> : score non calibré, ignoré dans la décision.
          </p>
        ) : (
          <p className="mb-1.5 text-[11px] text-amber-600">
            signal indicatif — la divergence RAG↔ML est arbitrée par le SAR
          </p>
        )}
        <div className="flex flex-wrap gap-1.5">
          {d.shap_top.slice(0, 3).map(s => (
            <span key={s.feature} className={chip}
              title={`${s.feature} · impact SHAP ${s.shap > 0 ? '+' : ''}${s.shap.toFixed(2)}`}>
              {FEAT_FR[s.feature] ?? s.feature} {s.shap > 0 ? '↗' : '↘'} {s.pushes}
            </span>
          ))}
        </div>
      </div>
    );
  }

  if (step === 'sar') {
    const d = data as { sar_id: string; recommendation: string; divergence_flag: string;
                        aml_pattern: string; coverage_bct: number; coverage_fatf: number;
                        risk_level: string; risk_score_combined: number };
    return (
      <div className={box}>
        <div className="grid gap-x-6 sm:grid-cols-2">
          <KV k="SAR" v={<span className="font-mono text-xs">{d.sar_id}</span>} />
          <KV k="Pattern AML" v={d.aml_pattern} />
          <KV k="Divergence RAG↔ML" v={d.divergence_flag} />
          <KV k="Couverture BCT / FATF" v={`${d.coverage_bct}% / ${d.coverage_fatf}%`} />
        </div>
        <p className="mt-1.5 rounded bg-slate-50 px-3 py-2 text-xs font-medium text-slate-700">
          → {d.recommendation}
        </p>
      </div>
    );
  }

  if (step === 'workflow') {
    const d = data as { state: string; summary: string; needs_supervisor: boolean };
    return (
      <div className={box}>
        <div className="mb-1"><StateBadge value={d.state} /></div>
        <p className="text-xs text-slate-600">{d.summary}</p>
        {d.needs_supervisor && (
          <p className="mt-1 text-xs font-semibold text-indigo-700">
            → placée dans la file superviseur
          </p>
        )}
      </div>
    );
  }

  return null;
}

// ── Carte « règle » : anatomie structurée d'une obligation réglementaire ──
//  Bandeau typé (sévérité · catégorie · provenance · FATF · statut), texte de
//  l'obligation, provenance (source→article→seuil), CITATION VERBATIM (preuve
//  anti-hallucination) et barre de confiance. C'est la vue qui rend le
//  raisonnement réglementaire lisible et convaincant.
type Rule = {
  rule_text: string; source_doc: string; rule_ref: string; fatf_rec: string;
  severity: string; category?: string; provenance?: string; rule_status?: string;
  violated?: boolean; mitigated?: boolean; confidence?: number;
  cross_validated?: boolean; cross_source_validated?: boolean;
  source_snippet?: string; threshold?: number | null; threshold_unit?: string;
};

const SEV_FR: Record<string, { label: string; cls: string }> = {
  CRITIQUE: { label: 'critique', cls: 'bg-red-100 text-red-800' },
  HAUTE:    { label: 'haute',    cls: 'bg-red-100 text-red-800' },
  MOYENNE:  { label: 'moyenne',  cls: 'bg-amber-100 text-amber-800' },
  BASSE:    { label: 'basse',    cls: 'bg-slate-100 text-slate-600' },
  FAIBLE:   { label: 'faible',   cls: 'bg-slate-100 text-slate-600' },
};

const PROV_FR: Record<string, { label: string; icon: string; cls: string }> = {
  SYMBOLIC:   { label: 'graphe symbolique', icon: '🔷', cls: 'bg-sky-50 text-sky-700 border-sky-200' },
  SEMANTIC:   { label: 'LLM sémantique',    icon: '🔶', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  RECONCILED: { label: 'réconciliée',        icon: '⚖️', cls: 'bg-violet-50 text-violet-700 border-violet-200' },
};

/** Obligations respectées : une rangée de pastilles ; le détail complet
 *  s'ouvre au clic. Rien n'est perdu, rien n'est imposé au lecteur. */
/** Nom de document lisible : `bct_circulaire_2017_08_aml` devient
 *  « Circulaire BCT 2017-08 ». Sans cela l'interface affiche des
 *  identifiants techniques illisibles pour un lecteur metier. */
function prettyDoc(raw?: string): string {
  if (!raw) return 'Referentiel interne';
  const d = raw.replace(/\.pdf$/i, '');
  let m = d.match(/^bct_circulaire_(\d{4})_(\d{2})/i);
  if (m) return `Circulaire BCT ${m[1]}-${m[2]}`;
  m = d.match(/^loi_(\d{4})_(\d{2})/i);
  if (m) return `Loi ${m[1]}-${m[2]}`;
  m = d.match(/^decret[_-]?loi_(\d{4})_(\d{2})/i);
  if (m) return `Decret-loi ${m[1]}-${m[2]}`;
  m = d.match(/^amld\d?_directive_(\d{4})_(\d{3})/i);
  if (m) return `Directive UE ${m[1]}/${m[2]}`;
  m = d.match(/^eba_gl_(\d{4})_(\d{2})/i);
  if (m) return `ABE - Orientations ${m[1]}/${m[2]}`;
  if (/^fatf_recommendations/i.test(d)) return 'GAFI - Recommandations';
  if (/^fatf_mvts/i.test(d)) return 'GAFI - Guide MVTS';
  if (/^fatf_virtual_assets/i.test(d)) return 'GAFI - Actifs virtuels';
  if (/^fatf_/i.test(d)) return 'GAFI - ' + d.replace(/^fatf_/i, '').replace(/_/g, ' ');
  m = d.match(/^(\d{4})_(.+)$/);
  if (m) return `${m[2].replace(/_/g, ' ')} ${m[1]}`;
  return d.replace(/_/g, ' ');
}

/** Un extrait reglementaire : la citation, et SOUS elle sa source. */
function Extrait({ a }: { a: { source_doc: string; article_ref: string; text: string;
                               from_graph?: boolean } }) {
  return (
    <div className="mb-2 last:mb-0">
      <p className="border-l-[3px] border-indigo-200 pl-2.5 text-[11.5px] italic leading-relaxed text-slate-700">
        « {a.text.trim().slice(0, 260)}{a.text.trim().length > 260 ? '…' : ''} »
      </p>
      <p className="mt-1 pl-2.5 text-[10.5px] font-semibold text-slate-500">
        {prettyDoc(a.source_doc)}{a.article_ref ? ` · ${a.article_ref}` : ''}
      </p>
    </div>
  );
}

function RespectedRules({ rules }: { rules: Rule[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div>
      <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-emerald-600">
        <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
        {rules.length} obligation(s) respectée(s)
      </p>
      <div className="flex flex-wrap gap-1.5">
        {rules.map((r, i) => (
          <button key={i} type="button" onClick={() => setOpen(open === i ? null : i)}
            title={r.rule_text?.slice(0, 160)}
            className={`rounded-md border px-2 py-1 text-[10px] font-bold transition ${
              open === i
                ? 'border-emerald-400 bg-emerald-100 text-emerald-900'
                : 'border-emerald-200 bg-emerald-50/60 text-emerald-700 hover:bg-emerald-100'}`}>
            {r.fatf_rec || r.rule_ref || `#${i + 1}`} ✓
          </button>
        ))}
      </div>
      {open !== null && rules[open] && (
        <div className="mt-2"><RuleCard r={rules[open]} /></div>
      )}
    </div>
  );
}

function RuleCard({ r, violated }: { r: Rule; violated?: boolean }) {
  const sev = SEV_FR[(r.severity || '').toUpperCase()] ?? { label: r.severity || '—', cls: 'bg-slate-100 text-slate-600' };
  const prov = PROV_FR[(r.provenance || '').toUpperCase()];
  const conf = Math.round((r.confidence ?? 0) * 100);
  const status = (r.rule_status || '').toUpperCase();
  return (
    <div className={`rounded-lg border px-3 py-2.5 ${
      violated ? 'border-red-200 bg-red-50/50' : 'border-emerald-200 bg-emerald-50/40'}`}>
      {/* Bandeau typé */}
      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${sev.cls}`}>
          {sev.label}
        </span>
        {r.category && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-slate-600">
            {r.category}
          </span>
        )}
        {prov && (
          <span className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold ${prov.cls}`}
            title="D'où vient la règle : graphe symbolique (fait dur), LLM sémantique (extraite du texte) ou réconciliée">
            {prov.icon} {prov.label}
          </span>
        )}
        {r.fatf_rec && (
          <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[9px] font-semibold text-indigo-600"
            title="Rattachement au standard FATF">
            FATF {r.fatf_rec}
          </span>
        )}
        {(status === 'LEARNED' || status === 'GROUND_TRUTH') && (
          <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[9px] font-bold text-violet-700"
            title="Statut dans le cycle de vie : apprise (réutilisée ≥3×) ou référentiel de vérité">
            {status === 'LEARNED' ? '🎓 apprise' : '📗 référentiel'}
          </span>
        )}
        <span className="ml-auto text-[10px] font-bold">
          {violated
            ? (r.mitigated ? <span className="text-amber-600">⚠ atténuée</span>
                           : <span className="text-red-600">✗ violée</span>)
            : <span className="text-emerald-600">✓ respectée</span>}
        </span>
      </div>

      {/* L'obligation */}
      <p className="text-xs font-medium leading-snug text-slate-800">{r.rule_text}</p>

      {/* Provenance : source → article → seuil */}
      {(r.source_doc || r.rule_ref || r.threshold != null) && (
        <p className="mt-1 text-[11px] text-slate-500">
          📄 {r.source_doc ? r.source_doc.replace('.pdf', '') : 'référentiel'}
          {r.rule_ref && <> · {r.rule_ref}</>}
          {r.threshold != null && (
            <> · seuil {r.threshold.toLocaleString('fr-FR')} {r.threshold_unit}</>
          )}
        </p>
      )}

      {/* CITATION VERBATIM — preuve anti-hallucination / ancrage graphe */}
      {r.source_snippet && (
        <blockquote className="mt-1.5 border-l-2 border-slate-300 pl-2 text-[11px] italic leading-snug text-slate-600">
          « {r.source_snippet} »
          <span className="mt-0.5 block not-italic text-[10px] font-semibold text-emerald-600">
            {(r.provenance || '').toUpperCase() === 'SEMANTIC'
              ? '✓ citation vérifiée mot-à-mot — garantie anti-hallucination (0 %)'
              : '✓ extrait verbatim du texte réglementaire (graphe ancré)'}
          </span>
        </blockquote>
      )}

      {/* Fiabilité : barre de confiance + corroboration multi-sources */}
      <div className="mt-1.5 flex items-center gap-2">
        <span className="text-[10px] text-slate-400">confiance</span>
        <span className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-200">
          <span className={`block h-full rounded-full ${
            conf >= 65 ? 'bg-emerald-500' : conf >= 40 ? 'bg-amber-500' : 'bg-slate-400'}`}
            style={{ width: `${conf}%` }} />
        </span>
        <span className="text-[10px] font-semibold tabular-nums text-slate-500">{conf}%</span>
        {r.cross_source_validated && (
          <span className="ml-1 rounded bg-emerald-100 px-1.5 py-0.5 text-[9px] font-bold text-emerald-700"
            title="Obligation corroborée par une AUTRE famille de sources (ex. BCT ↔ FATF) — corroboration indépendante forte">
            🔗 corroborée (multi-sources)
          </span>
        )}
      </div>
    </div>
  );
}

// ── Justificatif : upload (PDF/TXT/DOCX) + analyse de conformité ──────────
//  Extraction texte côté backend (docling/pypdf) → vérifs déterministes
//  (montant, noms, date) + jugement LLM sous contrainte. Si conforme, le
//  dossier passe de PENDING_DOCUMENTS à PENDING_REVIEW automatiquement.
function JustificatifCard({ txId, existing }: {
  txId: string; existing?: JustificatifResult[];
}) {
  const [docs, setDocs] = useState<JustificatifResult[]>(existing ?? []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const CHECK_FR: Record<string, string> = {
    montant_present: 'Montant de la transaction présent dans le document',
    emetteur_present: "Nom de l'émetteur présent",
    beneficiaire_present: 'Nom du bénéficiaire présent',
    date_recente_presente: 'Date récente (moins d’un an) trouvée',
  };

  // Upload MULTIPLE : chaque fichier sélectionné est envoyé et analysé
  // séquentiellement (facture, puis contrat…) — la liste s'accumule.
  const upload = (files: FileList) => {
    setErr(''); setBusy(true);
    const list = Array.from(files);
    const next = async () => {
      for (const file of list) {
        try {
          const b64 = await new Promise<string>((res, rej) => {
            const r = new FileReader();
            r.onload = () => res(String(r.result));
            r.onerror = () => rej(new Error('Lecture du fichier impossible'));
            r.readAsDataURL(file);
          });
          const result = await api.uploadJustificatif(txId, {
            filename: file.name, content_b64: b64,
          });
          setDocs(d => [...d, result]);
        } catch (e) {
          setErr(`${file.name} : ${e instanceof Error ? e.message : String(e)}`);
        }
      }
      setBusy(false);
    };
    next();
  };

  return (
    <Card
      title={<><Upload size={15} className="mr-1.5 inline text-slate-400" />Justificatifs économiques ({docs.length})</>}
      subtitle="PDF, DOCX ou TXT (plusieurs fichiers possibles : facture + contrat…) — extraction docling puis analyse de conformité (vérifications déterministes + LLM sous contrainte)">
      <div className="flex items-center gap-3">
        <input ref={fileRef} type="file" accept=".pdf,.txt,.md,.docx" multiple className="hidden"
          onChange={e => { if (e.target.files?.length) upload(e.target.files); e.target.value = ''; }} />
        <button onClick={() => fileRef.current?.click()} disabled={busy}
          className="flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-xs font-semibold text-white hover:bg-slate-700 disabled:opacity-40">
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
          {busy ? 'Analyse en cours…' : docs.length ? 'Ajouter un document' : 'Choisir le(s) document(s)'}
        </button>
        {docs.length === 0 && (
          <p className="text-xs text-slate-400">
            Le dossier attend des justificatifs — fournissez la facture, le
            contrat ou l'attestation demandée.
          </p>
        )}
      </div>

      {err && (
        <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {err}
        </p>
      )}

      {docs.map((result, di) => (
        <div key={di} className="mt-4 border-t border-slate-100 pt-3">
          <div className={`mb-2 flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold ${
            result.conforme ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'}`}>
            {result.conforme ? <CheckCircle2 size={16} /> : <X size={16} />}
            {result.conforme
              ? 'Document jugé RECEVABLE'
              : 'Document jugé NON RECEVABLE'}
            <span className="ml-auto font-mono text-[10px] font-normal text-slate-500">
              {result.filename} · {result.size_kb} Ko
            </span>
          </div>

          {/* Vérifications déterministes */}
          <div className="mb-2 grid gap-1 sm:grid-cols-2">
            {Object.entries(CHECK_FR).map(([k, lbl]) => (
              <p key={k} className="text-xs text-slate-600">
                {result.checks[k] ? '✅' : '❌'} {lbl}
              </p>
            ))}
          </div>

          {/* Jugement LLM (sous contrainte) */}
          {result.llm && (
            <div className="rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2">
              <p className="mb-1 text-[11px] font-bold uppercase tracking-wide text-slate-400">
                Analyse sémantique — type détecté : {result.llm.document_type ?? '?'}
              </p>
              {result.llm.verifications?.map((v, i) => (
                <p key={i} className="text-xs text-slate-600">
                  {v.statut === 'OK' ? '✅' : v.statut === 'DOUTE' ? '🟡' : '❌'}{' '}
                  <b>{v.critere}</b> — {v.commentaire}
                </p>
              ))}
              {result.llm.resume && (
                <p className="mt-1.5 text-xs italic text-slate-700">{result.llm.resume}</p>
              )}
            </div>
          )}

          {result.new_state === 'PENDING_REVIEW' && (
            <p className="mt-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-800">
              ✅ Dossier débloqué → transmis à la revue superviseur (actualisez la page).
            </p>
          )}
        </div>
      ))}
    </Card>
  );
}
