// ============================================================================
// ESPACE CONFORMITÉ — Graphe de connaissances Neo4j, réorganisé en 3 onglets :
//   Vue d'ensemble (chaîne d'ancrage réelle) · Statistiques · Explorer (Cypher)
// + bandeau « schéma du graphe » qui met en valeur la conception
//   (Document ← LegalUnit ← Chunk ← Rule) et le compteur de règles APPRISES.
// ============================================================================
import { useEffect, useState } from 'react';
import { GitFork, ExternalLink, Copy, Check, Network, KeyRound, BarChart3, TerminalSquare } from 'lucide-react';
import { api } from '../api/client';
import type { GraphStats, GraphSample } from '../types/api';
import { PageHeader } from '../components/Layout';
import { Card, StatTile, Spinner, EmptyState } from '../components/ui';
import { BarList } from '../components/charts';
import { GraphView } from '../components/GraphView';

const NEO4J_BROWSER = 'http://localhost:7474/browser/';

const PRESETS: { label: string; hint: string; cypher: string }[] = [
  { label: 'Vue du schéma', hint: 'Tous les types de nœuds et relations',
    cypher: 'CALL db.schema.visualization()' },
  { label: 'Règles ground truth (49)', hint: 'Les règles validées humainement',
    cypher: "MATCH (r:Rule) WHERE r.origin = 'human_verified' RETURN r LIMIT 49" },
  { label: 'Règles apprises par le RAG', hint: 'Persistées par le chemin sémantique',
    cypher: "MATCH (r:Rule) WHERE r.origin <> 'human_verified' RETURN r LIMIT 50" },
  { label: 'Chaîne de preuve complète', hint: 'Règle → chunk → article → document',
    cypher: 'MATCH p = (r:Rule)-[:SUPPORTED_BY]->(:Chunk)-[:REALIZES]->(:LegalUnit)-[:FROM_DOCUMENT]->(:Document) RETURN p LIMIT 25' },
  { label: 'Règles applicables SWIFT', hint: 'Filtrage par type de transaction',
    cypher: "MATCH (r:Rule)-[:APPLIES_TO]->(t:TransactionType {name: 'SWIFT'}) RETURN r, t LIMIT 50" },
  { label: 'Structure d’un document', hint: 'Articles reliés par NEXT_IN_DOC',
    cypher: 'MATCH (d:Document)<-[:FROM_DOCUMENT]-(lu:LegalUnit) OPTIONAL MATCH (lu)-[:NEXT_IN_DOC]->(next) RETURN d, lu, next LIMIT 80' },
];

const NODE_COLORS: Record<string, string> = {
  Chunk: 'bg-sky-500', LegalUnit: 'bg-indigo-500', Rule: 'bg-violet-500',
  Document: 'bg-teal-600', Jurisdiction: 'bg-slate-500',
  RoleCategory: 'bg-slate-400', TransactionType: 'bg-slate-400',
};

type Tab = 'apercu' | 'stats' | 'cypher';

// Bandeau vitrine : le schéma du graphe (la conception, pas les données)
function SchemaStrip() {
  const node = (label: string, sub: string, cls: string) => (
    <div className={`rounded-xl border-2 px-4 py-2 text-center ${cls}`}>
      <p className="text-xs font-bold">{label}</p>
      <p className="text-[9px] opacity-70">{sub}</p>
    </div>
  );
  const arrow = (label: string) => (
    <div className="flex flex-col items-center px-1">
      <span className="font-mono text-[8px] font-bold uppercase tracking-wide text-slate-400">{label}</span>
      <span className="text-slate-300">→</span>
    </div>
  );
  return (
    <div className="mb-6 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="mb-3 text-[11px] font-bold uppercase tracking-wide text-slate-400">
        Schéma — la chaîne d'ancrage anti-hallucination
      </p>
      <div className="flex flex-wrap items-center justify-center gap-1">
        {node('Rule', 'règle extraite', 'border-violet-300 bg-violet-50 text-violet-800')}
        {arrow('SUPPORTED_BY')}
        {node('Chunk', 'extrait indexé', 'border-sky-300 bg-sky-50 text-sky-800')}
        {arrow('REALIZES')}
        {node('LegalUnit', 'article de loi', 'border-indigo-300 bg-indigo-50 text-indigo-800')}
        {arrow('FROM_DOCUMENT')}
        {node('Document', 'texte officiel', 'border-teal-300 bg-teal-50 text-teal-800')}
      </div>
      <p className="mt-3 text-center text-[11px] text-slate-500">
        Chaque règle remonte jusqu'au texte officiel qui la fonde — chaque décision est
        <b> auditable devant la BCT/CTAF</b>. Le graphe <b>s'enrichit à chaque transaction</b>
        (règles validées persistées par le Compliance Agent).
      </p>
    </div>
  );
}

