"""
neo4j_graph/builder.py
======================
Construction de la couche structurelle du graphe Neo4j AML/KYC.

PORTÉE :
  Construit UNIQUEMENT la couche 1 (structurelle).
  La couche 2 (règles) est construite par l'agent Phase 6 via
  neo4j_graph/agent_interface.py.

NŒUDS CRÉÉS :
  • Document        — 26 documents PDF source
  • LegalUnit       — articles regroupés (~2 200)
  • Chunk           — 4 604 chunks (pont vers Weaviate via weaviate_ref)

RELATIONS CRÉÉES :
  • (Chunk)-[:REALIZES]→(LegalUnit)
  • (LegalUnit)-[:FROM_DOCUMENT]→(Document)
  • (LegalUnit)-[:HAS_ROLE]→(RoleCategory)
  • (LegalUnit)-[:APPLIES_TO]→(TransactionType)
  • (LegalUnit)-[:NEXT_IN_DOC]→(LegalUnit)
  • (Document)-[:UNDER_JURISDICTION]→(Jurisdiction)
"""

import logging
import re
import sys, os
from collections import defaultdict, Counter
from neo4j import Driver

from .loaders import chunk_meta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import TRANSACTION_TYPE_KEYWORDS

logger = logging.getLogger(__name__)

NODE_BATCH = 500
REL_BATCH  = 1_000


def detect_transaction_types(text: str) -> list[str]:
    """
    Détecte les types de transaction mentionnés dans un texte
    via les patterns de config/settings.py.

    Utilisé quand les chunks ne portent pas de champ transaction_types
    (format plat de la Phase 2, sans métadonnées imbriquées).
    """
    text_lower = text.lower()
    detected = []
    for tx_type, patterns in TRANSACTION_TYPE_KEYWORDS.items():
        if any(re.search(p, text_lower, re.IGNORECASE) for p in patterns):
            detected.append(tx_type)
    return detected


# ─────────────────────────────────────────────────────────────────────────────
#  Métadonnées statiques des 26 documents (priority, issuer, year, doc_type)
# ─────────────────────────────────────────────────────────────────────────────

DOC_METADATA = {
    # ── Tunisie ──────────────────────────────────────────────────────────
    "bct_circulaire_2017_08_aml.pdf":         {"doc_type": "Circular",   "priority": "HIGH",   "issuer": "BCT",        "year": 2017},
    "bct_circulaire_2018_09_aml_update.pdf":  {"doc_type": "Circular",   "priority": "HIGH",   "issuer": "BCT",        "year": 2018},
    "bct_circulaire_2024_02_aml.pdf":         {"doc_type": "Circular",   "priority": "HIGH",   "issuer": "BCT",        "year": 2024},
    "bct_circulaire_2025_17_aml_refonte.pdf": {"doc_type": "Circular",   "priority": "HIGH",   "issuer": "BCT",        "year": 2025},
    "bct_circulaire_2026_02_change.pdf":      {"doc_type": "Circular",   "priority": "HIGH",   "issuer": "BCT",        "year": 2026},
    "loi_2015_26_aml_cft.pdf":                {"doc_type": "Law",        "priority": "HIGH",   "issuer": "Parliament", "year": 2015},
    "ctaf_guide_signalement_dos.pdf":         {"doc_type": "Guide",      "priority": "HIGH",   "issuer": "CTAF",       "year": 2023},
    "ctaf_note_pays_haut_risque.pdf":         {"doc_type": "Note",       "priority": "HIGH",   "issuer": "CTAF",       "year": 2024},
    "ctaf_rapport_activite_2023.pdf":         {"doc_type": "Report",     "priority": "MEDIUM", "issuer": "CTAF",       "year": 2023},
    "ctaf_rapport_activite_2024.pdf":         {"doc_type": "Report",     "priority": "MEDIUM", "issuer": "CTAF",       "year": 2024},
    "2024_Reglementation_des_Changes.pdf":    {"doc_type": "Regulation", "priority": "LOW",    "issuer": "BCT",        "year": 2024},
    "2024_Reglementation_Bancaire.pdf":       {"doc_type": "Compilation","priority": "LOW",    "issuer": "BCT",        "year": 2024},
    # ── International ────────────────────────────────────────────────────
    "fatf_recommendations_2025.pdf":          {"doc_type": "Standards",  "priority": "HIGH",   "issuer": "FATF",       "year": 2025},
    "fatf_peps_guidance.pdf":                 {"doc_type": "Guidance",   "priority": "HIGH",   "issuer": "FATF",       "year": 2023},
    "fatf_correspondent_banking.pdf":         {"doc_type": "Guidance",   "priority": "HIGH",   "issuer": "FATF",       "year": 2022},
    "amld5_directive_2018_843.pdf":           {"doc_type": "Directive",  "priority": "HIGH",   "issuer": "EU",         "year": 2018},
    "amld6_directive_2018_1673.pdf":          {"doc_type": "Directive",  "priority": "HIGH",   "issuer": "EU",         "year": 2018},
    "eu_aml_regulation_2024.pdf":             {"doc_type": "Regulation", "priority": "HIGH",   "issuer": "EU",         "year": 2024},
    "eba_gl_2021_02_risk_factors.pdf":        {"doc_type": "Guidelines", "priority": "MEDIUM", "issuer": "EBA",        "year": 2021},
    "basel_aml_sound_management_d353.pdf":    {"doc_type": "Standards",  "priority": "MEDIUM", "issuer": "Basel",      "year": 2020},
    "basel_core_principles_d494.pdf":         {"doc_type": "Standards",  "priority": "MEDIUM", "issuer": "Basel",      "year": 2022},
    "wolfsberg_aml_principles_2019.pdf":      {"doc_type": "Principles", "priority": "MEDIUM", "issuer": "Wolfsberg",  "year": 2019},
    "wolfsberg_cdd_guidance_2019.pdf":        {"doc_type": "Guidance",   "priority": "MEDIUM", "issuer": "Wolfsberg",  "year": 2019},
    "fatf_virtual_assets_red_flags.pdf":      {"doc_type": "Guidance",   "priority": "MEDIUM", "issuer": "FATF",       "year": 2023},
    "fatf_cyber_enabled_fraud.pdf":           {"doc_type": "Guidance",   "priority": "MEDIUM", "issuer": "FATF",       "year": 2023},
}


