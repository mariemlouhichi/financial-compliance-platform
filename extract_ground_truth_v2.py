"""
extract_ground_truth_v2.py
==========================
Extraction semi-automatique du ground truth AML/KYC.

MÉTHODOLOGIE ACADÉMIQUE :
  Filtrage  → ObliQA  (Gokhan et al., 2024)  — passages obligatoires uniquement
  Structure → Compliance-to-Code (Chen, 2025) — 4 composantes logiques
  LLM       → Groq API, llama-3.3-70b-versatile, temperature 0.0

SORTIE : output/ground_truth_candidates_v2.json
  → Structure épurée : id, source, components, category, severity, jurisdiction

USAGE :
  python extract_ground_truth_v2.py                    ← extraction LLM (recommandé)
  python extract_ground_truth_v2.py --no-llm           ← regex seul (dégradé)
  python extract_ground_truth_v2.py --dry-run           ← simulation
  python extract_ground_truth_v2.py --target 50         ← quota total

CONFIG :
  $env:GROQ_API_KEY = "gsk_..."
"""

import argparse
import json
import os
import re
import sys
import time
import requests
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.3-70b-versatile"
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

CHUNKS_FILE  = "output/chunks_aml_kyc.json"
OUTPUT_FILE  = "output/ground_truth_candidates_v2.json"
LOG_FILE     = "output/ground_truth_extraction_log.txt"

# ── RÔLES RETENUS + QUOTAS (total = 50) ──────────────────────────────────
# NB : les noms correspondent EXACTEMENT aux chunk_role dans les chunks
ROLE_QUOTAS = {
    "OBLIGATION":      12,   # Obligations légales (shall/must/doit)
    "KYC":             10,   # Identification, vérification, due diligence
    "THRESHOLD":        8,   # Seuils numériques précis (montants, %)
    "SANCTIONS":        7,   # Blocage, gel, listes noires
    "REPORTING":        5,   # STR/DOS, déclaration, signalement
    "RED_FLAG":         4,   # Indicateurs comportementaux suspects
    "RISK_ASSESSMENT":  4,   # Évaluation et classification des risques
}

# ── MOTS-CLÉS AML (filtrage pré-sélection) ───────────────────────────────
# Un chunk doit contenir AU MOINS un de ces mots pour être éligible
AML_KEYWORDS = {
    "money laundering", "terrorist financing", "aml", "cft", "kyc", "cdd",
    "edd", "due diligence", "suspicious", "soupçon", "dos", "str", "sar",
    "sanction", "freeze", "gel", "block", "ofac", "pep", "politically exposed",
    "threshold", "seuil", "identification", "verification", "beneficial owner",
    "bénéficiaire effectif", "risk", "risque", "vigilance", "compliance",
    "declaration", "reporting", "red flag", "indicator", "unusual", "atypical",
    "structuring", "smurfing", "layering", "wire transfer", "correspondent",
    "transaction", "transfer", "fund", "account", "record keeping",
    "conservation", "archiv", "tipping off", "monitor", "surveillance",
}

# ── MOTS-CLÉS D'EXCLUSION (hors périmètre AML) ──────────────────────────
# Chunks dont le SUJET PRINCIPAL est l'un de ces thèmes → rejeter
EXCLUDE_KEYWORDS = {
    "crowdfunding", "lending crowdfunding", "credit reporting activity",
    "market maker", "bid and ask", "spread", "pips",
    "deposit guarantee fund", "contribution annuelle",
    "financial conglomerate", "conglomérat financier",
    "monetary policy", "politique monétaire",
    "exchange rate", "taux de change", "cours de change",
    "payment system operator", "opérateur de paiement",
    "capital must not be less than",   # exigence prudentielle, pas AML
    "minimum capital", "capital minimum",
}

# ── SOURCES PRIORITAIRES (documents AML core) ────────────────────────────
PRIORITY_DOCS = {
    "bct_circulaire_2017_08_aml":     3,
    "bct_circulaire_2024_02_aml":     3,
    "bct_circulaire_2025_17_aml":     3,
    "loi_2015_26_aml_cft":            3,
    "fatf_recommendations_2025":      2,
    "ctaf_guide_signalement_dos":     2,
    "eu_aml_regulation_2024":         2,
    "amld5_directive":                2,
    "amld6_directive":                2,
    "eba_gl_2021_02_risk_factors":    2,
    "wolfsberg_aml_principles":       1,
    "wolfsberg_cdd_guidance":         1,
    "fatf_peps_guidance":             1,
    "fatf_virtual_assets_red_flags":  1,
    "fatf_correspondent_banking":     1,
    "fatf_mvts_guidance_2016":        1,
    "basel_aml_sound_management":     1,
    "loi_2016_48_banques":            1,
    "cmf_reglement_aml_2026":         2,
    "decret_2019_54":                 2,
    "bct_circulaire_2026_02_change":  1,
}


