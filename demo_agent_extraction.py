"""
demo_agent_extraction.py
========================
Démonstration simulée : comment un agent Phase 6 extraira et persistera
des règles dans le graphe Neo4j.

Ce script N'EST PAS l'agent Phase 6 — c'est une SIMULATION pour :
  1. Démontrer le mécanisme anti-hallucination
  2. Peupler le graphe avec quelques règles d'exemple
  3. Tester l'API agent_interface.py

USAGE :
  python demo_agent_extraction.py                 ← ajoute 3 règles d'exemple
  python demo_agent_extraction.py --clean         ← supprime les règles de démo
  python demo_agent_extraction.py --stats         ← stats d'extraction
"""

import argparse
import os
import sys

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "amlkyc2025")


# ═════════════════════════════════════════════════════════════════════════════
#  Règles candidates simulées
#
#  En production (Phase 6), ces règles seraient EXTRAITES par un LLM
#  depuis les chunks retournés par Weaviate. Ici on les hard-code pour démo.
# ═════════════════════════════════════════════════════════════════════════════

DEMO_RULE_VALID = {
    "id":                  "DEMO_KYC_001",
    "name":                "Identification renforcée pour virements > 10000 TND",
    "description":         "Tout virement local supérieur à 10000 TND requiert "
                           "une identification renforcée du donneur d'ordre.",
    "severity":            "HAUTE",
    "category":            "KYC",
    "transaction_types":   ["VIREMENT_LOCAL"],
    "threshold":           10000,
    "threshold_currency":  "TND",
    "str_required":        False,
    "report_required":     True,
    "action_description":  "Demander pièce d'identité + justificatif d'origine des fonds",
    "source_text_snippet": "Pour tout virement supérieur à 10000 TND, "
                            "la banque doit procéder à une identification renforcée...",
    # Ces chunk_ids doivent EXISTER dans le graphe (sinon validation échoue)
    # À adapter selon vos chunk_ids réels
    "source_chunks":       [],   # ← à remplir dynamiquement (voir main)
}

DEMO_RULE_HALLUCINATION = {
    "id":                  "DEMO_FAKE_001",
    "name":                "Règle inventée — seuil hallucinatoire",
    "description":         "Tout virement supérieur à 99999 TND requiert un audit",
    "severity":            "CRITIQUE",
    "category":            "STR",
    "transaction_types":   ["VIREMENT_LOCAL"],
    "threshold":           99999,                        # ← n'existe PAS
    "threshold_currency":  "TND",
    "action_description":  "Audit obligatoire",
    "source_text_snippet": "Le texte parle de 10000 TND comme seuil...",
    # ↑ Le snippet ne contient PAS 99999 → la validation va RECALER
    "source_chunks":       [],
}


# ═════════════════════════════════════════════════════════════════════════════
def get_sample_chunk_ids(driver, n: int = 2) -> list[str]:
    """Récupère n chunk_ids existants depuis le graphe (pour la démo)."""
    with driver.session() as session:
        result = session.run("""
            MATCH (ch:Chunk) WHERE ch.has_threshold = true
            RETURN ch.chunk_id AS chunk_id
            LIMIT $n
        """, n=n)
        return [r["chunk_id"] for r in result]


