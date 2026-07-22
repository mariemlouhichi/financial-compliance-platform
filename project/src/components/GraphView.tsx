// ============================================================================
// GraphView — rendu SVG d'un petit graphe (force-directed maison, zéro
// dépendance). Rend la chaîne d'ancrage Rule → Chunk → LegalUnit → Document
// DIRECTEMENT dans la plateforme, sans dépendre de Neo4j Browser (qui refuse
// d'être embarqué en iframe). Layout déterministe (seed fixe).
// ============================================================================
import { useMemo, useState } from 'react';
import type { GraphNode, GraphEdge } from '../types/api';

// Couleurs par type de nœud (identité, ordre fixe — jamais réassignées au rang).
// 4 teintes franchement distinctes (évite violet/indigo trop proches ; pas de
// rouge, réservé aux statuts de décision).
const TYPE_COLOR: Record<string, string> = {
  Rule: '#f59e0b',       // ambre
  Chunk: '#0ea5e9',      // sky
  LegalUnit: '#8b5cf6',  // violet
  Document: '#0d9488',   // teal
};
const TYPE_LABEL: Record<string, string> = {
  Rule: 'Règle', Chunk: 'Chunk', LegalUnit: 'Unité légale', Document: 'Document',
};
const RADIUS: Record<string, number> = {
  Rule: 9, Chunk: 7, LegalUnit: 8, Document: 12,
};

const W = 760, H = 480;

// PRNG déterministe (mulberry32) → layout stable entre les rendus.
function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface Pos { x: number; y: number }

function layout(nodes: GraphNode[], edges: GraphEdge[]): Record<string, Pos> {
  const rnd = mulberry32(42);
  const pos: Record<string, Pos> = {};
  // Bande verticale par type → aide la convergence + lecture en couches.
  const band: Record<string, number> = {
    Rule: 0.12, Chunk: 0.38, LegalUnit: 0.64, Document: 0.9,
  };
  nodes.forEach(n => {
    pos[n.id] = {
      x: W * (0.15 + rnd() * 0.7),
      y: H * ((band[n.type] ?? 0.5) + (rnd() - 0.5) * 0.18),
    };
  });

  const k = 90;                 // distance idéale
  const ITER = 420;
  for (let it = 0; it < ITER; it++) {
    const t = 1 - it / ITER;    // refroidissement
    const disp: Record<string, Pos> = {};
    nodes.forEach(n => (disp[n.id] = { x: 0, y: 0 }));

    // Répulsion (toutes paires)
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = pos[nodes[i].id], b = pos[nodes[j].id];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d = Math.hypot(dx, dy) || 0.01;
        const f = (k * k) / d;
        dx /= d; dy /= d;
        disp[nodes[i].id].x += dx * f; disp[nodes[i].id].y += dy * f;
        disp[nodes[j].id].x -= dx * f; disp[nodes[j].id].y -= dy * f;
      }
    }
    // Attraction (le long des arêtes)
    edges.forEach(e => {
      const a = pos[e.source], b = pos[e.target];
      if (!a || !b) return;
      let dx = a.x - b.x, dy = a.y - b.y;
      let d = Math.hypot(dx, dy) || 0.01;
      const f = (d * d) / k;
      dx /= d; dy /= d;
      disp[e.source].x -= dx * f; disp[e.source].y -= dy * f;
      disp[e.target].x += dx * f; disp[e.target].y += dy * f;
    });
    // Application + gravité douce vers le centre + bornage
    nodes.forEach(n => {
      const dp = disp[n.id];
      const dl = Math.hypot(dp.x, dp.y) || 0.01;
      const step = Math.min(dl, 12 * t);
      let x = pos[n.id].x + (dp.x / dl) * step;
      let y = pos[n.id].y + (dp.y / dl) * step;
      x += (W / 2 - x) * 0.008;
      y += (H / 2 - y) * 0.008;
      pos[n.id] = {
        x: Math.max(24, Math.min(W - 24, x)),
        y: Math.max(20, Math.min(H - 20, y)),
      };
    });
  }
  return pos;
}

export function GraphView({ nodes, edges }: { nodes: GraphNode[]; edges: GraphEdge[] }) {
  const pos = useMemo(() => layout(nodes, edges), [nodes, edges]);
  const [hover, setHover] = useState<string | null>(null);

  const types = Array.from(new Set(nodes.map(n => n.type)));

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-lg border border-slate-200 bg-slate-50"
        style={{ maxHeight: 520 }}>
        {/* Arêtes */}
        {edges.map((e, i) => {
          const a = pos[e.source], b = pos[e.target];
          if (!a || !b) return null;
          const active = hover === e.source || hover === e.target;
          return (
            <g key={i}>
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                stroke={active ? '#475569' : '#cbd5e1'} strokeWidth={active ? 1.6 : 1} />
              {active && (
                <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 2}
                  textAnchor="middle" className="fill-slate-500"
                  style={{ fontSize: 8 }}>{e.type}</text>
              )}
            </g>
          );
        })}
        {/* Nœuds */}
        {nodes.map(n => {
          const p = pos[n.id];
          if (!p) return null;
          const r = RADIUS[n.type] ?? 7;
          const isHover = hover === n.id;
          const showLabel = n.type === 'Document' || isHover;
          return (
            <g key={n.id} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
              style={{ cursor: 'pointer' }}>
              <circle cx={p.x} cy={p.y} r={isHover ? r + 2 : r}
                fill={TYPE_COLOR[n.type] ?? '#94a3b8'}
                stroke="#fff" strokeWidth={2} />
              {showLabel && (
                <text x={p.x + r + 3} y={p.y + 3}
                  className="fill-slate-700" style={{ fontSize: 9, fontWeight: 600 }}>
                  {n.caption}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {/* Légende (identité jamais par la couleur seule) */}
      <div className="mt-3 flex flex-wrap gap-4">
        {types.map(t => (
          <span key={t} className="flex items-center gap-1.5 text-xs text-slate-600">
            <span className="inline-block h-3 w-3 rounded-full"
              style={{ background: TYPE_COLOR[t] ?? '#94a3b8' }} />
            {TYPE_LABEL[t] ?? t}
          </span>
        ))}
        <span className="text-xs text-slate-400">· survolez un nœud pour le libellé + la relation</span>
      </div>
    </div>
  );
}
