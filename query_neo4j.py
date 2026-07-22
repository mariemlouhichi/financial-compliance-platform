"""
query_neo4j.py
==============
Outil CLI pour interroger le graphe Neo4j AML/KYC.

USAGE :
  # Stats globales
  python query_neo4j.py --stats

  # Couche structurelle (toujours disponible)
  python query_neo4j.py --legalunits --jurisdiction Tunisia --threshold
  python query_neo4j.py --legalunits --tx-type SWIFT --role OBLIGATION
  python query_neo4j.py --context "bct_circulaire_2017_08_aml.pdf::Article 15"
  python query_neo4j.py --thresholds --jurisdiction Tunisia

  # Couche règles (si peuplée par l'agent)
  python query_neo4j.py --rules --tx-type SWIFT
  python query_neo4j.py --rules --tx-type SWIFT --severity CRITIQUE HAUTE
  python query_neo4j.py --rules --category KYC
  python query_neo4j.py --provenance DEMO_KYC_001
  python query_neo4j.py --extraction-stats
"""

import argparse
import os
import sys
import textwrap

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "amlkyc2025")

_SEV_ICON = {"CRITIQUE": "🔴", "HAUTE": "🟠", "MOYENNE": "🟡",
             "BASSE": "🟢", "INFORMATIVE": "ℹ️"}


def _div(c="─", w=72): return c * w


def _print_legalunit(lu, idx=None):
    prefix = f"[{idx}] " if idx is not None else ""
    flag = "💰" if lu.get("has_threshold") else "  "
    print(f"\n{prefix}{flag} {lu.get('article_ref','?'):<25} ({lu.get('role','?')})")
    print(f"    Document : {lu.get('document','?')}")
    if lu.get("issuer"):
        print(f"    Issuer   : {lu['issuer']}")