# ═════════════════════════════════════════════════════════════════════════════
#  StructuralBuilder
# ═════════════════════════════════════════════════════════════════════════════

class StructuralBuilder:
    """Construit la couche 1 (structurelle) du graphe AML/KYC."""

    def __init__(self, driver: Driver):
        self.driver = driver

    # ──────────────────────────────────────────────────────────────────────
    #  Point d'entrée
    # ──────────────────────────────────────────────────────────────────────

    def build(self, chunks: list[dict]) -> dict:
        """Construit toute la couche 1. Retourne stats."""
        print("\n" + "─" * 60)
        print("  COUCHE 1 — STRUCTURELLE")
        print("─" * 60)

        # 1. Documents
        doc_map = self._extract_documents(chunks)
        self._create_documents(doc_map)

        # 2. LegalUnits
        legal_units = self._extract_legal_units(chunks)
        self._create_legal_units(legal_units)

        # 3. Chunks
        self._create_chunks(chunks)

        # 4. Relations
        self._link_documents_to_jurisdictions(doc_map)
        self._link_legalunits_to_documents(legal_units)
        self._link_legalunits_to_roles(legal_units)
        self._link_legalunits_to_txtypes(legal_units)
        self._link_chunks_to_legalunits(chunks)
        self._link_next_in_doc(legal_units)

        return {
            "documents":   len(doc_map),
            "legal_units": len(legal_units),
            "chunks":      len(chunks),
        }

    # ══════════════════════════════════════════════════════════════════════
    #  Extraction
    # ══════════════════════════════════════════════════════════════════════

    def _extract_documents(self, chunks: list[dict]) -> dict:
        """Déduplique et enrichit les docs source avec métadonnées statiques."""
        doc_map = {}
        for chunk in chunks:
            m = chunk_meta(chunk)
            name = m["source_doc"]
            if name not in doc_map:
                static = DOC_METADATA.get(name, {
                    "doc_type": "Unknown", "priority": "MEDIUM",
                    "issuer": "Unknown", "year": 0,
                })
                doc_map[name] = {
                    "name":         name,
                    "jurisdiction": m.get("jurisdiction", "Unknown"),
                    "bloc":         m.get("bloc", "unknown"),
                    **static,
                }
        return doc_map

    def _extract_legal_units(self, chunks: list[dict]) -> dict:
        """
        Regroupe les chunks par (source_doc, article_ref) en LegalUnits.

        Agrégations :
          - primary_role      : rôle majoritaire (Counter.most_common)
          - has_threshold     : OR logique sur les chunks
          - transaction_types : union
          - topics            : union
          - first_chunk_index : min (pour ordonner NEXT_IN_DOC)
        """
        groups = defaultdict(list)
        for chunk in chunks:
            m = chunk_meta(chunk)
            key = (m["source_doc"], m.get("article_ref", "unknown"))
            groups[key].append(chunk)

        legal_units = {}
        for (source_doc, article_ref), grp in groups.items():
            unit_id = f"{source_doc}::{article_ref}"

            roles  = [chunk_meta(c).get("chunk_role", "INFORMATION") for c in grp]
            tx_set, topics = set(), set()
            for c in grp:
                m = chunk_meta(c)
                tx_set.update(m.get("transaction_types", []))
                topics.update(m.get("topics", []))

            # Si aucun type de transaction dans les métadonnées (format plat Phase 2),
            # on détecte depuis le texte des chunks du groupe.
            if not tx_set:
                for c in grp:
                    text = c.get("text", chunk_meta(c).get("text", ""))
                    tx_set.update(detect_transaction_types(text))

            legal_units[unit_id] = {
                "unit_id":           unit_id,
                "article_ref":       article_ref,
                "source_doc":        source_doc,
                "jurisdiction":      chunk_meta(grp[0]).get("jurisdiction", "Unknown"),
                "primary_role":      Counter(roles).most_common(1)[0][0],
                "has_threshold":     any(chunk_meta(c).get("has_threshold", False) for c in grp),
                "chunk_count":       len(grp),
                "transaction_types": sorted(tx_set),
                "topics":            sorted(topics),
                "first_chunk_index": min(chunk_meta(c).get("chunk_index", 0) for c in grp),
            }
        return legal_units

    # ══════════════════════════════════════════════════════════════════════
    #  Création des nœuds
    # ══════════════════════════════════════════════════════════════════════

    def _create_documents(self, doc_map: dict):
        docs = list(doc_map.values())
        with self.driver.session() as session:
            session.run("""
                UNWIND $docs AS d
                MERGE (doc:Document {name: d.name})
                SET doc.jurisdiction = d.jurisdiction,
                    doc.bloc         = d.bloc,
                    doc.doc_type     = d.doc_type,
                    doc.priority     = d.priority,
                    doc.issuer       = d.issuer,
                    doc.year         = d.year
            """, docs=docs)
        print(f"  ✓ {len(docs)} Document")

    def _create_legal_units(self, legal_units: dict):
        items = list(legal_units.values())
        self._batch(items, NODE_BATCH, """
            UNWIND $batch AS u
            MERGE (lu:LegalUnit {unit_id: u.unit_id})
            SET lu.article_ref       = u.article_ref,
                lu.source_doc        = u.source_doc,
                lu.jurisdiction      = u.jurisdiction,
                lu.primary_role      = u.primary_role,
                lu.has_threshold     = u.has_threshold,
                lu.chunk_count       = u.chunk_count,
                lu.transaction_types = u.transaction_types,
                lu.topics            = u.topics,
                lu.first_chunk_index = u.first_chunk_index
        """)
        print(f"  ✓ {len(items):,} LegalUnit")

    def _create_chunks(self, chunks: list[dict]):
        items = []
        for chunk in chunks:
            m = chunk_meta(chunk)
            items.append({
                "chunk_id":      chunk["chunk_id"],
                "chunk_role":    m.get("chunk_role", "INFORMATION"),
                "article_ref":   m.get("article_ref", "unknown"),
                "source_doc":    m["source_doc"],
                "chunk_index":   m.get("chunk_index", 0),
                "char_count":    m.get("char_count", 0),
                "has_threshold": m.get("has_threshold", False),
                "is_sub_chunk":  m.get("is_sub_chunk", False),
                "weaviate_ref":  f"{m['source_doc']}::idx::{m.get('chunk_index', 0)}",
            })
        self._batch(items, NODE_BATCH, """
            UNWIND $batch AS c
            MERGE (ch:Chunk {chunk_id: c.chunk_id})
            SET ch.chunk_role    = c.chunk_role,
                ch.article_ref   = c.article_ref,
                ch.source_doc    = c.source_doc,
                ch.chunk_index   = c.chunk_index,
                ch.char_count    = c.char_count,
                ch.has_threshold = c.has_threshold,
                ch.is_sub_chunk  = c.is_sub_chunk,
                ch.weaviate_ref  = c.weaviate_ref
        """)
        print(f"  ✓ {len(items):,} Chunk")

    # ══════════════════════════════════════════════════════════════════════
    #  Création des relations
    # ══════════════════════════════════════════════════════════════════════

    def _link_documents_to_jurisdictions(self, doc_map: dict):
        pairs = [{"name": d["name"], "jur": d["jurisdiction"]}
                 for d in doc_map.values()]
        self._batch(pairs, REL_BATCH, """
            UNWIND $batch AS p
            MATCH (doc:Document   {name: p.name})
            MATCH (j:Jurisdiction {name: p.jur})
            MERGE (doc)-[:UNDER_JURISDICTION]->(j)
        """)
        print(f"  ✓ UNDER_JURISDICTION ({len(pairs)})")

    def _link_legalunits_to_documents(self, legal_units: dict):
        pairs = [{"unit_id": lu["unit_id"], "doc": lu["source_doc"]}
                 for lu in legal_units.values()]
        self._batch(pairs, REL_BATCH, """
            UNWIND $batch AS p
            MATCH (lu:LegalUnit {unit_id: p.unit_id})
            MATCH (doc:Document {name:    p.doc})
            MERGE (lu)-[:FROM_DOCUMENT]->(doc)
        """)
        print(f"  ✓ FROM_DOCUMENT ({len(pairs):,})")

    def _link_legalunits_to_roles(self, legal_units: dict):
        pairs = [{"unit_id": lu["unit_id"], "role": lu["primary_role"]}
                 for lu in legal_units.values()]
        self._batch(pairs, REL_BATCH, """
            UNWIND $batch AS p
            MATCH (lu:LegalUnit   {unit_id: p.unit_id})
            MATCH (r:RoleCategory {name:    p.role})
            MERGE (lu)-[:HAS_ROLE]->(r)
        """)
        print(f"  ✓ HAS_ROLE ({len(pairs):,})")

    def _link_legalunits_to_txtypes(self, legal_units: dict):
        pairs = []
        for lu in legal_units.values():
            for tx in lu["transaction_types"]:
                pairs.append({"unit_id": lu["unit_id"], "tx": tx})
        if not pairs:
            return
        self._batch(pairs, REL_BATCH, """
            UNWIND $batch AS p
            MATCH (lu:LegalUnit      {unit_id: p.unit_id})
            MATCH (t:TransactionType {name:    p.tx})
            MERGE (lu)-[:APPLIES_TO]->(t)
        """)
        print(f"  ✓ APPLIES_TO ({len(pairs):,})")

    def _link_chunks_to_legalunits(self, chunks: list[dict]):
        pairs = []
        for chunk in chunks:
            m = chunk_meta(chunk)
            unit_id = f"{m['source_doc']}::{m.get('article_ref', 'unknown')}"
            pairs.append({"chunk_id": chunk["chunk_id"], "unit_id": unit_id})
        self._batch(pairs, REL_BATCH, """
            UNWIND $batch AS p
            MATCH (ch:Chunk    {chunk_id: p.chunk_id})
            MATCH (lu:LegalUnit {unit_id: p.unit_id})
            MERGE (ch)-[:REALIZES]->(lu)
        """)
        print(f"  ✓ REALIZES ({len(pairs):,})")

    def _link_next_in_doc(self, legal_units: dict):
        """Crée la chaîne séquentielle des LegalUnits dans chaque document."""
        by_doc = defaultdict(list)
        for lu in legal_units.values():
            by_doc[lu["source_doc"]].append(lu)

        pairs = []
        for units in by_doc.values():
            sorted_units = sorted(units, key=lambda u: u["first_chunk_index"])
            for i in range(len(sorted_units) - 1):
                pairs.append({
                    "from_id": sorted_units[i]["unit_id"],
                    "to_id":   sorted_units[i + 1]["unit_id"],
                })
        if not pairs:
            return
        self._batch(pairs, REL_BATCH, """
            UNWIND $batch AS p
            MATCH (a:LegalUnit {unit_id: p.from_id})
            MATCH (b:LegalUnit {unit_id: p.to_id})
            MERGE (a)-[:NEXT_IN_DOC]->(b)
        """)
        print(f"  ✓ NEXT_IN_DOC ({len(pairs):,})")

    # ══════════════════════════════════════════════════════════════════════
    #  Helper
    # ══════════════════════════════════════════════════════════════════════

    def _batch(self, data: list, size: int, cypher: str):
        """Exécute un UNWIND par batches."""
        for i in range(0, len(data), size):
            sub = data[i:i + size]
            with self.driver.session() as session:
                session.run(cypher, batch=sub)
