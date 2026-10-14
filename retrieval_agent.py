"""
retrieval_agent.py  —  VERSION OPTIMISÉE (v2)
=============================================
Pipeline de récupération hybride en 5 étapes pour le système AML/KYC.

ARCHITECTURE (conforme au mémoire PFE) :
  Step 0 — Cache Neo4j     : règles déjà validées → skip si cache hit (≥3 règles)
  Step 1 — Graph pre-filter: Neo4j filtre par juridiction/type → réduit l'espace
  Step 2 — Hybrid Search   : BM25 (top-20) + Vector (top-20) en parallèle (Weaviate)
  Step 3 — RRF Fusion      : Reciprocal Rank Fusion (k=60) → top-15 fusionnés
  Step 4 — Parent Expansion: Neo4j NEXT_IN_DOC → contexte réglementaire complet
  Step 5 — Reranking       : BGE-reranker-base (cross-encoder) → top-5 finaux

OPTIMISATIONS v2 (vs v1) :
  ✅ Step 1 filtre VRAIMENT par source_docs (avant: filtre inutilisé)
  ✅ Step 4 batché en 1 requête Cypher UNWIND (avant: N+1 query)
  ✅ Step 4 batché en 1 requête Weaviate par source_doc (avant: N requêtes)
  ✅ _fetch_neighbor_texts SORTI de la session Neo4j (anti-pattern corrigé)
  ✅ TX_TYPE_TO_ROLES = mapping réel par type (avant: stub renvoyant tout)
  ✅ _extract_keywords préserve les acronymes AML (PEP, EU, UN, EDD, KYC, STR...)
  ✅ Cache LRU des embeddings de requête (re-query gratuites)
  ✅ RetrievalMetrics dataclass + export JSON pour le rapport PFE
  ✅ Retry avec backoff sur Weaviate/Neo4j (résilience)
  ✅ Fix nullability bug score (is not None au lieu de truthy check)

USAGE :
  # En tant que module
  from retrieval_agent import RetrievalPipeline
  pipeline = RetrievalPipeline()
  results, metrics = pipeline.retrieve("KYC obligations for PEPs", tx_type="SWIFT")

  # CLI
  python retrieval_agent.py "KYC requirements for PEP" --tx-type SWIFT
  python retrieval_agent.py "threshold for cash transactions" --jurisdiction Tunisia
  python retrieval_agent.py "sanctions screening" --metrics-out metrics.json --verbose

PRÉREQUIS :
  - Weaviate lancé  : docker compose up -d weaviate
  - Neo4j lancé     : docker compose up -d neo4j
  - pip install sentence-transformers weaviate-client neo4j
"""

import argparse
import json
import os
import sys
import time
import logging
from collections import defaultdict, OrderedDict
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable, Any

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Neo4j
NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "amlkyc2025")

# Weaviate
WEAVIATE_HOST      = os.getenv("WEAVIATE_HOST",      "localhost")
WEAVIATE_HTTP_PORT = int(os.getenv("WEAVIATE_HTTP_PORT", "8088"))
WEAVIATE_GRPC_PORT = int(os.getenv("WEAVIATE_GRPC_PORT", "50051"))
# Mode CLOUD : si WEAVIATE_URL est défini, on se connecte au cluster distant
# (TLS + clé API) au lieu du conteneur local. Permet de basculer entre
# développement local et déploiement sans toucher au code.
WEAVIATE_URL       = os.getenv("WEAVIATE_URL", "").strip()
WEAVIATE_API_KEY   = os.getenv("WEAVIATE_API_KEY", "").strip()
COLLECTION_NAME    = "AMLChunk"

# Modèles
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
RERANKER_MODEL  = os.getenv("RERANKER_MODEL",  "BAAI/bge-reranker-base")
RERANKING_ENABLED = os.getenv("RERANKING_ENABLED", "true").lower() == "true"

# Paramètres pipeline
BM25_TOP_K       = 20    # Step 2 — résultats BM25
VECTOR_TOP_K     = 20    # Step 2 — résultats vectoriels
RRF_K            = 60    # Step 3 — paramètre RRF (standard académique)
FUSION_TOP_K     = 15    # Step 3 — résultats après fusion
EXPANSION_DEPTH  = 1     # Step 4 — articles voisins (±1 NEXT_IN_DOC)
FINAL_TOP_K      = 5     # Step 5 — résultats finaux
# Step 5 — seuil de pertinence du reranker : on écarte les extraits dont le
# score BGE est sous ce seuil (réduction du bruit → meilleure Context Precision).
# Réglable sans toucher au code : $env:RERANK_MIN_SCORE = "0.5"
RERANK_MIN_SCORE = float(os.getenv("RERANK_MIN_SCORE", "0.0"))
RERANK_MIN_KEEP  = int(os.getenv("RERANK_MIN_KEEP",  "2"))   # garde min (jamais vider)
CACHE_MIN_RULES  = 3     # Step 0 — seuil cache hit
SOURCE_DOC_FILTER_MAX = 25  # Step 1 — n'applique le filtre source_doc que si <25 docs
NEIGHBOR_TEXT_MAX     = 500 # Step 4 — max chars par voisin
RERANKER_PAIR_MAX     = 1500# Step 5 — max chars par paire query+doc
EMBED_CACHE_SIZE      = 128 # Cache LRU embeddings de requête
RETRY_MAX_ATTEMPTS    = 3   # Retry sur erreur Weaviate/Neo4j
RETRY_BACKOFF_BASE    = 0.5 # Backoff: 0.5s, 1s, 2s

