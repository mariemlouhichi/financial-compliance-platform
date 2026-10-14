// ============================================================================
// Client HTTP vers le backend FastAPI (api/main.py, port 8001).
// Base URL configurable : VITE_API_BASE_URL dans .env (défaut localhost:8001).
// Le token de session (auth.ts) est injecté en Authorization: Bearer sur
// chaque requête → le backend rattache les analyses à leur propriétaire.
// ============================================================================
import type {
  TransactionIn, JobResult, TxListItem, Stats, ClientProfile, DemoTransaction,
  Chunk, DatasetStats, SimulatorStatus,
  CorpusDocument, CorpusChunkMeta, RuleRecord, RuleDetail, GraphStats,
  GraphSample, CorpusFile, KycProfile, JustificatifResult, QueueItem,
} from '../types/api';
import { getSession, type Role } from '../auth';

/** Boucle RLHF — résumé pour le panneau d'amélioration continue. */
export interface RlhfSummary {
  stats: {
    total_events: number; labelled: number; correct: number;
    false_positive: number; true_fraud: number;
    agreement_rate: number; false_positive_rate: number;
  };
  model: {
    threshold?: number; metrics?: Record<string, number>;
    last_rlhf?: { at: string; warm_rounds: number; feedback_used: number } | null;
  };
  thresholds: { min_labels: number; fp_alert: number };
  last_cycle: Record<string, unknown>;
}

/** Rapport d'un cycle RLHF (agrégation → décision → warm-start garde-fou). */
export interface RlhfCycleReport {
  cycle_at: string;
  stats: {
    labelled: number; correct: number; false_positive: number; true_fraud: number;
    agreement_rate: number; false_positive_rate: number; new_since_last_cycle: number;
  };
  decision: { retrain: boolean; reason: string };
  training: {
    status: string; detail?: string; reason?: string; deployed: boolean;
    metrics_before?: Record<string, number>;
    metrics_after?: Record<string, number>;
    feedback?: { usable: number; positives: number; negatives: number; missing_features: number };
    warm_rounds?: number; val_rows?: number; duration_s?: number;
  };
  thresholds: { min_labels: number; fp_alert: number };
}

/** Statut d'un job d'ingestion de règlement (upload → Weaviate). */
export interface RegulationIngestJob {
  job_id: string;
  status: 'RUNNING' | 'DONE' | 'ERROR';
  stage: string;
  detail?: string;
  doc_name: string;
  jurisdiction?: string;
  error?: string;
  result?: {
    doc_name: string; jurisdiction: string; pages: number;
    pages_translated: number; chunks: number; chunks_indexed: number;
    roles: Record<string, number>; thresholds: number; elapsed_s: number;
    preview: { article_ref: string; chunk_role: string; text: string }[];
  };
}

