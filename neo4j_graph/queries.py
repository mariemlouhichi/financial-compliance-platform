"""
neo4j_graph/queries.py
======================
Requêtes Cypher pour les Phases 5 (Hybrid Retrieval), 6 (Agents), 7 (Eval).

ORGANISATION
────────────

  COUCHE STRUCTURELLE — toujours disponibles :
    • get_chunks_for_legalunit       — chunks d'un article
    • get_legalunit_context          — article + voisins NEXT_IN_DOC
    • get_legalunits_by_filter       — filtrage agent (juridiction/role/tx)
    • get_articles_with_threshold    — articles avec seuils financiers
    • get_chunk_to_neighbor_chunks   — depuis un chunk, ses voisins

  COUCHE RÈGLES — disponibles si l'agent a peuplé la couche 2 :
    • get_rules_for_transaction      — règles applicables à un type
    • get_rules_by_severity          — règles d'un niveau
    • get_rules_by_category          — règles d'une catégorie
    • get_rule_with_provenance       — règle + traçabilité complète
    • get_supporting_chunks_for_rule — chunks supportant une règle

  ÉVALUATION (Phase 7) :
    • compare_extracted_vs_verified  — comparaison automatique
    • get_unvalidated_rules          — règles à risque (faible confiance)

  MONITORING :
    • get_graph_stats                — comptes nœuds + relations
"""

from neo4j import Driver


# ═════════════════════════════════════════════════════════════════════════════
#  COUCHE STRUCTURELLE
# ═════════════════════════════════════════════════════════════════════════════

def get_chunks_for_legalunit(driver: Driver, unit_id: str) -> list[dict]:
    """Retourne tous les chunks d'une LegalUnit (avec leur weaviate_ref)."""
    with driver.session() as session:
        result = session.run("""
            MATCH (lu:LegalUnit {unit_id: $unit_id})<-[:REALIZES]-(ch:Chunk)
            RETURN ch.chunk_id      AS chunk_id,
                   ch.weaviate_ref  AS weaviate_ref,
                   ch.chunk_role    AS role,
                   ch.char_count    AS char_count,
                   ch.has_threshold AS has_threshold
            ORDER BY ch.chunk_index
        """, unit_id=unit_id)
        return [dict(r) for r in result]


def get_legalunit_context(driver: Driver, unit_id: str,
                           depth: int = 1) -> dict:
    """LegalUnit + voisins NEXT_IN_DOC à profondeur donnée (contexte agent)."""
    with driver.session() as session:
        result = session.run(f"""
            MATCH (lu:LegalUnit {{unit_id: $unit_id}})
            OPTIONAL MATCH (prev:LegalUnit)-[:NEXT_IN_DOC*1..{depth}]->(lu)
            OPTIONAL MATCH (lu)-[:NEXT_IN_DOC*1..{depth}]->(nxt:LegalUnit)
            OPTIONAL MATCH (lu)-[:FROM_DOCUMENT]->(doc:Document)
            RETURN lu,
                   doc.name AS document, doc.issuer AS issuer,
                   collect(DISTINCT prev.article_ref) AS previous_articles,
                   collect(DISTINCT nxt.article_ref)  AS next_articles
        """, unit_id=unit_id)
        record = result.single()
        if not record:
            return {}
        return {
            "legal_unit":        dict(record["lu"]),
            "document":          record["document"],
            "issuer":            record["issuer"],
            "previous_articles": [a for a in record["previous_articles"] if a],
            "next_articles":     [a for a in record["next_articles"] if a],
        }


def get_legalunits_by_filter(driver: Driver,
                              jurisdiction: str | None = None,
                              role: str | None = None,
                              tx_type: str | None = None,
                              has_threshold: bool | None = None,
                              limit: int = 20) -> list[dict]:
    """
    Filtrage souple — appelé par l'agent AVANT Weaviate pour réduire
    l'espace de recherche.
    """
    where, params = [], {"limit": limit}
    if jurisdiction:
        where.append("lu.jurisdiction = $jurisdiction"); params["jurisdiction"] = jurisdiction
    if role:
        where.append("lu.primary_role = $role");         params["role"] = role
    if tx_type:
        where.append("$tx_type IN lu.transaction_types"); params["tx_type"] = tx_type
    if has_threshold is not None:
        where.append("lu.has_threshold = $has_threshold"); params["has_threshold"] = has_threshold

    where_clause = " AND ".join(where) if where else "1=1"

    with driver.session() as session:
        result = session.run(f"""
            MATCH (lu:LegalUnit)
            WHERE {where_clause}
            OPTIONAL MATCH (lu)-[:FROM_DOCUMENT]->(doc:Document)
            RETURN lu.unit_id      AS unit_id,
                   lu.article_ref  AS article_ref,
                   lu.primary_role AS role,
                   lu.has_threshold AS has_threshold,
                   doc.name        AS document,
                   doc.issuer      AS issuer
            LIMIT $limit
        """, **params)
        return [dict(r) for r in result]


