"""
generate_scenarios.py — Générateur AUTOMATIQUE de scénarios étiquetés
=====================================================================
PFE AML/KYC 2025-2026 | CRISP-DM Phase 5 — Évaluation automatisée

OBJECTIF (remplace l'étiquetage manuel) :
  Au lieu d'écrire à la main les décisions attendues, on GÉNÈRE des
  transactions par programme et on les ÉTIQUETTE AUTOMATIQUEMENT avec le
  NIVEAU 1 déterministe (règles Python fixes, 0% LLM, issues des textes
  officiels BCT/FATF). Le Niveau 1 est notre ORACLE automatique.

POURQUOI C'EST DÉFENDABLE (et plus « intelligent » que le manuel) :
  - Aucune décision écrite à la main → pas de biais d'annotation humaine.
  - L'étiquette vient d'un moteur réglementaire déterministe et reproductible.
  - Scalable : 100+ cas générés en une seconde (vs 24 écrits à la main).
  - Couvre systématiquement les FRONTIÈRES de seuils (juste sous / juste au-
    dessus), les conversions de devises, les pays à risque, le smurfing.

CE QUE ÇA MESURE (honnêteté scientifique) :
  Un test de SÛRETÉ/CONSISTANCE à grande échelle : l'Agentic RAG (Niveau 2)
  ne doit JAMAIS décider EN DESSOUS du plancher réglementaire fixé par le
  Niveau 1. La décision attendue = plancher Niveau 1 ; toute décision PLUS
  STRICTE est acceptable (escalade contextuelle légitime du Niveau 2).
  → Complète (ne remplace pas) le petit jeu expert pour les cas nuancés.

USAGE :
  python generate_scenarios.py                  # affiche la distribution
  python generate_scenarios.py --out gen.json   # exporte les scénarios
  python evaluate_compliance.py --generated      # évalue dessus (voir harnais)
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Espace de décision de l'agent (du moins au plus sévère)
SEVERITY = ["PASS", "REVIEW", "EDD", "STR", "BLOCK"]

# Plancher Niveau 1 → décision attendue dans l'espace de l'agent.
# (mirroir EXACT de RiskScoringEngine : l1_floor + _to_decision)
L1_FLOOR = {"STR": 65, "EDD": 45, "KYC": 25, "RECORD": 15, "PASS": 0, "BLOCK": 100}

def _expected_from_l1(l1_decision: str) -> str:
    """Décision attendue de l'agent = plancher déterministe du Niveau 1."""
    if l1_decision == "BLOCK":
        return "BLOCK"
    floor = L1_FLOOR.get(l1_decision, 0)
    if floor >= 65: return "STR"
    if floor >= 45: return "EDD"
    if floor >= 25: return "REVIEW"
    return "PASS"

# Recommandations FATF applicables par type de transaction (checklist auto)
FATF_BY_TXTYPE = {
    "SWIFT":          ["R10", "R16", "R20", "R13", "R19"],
    "VIREMENT_LOCAL": ["R10", "R20"],
    "MANDAT":         ["R10", "R14", "R20"],
    "DEPOT_ESPECES":  ["R10", "R20", "R32"],
}


# ═══════════════════════════════════════════════════════════════════════
#  GRILLE DE GÉNÉRATION (frontières de seuils + variations de risque)
# ═══════════════════════════════════════════════════════════════════════

# (tx_type, currency, seuil_principal) — montants générés autour du seuil
THRESHOLD_GRID = [
    ("VIREMENT_LOCAL", "TND", 10_000),
    ("SWIFT",          "USD", 10_000),
    ("SWIFT",          "EUR", 15_000),
    ("DEPOT_ESPECES",  "TND",  5_000),
    ("MANDAT",         "TND",  3_000),
]

# Multiplicateurs : couvre sous-seuil, juste-sous, juste-au-dessus, élevé
AMOUNT_FACTORS = [0.4, 0.92, 1.15, 2.6]

CLEAN_DEST = "France"     # pays non listé FATF
BLACK_DEST = "Iran"       # liste noire FATF → BLOCK déterministe


def _base_tx(tx_type, currency, amount, dest, justif, suspicious):
    tx = {
        "tx_type": tx_type, "amount": round(float(amount), 2), "currency": currency,
        "sender_name": "Client Alpha", "beneficiary_name": "Beneficiaire Beta",
        "sender_country": "Tunisia", "destination_country": dest,
        "has_justification": justif,
        "kyc_complete": True, "sender_verified": True, "beneficiary_verified": True,
        "previous_strs": 0,
        "motive": "Paiement facture commerciale" if not suspicious else "Aide",
    }
    if suspicious:
        # 2 facteurs de suspicion → is_suspicious=True au Niveau 1
        tx["motive"] = "don"                 # motif vague (facteur 1)
        tx["new_receiver"] = True            # nouveau bénéficiaire (facteur 2)
    if tx_type == "MANDAT" and suspicious:
        tx["operator"] = "WESTERN_UNION"     # opérateur MVTS à risque
        tx["withdrawal_mode"] = "CASH"
    return tx


