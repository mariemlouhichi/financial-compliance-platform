"""
load_ground_truth.py
====================
Charge les règles ground truth validées dans Neo4j.

USAGE :
  python load_ground_truth.py
  python load_ground_truth.py --input output/aml_rules_complete_validated.json
  python load_ground_truth.py --dry-run     <- simule sans écrire
  python load_ground_truth.py --clear       <- supprime les règles existantes d'abord

PRÉREQUIS :
  - Neo4j lancé : docker compose up -d neo4j
  - Graphe structurel construit : python build_neo4j_graph.py
"""

import argparse
import json
import os
import sys
from pathlib import Path

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "amlkyc2025")


def load_rule(session, rule):
    """Charge une règle ground truth dans Neo4j (nouveau schéma simplifié)."""
    rule_id   = rule["id"]
    comp      = rule.get("components", {})
    
    # Extraction threshold
    threshold = rule.get("threshold_value")
    if threshold is not None:
        try: 
            threshold = float(threshold)
        except (ValueError, TypeError): 
            threshold = None

    # Noeud Rule
    session.run("""
        MERGE (r:Rule {id: $id})
        SET
          r.chunk_role               = $chunk_role,
          r.source_article           = $source_article,
          r.source_doc               = $source_doc,
          r.source_chunk_id          = $source_chunk_id,
          r.source_text_snippet      = $snippet,
          r.jurisdiction             = $jurisdiction,
          r.threshold_value          = $threshold_value,
          r.threshold_currency       = $threshold_currency,
          r.component_subject        = $subject,
          r.component_condition      = $condition,
          r.component_constraint     = $constraint,
          r.component_context        = $context,
          r.extraction_method        = $extraction_method,
          r.extraction_score         = $extraction_score,
          r.origin                   = $origin,
          r.validation_status        = $validation_status,
          r.validated_at             = $validated_at,
          r.confidence               = 1.0,
          r.validated_against_source = true
    """,
        id=rule_id,
        chunk_role=rule.get("chunk_role", ""),
        source_article=rule.get("source_article", ""),
        source_doc=rule.get("source_doc", ""),
        source_chunk_id=rule.get("source_chunk_id", ""),
        snippet=str(rule.get("source_text", ""))[:600],
        jurisdiction=rule.get("jurisdiction", "International"),
        threshold_value=threshold,
        threshold_currency=rule.get("threshold_currency", ""),
        subject=comp.get("subject", ""),
        condition=comp.get("condition", ""),
        constraint=comp.get("constraint", ""),
        context=comp.get("context", ""),
        extraction_method=rule.get("extraction_method", "unknown"),
        extraction_score=rule.get("extraction_score", 0),
        origin=rule.get("origin", "human_verified"),
        validation_status=rule.get("validation_status", "VALIDATED"),
        validated_at=rule.get("validated_at", ""),
    )

    # Relation -> Jurisdiction
    jur = rule.get("jurisdiction", "International")
    if jur:
        session.run("""
            MATCH (r:Rule {id: $id})
            MERGE (j:Jurisdiction {name: $jur})
            ON CREATE SET j.created_at = datetime()
            MERGE (r)-[:UNDER_JURISDICTION]->(j)
        """, id=rule_id, jur=jur)

    # Relation -> Chunk source (pont vers Weaviate)
    chunk_id = rule.get("source_chunk_id", "")
    if chunk_id:
        result = session.run("""
            MATCH (r:Rule {id: $id})
            OPTIONAL MATCH (ch:Chunk {chunk_id: $cid})
            WITH r, ch
            WHERE ch IS NOT NULL
            MERGE (r)-[:SUPPORTED_BY]->(ch)
            RETURN count(ch) AS linked
        """, id=rule_id, cid=chunk_id)
        rec = result.single()
        if rec and rec["linked"] > 0:
            # Lien indirect : Rule -> Chunk -> LegalUnit
            session.run("""
                MATCH (r:Rule {id: $id})-[:SUPPORTED_BY]->(ch:Chunk)-[:REALIZES]->(lu:LegalUnit)
                MERGE (r)-[:DERIVED_FROM]->(lu)
            """, id=rule_id)
    else:
        # Fallback : lier via source_doc + article_ref
        src_doc = rule.get("source_doc", "")
        src_art = rule.get("source_article", "")
        if src_doc and src_art:
            session.run("""
                MATCH (r:Rule {id: $id})
                MATCH (lu:LegalUnit)
                WHERE lu.source_doc = $src_doc 
                  AND (lu.unit_id CONTAINS $src_art OR lu.article_ref = $src_art)
                WITH r, lu LIMIT 1
                MERGE (r)-[:DERIVED_FROM]->(lu)
            """, id=rule_id, src_doc=src_doc, src_art=src_art)


