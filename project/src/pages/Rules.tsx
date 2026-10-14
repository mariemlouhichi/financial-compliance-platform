// ============================================================================
// ESPACE CONFORMITÉ — Règles extraites : les 49 règles ground-truth
// (origin=human_verified) + les règles apprises par le RAG au fil des
// transactions (chemin sémantique → validation SPO → persistance Neo4j).
// Clic sur une règle → panneau détail avec la chaîne d'ancrage (preuves).
// ============================================================================
import { useEffect, useState } from 'react';
import { BookMarked, ShieldCheck, X, Search } from 'lucide-react';
import { api } from '../api/client';
import type { RuleRecord, RuleDetail } from '../types/api';
import { PageHeader } from '../components/Layout';
import { Card, StatTile, Spinner, EmptyState } from '../components/ui';

type Tab = 'gt' | 'learned';

// Catégorie = chunk_role du graphe (le « type » de règle réglementaire).
const CATEGORY_STYLE: Record<string, string> = {
  OBLIGATION: 'bg-blue-100 text-blue-800 border-blue-300',
  KYC: 'bg-teal-100 text-teal-800 border-teal-300',
  THRESHOLD: 'bg-amber-100 text-amber-800 border-amber-300',
  SANCTIONS: 'bg-red-100 text-red-800 border-red-300',
  REPORTING: 'bg-violet-100 text-violet-800 border-violet-300',
  RISK_ASSESSMENT: 'bg-indigo-100 text-indigo-800 border-indigo-300',
  RED_FLAG: 'bg-orange-100 text-orange-800 border-orange-300',
};
const catStyle = (c?: string) =>
  CATEGORY_STYLE[String(c ?? '').toUpperCase()] ?? 'bg-slate-100 text-slate-600 border-slate-300';

