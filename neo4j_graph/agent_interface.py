"""
neo4j_graph/agent_interface.py
==============================
Interface utilisée par les agents LangGraph (Phase 6) pour interagir avec
le graphe Neo4j AML/KYC.

PRINCIPE D'UTILISATION
──────────────────────

Quand l'agent Phase 6 traite une transaction :

  1. Il décompose la transaction en N dimensions
  2. Pour chaque dimension, il fait une recherche Weaviate (chunks)
  3. Il extrait une règle candidate depuis les chunks (LLM)
  4. Il VALIDE la règle contre le texte source (anti-hallucination) :
       - Le seuil mentionné apparaît-il littéralement dans le chunk ?
       - L'article référencé existe-t-il dans le graphe ?
  5. Si validée, il appelle upsert_extracted_rule() pour persister

  → La prochaine fois qu'une transaction similaire arrive, l'agent peut
    aller chercher la règle DIRECTEMENT dans Neo4j (cache hit) au lieu
    de refaire toute la chaîne d'extraction.

CLASSES PRINCIPALES
───────────────────

  RuleCandidate    — règle extraite par l'agent, AVANT validation
  RuleValidator    — vérifie qu'une règle est bien ancrée dans le source
  AgentGraphAPI    — interface de haut niveau (upsert / query / stats)

USAGE TYPIQUE (Phase 6)
───────────────────────

  api = AgentGraphAPI(driver)

  # 1. Avant la recherche : checker le cache
  cached = api.find_rules_for_transaction(
      tx_type="SWIFT",
      keywords=["Iran", "PEP", "EDD"]
  )
  if cached and len(cached) > 3:
      return cached   # cache hit, skip Weaviate

  # 2. Si cache miss, l'agent fait sa recherche Weaviate + extraction LLM
  rule_candidate = RuleCandidate(
      id="AGENT_001",
      description="Vigilance renforcée pour PEP virements > 15000 TND",
      threshold=15000,
      threshold_currency="TND",
      severity="HAUTE",
      category="PEP",
      transaction_types=["SWIFT"],
      source_chunks=["CHK_bct_2017_08_..."],
      source_text_snippet="le seuil de 15 000 TND nécessite une vigilance accrue",
  )

  # 3. Valider contre le source
  validation = api.validate_rule(rule_candidate)
  if validation.is_valid:
      api.upsert_extracted_rule(rule_candidate, confidence=validation.confidence)
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from neo4j import Driver

from .schema import VALID_SEVERITIES, VALID_CATEGORIES, VALID_ORIGINS


# ═════════════════════════════════════════════════════════════════════════════
#  Dataclasses : règle candidate + résultat de validation
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class RuleCandidate:
    """Règle extraite par l'agent, AVANT validation et stockage."""
    id:                  str
    name:                str
    description:         str
    severity:            str                   # CRITIQUE / HAUTE / MOYENNE / BASSE / INFORMATIVE
    category:            str                   # KYC / PEP / STR / ...
    transaction_types:   list[str]             # ["SWIFT", "MANDAT"]
    source_chunks:       list[str]             # IDs des chunks Weaviate ayant servi
    source_text_snippet: str                   # extrait littéral du texte source

    # Optionnels
    threshold:           Optional[float] = None
    threshold_currency:  Optional[str]   = None
    str_required:        bool            = False
    report_required:     bool            = False
    action_description:  str             = ""  # texte informatif (pas une commande !)
    jurisdiction:        Optional[str]   = None
    related_articles:    list[str]       = field(default_factory=list)


@dataclass
class ValidationResult:
    """Résultat de la validation d'une règle candidate."""
    is_valid:        bool
    confidence:      float                     # 0.0 - 1.0
    threshold_found: bool                      # le seuil apparaît littéralement
    chunks_exist:    bool                      # les chunks référencés existent
    reasons:         list[str] = field(default_factory=list)


# ═════════════════════════════════════════════════════════════════════════════
#  RuleValidator — anti-hallucination
# ═════════════════════════════════════════════════════════════════════════════

