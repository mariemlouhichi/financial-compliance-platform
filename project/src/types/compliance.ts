// Types aligned with FastAPI backend models

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical';
export type ComplianceStatus = 'compliant' | 'non_compliant' | 'pending' | 'reviewing';

export interface Violation {
  id: string;
  rule_id: string;
  rule_name: string;
  severity: RiskLevel;
  description: string;
  evidence: string;
  remediation: string;
  confidence: number;
}

export interface ComplianceDecision {
  transaction_id: string;
  status: ComplianceStatus;
  risk_level: RiskLevel;
  confidence_score: number;
  violations: Violation[];
  reasoning: string;
  timestamp: string;
  agent_version: string;
}

export interface WorkflowOutcome {
  workflow_id: string;
  transaction_id: string;
  decision: ComplianceDecision;
  execution_time_ms: number;
  agent_path: string[];
  escalation_required: boolean;
  escalation_reason?: string;
  recommendations: string[];
  next_steps: string[];
}

export interface TransactionInput {
  transaction_id: string;
  transaction_type: string;
  amount: number;
  currency: string;
  source_country: string;
  destination_country: string;
  parties: Party[];
  metadata?: Record<string, unknown>;
}

export interface Party {
  id: string;
  name: string;
  type: 'individual' | 'business';
  country: string;
  risk_score?: number;
}

export interface Stats {
  total_transactions: number;
  compliant: number;
  non_compliant: number;
  pending: number;
  avg_confidence: number;
  avg_execution_time_ms: number;
  risk_distribution: Record<RiskLevel, number>;
}