export function GraphPage() {
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [sample, setSample] = useState<GraphSample | null>(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState('');
  const [tab, setTab] = useState<Tab>('apercu');

  useEffect(() => {
    api.graphStats().then(setStats).catch(e => setError(String(e)));
    api.graphSample(8).then(setSample).catch(() => { /* les stats suffisent */ });
  }, []);

  const copy = async (cypher: string, label: string) => {
    try {
      await navigator.clipboard.writeText(cypher);
      setCopied(label);
      setTimeout(() => setCopied(''), 2000);
    } catch { /* presse-papier indisponible */ }
  };

  const openInBrowser = (cypher: string) => {
    window.open(`${NEO4J_BROWSER}?cmd=edit&arg=${encodeURIComponent(cypher)}`,
      '_blank', 'noopener');
  };

  const learned = Math.max(0, (stats?.nodes.Rule ?? 49) - 49);

  const TABS: { key: Tab; label: string; Icon: typeof Network }[] = [
    { key: 'apercu', label: "Vue d'ensemble", Icon: Network },
    { key: 'stats', label: 'Statistiques', Icon: BarChart3 },
    { key: 'cypher', label: 'Explorer (Cypher)', Icon: TerminalSquare },
  ];

  return (
    <>
      <PageHeader
        title={<><GitFork size={22} className="mr-2 inline text-slate-400" />Graphe de connaissances</>}
        subtitle="La mémoire réglementaire gouvernée — structure du corpus + règles, enrichie à chaque analyse"
        actions={
          <a href={NEO4J_BROWSER} target="_blank" rel="noopener noreferrer"
            className="flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700">
            <ExternalLink size={13} /> Neo4j Browser
          </a>
        }
      />

      {error ? (
        <EmptyState>
          Neo4j injoignable — lance{' '}
          <code className="rounded bg-slate-200 px-1.5 py-0.5">docker compose up -d neo4j</code>
          <span className="mt-1 block text-xs text-slate-400">{error}</span>
        </EmptyState>
      ) : !stats ? (
        <Spinner label="Interrogation du graphe…" />
      ) : (
        <>
          {/* ── KPIs ──────────────────────────────────────────────────── */}
          <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-5">
            <StatTile label="Nœuds" value={stats.total_nodes.toLocaleString('fr-FR')} />
            <StatTile label="Relations" value={stats.total_relationships.toLocaleString('fr-FR')} />
            <StatTile label="Articles structurés" value={(stats.nodes.LegalUnit ?? 0).toLocaleString('fr-FR')}
              hint="unités légales (LegalUnit)" />
            <StatTile label="Règles ground truth" value={49} hint="validées humainement" />
            <StatTile label="Règles apprises" value={`+${learned}`} tone={learned > 0 ? 'success' : 'default'}
              hint="persistées par le RAG — croît à l'usage" />
          </div>

          {/* ── Bandeau vitrine : le schéma ───────────────────────────── */}
          <SchemaStrip />

          {/* ── Onglets ───────────────────────────────────────────────── */}
          <div className="mb-4 flex gap-1 rounded-xl bg-slate-100 p-1">
            {TABS.map(({ key, label, Icon }) => (
              <button key={key} onClick={() => setTab(key)}
                className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold transition-colors ${
                  tab === key ? 'bg-white text-slate-900 shadow-sm'
                              : 'text-slate-500 hover:text-slate-700'}`}>
                <Icon size={14} /> {label}
              </button>
            ))}
          </div>

          {/* ── Onglet 1 : vue d'ensemble (sous-graphe réel) ──────────── */}
          {tab === 'apercu' && (
            <Card
              title="Chaîne d'ancrage — extrait réel du graphe"
              subtitle="Données réelles Neo4j rendues dans la plateforme (survolez un nœud pour son libellé)">
              {!sample ? <Spinner label="Extraction d'un sous-graphe…" />
                : sample.nodes.length === 0
                  ? <EmptyState>Aucune règle sourcée à afficher.</EmptyState>
                  : <GraphView nodes={sample.nodes} edges={sample.edges} />}
            </Card>
          )}

          {/* ── Onglet 2 : statistiques ───────────────────────────────── */}
          {tab === 'stats' && (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card title="Nœuds par type" subtitle="Comptes réels du graphe">
                <BarList rows={Object.entries(stats.nodes).map(([k, v]) => ({
                  label: k, value: v, color: NODE_COLORS[k] ?? 'bg-slate-400',
                }))} />
              </Card>
              <Card title="Relations par type"
                    subtitle="FROM_DOCUMENT · REALIZES · NEXT_IN_DOC · SUPPORTED_BY…">
                <BarList rows={Object.entries(stats.relationships).map(([k, v]) => ({
                  label: k, value: v, color: 'bg-indigo-500',
                }))} />
              </Card>
            </div>
          )}

          {/* ── Onglet 3 : requêtes Cypher (compact) ──────────────────── */}
          {tab === 'cypher' && (
            <div className="space-y-4">
              <Card title="Requêtes prêtes pour la démo"
                    subtitle="« Ouvrir » pré-remplit la requête dans Neo4j Browser (⏎ pour exécuter)">
                <div className="divide-y divide-slate-100">
                  {PRESETS.map(p => (
                    <div key={p.label} className="flex flex-wrap items-center gap-3 py-2.5">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-slate-800">{p.label}</p>
                        <p className="truncate font-mono text-[10px] text-slate-400" title={p.cypher}>
                          {p.cypher}
                        </p>
                      </div>
                      <span className="hidden text-[11px] text-slate-400 md:block">{p.hint}</span>
                      <div className="flex shrink-0 gap-1.5">
                        <button onClick={() => openInBrowser(p.cypher)}
                          className="flex items-center gap-1 rounded-lg bg-slate-900 px-2.5 py-1.5 text-[11px] font-semibold text-white hover:bg-slate-700">
                          <ExternalLink size={11} /> Ouvrir
                        </button>
                        <button onClick={() => copy(p.cypher, p.label)}
                          className="flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-[11px] font-medium text-slate-600 hover:bg-slate-50">
                          {copied === p.label
                            ? <><Check size={11} className="text-emerald-600" /> Copié</>
                            : <><Copy size={11} /> Copier</>}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </Card>

              <Card title={<><KeyRound size={14} className="mr-1.5 inline text-slate-400" />Connexion Neo4j Browser</>}>
                <div className="flex flex-wrap items-center gap-x-6 gap-y-1 font-mono text-xs text-slate-600">
                  <span><span className="text-slate-400">URL :</span> bolt://localhost:7687</span>
                  <span><span className="text-slate-400">user :</span> neo4j</span>
                  <span><span className="text-slate-400">mot de passe :</span> amlkyc2025</span>
                </div>
                <details className="mt-2">
                  <summary className="cursor-pointer text-[11px] font-semibold text-indigo-600">
                    « Connection unauthorized » alors que le mot de passe est bon ?
                  </summary>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">
                    C'est le verrouillage anti-force-brute de Neo4j après des tentatives échouées.
                    Solution : <code className="rounded bg-slate-100 px-1">docker compose restart neo4j</code>,
                    attendre ~30 s, protocole <b>bolt://</b>, mot de passe sans espaces.
                    (La plateforme, elle, reste connectée — les statistiques ci-dessus sont réelles.)
                  </p>
                </details>
              </Card>
            </div>
          )}
        </>
      )}
    </>
  );
}