logger = logging.getLogger("retrieval_agent")

# ═══════════════════════════════════════════════════════════════════════════
#  CONSTANTES MÉTIER
# ═══════════════════════════════════════════════════════════════════════════

# Mapping tx_type → chunk_roles pertinents (FIX: v1 retournait tout)
# Source: analyse des règles ground truth (49 règles validées)
TX_TYPE_TO_ROLES = {
    "SWIFT": [
        # International → focus sanctions + Travel Rule + KYC renforcé
        "KYC", "THRESHOLD", "SANCTIONS", "REPORTING",
        "OBLIGATION", "RISK_ASSESSMENT", "RED_FLAG",
    ],
    "VIREMENT_LOCAL": [
        # Local → focus KYC standard + seuils + déclaration
        "KYC", "THRESHOLD", "REPORTING", "OBLIGATION", "RISK_ASSESSMENT",
    ],
    "MANDAT": [
        # Money order → focus identification + red flags MVTS
        "KYC", "THRESHOLD", "OBLIGATION", "RED_FLAG", "REPORTING",
    ],
    "DEPOT_ESPECES": [
        # Cash → focus seuils + smurfing/structuring + reporting
        "THRESHOLD", "REPORTING", "RED_FLAG", "OBLIGATION", "RISK_ASSESSMENT",
    ],
}
ALL_ROLES = [
    "OBLIGATION", "KYC", "THRESHOLD", "SANCTIONS",
    "REPORTING", "RED_FLAG", "RISK_ASSESSMENT",
]

# Acronymes métier AML/KYC à préserver dans l'extraction de mots-clés
# (FIX: v1 éliminait tout ce qui faisait < 3 chars)
DOMAIN_ACRONYMS = {
    "eu", "un", "us", "id", "ml", "tf",                # entités/concepts
    "aml", "kyc", "cdd", "edd", "cft", "pep", "bo",    # processus AML
    "str", "ctr", "sar", "fiu", "vasp",                # reporting/entités
    "ofac", "ctaf", "bct", "cmf", "eba", "fatf",       # régulateurs
    "iban", "bic", "swift", "mt",                       # paiements
    "uae", "uk",                                        # juridictions
}

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "must", "for", "of", "in",
    "on", "at", "to", "from", "by", "with", "and", "or", "not", "but",
    "if", "then", "than", "when", "what", "which", "who", "how", "where",
    "that", "this", "these", "those", "it", "its", "as",
}


# ═══════════════════════════════════════════════════════════════════════════
#  DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RetrievalResult:
    """Un chunk récupéré avec son score et son contexte."""
    text:            str
    source_doc:      str
    article_ref:     str
    jurisdiction:    str
    chunk_role:      str
    has_threshold:   bool
    score:           float
    rank:            int             = 0
    retrieval_path:  str             = ""     # "bm25" | "vector" | "rrf" | "cache" | "...+reranked"
    expanded_context: list[str]      = field(default_factory=list)


@dataclass
class RetrievalMetrics:
    """Métriques par étape — exportables pour le rapport PFE."""
    query: str
    tx_type: Optional[str]    = None
    jurisdiction: Optional[str] = None
    cache_hit: bool           = False
    # Timings (ms)
    step0_ms: float           = 0.0
    step1_ms: float           = 0.0
    step2_bm25_ms: float      = 0.0
    step2_vector_ms: float    = 0.0
    step3_ms: float           = 0.0
    step4_ms: float           = 0.0
    step5_ms: float           = 0.0
    total_ms: float           = 0.0
    # Counts
    prefilter_docs: int       = 0
    bm25_count: int           = 0
    vector_count: int         = 0
    fused_count: int          = 0
    expanded_with_context: int = 0
    final_count: int          = 0
    # Embedding cache
    embed_cache_hit: bool     = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════
#  RETRY DECORATOR
# ═══════════════════════════════════════════════════════════════════════════