# ═══════════════════════════════════════════════════════════════════════════
#  UTILITAIRES
# ═══════════════════════════════════════════════════════════════════════════

def get_meta(chunk):
    return chunk.get("metadata", {})


def get_content(chunk):
    return (chunk.get("content") or chunk.get("text", "")).strip()


def detect_jurisdiction(source_doc):
    d = source_doc.lower()
    if any(x in d for x in ["bct_", "loi_2015", "loi_2016", "ctaf", "cmf",
                              "decret", "reglementation"]):
        return "Tunisia"
    if any(x in d for x in ["amld", "eu_aml", "eba_", "eu_transfer"]):
        return "EU"
    return "International"


def is_aml_relevant(text):
    """Le chunk parle-t-il vraiment d'AML/KYC ?"""
    t = text.lower()
    # Vérifier exclusions d'abord
    exclude_hits = sum(1 for kw in EXCLUDE_KEYWORDS if kw in t)
    if exclude_hits >= 2:
        return False
    # Vérifier présence AML
    aml_hits = sum(1 for kw in AML_KEYWORDS if kw in t)
    return aml_hits >= 2


def doc_priority(source_doc):
    d = source_doc.lower().replace(".pdf", "")
    for prefix, score in PRIORITY_DOCS.items():
        if prefix in d:
            return score
    return 0


def score_chunk(chunk, role):
    """Score de pertinence pour la sélection."""
    meta    = get_meta(chunk)
    content = get_content(chunk)
    text    = content.lower()
    score   = 0

    # Longueur (chunks denses = meilleure qualité)
    length = len(content)
    if length > 400: score += 3
    if length > 700: score += 2

    # Priorité documentaire
    score += doc_priority(meta.get("source_doc", "")) * 2

    # Mots-clés AML dans le texte
    aml_hits = sum(1 for kw in AML_KEYWORDS if kw in text)
    score += min(aml_hits, 8)

    # Bonus : a un article_ref clair (pas "Unknown")
    art = meta.get("article_ref", "")
    if art and art not in ("?", "Unknown", ""):
        score += 2

    # Bonus : has_threshold pour rôle THRESHOLD
    if role == "THRESHOLD" and meta.get("has_threshold", False):
        score += 5

    return score


def extract_amounts(text):
    """Extrait les montants financiers et durées du texte."""
    patterns = [
        (r"(?:USD/EUR|EUR/USD|USD|EUR)\s*(\d[\d\s,.']*\d|\d+)", "USD/EUR"),
        (r"(\d[\d\s,.']*\d|\d+)\s*(?:TND|dinar[s]?)", "TND"),
        (r"(\d[\d\s,.']*\d|\d+)\s*(?:EUR|euro[s]?|€)", "EUR"),
        (r"(\d[\d\s,.']*\d|\d+)\s*(?:USD|dollar[s]?|\$)", "USD"),
        (r"(\d+(?:[.,]\d+)?)\s*%", "PERCENT"),
        (r"(\d+)\s*(?:jours?|days?)", "DAYS"),
        (r"(\d+)\s*(?:ans?|years?)", "YEARS"),
        (r"(\d+)\s*(?:mois|months?)", "MONTHS"),
    ]
    found = []
    seen  = set()
    for pattern, unit in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            raw = m.group(0).strip()
            try:
                num_str = re.sub(r"[^\d.]", "", m.group(1).replace(",", ".").replace(" ", ""))
                num = float(num_str)
                if num > 0 and (num, unit) not in seen:
                    seen.add((num, unit))
                    found.append({"value": num, "unit": unit, "raw": raw})
            except (ValueError, AttributeError):
                pass
    return found


# ═══════════════════════════════════════════════════════════════════════════
#  APPEL GROQ API
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
You are an expert AML/KYC regulatory compliance analyst.
Your task is to extract the 4 logical components of a regulatory clause,
following the Compliance-to-Code methodology (Chen et al., 2025).

ALWAYS return valid JSON only. No markdown fences, no explanation, no preamble.
If a component does not apply, set it to null."""

USER_PROMPT = """\
Extract the 4 compliance components from this regulatory text.

SOURCE: {source_doc}
ARTICLE: {article_ref}
ROLE: {role}

TEXT:
---
{text}
---