def clear_existing_rules(session):
    """Supprime toutes les règles existantes."""
    result = session.run("""
        MATCH (r:Rule)
        OPTIONAL MATCH (r)-[rel]-()
        DELETE rel, r
        RETURN count(r) AS deleted
    """)
    rec = result.single()
    return rec["deleted"] if rec else 0


def main():
    parser = argparse.ArgumentParser(description="Charge le ground truth dans Neo4j")
    parser.add_argument("--input",   default="output/aml_rules_complete_validated.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clear",   action="store_true",
                        help="Supprime les règles existantes avant chargement")
    args = parser.parse_args()

    if not Path(args.input).exists():
        sys.exit(f"❌ Fichier introuvable : {args.input}")

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    # Support ancien et nouveau format
    all_rules = data.get("rules", data.get("validated", []))
    
    if not all_rules and isinstance(data, list):
        all_rules = data

    # Filtrer uniquement les règles validées
    to_load = [r for r in all_rules if r.get("validation_status") == "VALIDATED"]
    rejected = data.get("rejected", [])

    print(f"\n{'='*70}")
    print(f"  CHARGEMENT GROUND TRUTH → NEO4J")
    print(f"{'='*70}")
    print(f"  Fichier      : {args.input}")
    print(f"  Total règles : {len(all_rules)}")
    print(f"  À charger    : {len(to_load)} (VALIDATED)")
    if rejected:
        print(f"  Rejetées     : {len(rejected)}")
    print(f"  Mode         : {'DRY-RUN' if args.dry_run else 'RÉEL'}")
    if args.clear:
        print(f"  ⚠️  CLEAR     : Les règles existantes seront SUPPRIMÉES")
    print()

    if not to_load:
        print("  ❌ Aucune règle VALIDATED trouvée.")
        sys.exit(0)

    if args.dry_run:
        print("  [DRY-RUN] Règles qui seraient chargées :")
        for r in to_load[:10]:
            comp = r.get("components", {})
            print(f"    {r['id']:20} {r.get('chunk_role','?'):15} "
                  f"{comp.get('subject','?')[:40]}")
        if len(to_load) > 10:
            print(f"    ... et {len(to_load)-10} autres")
        print("\n  Relance sans --dry-run pour charger.")
        return

    # Connexion Neo4j
    try:
        from neo4j import GraphDatabase
    except ImportError:
        sys.exit("❌ pip install neo4j")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
        print(f"  ✅ Connecté à Neo4j : {NEO4J_URI}\n")
    except Exception as e:
        sys.exit(f"  ❌ Connexion Neo4j échouée : {e}")

    # Clear si demandé
    if args.clear:
        with driver.session() as session:
            deleted = clear_existing_rules(session)
            print(f"  🗑️  {deleted} règles existantes supprimées\n")

    # Chargement
    loaded, errors = 0, []
    with driver.session() as session:
        for i, rule in enumerate(to_load, 1):
            try:
                load_rule(session, rule)
                loaded += 1
                if loaded % 10 == 0 or loaded == len(to_load):
                    print(f"  ... {loaded}/{len(to_load)} règles chargées")
            except Exception as e:
                errors.append((rule.get("id", "?"), str(e)))
                print(f"  ❌ Erreur règle {rule.get('id','?')}: {e}")

    driver.close()

    # Résumé
    print(f"\n{'='*70}")
    print(f"  RÉSULTAT")
    print(f"{'='*70}")
    print(f"  ✅ Chargées  : {loaded}/{len(to_load)}")
    if errors:
        print(f"  ❌ Erreurs   : {len(errors)}")
        for rid, err in errors[:3]:
            print(f"      {rid}: {err}")

    # Stats par rôle
    from collections import Counter
    by_role = Counter(r.get("chunk_role", "?") for r in to_load)
    print(f"\n  PAR RÔLE :")
    for role, cnt in by_role.most_common():
        print(f"    {role:<18} {cnt:2d}")

    print(f"\n{'='*70}")
    print(f"  VÉRIFICATION :")
    print(f"{'='*70}")
    print(f"  python build_neo4j_graph.py --stats-only")
    print(f"\n  NEO4J BROWSER (http://localhost:7474) :")
    print(f"    MATCH (r:Rule) RETURN count(r)")
    print(f"    MATCH (r:Rule {{origin: 'human_verified'}}) RETURN count(r)")
    print(f"    MATCH (r:Rule) RETURN r.chunk_role, count(r) ORDER BY count(r) DESC")
    print(f"    MATCH (r:Rule) RETURN r.id, r.component_subject, r.component_constraint LIMIT 5")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()