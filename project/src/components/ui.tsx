// ============================================================================
// Composants UI partagés — badges de statut (icône + libellé, jamais couleur
// seule), cartes, tuiles KPI, spinner. Palette de STATUT réservée aux états.
// ============================================================================
import type { ReactNode } from 'react';
import {
  CheckCircle2, AlertTriangle, ShieldAlert, FileWarning, Ban,
  Clock, UserCheck, Loader2, type LucideIcon,
} from 'lucide-react';

// ── Décisions du pipeline (PASS → BLOCK) ─────────────────────────────────
const DECISION_STYLE: Record<string, { cls: string; Icon: LucideIcon }> = {
  PASS:   { cls: 'bg-emerald-50 text-emerald-800 border-emerald-300', Icon: CheckCircle2 },
  REVIEW: { cls: 'bg-amber-50 text-amber-800 border-amber-300',       Icon: Clock },
  KYC:    { cls: 'bg-sky-50 text-sky-800 border-sky-300',             Icon: UserCheck },
  RECORD: { cls: 'bg-slate-50 text-slate-700 border-slate-300',       Icon: Clock },
  EDD:    { cls: 'bg-orange-50 text-orange-800 border-orange-300',    Icon: AlertTriangle },
  STR:    { cls: 'bg-red-50 text-red-800 border-red-300',             Icon: FileWarning },
  BLOCK:  { cls: 'bg-red-100 text-red-900 border-red-400',            Icon: Ban },
};

export function DecisionBadge({ value }: { value?: string }) {
  const s = DECISION_STYLE[value ?? ''] ?? DECISION_STYLE.RECORD;
  const I = s.Icon;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${s.cls}`}>
      <I size={13} strokeWidth={2.5} /> {value ?? '—'}
    </span>
  );
}

// ── Niveaux de risque (FAIBLE → CRITIQUE) ────────────────────────────────
const RISK_STYLE: Record<string, string> = {
  FAIBLE:   'bg-emerald-50 text-emerald-800 border-emerald-300',
  MOYEN:    'bg-amber-50 text-amber-800 border-amber-300',
  'ÉLEVÉ':  'bg-orange-50 text-orange-800 border-orange-300',
  CRITIQUE: 'bg-red-100 text-red-900 border-red-400',
};

export function RiskBadge({ value }: { value?: string }) {
  const cls = RISK_STYLE[value ?? ''] ?? 'bg-slate-50 text-slate-700 border-slate-300';
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${cls}`}>
      <ShieldAlert size={13} strokeWidth={2.5} /> {value ?? '—'}
    </span>
  );
}

// ── États du workflow (cycle de vie de la transaction) ───────────────────
const STATE_LABEL: Record<string, string> = {
  APPROVED: 'Approuvée (auto)',
  BLOCKED: 'Bloquée',
  PENDING_DOCUMENTS: 'En cours — justificatifs',
  PENDING_REVIEW: 'En cours — revue superviseur',
  APPROVED_BY_SUPERVISOR: 'Approuvée (superviseur)',
  REJECTED_BY_SUPERVISOR: 'Rejetée (superviseur)',
};

const STATE_STYLE: Record<string, string> = {
  APPROVED: 'bg-emerald-50 text-emerald-800 border-emerald-300',
  APPROVED_BY_SUPERVISOR: 'bg-emerald-50 text-emerald-800 border-emerald-300',
  BLOCKED: 'bg-red-100 text-red-900 border-red-400',
  REJECTED_BY_SUPERVISOR: 'bg-red-100 text-red-900 border-red-400',
  PENDING_DOCUMENTS: 'bg-amber-50 text-amber-800 border-amber-300',
  PENDING_REVIEW: 'bg-indigo-50 text-indigo-800 border-indigo-300',
};

export function StateBadge({ value }: { value?: string }) {
  if (!value) return <span className="text-xs text-slate-400">—</span>;
  const cls = STATE_STYLE[value] ?? 'bg-slate-50 text-slate-700 border-slate-300';
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${cls}`}>
      {STATE_LABEL[value] ?? value}
    </span>
  );
}

// ── Conteneurs ────────────────────────────────────────────────────────────
export function Card({ title, subtitle, actions, children, className = '' }: {
  title?: ReactNode; subtitle?: string; actions?: ReactNode;
  children: ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-2xl border border-slate-200 bg-white shadow-sm transition-shadow duration-200 hover:shadow-md ${className}`}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-3.5">
          <div>
            {title && <h2 className="text-sm font-semibold text-slate-800">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

export function StatTile({ label, value, hint, tone = 'default' }: {
  label: string; value: ReactNode; hint?: string;
  tone?: 'default' | 'warning' | 'danger' | 'success';
}) {
  const toneCls = {
    default: 'text-slate-900',
    warning: 'text-amber-700',
    danger: 'text-red-700',
    success: 'text-emerald-700',
  }[tone];
  // Liseré d'accent à gauche — la couleur de STATUT reste réservée aux états
  const accent = {
    default: 'border-l-slate-300',
    warning: 'border-l-amber-400',
    danger: 'border-l-red-400',
    success: 'border-l-emerald-400',
  }[tone];
  return (
    <div className={`rounded-2xl border border-l-4 border-slate-200 bg-white px-5 py-4 shadow-sm transition-shadow duration-200 hover:shadow-md ${accent}`}>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-3xl font-bold tabular-nums ${toneCls}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-slate-500">
      <Loader2 size={18} className="animate-spin" />
      {label && <span className="text-sm">{label}</span>}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center text-sm text-slate-500">
      {children}
    </div>
  );
}

export function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <span className="text-xs text-slate-500">{k}</span>
      <span className="text-sm font-medium text-slate-800 text-right">{v}</span>
    </div>
  );
}