def _with_retry(fn: Callable, *args, max_attempts: int = RETRY_MAX_ATTEMPTS,
                base: float = RETRY_BACKOFF_BASE, **kwargs) -> Any:
    """Exécute fn avec backoff exponentiel. Lève la dernière exception si échec total."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt < max_attempts - 1:
                wait = base * (2 ** attempt)
                logger.debug(f"Retry {attempt+1}/{max_attempts} after {wait}s: {e}")
                time.sleep(wait)
    raise last_exc


# ═══════════════════════════════════════════════════════════════════════════
#  PIPELINE PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════

class RetrievalPipeline:
    """
    Pipeline hybride Weaviate + Neo4j en 5 étapes.
    
    Initialise les connexions une seule fois, réutilisable pour
    plusieurs requêtes (pattern : une instance par session agent).
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._weaviate = None
        self._neo4j    = None
        self._embedder = None
        self._reranker = None
        # Cache LRU des embeddings (FIX v2: re-query gratuites)
        self._embed_cache: "OrderedDict[str, list]" = OrderedDict()

    # ── Lazy loading ──────────────────────────────────────────────────────

    @property
    def weaviate(self):
        if self._weaviate is None:
            import weaviate
            if WEAVIATE_URL:
                # Cluster distant (Weaviate Cloud) : TLS + authentification.
                # Délais élargis : le contrôle gRPC par défaut (~2 s) échoue
                # sur une connexion lente ou depuis Azure (latence réseau).
                from weaviate.classes.init import Auth, AdditionalConfig, Timeout
                self._weaviate = weaviate.connect_to_weaviate_cloud(
                    cluster_url=WEAVIATE_URL,
                    auth_credentials=Auth.api_key(WEAVIATE_API_KEY),
                    additional_config=AdditionalConfig(
                        timeout=Timeout(init=60, query=120, insert=300)),
                )
                if self.verbose:
                    print(f"  ✅ Weaviate Cloud connecté ({WEAVIATE_URL})")
            else:
                self._weaviate = weaviate.connect_to_local(
                    host=WEAVIATE_HOST,
                    port=WEAVIATE_HTTP_PORT,
                    grpc_port=WEAVIATE_GRPC_PORT,
                )
                if self.verbose:
                    print(f"  ✅ Weaviate connecté ({WEAVIATE_HOST}:{WEAVIATE_HTTP_PORT})")
        return self._weaviate

    @property
    def neo4j(self):
        if self._neo4j is None:
            from neo4j import GraphDatabase
            self._neo4j = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
            self._neo4j.verify_connectivity()
            if self.verbose:
                print(f"  ✅ Neo4j connecté ({NEO4J_URI})")
        return self._neo4j

    @property
    def embedder(self):
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer(EMBEDDING_MODEL)
            if self.verbose:
                print(f"  ✅ Embedder chargé ({EMBEDDING_MODEL})")
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is None and RERANKING_ENABLED:
            try:
                from sentence_transformers import CrossEncoder
                self._reranker = CrossEncoder(RERANKER_MODEL)
                if self.verbose:
                    print(f"  ✅ Reranker chargé ({RERANKER_MODEL})")
            except Exception as e:
                logger.warning(f"Reranker indisponible: {e}")
                self._reranker = False
        return self._reranker if self._reranker is not False else None

    def close(self):
        """Ferme les connexions proprement."""
        if self._weaviate:
            try: self._weaviate.close()
            except Exception: pass
        if self._neo4j:
            try: self._neo4j.close()
            except Exception: pass

    # FIX v2: cache LRU embeddings de requête
    def _encode_query(self, query: str) -> tuple[list, bool]:
        """Encode avec cache LRU. Retourne (vector, was_cached)."""
        if query in self._embed_cache:
            # Move to end (most recently used)
            self._embed_cache.move_to_end(query)
            return self._embed_cache[query], True

        vec = self.embedder.encode(query, normalize_embeddings=True).tolist()

        # Insert and evict if needed
        self._embed_cache[query] = vec
        if len(self._embed_cache) > EMBED_CACHE_SIZE:
            self._embed_cache.popitem(last=False)
        return vec, False

    # ══════════════════════════════════════════════════════════════════════
    #  MÉTHODE PRINCIPALE
    # ══════════════════════════════════════════════════════════════════════

    def retrieve(self,
                 query: str,
                 tx_type: str = None,
                 jurisdiction: str = None,
                 top_k: int = FINAL_TOP_K,
                 skip_cache: bool = False,
                 return_metrics: bool = False
                 ) -> "list[RetrievalResult] | tuple[list[RetrievalResult], RetrievalMetrics]":
        """
        Pipeline complet de récupération hybride.

        Args:
            query:        Sous-question de recherche réglementaire
            tx_type:      SWIFT | VIREMENT_LOCAL | MANDAT | DEPOT_ESPECES
            jurisdiction: Tunisia | International | European Union
            top_k:        Nombre de résultats finaux (défaut: 5)
            skip_cache:   Forcer la recherche même si cache hit
            return_metrics: Si True, renvoie aussi RetrievalMetrics

        Returns:
            list[RetrievalResult]  OU  (list[RetrievalResult], RetrievalMetrics)
        """
        t0 = time.time()
        metrics = RetrievalMetrics(query=query, tx_type=tx_type, jurisdiction=jurisdiction)

        # ── STEP 0 — Cache Neo4j ─────────────────────────────────────────
        t_step = time.time()
        if not skip_cache:
            cached = self._step0_cache_check(query, tx_type)
            metrics.step0_ms = (time.time() - t_step) * 1000
            if cached and len(cached) >= CACHE_MIN_RULES:
                metrics.cache_hit = True
                metrics.final_count = min(len(cached), top_k)
                metrics.total_ms = (time.time() - t0) * 1000
                if self.verbose:
                    self._print_step("0 — Cache", f"HIT: {len(cached)} règles", metrics.step0_ms/1000)
                final = cached[:top_k]
                for i, r in enumerate(final, 1):
                    r.rank = i
                return (final, metrics) if return_metrics else final

        # ── STEP 1 — Graph pre-filter ────────────────────────────────────
        t_step = time.time()
        prefilter = self._step1_graph_prefilter(tx_type, jurisdiction)
        metrics.step1_ms = (time.time() - t_step) * 1000
        metrics.prefilter_docs = len(prefilter.get("source_docs", []))
        if self.verbose:
            self._print_step("1 — Graph filter",
                             f"{metrics.prefilter_docs} docs, jurisdiction={jurisdiction or 'ALL'}",
                             metrics.step1_ms/1000)

        # ── STEP 2 — Hybrid Search ───────────────────────────────────────
        # FIX v2: embedding via cache
        query_vector, was_cached = self._encode_query(query)
        metrics.embed_cache_hit = was_cached

        # FIX v2: filtres incluent source_docs (avant: jamais utilisé)
        filters = self._build_weaviate_filters(prefilter)

        t_bm25 = time.time()
        bm25_results = _with_retry(self._step2_bm25, query, filters, BM25_TOP_K)
        metrics.step2_bm25_ms = (time.time() - t_bm25) * 1000
        metrics.bm25_count = len(bm25_results)

        t_vec = time.time()
        vector_results = _with_retry(self._step2_vector, query_vector, filters, VECTOR_TOP_K)
        metrics.step2_vector_ms = (time.time() - t_vec) * 1000
        metrics.vector_count = len(vector_results)

        if self.verbose:
            self._print_step("2 — Hybrid Search",
                             f"BM25={metrics.bm25_count}, Vector={metrics.vector_count}"
                             + (" [cached embed]" if was_cached else ""),
                             (metrics.step2_bm25_ms + metrics.step2_vector_ms)/1000)

        # ── STEP 3 — RRF Fusion ──────────────────────────────────────────
        t_step = time.time()
        fused = self._step3_rrf_fusion(bm25_results, vector_results, FUSION_TOP_K)
        metrics.step3_ms = (time.time() - t_step) * 1000
        metrics.fused_count = len(fused)
        if self.verbose:
            self._print_step("3 — RRF Fusion", f"{len(fused)} chunks fusionnés",
                             metrics.step3_ms/1000)

        # ── STEP 4 — Parent Expansion (BATCHÉ) ───────────────────────────
        t_step = time.time()
        expanded = self._step4_parent_expansion_batched(fused)
        metrics.step4_ms = (time.time() - t_step) * 1000
        metrics.expanded_with_context = sum(1 for r in expanded if r.expanded_context)
        if self.verbose:
            ctx_count = sum(len(r.expanded_context) for r in expanded)
            self._print_step("4 — Parent Expansion",
                             f"{metrics.expanded_with_context}/{len(expanded)} chunks enrichis ({ctx_count} voisins)",
                             metrics.step4_ms/1000)

        # ── STEP 5 — Reranking ───────────────────────────────────────────
        t_step = time.time()
        if self.reranker:
            final = self._step5_rerank(query, expanded, top_k)
            metrics.step5_ms = (time.time() - t_step) * 1000
            if self.verbose:
                self._print_step("5 — Reranking",
                                 f"{len(expanded)} → {len(final)}",
                                 metrics.step5_ms/1000)
        else:
            final = expanded[:top_k]
            metrics.step5_ms = 0.0
            if self.verbose:
                self._print_step("5 — Reranking", "SKIP (désactivé)", 0)

        # Assigner les rangs finaux
        for i, r in enumerate(final, 1):
            r.rank = i

        metrics.final_count = len(final)
        metrics.total_ms = (time.time() - t0) * 1000
        if self.verbose:
            print(f"\n  ⏱️  Total: {metrics.total_ms/1000:.2f}s — {len(final)} résultats\n")

        return (final, metrics) if return_metrics else final

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 0 — CACHE NEO4J
    # ══════════════════════════════════════════════════════════════════════

    def graph_lookup(self, query: str, tx_type: str = None) -> list[RetrievalResult]:
        """API publique : règles du GRAPHE (GT + apprises) qui matchent la
        question — sans court-circuiter la recherche corpus. Permet à
        l'orchestrateur de COMBINER graphe + chunks (au lieu de l'un OU l'autre)."""
        return self._step0_cache_check(query, tx_type)

    def _step0_cache_check(self, query: str, tx_type: str = None) -> list[RetrievalResult]:
        """
        GRAPHE-D'ABORD : consulte les règles du graphe Neo4j (49 ground truth
        + règles APPRISES persistées par le Compliance Agent) AVANT toute
        recherche corpus. Si ≥3 règles matchent → cache hit, skip Weaviate.

        DURCISSEMENT (fix du « cache trop permissif ») : au lieu d'un OR sur
        les mots-clés (presque tout matchait), on COMPTE les mots-clés présents
        dans la règle et on exige ≥2 correspondances (≥1 si la requête est
        courte). Tri par nb de correspondances PUIS confiance.
        """
        keywords = self._extract_keywords(query)
        if not keywords:
            return []
        kws = [k.lower() for k in keywords[:8]]
        min_hits = 2 if len(kws) >= 3 else 1

        # Champs texte selon le schéma : règles GT (component_*/snippet) ET
        # règles apprises par le RAG (name/description, cf. _persist_validated).
        blob = ("toLower(coalesce(r.component_subject,'') + ' ' + "
                "coalesce(r.component_constraint,'') + ' ' + "
                "coalesce(r.source_text_snippet,'') + ' ' + "
                "coalesce(r.name,'') + ' ' + coalesce(r.description,''))")

        where_parts = [
            # GT + apprises : par origin OU par status (les 2 schémas coexistent)
            "(r.origin IN ['human_verified','agent_validated'] "
            " OR r.status IN ['GROUND_TRUTH','VALIDATED','LEARNED'])",
        ]
        params = {"kws": kws, "min_hits": min_hits}

        # FIX v2 : mapping par tx_type — tolérant aux règles apprises sans rôle
        if tx_type:
            where_parts.append("(r.chunk_role IN $roles OR r.chunk_role IS NULL)")
            params["roles"] = TX_TYPE_TO_ROLES.get(tx_type, ALL_ROLES)

        where_clause = " AND ".join(where_parts)

        try:
            with self.neo4j.session() as session:
                result = session.run(f"""
                    MATCH (r:Rule)
                    WHERE {where_clause}
                    WITH r, size([kw IN $kws WHERE {blob} CONTAINS kw]) AS hits
                    WHERE hits >= $min_hits
                    RETURN r.id                    AS id,
                           r.source_doc            AS source_doc,
                           r.source_article        AS article_ref,
                           r.jurisdiction          AS jurisdiction,
                           r.chunk_role            AS chunk_role,
                           r.component_subject     AS subject,
                           r.component_constraint  AS constraint,
                           coalesce(r.source_text_snippet, r.description) AS text,
                           r.threshold_value       AS threshold,
                           r.confidence            AS confidence,
                           hits
                    ORDER BY hits DESC, r.confidence DESC
                    LIMIT 10
                """, **params)

                cached = []
                for rec in result:
                    text = rec["text"] or rec["constraint"] or ""
                    cached.append(RetrievalResult(
                        text=text,
                        source_doc=rec["source_doc"] or "",
                        article_ref=rec["article_ref"] or "",
                        jurisdiction=rec["jurisdiction"] or "",
                        chunk_role=rec["chunk_role"] or "",
                        has_threshold=rec["threshold"] is not None,
                        score=rec["confidence"] or 1.0,
                        retrieval_path="cache",
                    ))
            return cached
        except Exception as e:
            logger.warning(f"Step 0 cache check failed: {e}")
            return []

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 1 — GRAPH PRE-FILTER
    # ══════════════════════════════════════════════════════════════════════

    def _step1_graph_prefilter(self, tx_type: str = None,
                                jurisdiction: str = None) -> dict:
        """
        Interroge Neo4j pour déterminer les documents pertinents.
        FIX v2: les source_docs renvoyés sont VRAIMENT utilisés comme filtre.
        """
        where_parts, params = [], {}

        if jurisdiction:
            where_parts.append("doc.jurisdiction = $jur")
            params["jur"] = jurisdiction

        where_clause = " AND ".join(where_parts) if where_parts else "1=1"

        try:
            with self.neo4j.session() as session:
                result = session.run(f"""
                    MATCH (doc:Document)
                    WHERE {where_clause}
                    RETURN doc.name         AS name,
                           doc.jurisdiction AS jurisdiction,
                           doc.priority     AS priority
                    ORDER BY doc.priority DESC
                """, **params)

                source_docs   = []
                jurisdictions = set()
                for rec in result:
                    source_docs.append(rec["name"])
                    jurisdictions.add(rec["jurisdiction"])
        except Exception as e:
            logger.warning(f"Step 1 prefilter failed: {e}")
            return {"source_docs": [], "jurisdictions": [], "tx_type": tx_type}

        return {
            "source_docs":   source_docs,
            "jurisdictions": list(jurisdictions),
            "tx_type":       tx_type,
        }

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 2 — HYBRID SEARCH (BM25 + VECTOR)
    # ══════════════════════════════════════════════════════════════════════

    def _build_weaviate_filters(self, prefilter: dict):
        """
        Construit les filtres Weaviate depuis le pre-filter Neo4j.
        FIX v2: ajoute le filtre source_doc (avant: jamais appliqué).
        """
        from weaviate.classes.query import Filter

        filter_parts = []

        # Filtre jurisdiction (si discriminant)
        jurs = prefilter.get("jurisdictions", [])
        if jurs and len(jurs) < 3:
            filter_parts.append(
                Filter.by_property("jurisdiction").contains_any(jurs)
            )

        # FIX v2: Filtre source_doc — réduit drastiquement l'espace de recherche
        # On ne l'applique que si la liste est un sous-ensemble utile
        # (sinon = tous les docs, filtre inutile et coûteux)
        source_docs = prefilter.get("source_docs", [])
        if source_docs and 0 < len(source_docs) <= SOURCE_DOC_FILTER_MAX:
            filter_parts.append(
                Filter.by_property("source_doc").contains_any(source_docs)
            )

        if len(filter_parts) == 0:
            return None
        if len(filter_parts) == 1:
            return filter_parts[0]
        return Filter.all_of(filter_parts)

    def _step2_bm25(self, query: str, filters, limit: int) -> list[dict]:
        """Recherche par mots-clés (BM25) dans Weaviate."""
        collection = self.weaviate.collections.get(COLLECTION_NAME)
        results = collection.query.bm25(
            query=query,
            limit=limit,
            filters=filters,
            query_properties=["text"],
            return_metadata=["score"],
        )
        return [self._weaviate_to_dict(obj, "bm25") for obj in results.objects]

    def _step2_vector(self, query_vector: list, filters, limit: int) -> list[dict]:
        """Recherche sémantique (vecteur) dans Weaviate."""
        collection = self.weaviate.collections.get(COLLECTION_NAME)
        results = collection.query.near_vector(
            near_vector=query_vector,
            limit=limit,
            filters=filters,
            return_metadata=["distance"],
        )
        return [self._weaviate_to_dict(obj, "vector") for obj in results.objects]

    def _weaviate_to_dict(self, obj, method: str) -> dict:
        """Convertit un objet Weaviate en dict standardisé."""
        p = obj.properties
        meta = obj.metadata

        # FIX v2: 'is not None' au lieu de truthy check (gère score=0.0 légitime)
        if method == "bm25":
            score = meta.score if meta.score is not None else 0.0
        else:
            dist = meta.distance if meta.distance is not None else 1.0
            score = max(0.0, 1.0 - dist)

        return {
            "text":          p.get("text", ""),
            "source_doc":    p.get("source_doc", ""),
            "article_ref":   p.get("article_ref", ""),
            "jurisdiction":  p.get("jurisdiction", ""),
            "chunk_role":    p.get("chunk_role", ""),
            "has_threshold": p.get("has_threshold", False),
            "score":         score,
            "method":        method,
        }

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 3 — RRF FUSION
    # ══════════════════════════════════════════════════════════════════════

    def _step3_rrf_fusion(self,
                          bm25_results: list[dict],
                          vector_results: list[dict],
                          top_n: int = FUSION_TOP_K) -> list[RetrievalResult]:
        """
        Reciprocal Rank Fusion (Cormack et al., 2009).

          score(d) = Σ 1/(k + rank_i(d))   pour chaque système i

        Avantages :
        - Pas de calibration de poids (vs moyenne pondérée)
        - Robuste aux échelles de scores hétérogènes (BM25 vs cosine)
        - k=60 = standard empirique
        """
        rrf_scores = defaultdict(float)
        chunk_data = {}

        # BM25
        for rank, item in enumerate(bm25_results, 1):
            key = self._chunk_key(item)
            rrf_scores[key] += 1.0 / (RRF_K + rank)
            if key not in chunk_data:
                chunk_data[key] = item
            else:
                chunk_data[key]["method"] = "rrf"

        # Vector
        for rank, item in enumerate(vector_results, 1):
            key = self._chunk_key(item)
            rrf_scores[key] += 1.0 / (RRF_K + rank)
            if key not in chunk_data:
                chunk_data[key] = item
            chunk_data[key]["method"] = "rrf"

        sorted_keys = sorted(rrf_scores, key=lambda k: -rrf_scores[k])

        results = []
        for key in sorted_keys[:top_n]:
            item = chunk_data[key]
            results.append(RetrievalResult(
                text=item["text"],
                source_doc=item["source_doc"],
                article_ref=item["article_ref"],
                jurisdiction=item["jurisdiction"],
                chunk_role=item["chunk_role"],
                has_threshold=item["has_threshold"],
                score=rrf_scores[key],
                retrieval_path=item["method"],
            ))

        return results

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 4 — PARENT EXPANSION (BATCHÉ)
    # ══════════════════════════════════════════════════════════════════════

    def _step4_parent_expansion_batched(self,
                                         results: list[RetrievalResult]
                                         ) -> list[RetrievalResult]:
        """
        FIX v2 : 1 seule requête Cypher (UNWIND) au lieu de N.
        + Les fetch Weaviate sortent de la session Neo4j.
        + Batched par source_doc avec contains_any.

        Avant v1 : 15 requêtes Neo4j + 60 requêtes Weaviate = 75 RTs
        Après v2 : 1  requête Neo4j  + ~K requêtes Weaviate (K = nb docs distincts ≤ 15)
        """
        if not results:
            return results

        # ÉTAPE A : batch Neo4j en 1 requête UNWIND
        articles = [
            {"src": r.source_doc, "ref": r.article_ref}
            for r in results if r.source_doc and r.article_ref
        ]
        if not articles:
            return results

        neighbors_map = {}  # (src, ref) → set of neighbor refs
        try:
            with self.neo4j.session() as session:
                cypher = f"""
                UNWIND $articles AS a
                MATCH (lu:LegalUnit {{source_doc: a.src, article_ref: a.ref}})
                OPTIONAL MATCH (prev:LegalUnit)-[:NEXT_IN_DOC*1..{EXPANSION_DEPTH}]->(lu)
                  WHERE prev.source_doc = a.src
                OPTIONAL MATCH (lu)-[:NEXT_IN_DOC*1..{EXPANSION_DEPTH}]->(nxt:LegalUnit)
                  WHERE nxt.source_doc = a.src
                RETURN a.src AS src, a.ref AS ref,
                       collect(DISTINCT prev.article_ref) AS prev_arts,
                       collect(DISTINCT nxt.article_ref)  AS next_arts
                """
                cypher_results = session.run(cypher, articles=articles)
                for rec in cypher_results:
                    refs = set()
                    for a in (rec["prev_arts"] or []):
                        if a: refs.add(a)
                    for a in (rec["next_arts"] or []):
                        if a: refs.add(a)
                    refs.discard(rec["ref"])
                    if refs:
                        neighbors_map[(rec["src"], rec["ref"])] = refs
        except Exception as e:
            logger.warning(f"Step 4 batch Cypher failed: {e}")
            return results

        # ÉTAPE B : batch Weaviate par source_doc (hors session Neo4j)
        # Regrouper tous les voisins par source_doc pour 1 query / doc
        refs_by_doc: dict[str, set] = defaultdict(set)
        for (src, _ref), refs in neighbors_map.items():
            refs_by_doc[src].update(refs)

        # Fetch tous les textes en 1 query par source_doc
        text_lookup: dict[tuple[str, str], str] = {}  # (src, ref) → text
        try:
            from weaviate.classes.query import Filter
            collection = self.weaviate.collections.get(COLLECTION_NAME)

            for src, refs in refs_by_doc.items():
                refs_list = list(refs)
                if not refs_list:
                    continue
                try:
                    w_results = collection.query.fetch_objects(
                        filters=Filter.all_of([
                            Filter.by_property("source_doc").equal(src),
                            Filter.by_property("article_ref").contains_any(refs_list),
                        ]),
                        limit=len(refs_list) * 3,  # chaque article peut avoir plusieurs chunks
                    )
                    for obj in w_results.objects:
                        p = obj.properties
                        ref = p.get("article_ref", "")
                        text = p.get("text", "")
                        if ref and text:
                            key = (src, ref)
                            # Garder le 1er texte (ou le plus long)
                            if key not in text_lookup or len(text) > len(text_lookup[key]):
                                text_lookup[key] = text
                except Exception as e:
                    logger.debug(f"Weaviate batch fetch failed for {src}: {e}")
        except Exception as e:
            logger.warning(f"Step 4 batch Weaviate failed: {e}")

        # ÉTAPE C : assembler les contextes étendus dans chaque résultat
        for result in results:
            key = (result.source_doc, result.article_ref)
            if key not in neighbors_map:
                continue
            refs = neighbors_map[key]
            ctxs = []
            for ref in list(refs)[:4]:  # Max 4 voisins
                txt = text_lookup.get((result.source_doc, ref))
                if txt:
                    ctxs.append(f"[{ref}] {txt[:NEIGHBOR_TEXT_MAX]}")
            if ctxs:
                result.expanded_context = ctxs

        return results

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 5 — CROSS-ENCODER RERANKING
    # ══════════════════════════════════════════════════════════════════════

    def _step5_rerank(self, query: str,
                      results: list[RetrievalResult],
                      top_k: int) -> list[RetrievalResult]:
        """BGE-reranker-base évalue chaque paire (query, chunk_text)."""
        if not results:
            return []

        pairs = []
        for r in results:
            doc_text = r.text
            if r.expanded_context:
                ctx = " ".join(r.expanded_context[:2])
                doc_text = f"{r.text}\n\nContext: {ctx}"
            pairs.append([query, doc_text[:RERANKER_PAIR_MAX]])

        try:
            scores = self.reranker.predict(pairs)
            for i, r in enumerate(results):
                r.score = float(scores[i])
                r.retrieval_path += "+reranked"
            results.sort(key=lambda x: -x.score)
            # Seuil de pertinence : on écarte les extraits trop faibles (bruit)
            # → améliore la Context Precision. On garde toujours au moins
            # RERANK_MIN_KEEP extraits pour ne jamais priver le pipeline.
            kept = [r for r in results if r.score >= RERANK_MIN_SCORE]
            if len(kept) < RERANK_MIN_KEEP:
                kept = results[:RERANK_MIN_KEEP]
            return kept[:top_k]
        except Exception as e:
            logger.warning(f"Reranking failed: {e}")
            return results[:top_k]

    # ══════════════════════════════════════════════════════════════════════
    #  UTILITAIRES
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _chunk_key(item: dict) -> str:
        """Clé unique pour identifier un chunk (dédoublonnage RRF)."""
        return f"{item['source_doc']}::{item['article_ref']}::{item['text'][:80]}"

    @staticmethod
    def _extract_keywords(query: str) -> list[str]:
        """
        Extrait les mots-clés significatifs d'une requête.
        FIX v2: préserve les acronymes métier (PEP, EU, EDD, KYC...).
        """
        import re
        words = re.findall(r"[a-zA-Z]+", query.lower())
        kept = []
        for w in words:
            if w in STOPWORDS:
                continue
            if w in DOMAIN_ACRONYMS or len(w) > 2:
                kept.append(w)
        return kept

    @staticmethod
    def _print_step(step: str, detail: str, elapsed: float):
        print(f"  [{step}] {detail} ({elapsed:.3f}s)")