function OriginBadge({ origin }: { origin?: string }) {
  const isGt = origin === 'human_verified';
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${
      isGt ? 'border-blue-300 bg-blue-50 text-blue-800'
           : 'border-amber-300 bg-amber-50 text-amber-800'}`}>
      {isGt ? '🔵 Ground truth (humain)' : `🟡 ${origin ?? 'RAG'}`}
    </span>
  );
}

function ruleText(r: RuleRecord): string {
  return String(r.text ?? r.rule_text ?? r.description ?? '—');
}

export function RulesPage() {
  const [rules, setRules] = useState<RuleRecord[] | null>(null);
  const [counts, setCounts] = useState({ gt: 0, learned: 0 });
  const [tab, setTab] = useState<Tab>('gt');
  const [detail, setDetail] = useState<RuleDetail | null>(null);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');          // recherche plein-texte côté client
  const [cat, setCat] = useState('');      // filtre catégorie ('' = toutes)

  useEffect(() => {
    api.rules()
      .then(r => {
        setRules(r.rules);
        setCounts({ gt: r.ground_truth, learned: r.learned });
      })
      .catch(e => { setError(String(e)); setRules([]); });
  }, []);

  const open = async (r: RuleRecord) => {
    if (!r.id) return;
    try { setDetail(await api.rule(String(r.id))); }
    catch { /* détail indisponible — la ligne suffit */ }
  };

  if (!rules) return <Spinner label="Chargement des règles depuis Neo4j…" />;

  const needle = q.trim().toLowerCase();
  const tabRules = rules.filter(r =>
    tab === 'gt' ? r.origin === 'human_verified' : r.origin !== 'human_verified');
  const categories = [...new Set(tabRules.map(r => String(r.category ?? ''))
    .filter(Boolean))].sort();
  const shown = tabRules.filter(r => {
    if (cat && String(r.category ?? '') !== cat) return false;
    if (!needle) return true;
    return [ruleText(r), String(r.id ?? ''), String(r.source_doc ?? ''),
            String(r.article_ref ?? ''), String(r.category ?? ''),
            String(r.jurisdiction ?? '')]
      .join(' ').toLowerCase().includes(needle);
  });

  return (
    <>
      <PageHeader
        title="Règles extraites"
        subtitle="Base de règles gouvernée (Neo4j) — vérité terrain validée humainement + règles apprises par le chemin sémantique du Compliance Agent"
      />

      {error && (
        <EmptyState>
          {error.includes('503') || error.toLowerCase().includes('neo4j')
            ? <>Neo4j injoignable — lance <code className="rounded bg-slate-200 px-1.5 py-0.5">docker compose up -d neo4j</code></>
            : error}
        </EmptyState>
      )}

      {/* ── KPIs ────────────────────────────────────────────────────── */}
      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <StatTile label="Règles ground truth" value={counts.gt}
          hint="validées humainement (référence absolue)" />
        <StatTile label="Apprises par le RAG" value={counts.learned}
          tone={counts.learned > 0 ? 'warning' : 'default'}
          hint="persistées après validation SPO — s'accumulent" />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ── Table ───────────────────────────────────────────────────── */}
        <div className={detail ? 'lg:col-span-2' : 'lg:col-span-3'}>
          <Card
            title={
              <div className="flex flex-wrap items-center gap-2">
                <BookMarked size={15} className="text-slate-400" />
                <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5">
                  {(['gt', 'learned'] as Tab[]).map(t => (
                    <button key={t} onClick={() => setTab(t)}
                      className={`rounded-md px-3 py-1 text-xs font-semibold ${
                        tab === t ? 'bg-white text-slate-900 shadow-sm'
                                  : 'text-slate-500 hover:text-slate-700'}`}>
                      {t === 'gt' ? `Ground truth (${counts.gt})`
                                  : `Apprises par le RAG (${counts.learned})`}
                    </button>
                  ))}
                </div>
                <div className="relative ml-auto">
                  <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input value={q} onChange={e => setQ(e.target.value)}
                    placeholder="mot-clé, source (BCT, FATF…), article…"
                    className="w-56 rounded-lg border border-slate-300 py-1.5 pl-7 pr-2 text-xs font-normal focus:border-slate-900 focus:outline-none" />
                </div>
              </div>
            }>
            {/* ── Filtre par catégorie (chips colorées) ─────────────── */}
            {categories.length > 1 && (
              <div className="mb-3 flex flex-wrap items-center gap-1.5">
                <button onClick={() => setCat('')}
                  className={`rounded-full border px-2.5 py-1 text-[10px] font-bold ${
                    cat === '' ? 'border-slate-900 bg-slate-900 text-white'
                               : 'border-slate-200 text-slate-500 hover:border-slate-400'}`}>
                  Toutes ({tabRules.length})
                </button>
                {categories.map(c => (
                  <button key={c} onClick={() => setCat(cat === c ? '' : c)}
                    className={`rounded-full border px-2.5 py-1 text-[10px] font-bold ${
                      cat === c ? 'border-transparent ' + catStyle(c)
                                : 'border-slate-200 text-slate-500 hover:border-slate-400'}`}>
                    {c} ({tabRules.filter(r => String(r.category ?? '') === c).length})
                  </button>
                ))}
                <span className="ml-auto text-[11px] text-slate-400">
                  {shown.length} règle(s) affichée(s)
                </span>
              </div>
            )}
            {shown.length === 0 ? (
              <EmptyState>
                {tab === 'learned'
                  ? 'Aucune règle apprise pour l’instant — elles se créent au fil des analyses (chemin sémantique sur les lacunes).'
                  : 'Aucune règle ne correspond au filtre.'}
              </EmptyState>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400">
                      <th className="pb-2 pr-3">Règle</th>
                      <th className="pb-2 pr-3">Catégorie</th>
                      <th className="pb-2 pr-3">Source</th>
                      <th className="pb-2 pr-3">Juridiction</th>
                      <th className="pb-2 pr-3">Origine</th>
                      <th className="pb-2">Conf.</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r, i) => (
                      <tr key={String(r.id ?? i)} onClick={() => open(r)}
                        className="cursor-pointer border-b border-slate-100 hover:bg-slate-50">
                        <td className="max-w-md py-2.5 pr-3">
                          <p className="line-clamp-2 text-xs text-slate-700">{ruleText(r)}</p>
                          <p className="mt-0.5 font-mono text-[10px] text-slate-400">{String(r.id ?? '')}</p>
                        </td>
                        <td className="py-2.5 pr-3">
                          {r.category ? (
                            <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold ${catStyle(r.category)}`}>
                              {String(r.category)}
                            </span>
                          ) : <span className="text-xs text-slate-400">—</span>}
                        </td>
                        <td className="py-2.5 pr-3 text-xs text-slate-600">
                          {String(r.source_doc ?? '—').replace(/\.pdf$/i, '')}
                          {r.article_ref ? <span className="text-slate-400"> · {String(r.article_ref)}</span> : null}
                        </td>
                        <td className="py-2.5 pr-3 text-xs text-slate-600">{String(r.jurisdiction ?? '—')}</td>
                        <td className="py-2.5 pr-3"><OriginBadge origin={r.origin} /></td>
                        <td className="py-2.5 text-xs tabular-nums text-slate-700">
                          {typeof r.confidence === 'number' ? r.confidence.toFixed(2) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>

        {/* ── Panneau détail (chaîne d'ancrage) ──────────────────────── */}
        {detail && (
          <div>
            <Card
              title={<><ShieldCheck size={15} className="mr-1.5 inline text-slate-400" />Détail & preuves</>}
              actions={
                <button onClick={() => setDetail(null)}
                  className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
                  <X size={15} />
                </button>
              }>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <OriginBadge origin={detail.rule.origin} />
                {detail.rule.category && (
                  <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold ${catStyle(detail.rule.category)}`}>
                    {String(detail.rule.category)}
                  </span>
                )}
                {detail.rule.id && (
                  <span className="font-mono text-[10px] text-slate-400">{String(detail.rule.id)}</span>
                )}
              </div>

              {detail.rule.subject && (
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                  {String(detail.rule.subject)}
                </p>
              )}
              <p className="text-sm leading-relaxed text-slate-800">
                {String(detail.rule.constraint || ruleText(detail.rule))}
              </p>

              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
                {detail.rule.source_doc && (
                  <span>📘 {String(detail.rule.source_doc).replace(/\.pdf$/i, '')}</span>
                )}
                {detail.rule.article_ref && <span>§ {String(detail.rule.article_ref)}</span>}
                {detail.rule.jurisdiction && <span>🌍 {String(detail.rule.jurisdiction)}</span>}
                {typeof detail.rule.confidence === 'number' && (
                  <span>conf. {detail.rule.confidence.toFixed(2)}</span>
                )}
              </div>

              {typeof detail.rule.source_snippet === 'string' && detail.rule.source_snippet && (
                <div className="mt-3 rounded-lg border-l-2 border-indigo-400 bg-indigo-50/60 px-3 py-2">
                  <p className="mb-1 text-[10px] font-bold uppercase tracking-wide text-indigo-500">
                    Citation source (verbatim — anti-hallucination)
                  </p>
                  <p className="text-xs italic leading-relaxed text-slate-600">
                    « {detail.rule.source_snippet} »
                  </p>
                </div>
              )}

              {detail.evidence_chunks.length > 0 && (
                <div className="mt-3">
                  <p className="mb-1 text-xs font-semibold text-slate-600">
                    Chunks de preuve (SUPPORTED_BY) :
                  </p>
                  {detail.evidence_chunks.map((e, i) => (
                    <p key={i} className="text-xs text-slate-500">
                      📄 {e.source_doc?.replace('.pdf', '')} · {e.article_ref ?? '—'}
                    </p>
                  ))}
                </div>
              )}

              {detail.derived_from.length > 0 && (
                <div className="mt-3">
                  <p className="mb-1 text-xs font-semibold text-slate-600">
                    Unités légales d'origine (DERIVED_FROM) :
                  </p>
                  {detail.derived_from.map((e, i) => (
                    <p key={i} className="font-mono text-[11px] text-slate-500">{e.unit_id}</p>
                  ))}
                </div>
              )}

              <p className="mt-4 text-[11px] text-slate-400">
                Cette chaîne règle → chunk → article → document est ce qui rend
                chaque décision auditable devant la BCT/CTAF.
              </p>
            </Card>
          </div>
        )}
      </div>
    </>
  );
}