class RuleValidator:
    """
    Vérifie qu'une règle candidate est BIEN ancrée dans le texte source.
    C'est le mécanisme anti-hallucination du système.

    Vérifications :
      1. Tous les chunks référencés existent dans le graphe
      2. Si la règle a un seuil, ce seuil apparaît littéralement dans
         le snippet de texte source
      3. La sévérité et la catégorie sont des valeurs valides
    """

    def __init__(self, driver: Driver):
        self.driver = driver

    def validate(self, candidate: RuleCandidate) -> ValidationResult:
        reasons = []
        confidence = 1.0

        # ── 1. Sévérité valide ─────────────────────────────────────────
        if candidate.severity not in VALID_SEVERITIES:
            reasons.append(f"Sévérité invalide : '{candidate.severity}'")
            return ValidationResult(False, 0.0, False, False, reasons)

        # ── 2. Catégorie valide (avertissement, pas blocant) ───────────
        if candidate.category not in VALID_CATEGORIES:
            reasons.append(f"Catégorie inhabituelle : '{candidate.category}'")
            confidence -= 0.1

        # ── 3. Chunks source existent ──────────────────────────────────
        chunks_ok = self._verify_chunks_exist(candidate.source_chunks)
        if not chunks_ok:
            reasons.append("Certains chunks source n'existent pas dans le graphe")
            return ValidationResult(False, 0.0, False, False, reasons)

        # ── 4. Anti-hallucination : seuil littéralement présent ────────
        threshold_ok = True
        if candidate.threshold is not None:
            threshold_ok = self._verify_threshold_in_text(
                candidate.threshold, candidate.source_text_snippet
            )
            if not threshold_ok:
                reasons.append(
                    f"Seuil {candidate.threshold} {candidate.threshold_currency} "
                    f"NON trouvé littéralement dans le texte source"
                )
                confidence -= 0.5

        # ── 5. Décision finale ──────────────────────────────────────────
        is_valid = chunks_ok and (threshold_ok or candidate.threshold is None)
        if is_valid and not reasons:
            reasons.append("Règle ancrée dans le texte source")

        return ValidationResult(
            is_valid=is_valid,
            confidence=max(0.0, confidence),
            threshold_found=threshold_ok,
            chunks_exist=chunks_ok,
            reasons=reasons,
        )

    def _verify_chunks_exist(self, chunk_ids: list[str]) -> bool:
        if not chunk_ids:
            return False
        with self.driver.session() as session:
            result = session.run(
                "MATCH (ch:Chunk) WHERE ch.chunk_id IN $ids RETURN count(ch) AS c",
                ids=chunk_ids,
            )
            count = result.single()["c"]
        return count == len(chunk_ids)

    @staticmethod
    def _verify_threshold_in_text(threshold: float, source_text: str) -> bool:
        """
        Vérifie que le seuil apparaît littéralement dans le texte source.
        Tolère les variations courantes : 15000 / 15 000 / 15.000 / 15,000.
        """
        if not source_text:
            return False

        # Génère les patterns possibles pour ce nombre
        as_int    = int(threshold) if threshold == int(threshold) else threshold
        candidates = [
            str(as_int),
            f"{as_int:,}".replace(",", " "),     # 15 000
            f"{as_int:,}".replace(",", "."),     # 15.000
            f"{as_int:,}",                        # 15,000
        ]
        candidates = list(set(candidates))

        text_lower = source_text.lower()
        return any(c.lower() in text_lower for c in candidates)


# ═════════════════════════════════════════════════════════════════════════════
#  AgentGraphAPI — interface de haut niveau pour Phase 6
# ═════════════════════════════════════════════════════════════════════════════

