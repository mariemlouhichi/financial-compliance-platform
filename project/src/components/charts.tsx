// ============================================================================
// Graphiques maison (zéro dépendance) conformes aux règles dataviz :
//  - identité JAMAIS portée par la couleur seule (libellé texte sur chaque barre)
//  - texte en encre neutre, la couleur ne colore que la marque
//  - une seule échelle, barres fines, extrémités arrondies, écart entre barres
//  - valeur affichée en label direct, survol = title natif
// ============================================================================

export interface BarRow {
  label: string;
  value: number;
  color: string;   // classe Tailwind bg-* (statut ou catégorie fixe)
  hint?: string;
}

export function BarList({ rows, unit = '' }: { rows: BarRow[]; unit?: string }) {
  const max = Math.max(1, ...rows.map(r => r.value));
  return (
    <div className="space-y-2.5">
      {rows.map(r => (
        <div key={r.label} title={r.hint ?? `${r.label} : ${r.value}${unit}`}>
          <div className="mb-1 flex items-baseline justify-between">
            <span className="text-xs font-medium text-slate-600">{r.label}</span>
            <span className="text-xs font-semibold tabular-nums text-slate-800">
              {r.value}{unit}
            </span>
          </div>
          <div className="h-2 w-full rounded-full bg-slate-100">
            <div
              className={`h-2 rounded-full ${r.color} transition-all duration-500`}
              style={{ width: `${(r.value / max) * 100}%`, minWidth: r.value > 0 ? 8 : 0 }}
            />
          </div>
        </div>
      ))}
      {rows.length === 0 && (
        <p className="text-xs text-slate-400">Aucune donnée pour l'instant.</p>
      )}
    </div>
  );
}

