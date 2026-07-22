"""
validate_neo4j_graph.py
=======================
Validation et démonstration du graphe Neo4j AML/KYC.

USAGE :
  python validate_neo4j_graph.py             ← intégrité + démo
  python validate_neo4j_graph.py --quick     ← intégrité seulement
  python validate_neo4j_graph.py --demo      ← requêtes démo seulement

Vérifications :
  1. Comptes nœuds / relations cohérents
  2. Pas d'orphelins (LegalUnit sans Document, Chunk sans LegalUnit)
  3. NEXT_IN_DOC ne traverse jamais les frontières de documents
  4. (si couche 2 présente) Bridges Rule→Chunk fonctionnels
"""

import argparse
import os
import sys
from neo4j import GraphDatabase

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "amlkyc2025")


class Validator:
    def __init__(self, driver):
        self.driver = driver

    def run(self, cypher, **params):
        with self.driver.session() as session:
            return [dict(r) for r in session.run(cypher, **params)]

    # ──────────────────────────────────────────────────────────────────────
    def check_integrity(self) -> bool:
        SEP = "─" * 64
        print(f"\n{SEP}")
        print("  VÉRIFICATIONS D'INTÉGRITÉ")
        print(SEP)

        ok = True

        # 1.1 Comptes nœuds
        print("\n  [1.1] Comptes nœuds")
        labels = ["Document", "LegalUnit", "Chunk", "Rule",
                  "Jurisdiction", "RoleCategory", "TransactionType"]
        expected = {"TransactionType": 4, "Jurisdiction": 3, "RoleCategory": 9}
        for label in labels:
            cnt = self.run(f"MATCH (n:{label}) RETURN count(n) AS c")[0]["c"]
            exp = expected.get(label)
            status = "✓" if (exp is None or cnt == exp) else "⚠"
            exp_str = f" (attendu : {exp})" if exp else ""
            if cnt > 0:
                print(f"    {status} {label:<20} {cnt:>7,}{exp_str}")

        # 1.2 Comptes relations
        print("\n  [1.2] Comptes relations")
        for rel in ["UNDER_JURISDICTION", "FROM_DOCUMENT", "HAS_ROLE",
                    "APPLIES_TO", "REALIZES", "NEXT_IN_DOC",
                    "DERIVED_FROM", "SUPPORTED_BY", "RELATED_TO"]:
            cnt = self.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c")[0]["c"]
            if cnt > 0:
                print(f"    ✓ {rel:<22} {cnt:>7,}")

        # 1.3 Orphelins
        print("\n  [1.3] Détection d'orphelins")
        orphans = [
            ("LegalUnit sans FROM_DOCUMENT",
             "MATCH (lu:LegalUnit) WHERE NOT (lu)-[:FROM_DOCUMENT]->() RETURN count(lu) AS c"),
            ("Chunk sans REALIZES",
             "MATCH (ch:Chunk) WHERE NOT (ch)-[:REALIZES]->() RETURN count(ch) AS c"),
            ("Document sans UNDER_JURISDICTION",
             "MATCH (d:Document) WHERE NOT (d)-[:UNDER_JURISDICTION]->() RETURN count(d) AS c"),
        ]
        for label, cypher in orphans:
            cnt = self.run(cypher)[0]["c"]
            status = "✓" if cnt == 0 else "⚠"
            if cnt > 0:
                ok = False
            print(f"    {status} {label:<35} {cnt:>4,}")

        # 1.4 Intégrité NEXT_IN_DOC
        print("\n  [1.4] Chaîne NEXT_IN_DOC")
        cross = self.run("""
            MATCH (a:LegalUnit)-[:NEXT_IN_DOC]->(b:LegalUnit)
            WHERE a.source_doc <> b.source_doc
            RETURN count(*) AS c
        """)[0]["c"]
        status = "✓" if cross == 0 else "✗"
        if cross > 0:
            ok = False
        print(f"    {status} Liens inter-documents (doit = 0) : {cross}")

        # 1.5 Couche règles si présente
        rule_count = self.run("MATCH (r:Rule) RETURN count(r) AS c")[0]["c"]
        if rule_count > 0:
            print("\n  [1.5] Couche règles — qualité")
            no_lu = self.run("""
                MATCH (r:Rule) WHERE NOT (r)-[:DERIVED_FROM]->() RETURN count(r) AS c
            """)[0]["c"]
            no_ch = self.run("""
                MATCH (r:Rule) WHERE NOT (r)-[:SUPPORTED_BY]->() RETURN count(r) AS c
            """)[0]["c"]
            unvalid = self.run("""
                MATCH (r:Rule {validated_against_source: false}) RETURN count(r) AS c
            """)[0]["c"]
            print(f"    ⚠ Règles sans DERIVED_FROM         : {no_lu}")
            print(f"    ⚠ Règles sans SUPPORTED_BY         : {no_ch}")
            print(f"    ⚠ Règles non-validées vs source    : {unvalid}")
        else:
            print("\n  [1.5] Couche règles vide")
            print("        → Sera peuplée par l'agent Phase 6")
            print("        → Test rapide : python demo_agent_extraction.py")

        result = "✓ INTÉGRITÉ OK" if ok else "✗ PROBLÈMES DÉTECTÉS"
        print(f"\n  {result}")
        return ok

    # ──────────────────────────────────────────────────────────────────────
    def run_demo_queries(self):
        SEP = "─" * 64
        print(f"\n{SEP}")
        print("  REQUÊTES DE DÉMONSTRATION")
        print(SEP)

        # Q1 — Couche structurelle : Articles avec seuils SWIFT
        print("\n  [Q1] Articles avec seuils financiers — applicables aux SWIFT")
        rows = self.run("""
            MATCH (lu:LegalUnit)-[:APPLIES_TO]->(t:TransactionType {name: 'SWIFT'})
            MATCH (lu)-[:FROM_DOCUMENT]->(doc:Document)
            WHERE lu.has_threshold = true OR toString(lu.has_threshold) = 'true'
            RETURN lu.unit_id AS ref, doc.name AS doc_name, doc.priority AS priority
            ORDER BY doc.priority DESC LIMIT 5
            """)
        for r in rows:
            print(f"    💰 {r['ref']:<25}  ←  {r['doc_name'][:50]} ({r['priority']})")

        # Q2 — Navigation NEXT_IN_DOC
        print("\n  [Q2] Navigation NEXT_IN_DOC autour d'un article BCT")
        rows = self.run("""
            MATCH (lu:LegalUnit)
            WHERE lu.source_doc = 'bct_circulaire_2017_08_aml.pdf'
            WITH lu ORDER BY lu.first_chunk_index LIMIT 1
            MATCH path = (lu)-[:NEXT_IN_DOC*0..3]->(n:LegalUnit)
            RETURN [x IN nodes(path) | x.article_ref] AS chain LIMIT 1
        """)
        for r in rows:
            chain = " → ".join(filter(None, r["chain"]))
            print(f"    {chain}")

        # Q3 — Couverture par TransactionType
        print("\n  [Q3] Couverture du corpus par type de transaction")
        rows = self.run("""
            MATCH (lu:LegalUnit)-[:APPLIES_TO]->(t:TransactionType)
            RETURN t.name AS tx_type, count(DISTINCT lu) AS legal_units,
                   count(DISTINCT lu.source_doc) AS documents
            ORDER BY legal_units DESC
        """)
        for r in rows:
            print(f"    {r['tx_type']:<18} {r['legal_units']:>5,} unités  "
                  f"({r['documents']} documents)")

        # Q4 — Documents HIGH priority et leur volume
        print("\n  [Q4] Top 5 documents HIGH priority (par volume d'unités)")
        rows = self.run("""
            MATCH (doc:Document {priority: 'HIGH'})<-[:FROM_DOCUMENT]-(lu:LegalUnit)
            RETURN doc.name AS name, doc.issuer AS issuer,
                   count(lu) AS units
            ORDER BY units DESC LIMIT 5
        """)
        for r in rows:
            print(f"    {r['name'][:55]:<55} {r['units']:>5,} ({r['issuer']})")

        # Q5 — Si couche règles peuplée
        rule_count = self.run("MATCH (r:Rule) RETURN count(r) AS c")[0]["c"]
        if rule_count > 0:
            print(f"\n  [Q5] Règles extraites par l'agent (top 5 par confiance)")
            rows = self.run("""
                MATCH (r:Rule)
                RETURN r.id AS id, r.name AS name, r.severity AS sev,
                       r.confidence AS conf, r.origin AS origin
                ORDER BY r.confidence DESC LIMIT 5
            """)
            for r in rows:
                icon = {"agent_extracted": "🤖", "human_verified": "👤",
                        "agent_validated": "✓"}.get(r["origin"], "?")
                print(f"    {icon} {r['id']:<20} {r['sev']:<10} "
                      f"conf={r['conf']:.2f} — {r['name'][:40]}")
        else:
            print(f"\n  [Q5] Couche règles vide — lancer demo_agent_extraction.py")

        print(f"\n  Console interactive : http://localhost:7474")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--demo",  action="store_true")
    args = parser.parse_args()

    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
    except Exception as e:
        sys.exit(f"Connexion Neo4j échouée : {e}")

    v = Validator(driver)
    try:
        if args.demo:
            v.run_demo_queries()
        elif args.quick:
            v.check_integrity()
        else:
            ok = v.check_integrity()
            if ok:
                v.run_demo_queries()
    finally:
        driver.close()


if __name__ == "__main__":
    main()