class AgentGraphAPI:
    """
    API de haut niveau pour les agents LangGraph (Phase 6).

    Cette classe expose les opérations dont les agents ont besoin :
      • find_rules_for_transaction()  — cache check avant Weaviate
      • upsert_extracted_rule()       — persister une règle validée
      • mark_rule_as_validated()      — promouvoir agent_extracted → agent_validated
      • get_rule_provenance()         — pour génération SAR (Phase 7)
      • get_extraction_stats()        — monitoring qualité
    """

    def __init__(self, driver: Driver):
        self.driver = driver
        self.validator = RuleValidator(driver)

    # ──────────────────────────────────────────────────────────────────────
    #  Recherche dans le cache (avant d'appeler Weaviate)
    # ──────────────────────────────────────────────────────────────────────

    def find_rules_for_transaction(
        self,
        tx_type: str,
        keywords: Optional[list[str]] = None,
        category: Optional[str] = None,
        min_confidence: float = 0.7,
    ) -> list[dict]:
        """
        Cherche des règles déjà extraites pour ce contexte de transaction.
        Retourne uniquement les règles validées (validated_against_source=true).

        Si retourne >= 3 règles : cache hit, l'agent peut éviter Weaviate.
        Si retourne < 3 règles  : cache miss, l'agent doit faire la recherche.
        """
        where_clauses = ["r.validated_against_source = true",
                         "r.confidence >= $min_conf"]
        params = {"tx_type": tx_type, "min_conf": min_confidence}

        if category:
            where_clauses.append("r.category = $category")
            params["category"] = category

        if keywords:
            # Match si au moins un keyword apparaît dans description ou name
            kw_lower = [k.lower() for k in keywords]
            where_clauses.append(
                "any(kw IN $keywords WHERE toLower(r.description) CONTAINS kw "
                "OR toLower(r.name) CONTAINS kw)"
            )
            params["keywords"] = kw_lower

        where = " AND ".join(where_clauses)

        with self.driver.session() as session:
            result = session.run(f"""
                MATCH (r:Rule)-[:APPLIES_TO]->(t:TransactionType {{name: $tx_type}})
                WHERE {where}
                RETURN r.id                  AS id,
                       r.name                AS name,
                       r.description         AS description,
                       r.severity            AS severity,
                       r.category            AS category,
                       r.threshold           AS threshold,
                       r.threshold_currency  AS threshold_currency,
                       r.confidence          AS confidence,
                       r.origin              AS origin,
                       r.action_description  AS action_description
                ORDER BY r.confidence DESC, r.severity
                LIMIT 20
            """, **params)
            return [dict(r) for r in result]

    # ──────────────────────────────────────────────────────────────────────
    #  Persistance d'une règle extraite
    # ──────────────────────────────────────────────────────────────────────

    def upsert_extracted_rule(
        self,
        candidate: RuleCandidate,
        agent_run_id: Optional[str] = None,
    ) -> dict:
        """
        Persiste une règle extraite par l'agent dans le graphe.

        Workflow :
          1. Valide la règle (anti-hallucination)
          2. Si valide : MERGE le nœud Rule + crée toutes les relations
          3. Si invalide : retourne le rapport de validation sans rien créer

        Retourne :
          {
            "stored":     bool,
            "rule_id":    str,
            "validation": ValidationResult,
          }
        """
        validation = self.validator.validate(candidate)

        if not validation.is_valid:
            return {
                "stored":     False,
                "rule_id":    candidate.id,
                "validation": validation,
            }

        # MERGE Rule + propriétés de provenance
        with self.driver.session() as session:
            session.run("""
                MERGE (r:Rule {id: $id})
                SET r.name                    = $name,
                    r.description             = $description,
                    r.severity                = $severity,
                    r.category                = $category,
                    r.threshold               = $threshold,
                    r.threshold_currency      = $threshold_currency,
                    r.transaction_types       = $transaction_types,
                    r.str_required            = $str_required,
                    r.report_required         = $report_required,
                    r.action_description      = $action_description,
                    r.source_text_snippet     = $source_text_snippet,
                    r.origin                  = 'agent_extracted',
                    r.confidence              = $confidence,
                    r.validated_against_source = $threshold_ok,
                    r.extraction_date         = datetime(),
                    r.agent_run_id            = $agent_run_id
            """,
                id=candidate.id,
                name=candidate.name,
                description=candidate.description,
                severity=candidate.severity,
                category=candidate.category,
                threshold=candidate.threshold,
                threshold_currency=candidate.threshold_currency or "",
                transaction_types=candidate.transaction_types,
                str_required=candidate.str_required,
                report_required=candidate.report_required,
                action_description=candidate.action_description,
                source_text_snippet=candidate.source_text_snippet,
                confidence=validation.confidence,
                threshold_ok=validation.threshold_found,
                agent_run_id=agent_run_id or "manual",
            )

            # Relations vers TransactionTypes
            session.run("""
                UNWIND $tx_types AS tx
                MATCH (r:Rule {id: $id})
                MATCH (t:TransactionType {name: tx})
                MERGE (r)-[:APPLIES_TO]->(t)
            """, id=candidate.id, tx_types=candidate.transaction_types)

            # Relations vers Chunks (PONT VERS WEAVIATE)
            session.run("""
                UNWIND $chunk_ids AS cid
                MATCH (r:Rule {id: $id})
                MATCH (ch:Chunk {chunk_id: cid})
                MERGE (r)-[:SUPPORTED_BY]->(ch)
            """, id=candidate.id, chunk_ids=candidate.source_chunks)

            # Relations vers LegalUnits (DERIVED_FROM via les chunks)
            session.run("""
                MATCH (r:Rule {id: $id})-[:SUPPORTED_BY]->(ch:Chunk)-[:REALIZES]->(lu:LegalUnit)
                MERGE (r)-[:DERIVED_FROM]->(lu)
            """, id=candidate.id)

            # Relation vers Jurisdiction (déduite des chunks si non fournie)
            if candidate.jurisdiction:
                session.run("""
                    MATCH (r:Rule {id: $id})
                    MATCH (j:Jurisdiction {name: $jur})
                    MERGE (r)-[:UNDER_JURISDICTION]->(j)
                """, id=candidate.id, jur=candidate.jurisdiction)
            else:
                session.run("""
                    MATCH (r:Rule {id: $id})-[:DERIVED_FROM]->(lu:LegalUnit)
                    MATCH (j:Jurisdiction {name: lu.jurisdiction})
                    MERGE (r)-[:UNDER_JURISDICTION]->(j)
                """, id=candidate.id)

        return {
            "stored":     True,
            "rule_id":    candidate.id,
            "validation": validation,
        }

    # ──────────────────────────────────────────────────────────────────────
    #  Promotion : agent_extracted → agent_validated
    # ──────────────────────────────────────────────────────────────────────

    def mark_rule_as_validated(self, rule_id: str, validator_note: str = ""):
        """
        Promeut une règle agent_extracted vers agent_validated.
        Appelé après revue manuelle ou après que l'agent l'a confirmée
        sur plusieurs transactions.
        """
        with self.driver.session() as session:
            session.run("""
                MATCH (r:Rule {id: $id})
                SET r.origin         = 'agent_validated',
                    r.validator_note = $note,
                    r.validated_date = datetime()
            """, id=rule_id, note=validator_note)

    def mark_rule_as_human_verified(self, rule_id: str, verifier: str):
        """Marque une règle comme vérifiée manuellement (gold standard Phase 7)."""
        with self.driver.session() as session:
            session.run("""
                MATCH (r:Rule {id: $id})
                SET r.origin   = 'human_verified',
                    r.verifier = $verifier,
                    r.verified_date = datetime()
            """, id=rule_id, verifier=verifier)

    # ──────────────────────────────────────────────────────────────────────
    #  Provenance (pour génération SAR Phase 7)
    # ──────────────────────────────────────────────────────────────────────

    def get_rule_provenance(self, rule_id: str) -> dict:
        """
        Retourne la traçabilité COMPLÈTE d'une règle :
        Rule → Chunks → LegalUnits → Documents.

        Utilisé pour la génération de SAR (Phase 7) où chaque assertion
        doit être traçable à sa source réglementaire.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (r:Rule {id: $id})
                OPTIONAL MATCH (r)-[:SUPPORTED_BY]->(ch:Chunk)
                OPTIONAL MATCH (r)-[:DERIVED_FROM]->(lu:LegalUnit)
                OPTIONAL MATCH (lu)-[:FROM_DOCUMENT]->(doc:Document)
                RETURN r,
                       collect(DISTINCT {
                           chunk_id: ch.chunk_id,
                           weaviate_ref: ch.weaviate_ref,
                           article_ref: ch.article_ref
                       }) AS chunks,
                       collect(DISTINCT lu.unit_id) AS legal_units,
                       collect(DISTINCT {
                           name: doc.name,
                           issuer: doc.issuer,
                           year: doc.year
                       }) AS documents
            """, id=rule_id)
            record = result.single()
            if not record:
                return {}
            return {
                "rule":        dict(record["r"]),
                "chunks":      [c for c in record["chunks"] if c["chunk_id"]],
                "legal_units": [u for u in record["legal_units"] if u],
                "documents":   [d for d in record["documents"] if d["name"]],
            }

    # ──────────────────────────────────────────────────────────────────────
    #  Statistiques pour monitoring
    # ──────────────────────────────────────────────────────────────────────

    def get_extraction_stats(self) -> dict:
        """Stats sur la qualité d'extraction des règles (monitoring agent)."""
        with self.driver.session() as session:
            stats = {}

            # Total règles + distribution par origin
            r = session.run("""
                MATCH (r:Rule)
                RETURN r.origin AS origin, count(r) AS cnt
                ORDER BY cnt DESC
            """)
            stats["by_origin"] = {row["origin"]: row["cnt"] for row in r}
            stats["total_rules"] = sum(stats["by_origin"].values())

            # Distribution par sévérité
            r = session.run("""
                MATCH (r:Rule)
                RETURN r.severity AS severity, count(r) AS cnt
                ORDER BY cnt DESC
            """)
            stats["by_severity"] = {row["severity"]: row["cnt"] for row in r}

            # Confiance moyenne
            r = session.run("""
                MATCH (r:Rule) WHERE r.confidence IS NOT NULL
                RETURN avg(r.confidence) AS avg_conf,
                       min(r.confidence) AS min_conf,
                       max(r.confidence) AS max_conf
            """)
            row = r.single()
            stats["confidence"] = {
                "avg": round(row["avg_conf"] or 0, 3),
                "min": round(row["min_conf"] or 0, 3),
                "max": round(row["max_conf"] or 0, 3),
            }

            # Taux de validation
            r = session.run("""
                MATCH (r:Rule)
                RETURN sum(CASE WHEN r.validated_against_source THEN 1 ELSE 0 END) AS validated,
                       count(r) AS total
            """)
            row = r.single()
            stats["validation_rate"] = (
                row["validated"] / row["total"] if row["total"] > 0 else 0
            )

        return stats