def _print_rule(r, idx=None):
    prefix = f"[{idx}] " if idx is not None else ""
    icon = _SEV_ICON.get(r.get("severity", ""), "⚪")
    origin_tag = {"agent_extracted": "🤖", "agent_validated": "✓",
                  "human_verified":  "👤"}.get(r.get("origin"), "?")
    conf = r.get("confidence", 0) or 0
    print(f"\n{prefix}{icon} {r.get('severity','?'):<10} {origin_tag} "
          f"conf={conf:.2f} | {r.get('id','?')}")
    print(f"    Name   : {r.get('name','')}")
    desc = r.get("description") or ""
    print(f"    Desc   : {textwrap.shorten(desc, 100)}")
    if r.get("threshold") is not None:
        print(f"    Seuil  : {r['threshold']} {r.get('threshold_currency','')}")
    if r.get("category"):
        print(f"    Cat.   : {r['category']}")


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stats",            action="store_true")
    mode.add_argument("--legalunits",       action="store_true")
    mode.add_argument("--context",          metavar="UNIT_ID")
    mode.add_argument("--thresholds",       action="store_true")
    mode.add_argument("--rules",            action="store_true")
    mode.add_argument("--provenance",       metavar="RULE_ID")
    mode.add_argument("--extraction-stats", action="store_true")

    parser.add_argument("--jurisdiction", help="Tunisia | International | European Union")
    parser.add_argument("--role",         help="OBLIGATION | PROCEDURE | THRESHOLD ...")
    parser.add_argument("--tx-type",      help="VIREMENT_LOCAL | SWIFT | MANDAT")
    parser.add_argument("--threshold",    action="store_true",
                        help="Filtre : avec seuil financier")
    parser.add_argument("--severity",     nargs="+",
                        help="CRITIQUE HAUTE MOYENNE BASSE")
    parser.add_argument("--category",     help="KYC | PEP | STR ...")
    parser.add_argument("--depth",        type=int, default=1)
    parser.add_argument("--limit",        type=int, default=20)

    args = parser.parse_args()

    try:
        from neo4j import GraphDatabase
        from neo4j_graph import queries as Q
    except ImportError as e:
        sys.exit(f"Import error : {e}")

    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
    except Exception as e:
        sys.exit(f"Connexion Neo4j échouée : {e}")

    try:
        # ── STATS GLOBALES ─────────────────────────────────────────────────
        if args.stats:
            stats = Q.get_graph_stats(driver)
            print(_div("═"))
            print("  STATISTIQUES DU GRAPHE")
            print(_div("═"))
            print("  NŒUDS")
            for label, cnt in stats["nodes"].items():
                if cnt > 0:
                    print(f"    {label:<22} {cnt:>7,}")
            print("  RELATIONS")
            for rel, cnt in stats["relationships"].items():
                if cnt > 0:
                    print(f"    {rel:<26} {cnt:>7,}")
            print(_div("═"))

        # ── LEGAL UNITS ────────────────────────────────────────────────────
        elif args.legalunits:
            rows = Q.get_legalunits_by_filter(
                driver,
                jurisdiction=args.jurisdiction,
                role=args.role,
                tx_type=args.tx_type,
                has_threshold=True if args.threshold else None,
                limit=args.limit,
            )
            filters = []
            if args.jurisdiction: filters.append(f"jur={args.jurisdiction}")
            if args.role:         filters.append(f"role={args.role}")
            if args.tx_type:      filters.append(f"tx={args.tx_type}")
            if args.threshold:    filters.append("with_threshold")
            label = "LEGAL UNITS"
            if filters:
                label += f" [{', '.join(filters)}]"
            print(_div("═"))
            print(f"  {label} ({len(rows)} résultats)")
            print(_div("═"))
            for i, lu in enumerate(rows, 1):
                _print_legalunit(lu, i)
            print()

        # ── CONTEXT ────────────────────────────────────────────────────────
        elif args.context:
            ctx = Q.get_legalunit_context(driver, args.context, depth=args.depth)
            if not ctx:
                sys.exit(f"LegalUnit introuvable : {args.context}")
            print(_div("═"))
            print(f"  CONTEXTE — {args.context}")
            print(_div("═"))
            print(f"  Document  : {ctx['document']}")
            print(f"  Issuer    : {ctx['issuer']}")
            lu = ctx["legal_unit"]
            print(f"  Article   : {lu.get('article_ref')}")
            print(f"  Rôle      : {lu.get('primary_role')}")
            print(f"  Tx types  : {', '.join(lu.get('transaction_types', []))}")
            print(f"\n  ◀ Précédents : {', '.join(ctx['previous_articles']) or '(aucun)'}")
            print(f"  ▶ Suivants   : {', '.join(ctx['next_articles']) or '(aucun)'}")

        # ── THRESHOLDS ─────────────────────────────────────────────────────
        elif args.thresholds:
            rows = Q.get_articles_with_threshold(driver, args.jurisdiction,
                                                  limit=args.limit)
            print(_div("═"))
            label = "ARTICLES AVEC SEUILS"
            if args.jurisdiction:
                label += f" — {args.jurisdiction}"
            print(f"  {label} ({len(rows)})")
            print(_div("═"))
            for i, lu in enumerate(rows, 1):
                _print_legalunit(lu, i)
            print()

        # ── RÈGLES ─────────────────────────────────────────────────────────
        elif args.rules:
            if args.tx_type:
                rows = Q.get_rules_for_transaction(
                    driver, args.tx_type,
                    severity_filter=args.severity,
                    only_validated=False,
                )
                label = f"RÈGLES POUR {args.tx_type}"
            elif args.category:
                rows = Q.get_rules_by_category(driver, args.category,
                                                tx_type=args.tx_type)
                label = f"RÈGLES — Catégorie : {args.category}"
            else:
                sys.exit("Précisez --tx-type ou --category")

            if args.severity:
                label += f" [{', '.join(args.severity)}]"
            print(_div("═"))
            print(f"  {label} ({len(rows)} règles)")
            print(_div("═"))
            if not rows:
                print("\n  ⚠ Aucune règle dans le graphe.")
                print("    La couche règles est peuplée par l'agent Phase 6.")
                print("    Test rapide : python demo_agent_extraction.py")
            for i, r in enumerate(rows, 1):
                _print_rule(r, i)
            print()

        # ── PROVENANCE ─────────────────────────────────────────────────────
        elif args.provenance:
            prov = Q.get_rule_with_provenance(driver, args.provenance)
            if not prov:
                sys.exit(f"Règle introuvable : {args.provenance}")
            r = prov["rule"]
            print(_div("═"))
            print(f"  PROVENANCE — {args.provenance}")
            print(_div("═"))
            print(f"  Nom              : {r.get('name')}")
            print(f"  Description      : {r.get('description')}")
            print(f"  Sévérité         : {r.get('severity')}")
            print(f"  Catégorie        : {r.get('category')}")
            print(f"  Origin           : {r.get('origin')}")
            print(f"  Confiance        : {r.get('confidence')}")
            print(f"  Validée source   : {r.get('validated_against_source')}")
            print(f"  Juridiction      : {prov['jurisdiction']}")
            print(f"\n  Chunks supportant ({len(prov['chunks'])}) :")
            for ch in prov["chunks"][:5]:
                print(f"    • {ch['chunk_id'][:50]:<50} ({ch['article_ref']})")
                print(f"      → weaviate_ref : {ch['weaviate_ref']}")
            print(f"\n  Documents source ({len(prov['documents'])}) :")
            for d in prov["documents"]:
                print(f"    • {d['name']} ({d.get('issuer','?')}, {d.get('year','?')})")

        # ── EXTRACTION STATS ──────────────────────────────────────────────
        elif args.extraction_stats:
            from neo4j_graph.agent_interface import AgentGraphAPI
            api = AgentGraphAPI(driver)
            stats = api.get_extraction_stats()
            print(_div("═"))
            print("  STATS D'EXTRACTION (qualité agent)")
            print(_div("═"))
            print(f"\n  Total règles           : {stats['total_rules']}")
            print(f"  Confiance moyenne      : {stats['confidence']['avg']:.3f}")
            print(f"  Taux validation source : {stats['validation_rate']*100:.1f}%")
            print(f"\n  Distribution par origin :")
            for origin, cnt in stats["by_origin"].items():
                print(f"    {origin:<22} {cnt:>5,}")
            print(f"\n  Distribution par sévérité :")
            for sev, cnt in stats["by_severity"].items():
                print(f"    {sev:<22} {cnt:>5,}")
            print()

    finally:
        driver.close()


if __name__ == "__main__":
    main()
