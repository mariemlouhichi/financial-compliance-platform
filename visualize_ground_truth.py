"""Génère une visualisation des 49 règles ground truth pour la présentation."""
import json
import sys
from collections import Counter
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path("output/aml_rules_complete_validated.json")
OUT = Path("docs_soutenance/06_ground_truth_49_regles.md")

data = json.loads(SRC.read_text(encoding="utf-8"))
rules = data["rules"]

def abbr(doc):
    d = (doc or "").lower()
    table = {
        "bct_circulaire_2017_08": "BCT 2017-08", "bct_circulaire_2018_09": "BCT 2018-09",
        "bct_circulaire_2024_02": "BCT 2024-02", "bct_circulaire_2025_17": "BCT 2025-17",
        "loi_2015_26": "Loi 2015-26", "loi_2016_48": "Loi 2016-48",
        "2024_reglementation_bancaire": "Régl. Bancaire 2024", "ctaf": "CTAF",
        "fatf_recommendations": "FATF Rec.", "fatf_mvts": "FATF MVTS",
        "fatf_virtual_assets": "FATF Virtual Assets", "fatf_correspondent": "FATF Corresp.",
        "eba_gl_2021_02": "EBA GL 2021/02", "amld5": "EU AMLD5",
        "basel_aml_sound": "Basel d353", "basel_core": "Basel d494",
    }
    for k, v in table.items():
        if k in d:
            return v
    return doc

lines = ["# Les 49 règles Ground Truth — visualisation présentation", "",
         "> Extraites par LLM (Groq llama-3.3-70b) selon la méthode Compliance-to-Code,",
         "> puis **validées manuellement** (origin = human_verified). Réfèrence absolue.", ""]

# ── Distributions ──
lines += ["## 1. Distributions (pour graphiques)", ""]
jur = Counter(r.get("jurisdiction", "?") for r in rules)
lines += ["**Par juridiction :**", "", "| Juridiction | Nb | % |", "|---|---|---|"]
for k, n in jur.most_common():
    lines.append(f"| {k} | {n} | {n/len(rules)*100:.0f}% |")

role = Counter(r.get("chunk_role", "?") for r in rules)
lines += ["", "**Par rôle (type d'obligation) :**", "", "| Rôle | Nb |", "|---|---|"]
for k, n in role.most_common():
    lines.append(f"| {k} | {n} |")

src = Counter(abbr(r.get("source_doc", "?")) for r in rules)
lines += ["", "**Par source réglementaire :**", "", "| Source | Nb |", "|---|---|"]
for k, n in src.most_common():
    lines.append(f"| {k} | {n} |")

# Famille BCT vs international
def family(doc):
    d = (doc or "").lower()
    if d.startswith("bct") or "reglementation" in d or d.startswith("loi") or "ctaf" in d:
        return "Tunisie (BCT/loi/CTAF)"
    if d.startswith("fatf"):
        return "FATF"
    if d.startswith("eba") or d.startswith("amld") or d.startswith("eu"):
        return "UE/EBA"
    if d.startswith("basel"):
        return "Basel"
    return "autre"
fam = Counter(family(r.get("source_doc", "")) for r in rules)
lines += ["", "**Par famille de régulateur :**", "", "| Famille | Nb | % |", "|---|---|---|"]
for k, n in fam.most_common():
    lines.append(f"| {k} | {n} | {n/len(rules)*100:.0f}% |")

# ── Tableau complet ──
lines += ["", "## 2. Tableau complet des 49 règles", "",
          "| # | ID | Source | Article | Juridiction | Rôle | Obligation (résumé) |",
          "|---|----|--------|---------|-------------|------|---------------------|"]
for i, r in enumerate(rules, 1):
    comp = r.get("components", {})
    constraint = (comp.get("constraint") or comp.get("subject") or "")[:70].replace("\n", " ")
    lines.append(
        f"| {i} | {r.get('id','')} | {abbr(r.get('source_doc',''))} | "
        f"{r.get('source_article','')} | {r.get('jurisdiction','')} | "
        f"{r.get('chunk_role','')} | {constraint} |"
    )

OUT.parent.mkdir(exist_ok=True)
OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"OK -> {OUT}  ({len(rules)} regles)")
print(f"Juridictions : {dict(jur)}")
print(f"Familles : {dict(fam)}")
print(f"Roles : {dict(role)}")
