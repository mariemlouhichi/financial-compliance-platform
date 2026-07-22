// ============================================================================
// Corpus réglementaire — réorganisé en 3 onglets pour la lisibilité :
//   📚 Bibliothèque (les 34 PDF sources + prévisualisation)
//   🧬 Structure Neo4j (documents → articles → chunks)
//   🔎 Recherche hybride (le moteur réel du Retrieval Agent)
// + bandeau « pipeline de construction » qui met en valeur le travail :
//   PDF → extraction → traduction → chunking → embeddings → Weaviate + Neo4j
// ============================================================================
import { useEffect, useState } from 'react';
import { Search, BookOpen, Library, X, FileText, FileDown, ExternalLink, Network } from 'lucide-react';
import { api } from '../api/client';
import type { Chunk, CorpusDocument, CorpusChunkMeta, CorpusFile } from '../types/api';
import { PageHeader } from '../components/Layout';
import { Card, StatTile, EmptyState, Spinner } from '../components/ui';

const FOLDER_LABEL: Record<string, string> = {
  tunisia: '⚖️ Tunisie (BCT · lois · CTAF · CMF)',
  international: '🌐 International (FATF · UE · Bâle · Wolfsberg · EBA)',
};

const EXAMPLES = [
  'politically exposed persons enhanced vigilance',
  'suspicious transaction report filing CTAF',
  'structured transactions below threshold',
  'beneficial ownership verification requirements',
];

function familyBadge(sourceDoc: string) {
  const d = sourceDoc.toLowerCase();
  const isBct = d.startsWith('bct') || d.startsWith('loi') || d.includes('ctaf')
    || d.includes('reglementation');
  return isBct
    ? <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-800">⚖️ BCT / droit tunisien</span>
    : <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-bold text-indigo-800">🌐 Standard international</span>;
}

