"""
rebuild_validated_json.py
=========================
Transforme les candidats extraits en ground truth validé pour Neo4j.

WORKFLOW :
  1. Charge ground_truth_candidates_v2.json (50 règles LLM)
  2. Applique filtres qualité (composantes obligatoires présentes)
  3. Génère aml_rules_complete_validated.json (format Neo4j)

MODES :
  python rebuild_validated_json.py                    ← Validation automatique
  python rebuild_validated_json.py --preview          ← Aperçu avant validation
  python rebuild_validated_json.py --strict           ← Rejeter si composante manquante
  python rebuild_validated_json.py --interactive      ← Validation manuelle règle par règle

SORTIE : output/aml_rules_complete_validated.json
  → Compatible avec load_ground_truth.py --input ... --all
"""

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

SRC_FILE = Path("output/ground_truth_candidates_v2.json")
OUT_FILE = Path("output/aml_rules_complete_validated.json")

# IDs à rejeter explicitement (règles hors périmètre AML après review manuelle)
# Exemples : gouvernance pure, prudentiel sans lien AML, erreurs d'extraction
MANUAL_REJECTIONS = set()  # ← Ajouter ici après review : {"03", "24", "31"}

# Critères de qualité minimum
QUALITY_RULES = {
    "subject_required": True,     # subject ne peut pas être vide
    "constraint_required": True,  # constraint ne peut pas être vide
    "min_constraint_length": 30,  # constraint doit faire au moins 30 chars
}


# ═══════════════════════════════════════════════════════════════════════════
#  FONCTIONS DE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

def assess_quality(rule):
    """Évalue la qualité d'une règle extraite."""
    comp = rule.get("components", {})
    issues = []
    score = 100

    # Subject
    subject = (comp.get("subject") or "").strip()
    if not subject:
        issues.append("❌ Subject vide")
        score -= 50
    elif len(subject) < 10:
        issues.append("⚠️ Subject trop court")
        score -= 10

    # Constraint
    constraint = (comp.get("constraint") or "").strip()
    if not constraint:
        issues.append("❌ Constraint vide")
        score -= 50
    elif len(constraint) < QUALITY_RULES["min_constraint_length"]:
        issues.append("⚠️ Constraint trop court")
        score -= 20

    # Condition (optionnel mais si présent doit être cohérent)
    condition = comp.get("condition")
    if condition and len(str(condition)) < 5:
        issues.append("⚠️ Condition vague")
        score -= 5

    # Context (optionnel)
    context = comp.get("context")

    # Bonus : montants détectés pour THRESHOLD
    if rule.get("chunk_role") == "THRESHOLD":
        amounts = rule.get("amounts", [])
        if not amounts:
            issues.append("⚠️ THRESHOLD sans montant détecté")
            score -= 15
        elif any(a["unit"] in ["TND", "EUR", "USD", "USD/EUR", "PERCENT"] for a in amounts):
            score += 10  # Bonus pour montant financier

    return {
        "score": max(0, score),
        "issues": issues,
        "grade": "A" if score >= 90 else "B" if score >= 70 else "C" if score >= 50 else "D",
    }


def should_reject(rule, strict=False):
    """Détermine si une règle doit être rejetée."""
    rule_id = str(rule.get("id", "?"))

    # Rejets manuels explicites
    if rule_id in MANUAL_REJECTIONS:
        return True, "Rejet manuel (hors périmètre AML)"

    # Rejets automatiques (qualité insuffisante)
    qa = assess_quality(rule)

    if strict and qa["score"] < 70:
        return True, f"Qualité insuffisante (score {qa['score']}/100)"

    if QUALITY_RULES["subject_required"]:
        if not (rule.get("components", {}).get("subject") or "").strip():
            return True, "Subject manquant"

    if QUALITY_RULES["constraint_required"]:
        if not (rule.get("components", {}).get("constraint") or "").strip():
            return True, "Constraint manquant"

    return False, None


