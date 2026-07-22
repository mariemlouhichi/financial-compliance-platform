// ============================================================================
// Types alignés sur le backend réel : api/main.py (FastAPI, port 8001)
// Chaque interface reflète EXACTEMENT le JSON renvoyé par le pipeline.
// ============================================================================

export type Decision = 'PASS' | 'REVIEW' | 'EDD' | 'STR' | 'BLOCK' | 'KYC' | 'RECORD';
export type TxType = 'SWIFT' | 'VIREMENT_LOCAL' | 'MANDAT' | 'DEPOT_ESPECES';
export type RiskLevel = 'FAIBLE' | 'MOYEN' | 'ÉLEVÉ' | 'CRITIQUE';
export type JobStatus = 'RUNNING' | 'DONE' | 'ERROR';
export type TxState =
  | 'APPROVED' | 'BLOCKED'
  | 'PENDING_DOCUMENTS' | 'PENDING_REVIEW'
  | 'APPROVED_BY_SUPERVISOR' | 'REJECTED_BY_SUPERVISOR';

export type StepName =
  | 'level1' | 'client_scoring' | 'planning' | 'retrieval'
  | 'compliance' | 'ml' | 'sar' | 'workflow';

// ── Entrée d'analyse (POST /api/analyze) ────────────────────────────────
export interface TransactionIn {
  tx_type: TxType;
  amount: number;
  currency: string;
  sender_name: string;
  sender_country: string;
  beneficiary_name: string;
  destination_country: string;
  motive?: string;
  has_justification?: boolean | null;
  timestamp?: string | null;
  client_id?: string | null;
  sender_rib?: string;         // RIB/IBAN émetteur (validation par pays)
  beneficiary_iban?: string;   // RIB/IBAN bénéficiaire
  // Justificatifs joints à la soumission — analysés en étape 0 du pipeline ;
  // leur verdict REMPLACE la case déclarative has_justification.
  justificatifs?: { filename: string; content_b64: string }[];
}

// ── Sorties par étape ────────────────────────────────────────────────────
export interface Level1Step {
  decision: string;
  violations: string[];
  risk_signals: string[];
  duration_ms: number;
}

export interface ClientScoringStep {
  client_id: string;
  known: boolean;
  nationality?: string;
  is_pep?: boolean;
  kyc_complete?: boolean;
  previous_strs?: number;
  level?: RiskLevel;
  score?: number;
  factors?: Record<string, number>;
  signals?: string[];
  fatca?: string;
  duration_ms: number;
}

export interface QuestionDetail {
  question: string;
  priority: number;
  source: string;       // template | context | signal | history | llm_reason
  factor: string;       // déclencheur brut (ex : SANCTION_PEP_SENDER)
  explanation: string;  // le POURQUOI, en français, pour l'admin
}

export interface PlanningStep {
  questions: string[];
  priorities: number[];
  sources: string[];
  questions_detail?: QuestionDetail[];
  llm_used: boolean;
  duration_ms: number;
}

export interface Chunk {
  source_doc: string;
  article_ref: string;
  jurisdiction: string;
  chunk_role: string;
  score: number;
  retrieval_path: string;
  text: string;
}

export interface QuestionAnswer {
  question: string;
  path: string;                 // GRAPH+CORPUS | GRAPH | ERREUR
  graph_count?: number;         // règles trouvées dans le graphe Neo4j
  corpus_count?: number;        // chunks trouvés dans le corpus Weaviate
  synthesis?: string;           // réponse synthétisée en français (LLM sous contrainte)
  sources?: string[];           // sources citées (repli déterministe)
  answers: (Chunk & { from_graph?: boolean })[]; // extraits bruts (derrière « détail »)
}

export interface RetrievalStep {
  chunks_raw: number;
  chunks_unique: number;
  sources_count: number;
  graph_answered?: number;   // questions répondues par le GRAPHE (règles apprises)
  corpus_answered?: number;  // questions parties en recherche corpus (Weaviate)
  by_question?: QuestionAnswer[];
  top_chunks: Chunk[];
  duration_ms: number;
}

export interface Rule {
  rule_text: string;
  source_doc: string;
  rule_ref: string;
  fatf_rec: string;
  severity: string;
  provenance: string;
  violated: boolean;
  confidence: number;
  cross_validated: boolean;
  cross_source_validated: boolean;
}

export interface ComplianceStep {
  decision: Decision;
  risk_score: number;
  risk_components: Record<string, number>;
  violated_rules: Rule[];
  applicable_count: number;
  respected_rules: Rule[];
  fatf_coverage: Record<string, boolean>;
  binding_coverage: Record<string, boolean>;
  human_review_flags: string[];
  symbolic_rules?: number;    // règles évaluées par le chemin symbolique (Neo4j)
  semantic_rules?: number;    // règles extraites par le chemin sémantique (LLM)
  rules_persisted?: number;   // nouvelles règles PERSISTÉES dans le graphe
  duration_ms: number;
}

