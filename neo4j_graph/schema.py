"""
neo4j_graph/schema.py
=====================
Schéma Neo4j AML/KYC — Architecture finale à 2 couches.

DESIGN DECISIONS (justifiables devant le jury) :

  1. La sévérité, la catégorie et l'action ne sont PAS des nœuds.
     Elles sont stockées comme propriétés du nœud Rule, car :
     - Elles n'ont pas d'identité propre (pas de relations sortantes)
     - Stocker chaque sévérité comme nœud créerait des jointures inutiles
     - Le filtrage par propriété est aussi rapide via index B-Tree

  2. La relation [:TRIGGERS]→(:Action) a été supprimée.
     Elle suggérait une décision automatique alors que l'action dépend
     du contexte (montant, profil client, historique). L'action est un
     texte informatif que l'agent INTERPRÈTE, pas une commande exécutée.

  3. Les règles ont des propriétés de PROVENANCE (origin, confidence,
     validated_against_source, extraction_date) — essentielles pour
     l'audit, l'évaluation Phase 7, et la confiance dans les décisions.
"""

from neo4j import Driver

# ─────────────────────────────────────────────────────────────────────────────
#  CONTRAINTES D'UNICITÉ
# ─────────────────────────────────────────────────────────────────────────────

CONSTRAINTS = [
    # ── Couche 1 : structurelle ──────────────────────────────────────────
    "CREATE CONSTRAINT doc_name        IF NOT EXISTS FOR (d:Document)        REQUIRE d.name     IS UNIQUE",
    "CREATE CONSTRAINT legalunit_id    IF NOT EXISTS FOR (l:LegalUnit)       REQUIRE l.unit_id  IS UNIQUE",
    "CREATE CONSTRAINT chunk_id        IF NOT EXISTS FOR (c:Chunk)           REQUIRE c.chunk_id IS UNIQUE",

    # ── Couche 2 : règles ────────────────────────────────────────────────
    "CREATE CONSTRAINT rule_id         IF NOT EXISTS FOR (r:Rule)            REQUIRE r.id       IS UNIQUE",

    # ── Référentiels partagés ────────────────────────────────────────────
    "CREATE CONSTRAINT jurisdiction    IF NOT EXISTS FOR (j:Jurisdiction)    REQUIRE j.name     IS UNIQUE",
    "CREATE CONSTRAINT role_category   IF NOT EXISTS FOR (r:RoleCategory)    REQUIRE r.name     IS UNIQUE",
    "CREATE CONSTRAINT tx_type         IF NOT EXISTS FOR (t:TransactionType) REQUIRE t.name     IS UNIQUE",
]

# ─────────────────────────────────────────────────────────────────────────────
#  INDEX DE PERFORMANCE
# ─────────────────────────────────────────────────────────────────────────────

INDEXES = [
    # ── LegalUnit ────────────────────────────────────────────────────────
    "CREATE INDEX lu_source        IF NOT EXISTS FOR (l:LegalUnit) ON (l.source_doc)",
    "CREATE INDEX lu_role          IF NOT EXISTS FOR (l:LegalUnit) ON (l.primary_role)",
    "CREATE INDEX lu_jurisdiction  IF NOT EXISTS FOR (l:LegalUnit) ON (l.jurisdiction)",
    "CREATE INDEX lu_threshold     IF NOT EXISTS FOR (l:LegalUnit) ON (l.has_threshold)",

    # ── Chunk (bridge vers Weaviate) ─────────────────────────────────────
    "CREATE INDEX chunk_role       IF NOT EXISTS FOR (c:Chunk) ON (c.chunk_role)",
    "CREATE INDEX chunk_source     IF NOT EXISTS FOR (c:Chunk) ON (c.source_doc)",
    "CREATE INDEX chunk_threshold  IF NOT EXISTS FOR (c:Chunk) ON (c.has_threshold)",
    "CREATE INDEX chunk_wref       IF NOT EXISTS FOR (c:Chunk) ON (c.weaviate_ref)",
    "CREATE INDEX chunk_article    IF NOT EXISTS FOR (c:Chunk) ON (c.article_ref)",

    # ── Rule (filtrages agents Phase 6) ──────────────────────────────────
    "CREATE INDEX rule_category    IF NOT EXISTS FOR (r:Rule) ON (r.category)",
    "CREATE INDEX rule_severity    IF NOT EXISTS FOR (r:Rule) ON (r.severity)",
    "CREATE INDEX rule_origin      IF NOT EXISTS FOR (r:Rule) ON (r.origin)",
    "CREATE INDEX rule_validated   IF NOT EXISTS FOR (r:Rule) ON (r.validated_against_source)",
    "CREATE INDEX rule_str         IF NOT EXISTS FOR (r:Rule) ON (r.str_required)",

    # ── Document ─────────────────────────────────────────────────────────
    "CREATE INDEX doc_priority     IF NOT EXISTS FOR (d:Document) ON (d.priority)",
    "CREATE INDEX doc_jurisdiction IF NOT EXISTS FOR (d:Document) ON (d.jurisdiction)",
]