def transform_to_neo4j_format(rule):
    """Transforme une règle au format attendu par load_ground_truth.py."""
    comp = rule.get("components", {})
    amounts = rule.get("amounts", [])

    # Extraire le premier montant financier significatif
    threshold_value = None
    threshold_currency = None
    for amt in amounts:
        if amt["unit"] in ["TND", "EUR", "USD", "USD/EUR"]:
            threshold_value = amt["value"]
            threshold_currency = amt["unit"].replace("USD/EUR", "EUR")
            break

    return {
        "id": f"GT-{rule['chunk_role'][:3]}-{rule['id']}",
        "source_doc": rule.get("source_doc", ""),
        "source_article": rule.get("source_article", ""),
        "source_chunk_id": rule.get("chunk_id", ""),
        "source_text": rule.get("source_text", ""),
        "jurisdiction": rule.get("jurisdiction", "International"),
        "chunk_role": rule.get("chunk_role", ""),
        
        # 4 composantes Compliance-to-Code
        "components": {
            "subject": comp.get("subject", ""),
            "condition": comp.get("condition"),
            "constraint": comp.get("constraint", ""),
            "context": comp.get("context"),
        },
        
        # Métadonnées techniques
        "threshold_value": threshold_value,
        "threshold_currency": threshold_currency,
        "all_amounts": amounts,
        "extraction_method": rule.get("extraction", {}).get("method", "unknown"),
        "extraction_score": rule.get("extraction", {}).get("score", 0),
        
        # Statut validation
        "validation_status": "VALIDATED",
        "origin": "human_verified",
        "validated_at": datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  MODES D'EXÉCUTION
# ═══════════════════════════════════════════════════════════════════════════

def preview_mode(rules):
    """Affiche un aperçu des règles avec scores qualité."""
    print(f"\n{'='*80}")
    print(f"  APERÇU DES 50 RÈGLES EXTRAITES")
    print(f"{'='*80}\n")

    by_grade = Counter()
    issues_count = Counter()

    for i, rule in enumerate(rules, 1):
        qa = assess_quality(rule)
        by_grade[qa["grade"]] += 1
        comp = rule["components"]
        
        # Colorisation du grade
        grade_color = {
            "A": "✅", "B": "🟢", "C": "🟡", "D": "🔴"
        }[qa["grade"]]

        print(f"[{i:02d}] {grade_color} {qa['grade']} ({qa['score']:3d}/100) | "
              f"{rule['chunk_role']:<14} | {rule['source_article'][:20]:<20}")
        
        if qa["issues"]:
            for issue in qa["issues"]:
                print(f"     {issue}")
                issues_count[issue] += 1

        # Afficher les composantes si score < 80
        if qa["score"] < 80:
            print(f"     S: {(comp.get('subject') or '')[:60]}")
            print(f"     O: {(comp.get('constraint') or '')[:60]}")
        print()

    print(f"{'='*80}")
    print(f"  DISTRIBUTION QUALITÉ")
    print(f"{'='*80}")
    for grade in ["A", "B", "C", "D"]:
        cnt = by_grade[grade]
        bar = "█" * cnt
        print(f"  Grade {grade} : {cnt:2d}/50  {bar}")

    if issues_count:
        print(f"\n  PROBLÈMES FRÉQUENTS :")
        for issue, cnt in issues_count.most_common(5):
            print(f"    {issue:<40} {cnt:2d}×")

    print(f"\n{'='*80}")
    print(f"  RECOMMANDATIONS :")
    print(f"{'='*80}")
    low_quality = sum(1 for r in rules if assess_quality(r)["score"] < 70)
    if low_quality == 0:
        print(f"  ✅ Toutes les règles ont un score ≥ 70/100")
        print(f"  → Lancer : python rebuild_validated_json.py")
    else:
        print(f"  ⚠️  {low_quality} règles avec score < 70/100")
        print(f"  → Option 1 : Accepter tel quel → python rebuild_validated_json.py")
        print(f"  → Option 2 : Mode strict → python rebuild_validated_json.py --strict")
        print(f"  → Option 3 : Review manuel → éditer MANUAL_REJECTIONS dans le script")
    print()


def interactive_mode(rules):
    """Validation interactive règle par règle."""
    print(f"\n{'='*80}")
    print(f"  MODE INTERACTIF — {len(rules)} règles")
    print(f"{'='*80}\n")

    validated = []
    rejected = []

    for i, rule in enumerate(rules, 1):
        qa = assess_quality(rule)
        comp = rule["components"]
        
        print(f"\n[{i:02d}/{len(rules)}] {rule['chunk_role']} — {rule['source_article']}")
        print(f"  Score : {qa['score']}/100 (Grade {qa['grade']})")
        print(f"  S: {comp.get('subject') or '(empty)'}")
        print(f"  C: {comp.get('condition') or '(none)'}")
        print(f"  O: {comp.get('constraint') or '(empty)'}")
        print(f"  X: {comp.get('context') or '(none)'}")
        
        if qa["issues"]:
            print(f"\n  ⚠️  Problèmes : {', '.join(qa['issues'])}")

        choice = input(f"\n  [V]alider / [R]ejeter / [S]kip to end ? ").strip().lower()
        
        if choice == "s":
            print(f"\n  Validation auto des {len(rules) - i + 1} règles restantes...")
            validated.extend(rules[i-1:])
            break
        elif choice == "r":
            reason = input(f"    Raison du rejet : ").strip() or "Qualité insuffisante"
            rule["rejection_reason"] = reason
            rejected.append(rule)
        else:  # 'v' ou autre = valider par défaut
            validated.append(rule)

    return validated, rejected


def batch_validate(rules, strict=False):
    """Validation automatique par batch."""
    print(f"\n{'='*80}")
    print(f"  VALIDATION AUTOMATIQUE")
    print(f"  Mode : {'STRICT (score ≥ 70)' if strict else 'STANDARD'}")
    print(f"{'='*80}\n")

    validated = []
    rejected = []

    for rule in rules:
        reject, reason = should_reject(rule, strict=strict)
        if reject:
            rule["rejection_reason"] = reason
            rejected.append(rule)
        else:
            validated.append(rule)

    print(f"  ✅ Validées : {len(validated)}/50")
    print(f"  ❌ Rejetées : {len(rejected)}/50")

    if rejected:
        print(f"\n  RÈGLES REJETÉES :")
        for r in rejected:
            print(f"    [{r['id']}] {r['chunk_role']:<14} {r.get('rejection_reason', '')}")

    return validated, rejected


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Validation ground truth AML/KYC")
    parser.add_argument("--preview", action="store_true", 
                        help="Aperçu qualité sans valider")
    parser.add_argument("--interactive", action="store_true",
                        help="Validation manuelle règle par règle")
    parser.add_argument("--strict", action="store_true",
                        help="Rejeter règles score < 70")
    parser.add_argument("--input", default=SRC_FILE, type=Path)
    parser.add_argument("--output", default=OUT_FILE, type=Path)
    args = parser.parse_args()

    # ── Chargement ────────────────────────────────────────────────────────
    if not args.input.exists():
        print(f"\n❌ ERREUR : {args.input} introuvable")
        print(f"   Lancer d'abord : python extract_ground_truth_v2.py\n")
        return 1

    print(f"\n📂 Source : {args.input}")
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    rules = data.get("rules", [])
    print(f"   {len(rules)} règles chargées\n")

    # ── Mode Preview ──────────────────────────────────────────────────────
    if args.preview:
        preview_mode(rules)
        return 0

    # ── Mode Interactive ──────────────────────────────────────────────────
    if args.interactive:
        validated, rejected = interactive_mode(rules)
    else:
        validated, rejected = batch_validate(rules, strict=args.strict)

    # ── Transformation Neo4j format ───────────────────────────────────────
    validated_neo4j = [transform_to_neo4j_format(r) for r in validated]

    # ── Sortie ────────────────────────────────────────────────────────────
    output_data = {
        "metadata": {
            "version": "2.1",
            "generated": datetime.now().isoformat(),
            "source_file": str(args.input),
            "total_extracted": len(rules),
            "total_validated": len(validated_neo4j),
            "total_rejected": len(rejected),
            "validation_mode": "interactive" if args.interactive else ("strict" if args.strict else "standard"),
            "methodology": {
                "extraction": "ObliQA + Compliance-to-Code (Chen et al., 2025)",
                "llm_model": data.get("metadata", {}).get("llm_model", "unknown"),
                "components": ["subject", "condition", "constraint", "context"],
            },
        },
        "rules": validated_neo4j,
        "rejected": rejected if rejected else [],
    }

    args.output.parent.mkdir(exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    # ── Résumé ────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  RÉSUMÉ VALIDATION")
    print(f"{'='*80}")
    print(f"  ✅ Règles validées   : {len(validated_neo4j)}")
    print(f"  ❌ Règles rejetées   : {len(rejected)}")
    print(f"  📊 Taux validation   : {len(validated_neo4j)/len(rules)*100:.1f}%")
    
    # Stats par rôle
    by_role = Counter(r["chunk_role"] for r in validated_neo4j)
    print(f"\n  PAR RÔLE :")
    for role, cnt in by_role.most_common():
        print(f"    {role:<18} {cnt:2d}")

    # Stats par juridiction
    by_jur = Counter(r["jurisdiction"] for r in validated_neo4j)
    print(f"\n  PAR JURIDICTION :")
    for jur, cnt in by_jur.most_common():
        print(f"    {jur:<25} {cnt:2d}")

    print(f"\n  📄 Fichier validé : {args.output}")
    print(f"     Taille         : {args.output.stat().st_size / 1024:.1f} KB")

    print(f"\n{'='*80}")
    print(f"  PROCHAINE ÉTAPE :")
    print(f"{'='*80}")
    print(f"  python load_ground_truth.py --input {args.output} --all")
    print(f"  python build_neo4j_graph.py --stats-only")
    print(f"{'='*80}\n")

    return 0


if __name__ == "__main__":
    exit(main())