export interface ShapEntry {
  feature: string;
  value: number;
  shap: number;
  pushes: 'FRAUDE' | 'NORMAL';
}

export interface MLStep {
  ml_score: number;
  ml_score_raw?: number;
  ml_decision: string;
  calibrated?: boolean;
  shap_top: ShapEntry[];
  profile_status: string;
  history_size: number;
  used_lstm_ae: boolean;
  duration_ms: number;
}

export interface SARStep {
  sar_id: string;
  risk_score_combined: number;
  risk_level: RiskLevel;
  recommendation: string;
  divergence_flag: string;
  divergence_action: string;
  aml_pattern: string;
  aml_pattern_explanation: string;
  coverage_fatf: number;
  coverage_bct: number;
  required_documents: string[];
  duration_ms: number;
}

export interface WorkflowStep {
  state: TxState;
  summary: string;
  needs_supervisor: boolean;
  str_required: boolean;
  required_documents: unknown[];
  duration_ms: number;
}

export interface Steps {
  level1?: Level1Step;
  client_scoring?: ClientScoringStep;
  planning?: PlanningStep;
  retrieval?: RetrievalStep;
  compliance?: ComplianceStep;
  ml?: MLStep;
  sar?: SARStep;
  workflow?: WorkflowStep;
}

// ── Job complet (GET /api/result/{tx_id}) ────────────────────────────────
export interface JobSummary {
  l1_decision: string;
  ca_decision: Decision;
  ca_score: number;
  ml_decision: string;
  risk_level: RiskLevel;
  combined_score: number;
  state: TxState;
  aml_pattern: string;
}

export interface SupervisorDecision {
  action: 'APPROVE' | 'REJECT' | 'ANNOTATE';
  label: string;
  annotation: string;
  by: string;
  at: string;
}

// ── Justificatif uploadé (POST /api/justificatif/{tx_id}) ────────────────
export interface JustificatifResult {
  filename: string;
  size_kb: number;
  uploaded_at: string;
  extracted_chars: number;
  checks: Record<string, boolean | number>;  // vérifs déterministes (montant, noms, date)
  llm: {
    document_type?: string;
    conforme?: boolean;
    verifications?: { critere: string; statut: 'OK' | 'MANQUE' | 'DOUTE'; commentaire: string }[];
    resume?: string;
  } | null;
  conforme: boolean;
  new_state: string | null;   // PENDING_REVIEW si le dossier a été débloqué
}

export interface JobResult {
  tx_id: string;
  status: JobStatus;
  current_step: StepName | null;
  transaction: TransactionIn;
  steps: Steps;
  source?: 'USER' | 'SIMULATOR';
  owner?: string | null;
  summary?: JobSummary;
  sar_json?: Record<string, unknown>;
  supervisor_decision?: SupervisorDecision;
  justificatif?: JustificatifResult;      // dernier document (compat)
  justificatifs?: JustificatifResult[];   // tous les documents analysés
  created_at: string;
  finished_at?: string;
  error?: string;
}

// ── Historique (GET /api/transactions) ───────────────────────────────────
export interface TxListItem {
  tx_id: string;
  status: JobStatus;
  created_at: string;
  source?: 'USER' | 'SIMULATOR';
  owner?: string | null;
  transaction: Partial<TransactionIn>;
  summary?: JobSummary;
  supervisor_decision?: SupervisorDecision;
}

// File superviseur enrichie (GET /api/supervisor/queue) — Tâche 3
export interface QueueItem extends TxListItem {
  priority: 'HAUTE' | 'MOYENNE' | 'NORMALE';
  priority_rank: number;
  age_hours: number | null;
}

// ── KPIs (GET /api/stats) ────────────────────────────────────────────────
export interface Stats {
  total_analyzed: number;
  running: number;
  errors: number;
  by_decision: Record<string, number>;
  by_risk_level: Record<string, number>;
  by_pattern: Record<string, number>;
  pending_supervisor: string[];
  avg_pipeline_ms: number;
}

// ── Client KYC (GET /api/clients/{id}) ───────────────────────────────────
export interface ClientProfile {
  client_id: string;
  known: boolean;
  nationality: string;
  country_residence: string;
  is_pep: boolean;
  pep_type: string | null;
  kyc_complete: boolean;
  previous_strs: number;
  account_type: string;
  history: Record<string, unknown>;
}

// ── Simulateur (GET /api/demo-transactions) ──────────────────────────────
export interface DemoTransaction {
  tx_id: string;
  expected_label: string;
  transaction: TransactionIn & Record<string, unknown>;
}