const BASE: string =
  (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL
  ?? 'http://localhost:8001';

function authHeaders(): Record<string, string> {
  const s = getSession();
  return s?.token ? { Authorization: `Bearer ${s.token}` } : {};
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    // FastAPI renvoie {"detail": "..."} — remonter le message lisible
    let msg = `${r.status}`;
    try {
      const j = await r.json() as { detail?: string };
      msg = j.detail ?? msg;
    } catch { /* corps non-JSON */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { const j = await r.json() as { detail?: string }; msg = j.detail ?? msg; }
    catch { /* corps non-JSON */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  health: () =>
    get<{ status: string; components_ready: boolean; neo4j: boolean; jobs: number }>(
      '/api/health'),

  // ── Authentification (api/auth.py) ────────────────────────────────────
  register: (body: { email: string; password: string; full_name: string; role: Role }) =>
    post<{ registered: boolean; email: string; message: string }>('/api/auth/register', body),

  verifyEmail: (body: { email: string; code: string }) =>
    post<{ verified: boolean; message: string }>('/api/auth/verify', body),

  resendCode: (email: string) =>
    post<{ resent: boolean; message: string }>('/api/auth/resend', { email }),

  forgotPassword: (email: string) =>
    post<{ sent: boolean; message: string }>('/api/auth/forgot', { email }),

  resetPassword: (body: { email: string; code: string; new_password: string }) =>
    post<{ reset: boolean; message: string }>('/api/auth/reset', body),

  login: (body: { email: string; password: string }) =>
    post<{ token: string; username: string; role: Role; name?: string }>('/api/auth/login', body),

  getProfile: () =>
    get<{ profile: KycProfile; complete: boolean }>('/api/auth/profile'),

  updateProfile: (body: KycProfile) =>
    put<{ saved: boolean; profile: Record<string, unknown> }>('/api/auth/profile', body),

  // ── Pipeline ──────────────────────────────────────────────────────────
  analyze: (tx: TransactionIn) =>
    post<{ tx_id: string; status: string }>('/api/analyze', tx),

  result: (txId: string) => get<JobResult>(`/api/result/${txId}`),

  sarMarkdown: async (txId: string): Promise<string> => {
    const r = await fetch(`${BASE}/api/sar/${txId}/markdown`, { headers: authHeaders() });
    if (!r.ok) throw new Error(`${r.status}`);
    return r.text();
  },

  /** Historique. opts.mine=true → uniquement MES dossiers (espace utilisateur) ;
   *  opts.source → filtre USER | SIMULATOR (espace admin). */
  transactions: (opts?: { mine?: boolean; source?: 'USER' | 'SIMULATOR' }) => {
    const p = new URLSearchParams();
    if (opts?.mine) p.set('mine', 'true');
    if (opts?.source) p.set('source', opts.source);
    const qs = p.toString();
    return get<TxListItem[]>(`/api/transactions${qs ? `?${qs}` : ''}`);
  },

  /** Requête complexe côté serveur (Tâche 4 — SQL PostgreSQL, repli mémoire).
   *  Filtres : montant minimum, décision, client (owner), période. */
  transactionsQuery: (f: {
    minAmount?: number; decision?: string; client?: string;
    dateFrom?: string; dateTo?: string; limit?: number;
  }) => {
    const p = new URLSearchParams();
    if (f.minAmount != null) p.set('min_amount', String(f.minAmount));
    if (f.decision) p.set('decision', f.decision);
    if (f.client) p.set('client', f.client);
    if (f.dateFrom) p.set('date_from', f.dateFrom);
    if (f.dateTo) p.set('date_to', f.dateTo);
    if (f.limit) p.set('limit', String(f.limit));
    const qs = p.toString();
    return get<{ count: number; backend: string; results: TxListItem[] }>(
      `/api/transactions/query${qs ? `?${qs}` : ''}`);
  },

  validate: (txId: string, body: {
    action: 'APPROVE' | 'REJECT' | 'ANNOTATE';
    label: string; annotation: string; supervisor_id: string;
  }) => post<{ tx_id: string; recorded: boolean }>(`/api/validate/${txId}`, body),

  /** File superviseur enrichie : priorité + ancienneté, triée par urgence. */
  supervisorQueue: () => get<QueueItem[]>('/api/supervisor/queue'),

  /** Historique des décisions superviseur passées (plus récentes d'abord). */
  supervisorHistory: (limit = 100) =>
    get<TxListItem[]>(`/api/supervisor/history?limit=${limit}`),

  stats: () => get<Stats>('/api/stats'),

  /** Envoie le rapport hebdomadaire par email (Compliance Officer / Admin). */
  weeklyReport: (email?: string) =>
    post<{ sent: boolean; recipient: string; stats: Record<string, unknown> }>(
      '/api/reports/weekly', { email: email ?? '' }),

  client: (clientId: string) => get<ClientProfile>(`/api/clients/${clientId}`),

  demoTransactions: () => get<DemoTransaction[]>('/api/demo-transactions'),

  searchRegulations: (q: string, topK = 5) =>
    get<{ query: string; results: Chunk[] }>(
      `/api/regulations/search?q=${encodeURIComponent(q)}&top_k=${topK}`),

  // ── Upload d'un nouveau règlement (ingestion à la demande) ────────────
  uploadRegulation: (body: {
    filename: string; content_b64: string;
    jurisdiction: string; translate: boolean;
  }) => post<{ job_id: string; status: string }>('/api/regulations/upload', body),

  regulationUploadStatus: (jobId: string) =>
    get<RegulationIngestJob>(`/api/regulations/upload/${jobId}`),

  // ── Simulateur de flux bancaire (espace admin) ────────────────────────
  simulatorStart: (intervalSeconds = 30) =>
    post<{ running: boolean; interval_seconds?: number; note?: string }>(
      '/api/simulator/start', { interval_seconds: intervalSeconds }),

  simulatorStop: () =>
    post<{ running: boolean; generated: number }>('/api/simulator/stop', {}),

  /** Démo rapide : les 6 cas de test enchaînés (attendu vs obtenu). */
  speedrunStart: () =>
    post<{ running: boolean; mode: string }>('/api/simulator/speedrun', {}),

  simulatorStatus: () => get<SimulatorStatus>('/api/simulator/status'),

  // ── Dataset IBM HI-Small (stats pré-calculées offline) ────────────────
  datasetStats: () => get<DatasetStats>('/api/dataset-stats'),

  // ── Couche connaissance (corpus / règles / graphe Neo4j) ──────────────
  corpusDocuments: () =>
    get<{ documents: CorpusDocument[]; total_documents: number;
          total_chunks: number; jurisdictions: string[] }>(
      '/api/corpus/documents'),

  corpusChunks: (doc: string, limit = 40) =>
    get<{ doc: string; chunks: CorpusChunkMeta[] }>(
      `/api/corpus/chunks?doc=${encodeURIComponent(doc)}&limit=${limit}`),

  rules: (origin?: string) =>
    get<{ rules: RuleRecord[]; total: number;
          ground_truth: number; learned: number }>(
      `/api/rules${origin ? `?origin=${encodeURIComponent(origin)}` : ''}`),

  rule: (ruleId: string) =>
    get<RuleDetail>(`/api/rules/${encodeURIComponent(ruleId)}`),

  graphStats: () => get<GraphStats>('/api/graph/stats'),

  graphSample: (limit = 8) => get<GraphSample>(`/api/graph/sample?limit=${limit}`),

  // ── Corpus : fichiers PDF sources (01_regulations) ────────────────────
  corpusFiles: () =>
    get<{ files: CorpusFile[]; total: number; folders: string[] }>('/api/corpus/files'),

  /** URL directe d'un PDF servi par le backend (preview / téléchargement). */
  corpusFileUrl: (relPath: string) =>
    `${BASE}/api/corpus/file?path=${encodeURIComponent(relPath)}`,

  // ── Justificatif (upload base64 + analyse de conformité) ──────────────
  uploadJustificatif: (txId: string, body: { filename: string; content_b64: string }) =>
    post<JustificatifResult>(`/api/justificatif/${txId}`, body),

  // ── RLHF — boucle d'amélioration continue (feedback superviseur) ──────
  rlhfSummary: () => get<RlhfSummary>('/api/rlhf/summary'),

  rlhfRun: (force = false) =>
    post<RlhfCycleReport>(`/api/rlhf/run?force=${force}`, {}),

  // ── Listes FATF (alertes métier du formulaire) ────────────────────────
  fatfLists: () =>
    get<{ black_names: Record<string, string>; grey_names: Record<string, string>;
          last_updated: string }>('/api/fatf-lists'),
};

/**
 * Sonde GET /api/result/{tx_id} toutes les `intervalMs` millisecondes et
 * appelle `onUpdate` à chaque réponse, jusqu'à DONE/ERROR (ou stop()).
 * Utilisé par la page Pipeline Live pour l'affichage progressif des étapes.
 */
export function pollResult(
  txId: string,
  onUpdate: (job: JobResult) => void,
  intervalMs = 2000,
): { stop: () => void } {
  let active = true;
  const tick = async () => {
    if (!active) return;
    try {
      const job = await api.result(txId);
      onUpdate(job);
      if (job.status === 'RUNNING' && active) {
        setTimeout(tick, intervalMs);
      }
    } catch {
      if (active) setTimeout(tick, intervalMs * 2);
    }
  };
  tick();
  return { stop: () => { active = false; } };
}

/** Événement temps réel poussé par le serveur (SSE, /api/events). */
export interface RealtimeEvent {
  type: 'job' | 'decision';
  tx_id: string;
  owner?: string | null;
  status?: string;
  decision?: string;
  state?: string;
  at?: string;
}

/**
 * Flux temps réel SSE (Tâche 2) — remplace le polling. Ouvre une connexion
 * EventSource vers /api/events et appelle `onEvent` à chaque notification
 * (analyse terminée, décision superviseur). La reconnexion est gérée par le
 * navigateur. Renvoie une fonction de fermeture. Sans session : no-op.
 */
export function subscribeEvents(onEvent: (e: RealtimeEvent) => void): () => void {
  const s = getSession();
  if (!s?.token) return () => { /* non connecté */ };
  const es = new EventSource(
    `${BASE}/api/events?token=${encodeURIComponent(s.token)}`);
  es.onmessage = (m) => {
    try { onEvent(JSON.parse(m.data) as RealtimeEvent); }
    catch { /* message de service (ready / keep-alive) */ }
  };
  // onerror : EventSource se reconnecte automatiquement — rien à gérer ici.
  return () => es.close();
}