# ═══════════════════════════════════════════════════════════════════════════
#  CLI — TEST INTERACTIF
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Retrieval Agent — Pipeline hybride AML/KYC (v2 optimisée)"
    )
    parser.add_argument("query", help="Question de recherche réglementaire")
    parser.add_argument("--tx-type", choices=["SWIFT", "VIREMENT_LOCAL", "MANDAT", "DEPOT_ESPECES"])
    parser.add_argument("--jurisdiction", choices=["Tunisia", "International", "European Union"])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--skip-cache", action="store_true")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--metrics-out", type=str,
                        help="Fichier JSON pour exporter les métriques (rapport PFE)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.no_rerank:
        global RERANKING_ENABLED
        RERANKING_ENABLED = False

    print(f"\n{'='*70}")
    print(f"  RETRIEVAL AGENT v2 — Pipeline hybride AML/KYC")
    print(f"{'='*70}")
    print(f"  Query       : {args.query}")
    print(f"  TX Type     : {args.tx_type or 'ALL'}")
    print(f"  Jurisdiction: {args.jurisdiction or 'ALL'}")
    print(f"  Reranking   : {'ON' if RERANKING_ENABLED else 'OFF'}")
    print(f"{'='*70}\n")

    pipeline = RetrievalPipeline(verbose=True)

    try:
        results, metrics = pipeline.retrieve(
            query=args.query,
            tx_type=args.tx_type,
            jurisdiction=args.jurisdiction,
            top_k=args.top_k,
            skip_cache=args.skip_cache,
            return_metrics=True,
        )

        print(f"\n{'='*70}")
        print(f"  RÉSULTATS — {len(results)} chunks")
        print(f"{'='*70}\n")

        for r in results:
            print(f"  #{r.rank}  [{r.chunk_role}]  {r.source_doc}")
            print(f"       {r.article_ref}  |  {r.jurisdiction}")
            print(f"       Score: {r.score:.4f}  |  Via: {r.retrieval_path}")
            if r.has_threshold:
                print(f"       📊 Contient un seuil financier")
            if r.expanded_context:
                print(f"       📎 +{len(r.expanded_context)} articles voisins")
            print()
            text = r.text[:300] + "..." if len(r.text) > 300 else r.text
            print(f"       {text}\n")

        # Export métriques si demandé
        if args.metrics_out:
            with open(args.metrics_out, "w", encoding="utf-8") as f:
                f.write(metrics.to_json())
            print(f"  ✅ Métriques exportées → {args.metrics_out}")

    except Exception as e:
        print(f"\n  ❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
    finally:
        pipeline.close()


if __name__ == "__main__":
    main()