/** Jauge 0-100 pour le score de risque (sequential : une teinte, clair→foncé). */
export function ScoreGauge({ score, level }: { score: number; level?: string }) {
  const pct = Math.max(0, Math.min(100, score));
  const color =
    pct >= 80 ? 'bg-red-600' :
    pct >= 50 ? 'bg-orange-500' :
    pct >= 25 ? 'bg-amber-500' : 'bg-emerald-500';
  return (
    <div title={`Score ${pct.toFixed(0)}/100${level ? ` — ${level}` : ''}`}>
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-slate-500">Score de risque combiné</span>
        <span className="text-lg font-bold tabular-nums text-slate-900">
          {pct.toFixed(0)}<span className="text-xs font-normal text-slate-500">/100</span>
        </span>
      </div>
      <div className="mt-1 h-2.5 w-full rounded-full bg-slate-100">
        <div className={`h-2.5 rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ── Donut (répartition) — anneau fin + libellés directs dans la légende ──
export interface DonutSegment { label: string; value: number; hex: string }

export function Donut({ segments, centerLabel }: {
  segments: DonutSegment[]; centerLabel?: string;
}) {
  const total = Math.max(1, segments.reduce((s, x) => s + x.value, 0));
  const R = 42, C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="flex items-center gap-5">
      <svg viewBox="0 0 110 110" className="h-36 w-36 shrink-0">
        <circle cx="55" cy="55" r={R} fill="none" stroke="currentColor"
          className="text-slate-100" strokeWidth="12" />
        {segments.map(s => {
          const frac = s.value / total;
          const el = (
            <circle key={s.label} cx="55" cy="55" r={R} fill="none"
              stroke={s.hex} strokeWidth="12"
              strokeDasharray={`${Math.max(frac * C - 2, 0)} ${C}`}
              strokeDashoffset={-offset * C}
              strokeLinecap="round"
              transform="rotate(-90 55 55)" />
          );
          offset += frac;
          return el;
        })}
        <text x="55" y="52" textAnchor="middle"
          className="fill-slate-900 text-xl font-bold"
          style={{ fontSize: 20 }}>{total}</text>
        {centerLabel && (
          <text x="55" y="68" textAnchor="middle" className="fill-slate-400"
            style={{ fontSize: 8 }}>{centerLabel}</text>
        )}
      </svg>
      {/* légende : identité par libellé + valeur, jamais couleur seule */}
      <div className="min-w-0 flex-1 space-y-1.5">
        {segments.map(s => (
          <div key={s.label} className="flex items-center gap-2 text-xs">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: s.hex }} />
            <span className="truncate text-slate-600">{s.label}</span>
            <span className="ml-auto font-semibold tabular-nums text-slate-800">
              {s.value}
            </span>
            <span className="w-9 text-right text-[10px] tabular-nums text-slate-400">
              {Math.round((s.value / total) * 100)} %
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Courbe d'aire (évolution temporelle) — 1 série, crête annotée ─────────
export function AreaChart({ points, height = 120 }: {
  points: { label: string; value: number }[]; height?: number;
}) {
  if (points.length === 0) {
    return <p className="text-xs text-slate-400">Aucune donnée pour l'instant.</p>;
  }
  const W = 600, H = height, PAD = 6;
  const max = Math.max(1, ...points.map(p => p.value));
  const x = (i: number) => points.length === 1 ? W / 2
    : PAD + (i / (points.length - 1)) * (W - 2 * PAD);
  const y = (v: number) => H - PAD - (v / max) * (H - 2 * PAD - 14);
  const line = points.map((p, i) => `${x(i)},${y(p.value)}`).join(' ');
  const area = `${PAD},${H - PAD} ${line} ${W - PAD},${H - PAD}`;
  const peak = points.reduce((a, b, i) => b.value > points[a].value ? i : a, 0);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: H + 20 }}>
        <polygon points={area} fill="rgb(99 102 241 / 0.12)" />
        <polyline points={line} fill="none" stroke="#6366f1" strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) => (
          <circle key={i} cx={x(i)} cy={y(p.value)} r={i === peak ? 3.5 : 2}
            fill={i === peak ? '#4f46e5' : '#a5b4fc'}>
            <title>{`${p.label} : ${p.value}`}</title>
          </circle>
        ))}
        {/* crête annotée (libellé direct sélectif) */}
        <text x={Math.min(Math.max(x(peak), 30), W - 30)} y={y(points[peak].value) - 7}
          textAnchor="middle" className="fill-indigo-700"
          style={{ fontSize: 11, fontWeight: 700 }}>
          {points[peak].value}
        </text>
      </svg>
      <div className="mt-1 flex justify-between text-[10px] text-slate-400">
        <span>{points[0].label}</span>
        {points.length > 2 && <span>{points[Math.floor(points.length / 2)].label}</span>}
        <span>{points[points.length - 1].label}</span>
      </div>
    </div>
  );
}

// Couleurs hex des décisions (pour le Donut — mêmes teintes que les badges)
export const DECISION_HEX: Record<string, string> = {
  PASS: '#10b981', REVIEW: '#f59e0b', KYC: '#0ea5e9',
  RECORD: '#94a3b8', EDD: '#f97316', STR: '#ef4444', BLOCK: '#b91c1c',
};

// Couleurs de STATUT (réservées aux états — jamais réutilisées en "série 4")
export const DECISION_COLORS: Record<string, string> = {
  PASS: 'bg-emerald-500', REVIEW: 'bg-amber-500', KYC: 'bg-sky-500',
  RECORD: 'bg-slate-400', EDD: 'bg-orange-500', STR: 'bg-red-500',
  BLOCK: 'bg-red-700',
};

export const RISK_COLORS: Record<string, string> = {
  FAIBLE: 'bg-emerald-500', MOYEN: 'bg-amber-500',
  'ÉLEVÉ': 'bg-orange-500', CRITIQUE: 'bg-red-600',
};

// Catégories de patterns AML (ordre FIXE, jamais recyclé)
export const PATTERN_COLORS: Record<string, string> = {
  HIGH_RISK_ROUTE: 'bg-indigo-500', FAN_OUT: 'bg-violet-500',
  FAN_IN: 'bg-fuchsia-500', STRUCTURING: 'bg-cyan-600',
  LAYERING: 'bg-teal-600', NIGHT_BURST: 'bg-blue-500',
  NORMAL: 'bg-slate-400',
};
