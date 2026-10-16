# -*- coding: utf-8 -*-
"""
test_rule_judge.py — Test du JUGE DE QUALITÉ des règles (étape 2 de la validation).
Vérifie que le LLM distingue une VRAIE obligation d'un texte descriptif.
Nécessite groq_keys.json (le LLM). Ne nécessite ni Neo4j ni Weaviate.

    python test_rule_judge.py
"""

# Ce script vit dans un sous-dossier : on expose la racine du projet aux imports.
import sys as _sys, pathlib as _pathlib
_RACINE = _pathlib.Path(__file__).resolve().parents[1]
if str(_RACINE) not in _sys.path:
    _sys.path.insert(0, str(_RACINE))

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from types import SimpleNamespace
from llm_client import get_llm
from compliance_agent import SemanticReasoner

# Le juge de qualité vit dans SemanticReasoner (l'extracteur de règles).
llm = get_llm(verbose=False)
ca = SemanticReasoner(llm=llm, neo4j_driver=None, verbose=False)
print(f"  LLM chargé : {ca.llm is not None}")

cas = [
    ("VRAIE OBLIGATION",
     "Les établissements doivent déclarer toute transaction dont le montant "
     "dépasse 10 000 TND aux autorités compétentes.",
     "doivent déclarer toute transaction dont le montant dépasse 10 000"),
    ("VRAIE OBLIGATION",
     "Le bénéficiaire effectif doit être identifié et vérifié avant l'entrée "
     "en relation d'affaires.",
     "Le bénéficiaire effectif doit être identifié et vérifié"),
    ("TEXTE DESCRIPTIF (pas une règle)",
     "Le blanchiment d'argent représente entre 2 et 5 % du PIB mondial et "
     "constitue un enjeu majeur pour les institutions financières.",
     "Le blanchiment d'argent représente entre 2 et 5 % du PIB mondial"),
    ("TEXTE DESCRIPTIF (définition)",
     "Une personne politiquement exposée est une personne qui exerce ou a "
     "exercé d'importantes fonctions publiques.",
     "Une personne politiquement exposée est une personne qui exerce"),
]

print("\n  JUGE DE QUALITÉ DES RÈGLES (étape 2)\n" + "  " + "-" * 60)
for label, text, snippet in cas:
    r = SimpleNamespace(rule_text=text, description="", source_snippet=snippet)
    ok = ca._rule_quality_ok(r)
    verdict = "✅ RÈGLE (gardée)" if ok else "❌ REJETÉE (descriptif)"
    print(f"  [{label}]")
    print(f"     {text[:68]}…")
    print(f"     → {verdict}\n")

print("  Attendu : les 2 obligations GARDÉES, les 2 textes descriptifs REJETÉS.\n")