// Bandeau vitrine : le pipeline de construction de la base de connaissance
function PipelineStrip({ docs, chunks }: { docs: number; chunks: number }) {
  const steps = [
    { t: `${docs || 34} PDF sources`, s: 'BCT · FATF · UE · Bâle' },
    { t: 'Extraction + traduction', s: 'FR/AR → anglais réglementaire' },
    { t: 'Chunking sémantique', s: `${chunks ? chunks.toLocaleString('fr-FR') : '6 678'} extraits structurés` },
    { t: 'Embeddings BGE 768d', s: 'bge-base-en-v1.5' },
    { t: 'Weaviate + Neo4j', s: 'texte + structure articulaire' },
  ];
  return (
    <div className="mb-6 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="mb-3 text-[11px] font-bold uppercase tracking-wide text-slate-400">
        Pipeline de construction de la base de connaissance (hors-ligne)
      </p>
      <div className="flex flex-wrap items-center justify-center gap-1">
        {steps.map((st, i) => (
          <div key={st.t} className="flex items-center gap-1">
            <div className="rounded-xl border-2 border-indigo-200 bg-indigo-50/60 px-3.5 py-2 text-center">
              <p className="text-xs font-bold text-indigo-800">{st.t}</p>
              <p className="text-[9px] text-indigo-500">{st.s}</p>
            </div>
            {i < steps.length - 1 && <span className="px-0.5 text-slate-300">→</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

type Tab = 'biblio' | 'structure' | 'recherche';

export function RegulationsPage() {
  // ── Neo4j : documents structurés ────────────────────────────────────
  const [docs, setDocs] = useState<CorpusDocument[] | null>(null);
  const [totals, setTotals] = useState({ docs: 0, chunks: 0, jurisdictions: 0 });
  const [docsError, setDocsError] = useState('');
  const [selectedDoc, setSelectedDoc] = useState('');
  const [docChunks, setDocChunks] = useState<CorpusChunkMeta[] | null>(null);

  // ── Fichiers PDF sources ────────────────────────────────────────────
  const [files, setFiles] = useState<CorpusFile[] | null>(null);
  const [filesError, setFilesError] = useState('');
  const [preview, setPreview] = useState<CorpusFile | null>(null);

  // ── Recherche hybride ───────────────────────────────────────────────
  const [q, setQ] = useState('');
  const [results, setResults] = useState<Chunk[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [tab, setTab] = useState<Tab>('biblio');

  useEffect(() => {
    api.corpusDocuments()
      .then(r => {
        setDocs(r.documents);
        setTotals({ docs: r.total_documents, chunks: r.total_chunks,
                    jurisdictions: r.jurisdictions.length });
      })
      .catch(e => { setDocsError(String(e)); setDocs([]); });
    api.corpusFiles()
      .then(r => setFiles(r.files))
      .catch(e => { setFilesError(String(e)); setFiles([]); });
  }, []);

  const openDoc = async (name: string) => {
    setSelectedDoc(name); setDocChunks(null);
    try {
      const r = await api.corpusChunks(name, 40);
      setDocChunks(r.chunks);
    } catch { setDocChunks([]); }
  };

  const search = async (query: string) => {
    if (!query.trim()) return;
    setLoading(true); setError(''); setQ(query);
    try {
      const r = await api.searchRegulations(query.trim(), 8);
      setResults(r.results);
    } catch (e) {
      setError(`Recherche indisponible : ${e}`);
    } finally {
      setLoading(false);
    }
  };

  const TABS: { key: Tab; label: string; Icon: typeof Library }[] = [
    { key: 'biblio', label: `Bibliothèque (${files?.length ?? '…'})`, Icon: Library },
    { key: 'structure', label: 'Structure Neo4j', Icon: Network },
    { key: 'recherche', label: 'Recherche hybride', Icon: Search },
  ];

  return (
    <>
      <PageHeader
        title={<><Library size={22} className="mr-2 inline text-slate-400" />Corpus réglementaire</>}
        subtitle="Les textes qui alimentent le RAG — BCT, lois tunisiennes, CTAF, FATF, UE, Bâle, Wolfsberg"
      />

      {/* ── KPIs ──────────────────────────────────────────────────────── */}
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Documents sources" value={totals.docs || files?.length || '—'} />
        <StatTile label="Chunks indexés" value={totals.chunks ? totals.chunks.toLocaleString('fr-FR') : '—'}
          hint="Weaviate (texte) + Neo4j (structure)" />
        <StatTile label="Juridictions" value={totals.jurisdictions || '—'}
          hint="Tunisie · International · UE" />
        <StatTile label="Recs FATF couvertes" value="10"
          hint="par les 49 règles ground truth" />
      </div>

      {/* ── Bandeau vitrine : le pipeline de construction ─────────────── */}
      <PipelineStrip docs={totals.docs || files?.length || 0} chunks={totals.chunks} />

      {/* ── Onglets ───────────────────────────────────────────────────── */}
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

      {/* ══ Onglet 1 : BIBLIOTHÈQUE PDF ═══════════════════════════════ */}
      {tab === 'biblio' && (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className={preview ? 'lg:col-span-1' : 'lg:col-span-3'}>
            <Card title="Documents PDF sources"
                  subtitle="Les textes officiels originaux — clic pour prévisualiser">
              {!files ? <Spinner label="Lecture du dossier…" /> :
                filesError ? (
                  <EmptyState>Dossier PDF introuvable — vérifie{' '}
                    <code className="rounded bg-slate-200 px-1.5 py-0.5">PFE_AML_KYC/01_regulations</code>
                  </EmptyState>
                ) : (
                  <div className="space-y-4">
                    {['tunisia', 'international'].map(folder => {
                      const group = files.filter(f => f.folder === folder);
                      if (group.length === 0) return null;
                      return (
                        <div key={folder}>
                          <p className="mb-1.5 text-xs font-semibold text-slate-500">
                            {FOLDER_LABEL[folder] ?? folder}{' '}
                            <span className="font-normal text-slate-400">· {group.length}</span>
                          </p>
                          <div className={`grid gap-1.5 ${preview ? 'grid-cols-1' : 'sm:grid-cols-2 lg:grid-cols-3'}`}>
                            {group.map(f => (
                              <button key={f.rel_path} onClick={() => setPreview(f)}
                                className={`flex items-center gap-2 rounded-lg border px-2.5 py-2 text-left text-xs hover:border-slate-400 hover:bg-slate-50 ${
                                  preview?.rel_path === f.rel_path ? 'border-slate-900 bg-slate-50' : 'border-slate-200'}`}>
                                <FileText size={14} className="shrink-0 text-red-500" />
                                <span className="flex-1 truncate text-slate-700">
                                  {f.name.replace(/\.pdf$/i, '')}
                                </span>
                                <span className="shrink-0 tabular-nums text-[10px] text-slate-400">
                                  {f.size_kb > 1024 ? `${(f.size_kb / 1024).toFixed(1)} Mo` : `${Math.round(f.size_kb)} Ko`}
                                </span>
                              </button>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
            </Card>
          </div>

          {preview && (
            <div className="lg:col-span-2">
              <Card
                title={<span className="text-sm">{preview.name.replace(/\.pdf$/i, '')}</span>}
                subtitle={FOLDER_LABEL[preview.folder] ?? preview.folder}
                actions={
                  <div className="flex items-center gap-1.5">
                    <a href={api.corpusFileUrl(preview.rel_path)} target="_blank" rel="noopener noreferrer"
                      className="flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-[11px] font-medium text-slate-600 hover:bg-slate-50">
                      <ExternalLink size={12} /> Onglet
                    </a>
                    <a href={api.corpusFileUrl(preview.rel_path)} download={preview.name}
                      className="flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-[11px] font-medium text-slate-600 hover:bg-slate-50">
                      <FileDown size={12} /> Télécharger
                    </a>
                    <button onClick={() => setPreview(null)}
                      className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
                      <X size={15} />
                    </button>
                  </div>
                }>
                <iframe
                  src={api.corpusFileUrl(preview.rel_path)}
                  title={preview.name}
                  className="h-[640px] w-full rounded-lg border border-slate-200 bg-white"
                />
              </Card>
            </div>
          )}
        </div>
      )}

      {/* ══ Onglet 2 : STRUCTURE NEO4J ════════════════════════════════ */}
      {tab === 'structure' && (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className={selectedDoc ? 'lg:col-span-2' : 'lg:col-span-3'}>
            <Card title="Documents structurés dans le graphe"
                  subtitle="Clic sur un document → sa structure articulaire (articles / chunks)">
              {!docs ? <Spinner label="Chargement depuis Neo4j…" /> :
                docsError ? (
                  <EmptyState>Neo4j injoignable — lance{' '}
                    <code className="rounded bg-slate-200 px-1.5 py-0.5">docker compose up -d neo4j</code>
                  </EmptyState>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400">
                          <th className="pb-2 pr-4">Document</th>
                          <th className="pb-2 pr-4">Famille</th>
                          <th className="pb-2 pr-4">Juridiction</th>
                          <th className="pb-2 pr-4">Articles</th>
                          <th className="pb-2">Chunks</th>
                        </tr>
                      </thead>
                      <tbody>
                        {docs.map(d => (
                          <tr key={d.name} onClick={() => openDoc(d.name)}
                            className={`cursor-pointer border-b border-slate-100 hover:bg-slate-50 ${
                              selectedDoc === d.name ? 'bg-slate-50' : ''}`}>
                            <td className="py-2.5 pr-4">
                              <span className="flex items-center gap-1.5 text-xs font-semibold text-slate-800">
                                <FileText size={13} className="shrink-0 text-slate-400" />
                                {d.name.replace('.pdf', '')}
                              </span>
                            </td>
                            <td className="py-2.5 pr-4">{familyBadge(d.name)}</td>
                            <td className="py-2.5 pr-4 text-xs text-slate-600">{d.jurisdiction ?? '—'}</td>
                            <td className="py-2.5 pr-4 text-xs tabular-nums text-slate-700">{d.legal_units}</td>
                            <td className="py-2.5 text-xs tabular-nums text-slate-700">{d.chunks}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
            </Card>
          </div>

          {selectedDoc && (
            <div>
              <Card
                title={<span className="text-sm">{selectedDoc.replace('.pdf', '')}</span>}
                actions={
                  <button onClick={() => setSelectedDoc('')}
                    className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
                    <X size={15} />
                  </button>
                }>
                {!docChunks ? <Spinner /> : docChunks.length === 0 ? (
                  <EmptyState>Aucun chunk trouvé pour ce document.</EmptyState>
                ) : (
                  <ul className="max-h-[480px] space-y-1.5 overflow-y-auto pr-1">
                    {docChunks.map(c => (
                      <li key={c.chunk_id}
                        className="rounded-lg border border-slate-100 bg-slate-50/60 px-2.5 py-1.5">
                        <p className="text-xs font-semibold text-slate-700">
                          {c.article_ref ?? 'sans référence'}
                          {c.has_threshold && (
                            <span className="ml-1.5 rounded bg-amber-100 px-1 py-0.5 text-[9px] font-bold text-amber-800"
                              title="contient un seuil chiffré">SEUIL</span>
                          )}
                        </p>
                        <p className="text-[10px] text-slate-400">
                          rôle : {c.chunk_role ?? c.unit_role ?? '—'}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          )}
        </div>
      )}

      {/* ══ Onglet 3 : RECHERCHE HYBRIDE ══════════════════════════════ */}
      {tab === 'recherche' && (
        <>
          <Card title="Recherche hybride dans le corpus"
                subtitle="BM25 + vecteurs (Weaviate) → fusion RRF → reranker BGE — le moteur réel du Retrieval Agent">
            <form onSubmit={e => { e.preventDefault(); search(q); }} className="flex gap-2">
              <input value={q} onChange={e => setQ(e.target.value)}
                placeholder="Requête en anglais réglementaire — ex : customer due diligence identification…"
                className="w-full rounded-xl border border-slate-200 bg-slate-50/70 px-3.5 py-2.5 text-sm focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-100" />
              <button type="submit" disabled={loading || !q.trim()}
                className="flex shrink-0 items-center gap-1.5 rounded-xl bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-40">
                <Search size={15} /> Rechercher
              </button>
            </form>
            <div className="mt-3 flex flex-wrap gap-2">
              {EXAMPLES.map(ex => (
                <button key={ex} onClick={() => search(ex)}
                  className="rounded-full border border-slate-200 px-3 py-1 text-[11px] text-slate-500 hover:border-slate-400 hover:text-slate-700">
                  {ex}
                </button>
              ))}
            </div>
          </Card>

          <div className="mt-5">
            {loading && <Spinner label="Recherche dans le corpus (pipeline 5 étapes)… ~10-20 s" />}
            {error && <EmptyState>{error}</EmptyState>}
            {results && !loading && (
              results.length === 0 ? <EmptyState>Aucun résultat.</EmptyState> : (
                <div className="space-y-3">
                  {results.map((c, i) => (
                    <div key={i} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                      <div className="mb-1.5 flex flex-wrap items-center gap-2">
                        <BookOpen size={14} className="text-slate-400" />
                        <span className="text-sm font-semibold text-slate-800">
                          {c.source_doc.replace('.pdf', '')}
                        </span>
                        <span className="text-xs text-slate-500">· {c.article_ref}</span>
                        {familyBadge(c.source_doc)}
                        <span className="ml-auto text-[11px] tabular-nums text-slate-400"
                          title="score du reranker">
                          score {c.score.toFixed(3)} · via {c.retrieval_path}
                        </span>
                      </div>
                      <p className="text-sm leading-relaxed text-slate-600">{c.text}…</p>
                      <p className="mt-1.5 text-[11px] text-slate-400">
                        {c.jurisdiction} · rôle : {c.chunk_role}
                      </p>
                    </div>
                  ))}
                </div>
              )
            )}
          </div>
        </>
      )}
    </>
  );
}