def get_articles_with_threshold(driver: Driver,
                                 jurisdiction: str | None = None,
                                 limit: int = 50) -> list[dict]:
    """Articles contenant un seuil financier."""
    where = "AND lu.jurisdiction = $jurisdiction" if jurisdiction else ""
    with driver.session() as session:
        result = session.run(f"""
            MATCH (lu:LegalUnit {{has_threshold: true}})-[:FROM_DOCUMENT]->(doc:Document)
            WHERE 1=1 {where}
            RETURN lu.unit_id           AS unit_id,
                   lu.article_ref       AS article,
                   lu.primary_role      AS role,
                   lu.transaction_types AS tx_types,
                   doc.name             AS document,
                   doc.issuer           AS issuer
            ORDER BY doc.priority DESC
            LIMIT $limit
        """, jurisdiction=jurisdiction, limit=limit)
        return [dict(r) for r in result]


def get_chunk_to_neighbor_chunks(driver: Driver, chunk_id: str) -> list[dict]:
    """
    Depuis un chunk (résultat Weaviate), retourne les chunks voisins
    via la traversée Chunk → LegalUnit → NEXT_IN_DOC → LegalUnit ← Chunk.
    """
    with driver.session() as session:
        result = session.run("""
            MATCH (start:Chunk {chunk_id: $chunk_id})-[:REALIZES]->(lu:LegalUnit)
            OPTIONAL MATCH (lu)-[:NEXT_IN_DOC]-(neighbor_lu:LegalUnit)
            OPTIONAL MATCH (neighbor_ch:Chunk)-[:REALIZES]->(neighbor_lu)
            RETURN DISTINCT neighbor_ch.chunk_id     AS chunk_id,
                            neighbor_ch.weaviate_ref AS weaviate_ref,
                            neighbor_ch.article_ref  AS article_ref,
                            neighbor_ch.chunk_role   AS role
            LIMIT 10
        """, chunk_id=chunk_id)
        return [dict(r) for r in result if r["chunk_id"]]


# ═════════════════════════════════════════════════════════════════════════════
#  COUCHE RÈGLES (si l'agent a peuplé)
# ═════════════════════════════════════════════════════════════════════════════

def get_rules_for_transaction(driver: Driver,
                               tx_type: str,
                               severity_filter: list[str] | None = None,
                               min_confidence: float = 0.0,
                               only_validated: bool = True) -> list[dict]:
    """Règles applicables à un type de transaction."""
    where = ["r.confidence >= $min_conf"]
    params = {"tx_type": tx_type, "min_conf": min_confidence}

    if only_validated:
        where.append("r.validated_against_source = true")
    if severity_filter:
        where.append("r.severity IN $severities")
        params["severities"] = severity_filter

    where_clause = " AND ".join(where)

    with driver.session() as session:
        result = session.run(f"""
            MATCH (r:Rule)-[:APPLIES_TO]->(t:TransactionType {{name: $tx_type}})
            WHERE {where_clause}
            RETURN r.id                  AS id,
                   r.name                AS name,
                   r.description         AS description,
                   r.severity            AS severity,
                   r.category            AS category,
                   r.threshold           AS threshold,
                   r.threshold_currency  AS threshold_currency,
                   r.confidence          AS confidence,
                   r.origin              AS origin,
                   r.action_description  AS action_description,
                   r.str_required        AS str_required
            ORDER BY r.confidence DESC, r.severity
        """, **params)
        return [dict(r) for r in result]


def get_rules_by_category(driver: Driver, category: str,
                           tx_type: str | None = None) -> list[dict]:
    """Règles d'une catégorie (KYC, PEP, STR, ...)."""
    tx_filter = "AND $tx_type IN r.transaction_types" if tx_type else ""
    with driver.session() as session:
        result = session.run(f"""
            MATCH (r:Rule {{category: $category}})
            WHERE 1=1 {tx_filter}
            RETURN r.id          AS id,
                   r.name        AS name,
                   r.description AS description,
                   r.severity    AS severity,
                   r.threshold   AS threshold,
                   r.confidence  AS confidence,
                   r.origin      AS origin
            ORDER BY r.confidence DESC
        """, category=category, tx_type=tx_type)
        return [dict(r) for r in result]