def demo_extraction(driver):
    from neo4j_graph.agent_interface import AgentGraphAPI, RuleCandidate

    api = AgentGraphAPI(driver)

    # On récupère 2 chunk_ids réels du graphe
    sample_chunks = get_sample_chunk_ids(driver, n=2)
    if not sample_chunks:
        print("  ⚠ Aucun chunk avec seuil dans le graphe. Lancez build_neo4j_graph.py d'abord.")
        return

    print(f"\n  Chunks utilisés pour démo : {sample_chunks}")

    # ── Cas 1 : règle VALIDE (le seuil 10000 apparaît dans le snippet) ──
    print("\n" + "─" * 60)
    print("  CAS 1 : Règle VALIDE — seuil ancré dans le source")
    print("─" * 60)

    DEMO_RULE_VALID["source_chunks"] = sample_chunks
    candidate1 = RuleCandidate(**DEMO_RULE_VALID)
    result1 = api.upsert_extracted_rule(candidate1, agent_run_id="demo_run_001")

    print(f"\n  Stockée    : {result1['stored']}")
    print(f"  Confiance  : {result1['validation'].confidence:.2f}")
    print(f"  Seuil OK   : {result1['validation'].threshold_found}")
    print(f"  Raisons    :")
    for r in result1["validation"].reasons:
        print(f"    • {r}")

    # ── Cas 2 : règle HALLUCINÉE (seuil 99999 ABSENT du source) ──
    print("\n" + "─" * 60)
    print("  CAS 2 : Règle HALLUCINÉE — seuil absent du source")
    print("─" * 60)

    DEMO_RULE_HALLUCINATION["source_chunks"] = sample_chunks
    candidate2 = RuleCandidate(**DEMO_RULE_HALLUCINATION)
    result2 = api.upsert_extracted_rule(candidate2, agent_run_id="demo_run_001")

    print(f"\n  Stockée    : {result2['stored']}")
    print(f"  Confiance  : {result2['validation'].confidence:.2f}")
    print(f"  Seuil OK   : {result2['validation'].threshold_found}")
    print(f"  Raisons    :")
    for r in result2["validation"].reasons:
        print(f"    • {r}")
    if not result2["stored"]:
        print(f"\n  ✓ ANTI-HALLUCINATION FONCTIONNE — règle rejetée")

    # ── Cas 3 : Recherche dans le cache ────────────────────────────────────
    print("\n" + "─" * 60)
    print("  CAS 3 : Cache check pour transaction VIREMENT_LOCAL avec KYC")
    print("─" * 60)

    cached = api.find_rules_for_transaction(
        tx_type="VIREMENT_LOCAL",
        category="KYC",
        keywords=["identification", "10000"],
    )
    print(f"\n  {len(cached)} règle(s) dans le cache :")
    for rule in cached:
        print(f"    • {rule['id']:<20} ({rule['severity']:<8}) "
              f"conf={rule['confidence']:.2f}  origin={rule['origin']}")

    # ── Cas 4 : Provenance complète d'une règle ─────────────────────────────
    if result1["stored"]:
        print("\n" + "─" * 60)
        print("  CAS 4 : Provenance complète (utilisée pour SAR Phase 7)")
        print("─" * 60)
        prov = api.get_rule_provenance(candidate1.id)
        print(f"\n  Règle         : {prov['rule']['name']}")
        print(f"  Chunks        : {len(prov['chunks'])}")
        for ch in prov["chunks"][:3]:
            print(f"    → {ch['chunk_id']}  ({ch['article_ref']})")
        print(f"  Documents     :")
        for doc in prov["documents"]:
            print(f"    → {doc['name']} ({doc.get('issuer','?')}, {doc.get('year','?')})")


def show_extraction_stats(driver):
    from neo4j_graph.agent_interface import AgentGraphAPI
    api = AgentGraphAPI(driver)
    stats = api.get_extraction_stats()

    print("\n" + "═" * 60)
    print("  STATS D'EXTRACTION (qualité agent)")
    print("═" * 60)
    print(f"\n  Total règles            : {stats['total_rules']}")
    print(f"  Confiance moy.          : {stats['confidence']['avg']:.3f}")
    print(f"  Confiance min/max       : {stats['confidence']['min']:.3f} / "
          f"{stats['confidence']['max']:.3f}")
    print(f"  Taux validation source  : {stats['validation_rate']*100:.1f}%")
    print(f"\n  Distribution par origin :")
    for origin, cnt in stats["by_origin"].items():
        print(f"    {origin:<22} {cnt:>5,}")
    print(f"\n  Distribution par sévérité :")
    for sev, cnt in stats["by_severity"].items():
        print(f"    {sev:<22} {cnt:>5,}")
    print()


def cleanup_demo(driver):
    """Supprime les règles DEMO_*."""
    with driver.session() as session:
        result = session.run("""
            MATCH (r:Rule) WHERE r.id STARTS WITH 'DEMO_'
            DETACH DELETE r
            RETURN count(r) AS cnt
        """)
        cnt = result.single()["cnt"]
    print(f"  ✓ {cnt} règles DEMO_* supprimées")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true",
                        help="Supprime les règles DEMO_*")
    parser.add_argument("--stats", action="store_true",
                        help="Affiche stats d'extraction")
    args = parser.parse_args()

    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
    except Exception as e:
        sys.exit(f"Connexion Neo4j échouée : {e}")

    try:
        if args.clean:
            cleanup_demo(driver)
        elif args.stats:
            show_extraction_stats(driver)
        else:
            demo_extraction(driver)
            print("\n" + "═" * 60)
            show_extraction_stats(driver)
            print("\n  → python demo_agent_extraction.py --clean   pour nettoyer")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