// ── Flux bancaire simulé (GET /api/simulator/status) ─────────────────────
export interface SpeedrunEntry {
  tx_id: string;
  label: string;          // ex : TX-FRAUD-001
  expected: string;       // FRAUDE | NORMALE
  got: Decision | null;   // décision obtenue (null tant que RUNNING)
  status: string;         // RUNNING | DONE | ERROR
}

export interface SimulatorStatus {
  running: boolean;
  interval_seconds: number;
  generated: number;
  last_tx_id: string | null;
  started_at: string | null;
  mode?: 'speedrun' | null;
  speedrun?: SpeedrunEntry[];
}

// ── Coordonnées KYC de l'utilisateur (page « Mon profil ») ───────────────
// Alimentent le Client Scoring du pipeline (schéma clients_db côté backend).
// Chaque champ = un FACTEUR du RiskFactorScorer (client_scoring_agent.py).
export interface KycProfile {
  full_name: string;
  nationality: string;             // facteur géographique (high_risk/grey_country)
  country_of_residence: string;    // facteur géographique (résidence)
  business_type: string;           // facteur high_risk_profession
  account_type: string;            // INDIVIDUAL|BUSINESS|CORRESPONDENT|TRUST
  rib: string;                     // RIB unique de l'utilisateur (auto-rempli au formulaire)
  address?: string;                // adresse postale
  phone?: string;                  // téléphone de contact
  marital_status?: string;         // état civil (personne physique)
  employer?: string;               // employeur / organisme (personne physique)
  account_opened: string;          // date → ancienneté (facteur new_relationship)
  previous_strs: number;           // facteur prior_strs
  beneficial_owner_identified: boolean; // facteur no_beneficial_owner (sociétés)
  is_pep: boolean;                 // facteur pep_domestic / pep_foreign (R12)
  pep_type: string;                // foreign | domestic (si PEP)
  fatca_us_person: boolean;        // « US person » (obligation fiscale FATCA)
  foreign_tax_residence: string;   // code pays résidence fiscale étrangère (CRS)
  birth_date?: string;
}

// ── Couche connaissance : corpus / règles / graphe ───────────────────────
export interface CorpusDocument {
  name: string;
  jurisdiction: string | null;
  priority: number | null;
  legal_units: number;
  chunks: number;
}

export interface CorpusChunkMeta {
  chunk_id: string;
  article_ref: string | null;
  chunk_role: string | null;
  has_threshold: boolean | null;
  unit_role: string | null;
}

export interface RuleRecord {
  id?: string;
  text?: string;            // phrase lisible (subject — constraint)
  subject?: string;         // component_subject
  constraint?: string;      // component_constraint
  rule_text?: string;
  category?: string;        // chunk_role (OBLIGATION/KYC/THRESHOLD/…)
  severity?: string;
  origin?: string;
  status?: string;
  confidence?: number;
  fatf_rec?: string;
  jurisdiction?: string;
  source_doc?: string;
  article_ref?: string;     // source_article
  source_snippet?: string;  // source_text_snippet (verbatim)
  validation_status?: string;
  validated_against_source?: boolean;
  extraction_date?: string;
  [k: string]: unknown;
}

export interface RuleEvidence {
  source_doc?: string;
  article_ref?: string;
  chunk_role?: string;
  unit_id?: string;
}

export interface RuleDetail {
  rule: RuleRecord;
  evidence_chunks: RuleEvidence[];
  derived_from: RuleEvidence[];
}

export interface GraphStats {
  nodes: Record<string, number>;
  relationships: Record<string, number>;
  total_nodes: number;
  total_relationships: number;
}

// Sous-graphe réel pour le rendu SVG intégré (GET /api/graph/sample)
export interface GraphNode {
  id: string;
  type: 'Rule' | 'Chunk' | 'LegalUnit' | 'Document' | string;
  caption: string;
}
export interface GraphEdge {
  source: string;
  target: string;
  type: string;
}
export interface GraphSample {
  nodes: GraphNode[];
  edges: GraphEdge[];
  counts: { nodes: number; edges: number };
}

// Fichiers PDF sources (GET /api/corpus/files)
export interface CorpusFile {
  name: string;
  folder: string;      // 'international' | 'tunisia'
  rel_path: string;
  size_kb: number;
}

// ── Dataset IBM HI-Small (GET /api/dataset-stats, pré-calculé offline) ───
export interface DatasetStats {
  dataset: string;
  computed_at: string;
  total_transactions: number;
  fraud_count: number;
  fraud_rate_pct: number;
  by_payment_format: Record<string, number>;
  fraud_by_payment_format: Record<string, number>;
  by_currency: Record<string, number>;
  by_amount_bucket: Record<string, number>;
  fraud_by_hour: Record<string, number>;
  total_amount_usd_billion?: number;
}