# ─────────────────────────────────────────────────────────────────────────────
#  Valeurs de référence (créées comme nœuds)
# ─────────────────────────────────────────────────────────────────────────────

ROLE_CATEGORIES = [
    "OBLIGATION", "PROCEDURE", "THRESHOLD", "DEFINITION", "SANCTION",
    "RED_FLAG", "REPORTING", "EXEMPTION", "INFORMATION",
]

TRANSACTION_TYPES = [
    "VIREMENT_LOCAL", "SWIFT", "MANDAT", "DEPOT_ESPECES",
]

JURISDICTIONS = [
    "Tunisia", "International", "European Union",
]


# ─────────────────────────────────────────────────────────────────────────────
#  Référentiel des valeurs ENUM (pour validation, pas comme nœuds)
# ─────────────────────────────────────────────────────────────────────────────

VALID_SEVERITIES = ["CRITIQUE", "HAUTE", "MOYENNE", "BASSE", "INFORMATIVE"]

VALID_CATEGORIES = [
    "KYC", "PEP", "STR", "EDD", "SDD", "SANCTIONS",
    "THRESHOLD", "MONITORING", "REPORTING", "TRAVEL_RULE",
    "BENEFICIAL_OWNER", "CORRESPONDENT_BANKING", "VIRTUAL_ASSETS",
    "RECORD_KEEPING", "RISK_ASSESSMENT", "GENERAL",
]

VALID_ORIGINS = [
    "agent_extracted",   # règle extraite automatiquement par l'agent Phase 6
    "human_verified",    # règle validée manuellement (pour évaluation Phase 7)
    "agent_validated",   # règle agent_extracted, vérifiée a posteriori
]


# ─────────────────────────────────────────────────────────────────────────────
#  Application
# ─────────────────────────────────────────────────────────────────────────────

def apply_schema(driver: Driver) -> None:
    """Applique contraintes + index. Idempotent (safe si relancé)."""
    with driver.session() as session:
        for stmt in CONSTRAINTS:
            session.run(stmt)
        for stmt in INDEXES:
            session.run(stmt)
    print(f"  Schema : {len(CONSTRAINTS)} contraintes + {len(INDEXES)} index appliques")


def create_reference_nodes(driver: Driver) -> None:
    """Crée les nœuds de référence (Jurisdiction, RoleCategory, TransactionType)."""
    with driver.session() as session:
        session.run(
            "UNWIND $items AS name MERGE (:Jurisdiction {name: name})",
            items=JURISDICTIONS,
        )
        session.run(
            "UNWIND $items AS name MERGE (:RoleCategory {name: name})",
            items=ROLE_CATEGORIES,
        )
        session.run(
            "UNWIND $items AS name MERGE (:TransactionType {name: name})",
            items=TRANSACTION_TYPES,
        )
    print(f"  Referentiels : {len(JURISDICTIONS)} Jurisdictions, "
          f"{len(ROLE_CATEGORIES)} RoleCategories, "
          f"{len(TRANSACTION_TYPES)} TransactionTypes")