Return ONLY this JSON:
{{
  "subject": "Who must comply (e.g. 'Banks and financial institutions', 'Reporting entities')",
  "condition": "Under what condition (e.g. 'When transaction exceeds 15,000 EUR', 'For high-risk customers'). null if unconditional.",
  "constraint": "What is required/prohibited (e.g. 'Must file STR with CTAF within 3 days', 'Must verify identity using official documents')",
  "context": "Deadline, duration, or exception (e.g. 'Within 30 days', 'Records kept for 10 years', 'Except for low-risk clients'). null if none."
}}"""


def call_groq(text, source_doc, article_ref, role, api_key, max_retries=3):
    """Appelle Groq API pour extraire les 4 composantes."""
    if not api_key:
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": USER_PROMPT.format(
                text=text[:2500], source_doc=source_doc,
                article_ref=article_ref, role=role,
            )},
        ],
        "temperature": 0.0,
        "max_tokens":  400,
        "response_format": {"type": "json_object"},
    }

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw).strip()
                return json.loads(raw)
            elif resp.status_code == 429:
                wait = min(60 * attempt, 120)
                print(f"        Rate limit → attente {wait}s...")
                time.sleep(wait)
            elif resp.status_code == 401:
                print("        ✗ Clé API invalide.")
                return None
            else:
                print(f"        Erreur {resp.status_code}")
                time.sleep(3)
        except json.JSONDecodeError:
            print(f"        JSON invalide (tentative {attempt})")
            time.sleep(2)
        except requests.Timeout:
            print(f"        Timeout (tentative {attempt})")
            time.sleep(5)
        except Exception as e:
            print(f"        Erreur: {e}")
            time.sleep(3)
    return None


def fallback_regex(text, role):
    """Extraction dégradée par regex quand pas de LLM."""
    t = text.lower()
    amounts = extract_amounts(text)
    fin = [a for a in amounts if a["unit"] in ("TND", "EUR", "USD", "USD/EUR")]

    # Subject
    if any(w in t for w in ["financial institution", "bank", "banque", "établissement"]):
        subject = "Banks and financial institutions"
    elif any(w in t for w in ["reporting entit", "entités assujetties"]):
        subject = "Reporting entities"
    else:
        subject = "Obliged entities"

    # Condition
    if fin:
        a = fin[0]
        condition = f"Transaction exceeding {int(a['value']):,} {a['unit']}"
    elif "pep" in t or "politically exposed" in t:
        condition = "Client identified as PEP"
    elif "high risk" in t or "haut risque" in t:
        condition = "High-risk business relationship"
    else:
        condition = None

    # Constraint
    if any(w in t for w in ["block", "freeze", "gel", "prohibit", "interdit"]):
        constraint = "Block transaction and report immediately"
    elif any(w in t for w in ["dos", "str", "sar", "soupçon", "suspicious"]):
        constraint = "File suspicious transaction report (STR/DOS)"
    elif any(w in t for w in ["edd", "enhanced", "renforcée", "vigilance renforcée"]):
        constraint = "Apply enhanced due diligence (EDD)"
    elif any(w in t for w in ["identify", "verify", "vérif", "identif"]):
        constraint = "Verify customer identity (KYC)"
    elif any(w in t for w in ["conserv", "archiv", "retain", "record"]):
        constraint = "Maintain records and documentation"
    elif any(w in t for w in ["monitor", "surveillance", "ongoing"]):
        constraint = "Conduct ongoing transaction monitoring"
    else:
        constraint = "Apply required compliance measures"

    # Context (deadlines)
    dur = [a for a in amounts if a["unit"] in ("DAYS", "YEARS", "MONTHS")]
    units_map = {"DAYS": "days", "YEARS": "years", "MONTHS": "months"}
    if dur:
        context = f"Within {int(dur[0]['value'])} {units_map[dur[0]['unit']]}"
    elif "immediately" in t or "immédiatement" in t or "without delay" in t:
        context = "Immediately / without delay"
    else:
        context = None

    return {
        "subject": subject, "condition": condition,
        "constraint": constraint, "context": context,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def load_and_filter(chunks_path):
    """Charge les chunks et filtre pour ne garder que les AML pertinents."""
    print(f"\n[1] CHARGEMENT")
    with open(chunks_path, encoding="utf-8") as f:
        chunks = json.load(f)
    print(f"    {len(chunks):,} chunks chargés")

    # Distribution des rôles
    all_roles = Counter(get_meta(c).get("chunk_role", "?") for c in chunks)
    print(f"\n    Distribution chunk_role :")
    for role, cnt in all_roles.most_common():
        marker = "✓ RETENU" if role in ROLE_QUOTAS else "  exclu "
        print(f"      [{marker}] {role:<18} {cnt:>5}")

    # Filtrer : rôle retenu + contenu AML pertinent
    eligible = defaultdict(list)
    excluded_not_aml = 0
    for c in chunks:
        role = get_meta(c).get("chunk_role", "?")
        if role not in ROLE_QUOTAS:
            continue
        content = get_content(c)
        if not is_aml_relevant(content):
            excluded_not_aml += 1
            continue
        eligible[role].append(c)

    total = sum(len(v) for v in eligible.values())
    print(f"\n    Éligibles AML   : {total:,}")
    print(f"    Exclus non-AML  : {excluded_not_aml}")
    for role in ROLE_QUOTAS:
        n = len(eligible.get(role, []))
        q = ROLE_QUOTAS[role]
        print(f"      {role:<18} {n:>5} dispo  (quota {q})")

    return eligible


def select_best(eligible):
    """Sélectionne les meilleurs chunks par rôle, avec diversité documentaire."""
    print(f"\n[2] SÉLECTION (diversité documentaire + score)")
    selected = []

    for role, quota in ROLE_QUOTAS.items():
        chunks = eligible.get(role, [])
        if not chunks:
            print(f"    {role:<18} 0 dispo → IGNORÉ")
            continue

        # Scorer et trier
        scored = sorted(
            ((score_chunk(c, role), c) for c in chunks),
            key=lambda x: -x[0]
        )

        # Sélection avec diversité documentaire (max 3 par doc)
        seen_articles = set()
        doc_count     = Counter()
        final         = []

        for score, chunk in scored:
            meta = get_meta(chunk)
            art_key = (meta.get("source_doc", ""), meta.get("article_ref", ""))
            src_doc = meta.get("source_doc", "")

            if art_key in seen_articles:
                continue
            if doc_count[src_doc] >= 3:
                continue

            seen_articles.add(art_key)
            doc_count[src_doc] += 1
            final.append((score, chunk, role))

            if len(final) >= quota:
                break

        print(f"    {role:<18} {len(final):>3} sélectionnés / {quota} quota")
        selected.extend(final)

    print(f"\n    Total : {len(selected)} règles")
    return selected


def extract_rules(selected, api_key, dry_run):
    """Extrait les 4 composantes pour chaque chunk sélectionné."""
    use_llm = bool(api_key) and not dry_run
    mode = "Groq LLM" if use_llm else ("DRY-RUN" if dry_run else "Regex fallback")

    print(f"\n[3] EXTRACTION — {mode}")
    print(f"    {len(selected)} règles à traiter\n")

    rules    = []
    log      = []
    llm_ok   = 0
    llm_fail = 0

    for i, (score, chunk, role) in enumerate(selected, 1):
        meta        = get_meta(chunk)
        content     = get_content(chunk)
        source_doc  = meta.get("source_doc", "unknown.pdf")
        article_ref = meta.get("article_ref", "?")
        jurisdiction= detect_jurisdiction(source_doc)
        amounts     = extract_amounts(content)

        print(f"    [{i:02d}/{len(selected)}] {role:<14} {source_doc[:35]:<35} {article_ref}")

        # Extraction
        if use_llm:
            extracted = call_groq(content, source_doc, article_ref, role, api_key)
            if extracted:
                llm_ok += 1
                method = "groq_llm"
            else:
                llm_fail += 1
                extracted = fallback_regex(content, role)
                method = "regex_fallback"
        elif dry_run:
            extracted = fallback_regex(content, role)
            method = "dry_run"
        else:
            extracted = fallback_regex(content, role)
            method = "regex_fallback"

        # Construire la règle (schéma épuré)
        rule = {
            "id":             f"{i:02d}",
            "source_doc":     source_doc,
            "source_article": article_ref,
            "jurisdiction":   jurisdiction,
            "chunk_role":     role,
            "chunk_id":       chunk.get("chunk_id", ""),
            "source_text":    content[:600].strip(),
            "components": {
                "subject":    extracted.get("subject", ""),
                "condition":  extracted.get("condition"),
                "constraint": extracted.get("constraint", ""),
                "context":    extracted.get("context"),
            },
            "amounts":        amounts,
            "extraction": {
                "method": method,
                "score":  score,
            },
        }
        rules.append(rule)

        log.append(
            f"[{i:02d}] {method} | {source_doc} | {article_ref}\n"
            f"  S: {(extracted.get('subject') or '')[:80]}\n"
            f"  C: {str(extracted.get('condition') or '')[:80]}\n"
            f"  O: {(extracted.get('constraint') or '')[:80]}\n"
            f"  X: {str(extracted.get('context') or '')[:80]}\n"
        )

        if use_llm:
            time.sleep(0.4)  # respect rate limit Groq

    print(f"\n    LLM OK       : {llm_ok}")
    print(f"    LLM échoué   : {llm_fail}")
    print(f"    Regex used   : {len(selected) - llm_ok}")

    return rules, log


def print_summary(rules):
    """Affiche un résumé des règles extraites."""
    by_role = Counter(r["chunk_role"]   for r in rules)
    by_jur  = Counter(r["jurisdiction"] for r in rules)
    by_src  = Counter(r["source_doc"]   for r in rules)
    has_amt = sum(1 for r in rules if r["amounts"])

    print(f"\n{'='*62}")
    print(f"  RÉSUMÉ — {len(rules)} RÈGLES")
    print(f"{'='*62}")
    print(f"  Avec montant détecté : {has_amt}/{len(rules)}")

    print(f"\n  PAR RÔLE :")
    for role, cnt in by_role.most_common():
        bar = "█" * cnt
        print(f"    {role:<18} {cnt:>3}  {bar}")

    print(f"\n  PAR JURIDICTION :")
    for j, n in by_jur.most_common():
        print(f"    {j:<25} {n}")

    print(f"\n  TOP DOCUMENTS :")
    for src, cnt in by_src.most_common(10):
        print(f"    {src[:48]:<48} {cnt}")


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Extraction ground truth AML/KYC")
    parser.add_argument("--chunks",  default=CHUNKS_FILE)
    parser.add_argument("--out",     default=OUTPUT_FILE)
    parser.add_argument("--log",     default=LOG_FILE)
    parser.add_argument("--target",  type=int, default=50)
    parser.add_argument("--api-key", default=GROQ_API_KEY)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-llm",  action="store_true")
    args = parser.parse_args()

    api_key = "" if (args.no_llm or args.dry_run) else args.api_key

    # Ajuster quotas si target ≠ 50
    if args.target != 50:
        factor = args.target / 50
        for r in ROLE_QUOTAS:
            ROLE_QUOTAS[r] = max(1, round(ROLE_QUOTAS[r] * factor))

    print(f"\n{'='*62}")
    print(f"  EXTRACTION GROUND TRUTH AML/KYC")
    print(f"  Filtrage  : ObliQA (Gokhan et al., 2024)")
    print(f"  Structure : Compliance-to-Code (Chen et al., 2025)")
    print(f"  LLM       : {GROQ_MODEL if api_key else 'Regex fallback'}")
    print(f"  Cible     : {args.target} règles")
    print(f"{'='*62}")

    if not Path(args.chunks).exists():
        sys.exit(f"\n  ERREUR : {args.chunks} introuvable.")

    if not api_key and not args.no_llm and not args.dry_run:
        print(f"\n  ⚠ Pas de clé Groq → regex fallback (qualité dégradée)")
        print(f"    Pour LLM : $env:GROQ_API_KEY='gsk_...'")
        print(f"    Groq est GRATUIT : https://console.groq.com/keys\n")

    eligible = load_and_filter(args.chunks)
    selected = select_best(eligible)
    rules, log_lines = extract_rules(selected, api_key, args.dry_run)
    print_summary(rules)

    # ── Écriture ──────────────────────────────────────────────────────────
    output = {
        "metadata": {
            "version":      "2.1",
            "generated":    datetime.now().isoformat(),
            "total_rules":  len(rules),
            "target":       args.target,
            "llm_model":    GROQ_MODEL if api_key else "regex_only",
            "methodology": {
                "filtering":   "ObliQA (Gokhan et al., 2024)",
                "structuring": "Compliance-to-Code (Chen et al., 2025)",
                "components":  ["subject", "condition", "constraint", "context"],
            },
        },
        "rules": rules,
    }

    Path(args.out).parent.mkdir(exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    Path(args.log).parent.mkdir(exist_ok=True)
    with open(args.log, "w", encoding="utf-8") as f:
        f.write(f"Extraction : {datetime.now().isoformat()}\n")
        f.write(f"Modèle     : {GROQ_MODEL if api_key else 'regex'}\n")
        f.write("=" * 60 + "\n\n")
        f.write("\n".join(log_lines))

    print(f"\n  ✓ {args.out}")
    print(f"  ✓ {args.log}")
    print(f"\n{'='*62}")
    print(f"  ÉTAPE SUIVANTE :")
    print(f"    1. Ouvrir {args.out}")
    print(f"    2. Valider chaque règle (composantes + catégorie)")
    print(f"    3. python rebuild_validated_json.py")
    print(f"    4. python load_ground_truth.py --input output/aml_rules_complete_validated.json --all")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    main()
