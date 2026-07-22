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
        actions={job.status === 'DONE' && canSeeSar && (
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
    const SOURCE_FR: Record<string, string> = {
      template: 'obligation de base', context: 'contexte transaction',
      signal: 'signal Level 1', history: 'historique client', llm_reason: 'raisonnement LLM',
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
          {d.questions_detail.length} questions réglementaires {d.llm_used ? '(déterministe + LLM)' : '(déterministe)'} —
          chaque question indique <b>pourquoi</b> le système la pose
        </p>
        <ol className="space-y-2">
          {d.questions_detail.map((q, i) => (
            <li key={i} className="rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2">
              <div className="mb-0.5 flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] font-bold text-slate-500">Q{i + 1}</span>
                <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${prioStyle(q.priority)}`}>
                  priorité {q.priority}/5
                </span>
                <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-indigo-600">
                  {SOURCE_FR[q.source] ?? q.source}
                </span>
              </div>
              <p className="text-xs font-medium text-slate-800">💡 {q.explanation}</p>
              <p className="mt-0.5 font-mono text-[10px] text-slate-400">
                requête corpus : « {q.question} »
              </p>
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
        <p className="mb-2 text-xs text-slate-500">
          {d.chunks_unique} extraits réglementaires uniques · {d.sources_count} sources
          {typeof d.graph_answered === 'number' && (
            <>
              {' '}· sources combinées :{' '}
              <span className="font-semibold text-violet-700">📗 graphe ({d.graph_answered} q.)</span>
              {' '}+{' '}
              <span className="font-semibold text-sky-700">🔎 corpus ({d.corpus_answered ?? 0} q.)</span>
            </>
          )}
        </p>
        {d.by_question?.length ? (
          <div className="space-y-2">
            {d.by_question.map((b, i) => (
              <div key={i} className="rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2">
                {/* Question + provenance */}
                <div className="mb-1 flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-bold text-slate-500">Q{i + 1}</span>
                  {(b.graph_count ?? 0) > 0 && (
                    <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[9px] font-bold uppercase text-violet-700"
                      title="Règles du graphe Neo4j (ground truth + apprises)">
                      📗 graphe ×{b.graph_count}
                    </span>
                  )}
                  {(b.corpus_count ?? 0) > 0 && (
                    <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[9px] font-bold uppercase text-sky-700"
                      title="Recherche hybride Weaviate : BM25 + vecteurs + reranker BGE">
                      🔎 corpus ×{b.corpus_count}
                    </span>
                  )}
                  {b.path === 'ERREUR' && (
                    <span className="rounded bg-red-100 px-1.5 py-0.5 text-[9px] font-bold uppercase text-red-700">erreur</span>
                  )}
                  <span className="truncate font-mono text-[10px] text-slate-400">{b.question}</span>
                </div>

                {/* RÉPONSE synthétisée (LLM sous contrainte) ou sources (repli) */}
                {b.synthesis ? (
                  <p className="text-xs leading-relaxed text-slate-800">
                    <span className="font-bold text-emerald-700">Réponse : </span>{b.synthesis}
                  </p>
                ) : (b.sources?.length ?? 0) > 0 ? (
                  <p className="text-xs text-slate-700">
                    <span className="font-bold">Obligations identifiées dans : </span>
                    {b.sources!.join(' · ')}
                  </p>
                ) : (
                  <p className="text-[11px] italic text-slate-400">aucun extrait trouvé</p>
                )}

                {/* Extraits bruts — repliés derrière « détail » */}
                {b.answers.length > 0 && (
                  <details className="mt-1.5">
                    <summary className="cursor-pointer text-[11px] font-semibold text-indigo-600 hover:text-indigo-800">
                      détail — {b.answers.length} extrait(s) source (texte verbatim)
                    </summary>
                    <div className="mt-1 space-y-1">
                      {b.answers.map((a, j) => (
                        <p key={j} className="border-l-2 border-slate-200 pl-2 text-[11px] leading-relaxed text-slate-600">
                          <b className="text-slate-700">
                            {a.from_graph ? '📗 ' : '🔎 '}
                            {a.source_doc.replace('.pdf', '') || 'règle du graphe'} · {a.article_ref || '—'}
                          </b>
                          {' — '}{a.text.slice(0, 220)}…
                        </p>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {d.top_chunks.slice(0, 5).map((c, i) => (
              <span key={i} className={chip} title={`score ${c.score}`}>
                {c.source_doc.replace('.pdf', '')} · {c.article_ref}
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
                        violated_rules: { severity: string; source_doc: string; rule_ref: string;
                                          fatf_rec: string; rule_text: string }[] };
    return (
      <div className={box}>
        <div className="mb-2 flex items-center gap-3">
          <DecisionBadge value={d.decision} />
          <span className="text-xs text-slate-500">
            score {d.risk_score}/100 · {d.applicable_count} règles applicables ·{' '}
            {d.violated_rules.length} violation(s)
          </span>
        </div>
        {typeof d.symbolic_rules === 'number' && (
          <p className="mb-1.5 text-[11px] text-slate-500">
            🔷 symbolique (graphe) : {d.symbolic_rules} règles ·
            🔶 sémantique (LLM + citation verbatim exigée) : {d.semantic_rules ?? 0} règles
            {(d.rules_persisted ?? 0) > 0 && (
              <span className="ml-1 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-bold text-violet-700"
                title="Règles validées (conf ≥ 0.65, citation vérifiée) écrites dans Neo4j — le graphe pourra répondre directement aux prochaines transactions similaires">
                💾 +{d.rules_persisted} règle(s) apprise(s) ajoutée(s) au graphe
              </span>
            )}
          </p>
        )}
        {d.violated_rules.slice(0, 3).map((r, i) => (
          <p key={i} className="text-xs text-red-700">
            • [{r.severity}] {r.source_doc} {r.rule_ref} (réf. {r.fatf_rec}) — {r.rule_text.slice(0, 90)}
          </p>
        ))}
      </div>
    );
  }

  if (step === 'ml') {
    const d = data as { ml_score: number; ml_decision: string; profile_status: string;
                        used_lstm_ae: boolean; shap_top: { feature: string; pushes: string; shap: number }[] };
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
        {isNew ? (
          <p className="mb-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-800">
            ⚠️ <b>Client sans historique</b> : les features comportementales sont estimées
            (médianes) → la probabilité n'est pas calibrée sur ce cas. Ce signal est
            affiché mais <b>ignoré</b> dans le score combiné et la divergence —
            la décision repose sur les règles L1 + RAG. Le ML s'activera quand
            l'historique sera suffisant (≥ 5 transactions sur 30 j).
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