def get_rule_with_provenance(driver: Driver, rule_id: str) -> dict | None:
    """
    Retourne une règle + sa traçabilité complète (chunks → LegalUnits → Documents).
    Utilisé pour la génération SAR (chaque assertion doit être tracée à la source).
    """
    with driver.session() as session:
        result = session.run("""
            MATCH (r:Rule {id: $rule_id})
            OPTIONAL MATCH (r)-[:SUPPORTED_BY]->(ch:Chunk)
            OPTIONAL MATCH (r)-[:DERIVED_FROM]->(lu:LegalUnit)
            OPTIONAL MATCH (lu)-[:FROM_DOCUMENT]->(doc:Document)
            OPTIONAL MATCH (r)-[:UNDER_JURISDICTION]->(j:Jurisdiction)
            RETURN r,
                   j.name AS jurisdiction,
                   collect(DISTINCT {
                       chunk_id: ch.chunk_id,
                       weaviate_ref: ch.weaviate_ref,
                       article_ref: ch.article_ref,
                       role: ch.chunk_role
                   }) AS chunks,
                   collect(DISTINCT lu.unit_id) AS legal_units,
                   collect(DISTINCT {
                       name: doc.name,
                       issuer: doc.issuer,
                       year: doc.year
                   }) AS documents
        """, rule_id=rule_id)
        record = result.single()
        if not record:
            return None
        return {
            "rule":         dict(record["r"]),
            "jurisdiction": record["jurisdiction"],
            "chunks":       [c for c in record["chunks"] if c["chunk_id"]],
            "legal_units":  [u for u in record["legal_units"] if u],
            "documents":    [d for d in record["documents"] if d["name"]],
        }


def get_supporting_chunks_for_rule(driver: Driver, rule_id: str) -> list[dict]:
    """Chunks supportant une règle (avec weaviate_ref pour aller chercher le texte)."""
    with driver.session() as session:
        result = session.run("""
            MATCH (r:Rule {id: $rule_id})-[:SUPPORTED_BY]->(ch:Chunk)
            RETURN ch.chunk_id      AS chunk_id,
                   ch.weaviate_ref  AS weaviate_ref,
                   ch.article_ref   AS article_ref,
                   ch.chunk_role    AS role,
                   ch.has_threshold AS has_threshold,
                   ch.source_doc    AS source_doc
            ORDER BY ch.has_threshold DESC, ch.chunk_index
        """, rule_id=rule_id)
        return [dict(r) for r in result]


# ═════════════════════════════════════════════════════════════════════════════
#  ÉVALUATION (Phase 7)
# ═════════════════════════════════════════════════════════════════════════════

def compare_extracted_vs_verified(driver: Driver,
                                    tx_type: str | None = None) -> dict:
    """
    Compare règles agent_extracted vs human_verified.
    Métriques : précision, rappel, couverture par catégorie.
    """
    tx_filter = "WHERE $tx_type IN r.transaction_types" if tx_type else ""
    with driver.session() as session:
        # Règles totales par origin
        result = session.run(f"""
            MATCH (r:Rule) {tx_filter}
            RETURN r.origin AS origin, count(r) AS cnt
        """, tx_type=tx_type)
        by_origin = {row["origin"]: row["cnt"] for row in result}

        # Distribution par sévérité × origin
        result = session.run(f"""
            MATCH (r:Rule) {tx_filter}
            RETURN r.severity AS sev, r.origin AS origin, count(r) AS cnt
        """, tx_type=tx_type)
        by_sev_origin = [dict(r) for r in result]

        return {
            "by_origin":    by_origin,
            "by_sev_origin": by_sev_origin,
            "tx_type":       tx_type or "ALL",
        }


def get_unvalidated_rules(driver: Driver,
                           confidence_threshold: float = 0.7) -> list[dict]:
    """Règles à risque : faible confiance ou non-validées contre source."""
    with driver.session() as session:
        result = session.run("""
            MATCH (r:Rule)
            WHERE r.validated_against_source = false
               OR r.confidence < $threshold
            RETURN r.id          AS id,
                   r.name        AS name,
                   r.confidence  AS confidence,
                   r.validated_against_source AS validated,
                   r.origin      AS origin,
                   r.severity    AS severity
            ORDER BY r.confidence ASC
        """, threshold=confidence_threshold)
        return [dict(r) for r in result]


# ═════════════════════════════════════════════════════════════════════════════
#  MONITORING
# ═════════════════════════════════════════════════════════════════════════════

def get_graph_stats(driver: Driver) -> dict:
    """Comptes nœuds + relations pour monitoring."""
    nodes, rels = {}, {}
    with driver.session() as session:
        for label in ["Document", "LegalUnit", "Chunk", "Rule",
                      "Jurisdiction", "RoleCategory", "TransactionType"]:
            r = session.run(f"MATCH (n:{label}) RETURN count(n) AS c")
            nodes[label] = r.single()["c"]

        for rel in ["UNDER_JURISDICTION", "FROM_DOCUMENT", "HAS_ROLE",
                    "APPLIES_TO", "REALIZES", "NEXT_IN_DOC",
                    "DERIVED_FROM", "SUPPORTED_BY", "RELATED_TO"]:
            r = session.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c")
            rels[rel] = r.single()["c"]

    return {"nodes": nodes, "relationships": rels}
