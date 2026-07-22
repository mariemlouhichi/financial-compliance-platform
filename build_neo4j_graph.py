"""
build_neo4j_graph.py
====================
Phase 4 — Construction du graphe Neo4j AML/KYC.

CE SCRIPT CONSTRUIT UNIQUEMENT LA COUCHE STRUCTURELLE.
La couche 2 (règles) est peuplée par l'agent Phase 6 via agent_interface.py.

USAGE :
  python build_neo4j_graph.py                    ← construit la couche structurelle
  python build_neo4j_graph.py --clear            ← reset complet du graphe
  python build_neo4j_graph.py --stats-only       ← stats sans construction

PRÉREQUIS :
  docker compose up -d neo4j
  pip install neo4j
"""

import argparse
import os
import sys
import time
import logging
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "amlkyc2025")

_BASE = Path(__file__).parent
DEFAULT_CHUNKS = _BASE / "output" / "chunks_aml_kyc.json"
# ─────────────────────────────────────────────────────────────────────────────

Path("output").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("output/neo4j_build.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
#  Connexion + utilitaires
# ═════════════════════════════════════════════════════════════════════════════

def connect_with_retry(uri: str, user: str, password: str, max_attempts: int = 3):
    from neo4j import GraphDatabase
    for attempt in range(1, max_attempts + 1):
        try:
            driver = GraphDatabase.driver(uri, auth=(user, password))
            driver.verify_connectivity()
            logger.info(f"  ✓ Connecté à Neo4j : {uri}")
            return driver
        except Exception as e:
            if attempt == max_attempts:
                raise ConnectionError(
                    f"Impossible de se connecter à Neo4j ({uri})\n"
                    "  → Démarrer Neo4j : docker compose up -d neo4j\n"
                    "  → Attendre ~30s et réessayer"
                ) from e
            logger.warning(f"  Tentative {attempt}/{max_attempts} échouée, retry 5s...")
            time.sleep(5)


def clear_graph(driver):
    logger.info("  ⚠ RESET du graphe...")
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    logger.info("  ✓ Graphe vidé")


# ═════════════════════════════════════════════════════════════════════════════
#  Affichage des statistiques
# ═════════════════════════════════════════════════════════════════════════════

def print_stats(driver, elapsed: float = 0.0):
    from neo4j_graph.queries import get_graph_stats
    SEP = "=" * 64

    print(f"\n{SEP}")
    print("  STATISTIQUES DU GRAPHE NEO4J")
    print(SEP)

    stats = get_graph_stats(driver)

    print("\n  NOEUDS")
    total_nodes = 0
    for label, cnt in stats["nodes"].items():
        if cnt > 0:
            total_nodes += cnt
            print(f"    {label:<22} {cnt:>7,}")
    print(f"    {'-' * 30}")
    print(f"    {'TOTAL':<22} {total_nodes:>7,}")

    print("\n  RELATIONS")
    total_rels = 0
    for rel, cnt in stats["relationships"].items():
        if cnt > 0:
            total_rels += cnt
            print(f"    {rel:<26} {cnt:>7,}")
    print(f"    {'-' * 34}")
    print(f"    {'TOTAL':<26} {total_rels:>7,}")

    rule_count = stats["nodes"].get("Rule", 0)
    if rule_count > 0:
        print(f"\n  COUCHE REGLES (peuplee par l'agent)")
        from neo4j_graph.agent_interface import AgentGraphAPI
        api = AgentGraphAPI(driver)
        ext_stats = api.get_extraction_stats()
        print(f"    Total regles               {ext_stats['total_rules']:>7,}")
        print(f"    Confiance moyenne          {ext_stats['confidence']['avg']:>7.3f}")
        print(f"    Taux validation source     {ext_stats['validation_rate']*100:>6.1f}%")
    else:
        print(f"\n  COUCHE REGLES : vide (sera peuplee par l'agent Phase 6)")

    if elapsed > 0:
        print(f"\n  Temps total : {elapsed:.1f}s")
    print(f"  Console Neo4j : http://localhost:7474")
    print(f"  Identifiants  : neo4j / amlkyc2025")
    print(SEP + "\n")


# ═════════════════════════════════════════════════════════════════════════════
#  Main
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Phase 4 — Construction couche structurelle du graphe Neo4j",
    )
    parser.add_argument("--chunks",     default=str(DEFAULT_CHUNKS),
                        help="Chemin vers chunks_aml_kyc.json")
    parser.add_argument("--clear",      action="store_true",
                        help="Vide le graphe avant construction (RESET COMPLET)")
    parser.add_argument("--stats-only", action="store_true",
                        help="Affiche les stats sans construction")
    args = parser.parse_args()

    # Imports tardifs
    try:
        from neo4j_graph.schema  import apply_schema, create_reference_nodes
        from neo4j_graph.loaders import load_chunks
        from neo4j_graph.builder import StructuralBuilder
    except ImportError as e:
        sys.exit(f"Import error : {e}\n  → pip install neo4j")

    SEP = "=" * 64
    print(f"\n{SEP}")
    print("  AML/KYC Agentic RAG - Phase 4 : Graphe Neo4j (couche structurelle)")
    print(SEP)
    print(f"  Architecture : 2 couches (structurelle + regles agent)")
    print(f"  Chunks       : {args.chunks}")
    print(f"  Neo4j URI    : {NEO4J_URI}")
    print()

    # Connexion
    try:
        driver = connect_with_retry(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    except ConnectionError as e:
        sys.exit(str(e))

    try:
        if args.stats_only:
            print_stats(driver)
            return

        start = time.time()

        if args.clear:
            clear_graph(driver)

        # ── Schéma + référentiels ──────────────────────────────────────────
        print("[ÉTAPE 1] Application du schéma")
        apply_schema(driver)
        create_reference_nodes(driver)

        # ── Chargement chunks ──────────────────────────────────────────────
        print("\n[ÉTAPE 2] Chargement des chunks")
        if not Path(args.chunks).exists():
            sys.exit(f"Fichier introuvable : {args.chunks}\n"
                     "  Lancez d'abord pipeline.py pour générer chunks_aml_kyc.json")
        chunks = load_chunks(args.chunks)

        # ── Construction couche 1 ──────────────────────────────────────────
        print("\n[ÉTAPE 3] Construction de la couche structurelle")
        builder = StructuralBuilder(driver)
        builder.build(chunks)

        elapsed = time.time() - start
        logger.info(f"\n  ✓ Construction terminée en {elapsed:.1f}s")

        print_stats(driver, elapsed=elapsed)

        print("  PROCHAINES ÉTAPES :")
        print("    python validate_neo4j_graph.py  ← vérifier intégrité")
        print("    python query_neo4j.py --stats   ← explorer le graphe")
        print("    Phase 5 : implémenter le retriever hybride Weaviate + Neo4j")
        print("    Phase 6 : agent peuple la couche règles via agent_interface.py")
        print()

    except Exception as e:
        logger.error(f"Erreur : {e}", exc_info=True)
        sys.exit(1)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