def _build_transactions():
    """Construit la grille brute de transactions (avant étiquetage)."""
    txs = []
    for tx_type, currency, threshold in THRESHOLD_GRID:
        for f in AMOUNT_FACTORS:
            amount = threshold * f
            for dest in (CLEAN_DEST, BLACK_DEST):
                for justif in (True, False):
                    for suspicious in (False, True):
                        # SWIFT/justif n'a de sens que pour SWIFT ; on garde
                        # quand même pour tester la robustesse cross-type.
                        txs.append(_base_tx(tx_type, currency, amount,
                                            dest, justif, suspicious))
    return txs


# ═══════════════════════════════════════════════════════════════════════
#  ÉTIQUETAGE AUTOMATIQUE PAR L'ORACLE NIVEAU 1
# ═══════════════════════════════════════════════════════════════════════

def build_scenarios(verbose: bool = False) -> list:
    """
    Génère les transactions, les étiquette via le Niveau 1, et produit des
    scénarios au format du harnais d'évaluation (evaluate_compliance.py).
    """
    try:
        from level1_rules.level1_orchestrator import Level1Orchestrator
    except Exception as e:
        raise RuntimeError(f"Niveau 1 indisponible (oracle requis) : {e}")

    # Le Niveau 1 imprime à l'init → on capture pour rester silencieux si besoin
    orch = Level1Orchestrator()

    raw_txs = _build_transactions()
    scenarios = []
    seen_keys = set()

    for i, tx in enumerate(raw_txs):
        res = orch.check_transaction(tx)
        expected = _expected_from_l1(res.decision)
        # Tout ce qui est PLUS STRICT que le plancher est acceptable
        # (escalade contextuelle légitime du Niveau 2).
        idx = SEVERITY.index(expected)
        acceptable = SEVERITY[idx + 1:]

        # Déduplication : éviter les transactions menant au même cas trivial
        key = (tx["tx_type"], tx["currency"], expected, tuple(sorted(res.risk_signals)),
               round(tx["amount"] / 1000))
        if key in seen_keys:
            continue
        seen_keys.add(key)

        justification = "; ".join(res.violations) if res.violations else \
            f"Niveau 1 déterministe → {res.decision} (montant {tx['amount']:.0f} "\
            f"{tx['currency']}, dest {tx['destination_country']})"

        scenarios.append({
            "id":              f"GEN_{len(scenarios)+1:03d}_{res.decision}",
            "category":        expected,
            "description":     f"{tx['tx_type']} {tx['amount']:.0f} {tx['currency']} "
                               f"→ {tx['destination_country']} "
                               f"(justif={tx['has_justification']})",
            "transaction":     tx,
            "risk_signals":    res.risk_signals,
            "fatf_checklist":  FATF_BY_TXTYPE.get(tx["tx_type"], ["R10", "R20"]),
            "level1_decision": res.decision,
            "expected":        expected,
            "acceptable":      acceptable,
            "justification":   justification[:200],
        })

    if verbose:
        dist = Counter(s["expected"] for s in scenarios)
        print(f"\n  {len(scenarios)} scénarios générés (oracle Niveau 1) :")
        for d in SEVERITY:
            if dist.get(d):
                print(f"    {d:<8} : {dist[d]}")
    return scenarios


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

    ap = argparse.ArgumentParser(description="Générateur automatique de scénarios (oracle Niveau 1)")
    ap.add_argument("--out", default=None, help="Exporter les scénarios en JSON")
    args = ap.parse_args()

    print("=" * 70)
    print("  GÉNÉRATION AUTOMATIQUE DE SCÉNARIOS — Oracle Niveau 1 (0% LLM)")
    print("=" * 70)

    scenarios = build_scenarios(verbose=True)

    dist = Counter(s["expected"] for s in scenarios)
    l1_dist = Counter(s["level1_decision"] for s in scenarios)
    print(f"\n  Distribution Niveau 1 (brut) : {dict(l1_dist)}")
    print(f"  Distribution attendue (agent): {dict(dist)}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(scenarios, f, indent=2, ensure_ascii=False)
        print(f"\n  💾 Exporté : {args.out} ({len(scenarios)} scénarios)")

    print(f"\n  → Évaluer dessus : python evaluate_compliance.py --generated")
    print("=" * 70)


if __name__ == "__main__":
    main()
