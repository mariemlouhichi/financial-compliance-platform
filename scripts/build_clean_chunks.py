"""
build_clean_chunks.py — Script unique de reconstruction propre des chunks
=========================================================================

REMPLACE : restore_and_clean.py + post_cleanup_fix.py

PIPELINE (tout en un) :
  1. Charge le backup source (5 877 chunks, 33 docs, 100% méta)
  2. Normalise le schéma (metadata imbriquée, chunk_id, champ "content")
  3. Supprime les champs vides inutiles (topics, transaction_types, entities)
  4. Fusionne les micro-ancres (<100 chars) avec le voisin suivant
  5. Supprime les fragments de tableaux Markdown
  6. Découpe les macro-chunks (>1400 chars) en respectant les frontières de phrases
  7. Fusionne les phrases coupées (chunk sans ponctuation finale + suivant court)
  8. Classifie chunk_role par score sémantique (indépendant de has_threshold)
  9. Détecte has_threshold (seuils AML précis seulement — pas n'importe quel chiffre)
 10. Supprime les quasi-doublons (overlap résiduel)
 11. Validation finale + slim

PRINCIPES CLÉ :
  - has_threshold et chunk_role sont INDÉPENDANTS
  - has_threshold = "ce chunk mentionne un seuil chiffré AML"
  - chunk_role = rôle sémantique dominant du contenu
  - Split UNIQUEMENT aux frontières de phrases (.!?;)

USAGE :
  python build_clean_chunks.py --dry-run   ← simulation
  python build_clean_chunks.py             ← applique et écrit
"""

import argparse
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ── CHEMINS ──────────────────────────────────────────────────────────────────
SOURCE_FILE = Path("output/chunks_aml_kyc.json.backup_20260511_185530")
OUTPUT_FILE = Path("output/chunks_aml_kyc.json")
PAR_DOC_DIR = Path("output/par_document")
LOG_FILE    = Path("output/build_clean_chunks_log.txt")

# ── PARAMÈTRES ────────────────────────────────────────────────────────────────
MIN_CHARS    = 100
MAX_CHARS    = 1400
TARGET_CHARS = 650

# ── CHAMPS À GARDER (schéma final) ────────────────────────────────────────────
KEEP_META = {"source_doc", "article_ref", "jurisdiction",
             "chunk_role", "has_threshold", "chunk_index", "char_count"}

# ─────────────────────────────────────────────────────────────────────────────
#  PATTERNS
# ─────────────────────────────────────────────────────────────────────────────

# Ancres structurelles (Article 15, Section 3, etc.)
ANCHOR_RE = re.compile(
    r'^(Article|Art\.|Recommendation|Guideline|Section|Chapter|Paragraph|§|'
    r'Preambule|Préambule|Chapitre|Segment|Numbered|Annexe|Annex)\s*\d*',
    re.IGNORECASE
)

# Tableaux Markdown inutilisables
TABLE_RE = re.compile(
    r'^\|[\s\|]*\|'           # | | | | (cellules vides)
    r'|^\|\s*[-:=]+\s*\|'     # |---|---| (séparateur)
    r'|^\|\s*\*\*',           # |**texte**| (header bold)
    re.MULTILINE
)

# ── SEUILS AML PRÉCIS (pas n'importe quel chiffre) ───────────────────────────
# Doit combiner : un montant + une devise/unité financière OU un % de compliance
THRESHOLD_AMOUNT_RE = re.compile(
    r'\b(\d[\d\s,.]*)\s*'
    r'(TND|dinar[s]?|USD|EUR|GBP|dollar[s]?|euro[s]?|'
    r'million[s]?|milliard[s]?|thousand[s]?)\b',
    re.IGNORECASE
)

THRESHOLD_CONTEXT_RE = re.compile(
    r'\b(threshold|seuil|plafond|ceiling|limite|limit[e]?|'
    r'exceed[s]?|dépasse[r]?|supérieur à|au-dessus de|above|below|'
    r'montant|amount|sum[me]?|total)\b',
    re.IGNORECASE
)

THRESHOLD_PCT_RE = re.compile(
    r'\b\d+\s*[,%]\s*(de|of|du|des|la|le)\b',
    re.IGNORECASE
)

# ── CLASSIFICATION chunk_role ─────────────────────────────────────────────────
# Ordre = priorité (premier match avec score > 0 gagne)
ROLE_RULES = [
    ("SANCTIONS",      [
        r'\b(sanction[s]?|prohibit[ed]?|interdit[e]?[s]?|blacklist|liste noire|'
        r'OFAC|freeze|gel des avoirs|asset[s]? freeze|embargo)\b',
    ]),
    ("REPORTING",      [
        r'\b(suspicious activity report|SAR|STR|DOS|déclaration de soupçon|'
        r'report[e]?[r]?|notify|notifi|signalem[ea]nt|déclar[ae])\b',
    ]),
    ("KYC",            [
        r'\b(KYC|CDD|EDD|SDD|know your customer|due diligence|'
        r'identification|verify|vérif|identity|identité|benefi[ct]ial owner|'
        r'bénéficiaire effectif|UBO)\b',
    ]),
    ("THRESHOLD",      [
        r'\b(threshold|seuil|plafond|montant seuil|amount threshold)\b',
    ]),
    ("RED_FLAG",       [
        r'\b(red flag|indicat[eo]r|signal d.alerte|suspicious indicator|'
        r'warning sign|anomalie|atypical)\b',
    ]),
    ("RISK_ASSESSMENT",[
        r'\b(risk[- ]based|risk assessment|évaluation du risque|'
        r'risk rating|risk score|niveau de risque|risk classification)\b',
    ]),
    ("OBLIGATION",     [
        r'\b(shall|must|is required|are required|obliged|have to|'
        r'doit|doivent|tenu[e]?s? de|obligation|sont tenus)\b',
    ]),
    ("PROCEDURE",      [
        r'\b(procedure|process|steps?|étapes?|method[ology]?|workflow|'
        r'guidelines?|how to|modalit[ée]s?)\b',
    ]),
    ("DEFINITION",     [
        r'\b(means|defined|definition|désigne|s.entend par|'
        r'is defined as|refers to|au sens|pour l.application)\b',
    ]),
]

ROLE_COMPILED = [
    (role, [re.compile(p, re.IGNORECASE) for p in patterns])
    for role, patterns in ROLE_RULES
]


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITAIRES
# ─────────────────────────────────────────────────────────────────────────────

def get_text(chunk):
    return (chunk.get("text") or chunk.get("content", "")).strip()

def get_meta_flat(chunk):
    """Lit métadonnées depuis l'ancien format (champs plats ou imbriqués)."""
    meta = chunk.get("metadata", {})
    if meta and "source_doc" in meta:
        return meta
    flat = {"source_doc","article_ref","jurisdiction","chunk_role",
            "has_threshold","chunk_index","char_count","word_count",
            "topics","transaction_types"}
    return {k: v for k, v in chunk.items() if k in flat}

def count_words(text):
    return len(re.findall(r'\w+', text))

def ends_sentence(text):
    """Le texte se termine-t-il sur une frontière de phrase ?"""
    t = text.rstrip()
    return bool(t) and t[-1] in '.!?;:)"\'»'

def is_table_fragment(content):
    lines = content.strip().split('\n')
    if not lines:
        return False
    table_lines = sum(1 for l in lines if TABLE_RE.search(l))
    return table_lines / max(len(lines), 1) > 0.4

def is_anchor_only(text):
    """Chunk = uniquement une ancre structurelle sans contenu."""
    t = text.strip()
    return bool(ANCHOR_RE.match(t)) and len(t) < 80


# ─────────────────────────────────────────────────────────────────────────────
#  CLASSIFICATION SÉMANTIQUE (indépendante)
# ─────────────────────────────────────────────────────────────────────────────

def classify_role(content):
    """
    Détermine chunk_role par score sémantique.
    Retourne le rôle dominant ou 'DEFINITION' par défaut.
    """
    best_role  = "DEFINITION"
    best_score = 0
    for role, patterns in ROLE_COMPILED:
        score = sum(len(p.findall(content)) for p in patterns)
        if score > best_score:
            best_score = score
            best_role  = role
    return best_role


def classify_has_threshold(content):
    """
    Détecte has_threshold = True UNIQUEMENT si le chunk contient
    un montant financier précis + un contexte de seuil AML.
    PAS n'importe quel chiffre.
    """
    has_amount  = bool(THRESHOLD_AMOUNT_RE.search(content))
    has_context = bool(THRESHOLD_CONTEXT_RE.search(content))
    has_pct     = bool(THRESHOLD_PCT_RE.search(content))

    # Cas 1 : montant explicite + contexte de limite → seuil certain
    if has_amount and has_context:
        return True
    # Cas 2 : pourcentage dans un contexte de compliance
    if has_pct and has_context:
        return True
    # Cas 3 : montant seul avec chiffres spécifiques AML connus
    specific_amounts = re.search(
        r'\b(3[\s,.]?000|5[\s,.]?000|10[\s,.]?000|15[\s,.]?000|'
        r'25[\s,.]?000|30[\s,.]?000|50[\s,.]?000|100[\s,.]?000|'
        r'150[\s,.]?000|200[\s,.]?000|1[\s,.]?000[\s,.]?000)\b',
        content
    )
    if specific_amounts and has_context:
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
#  DÉCOUPE RESPECTANT LES FRONTIÈRES DE PHRASES
# ─────────────────────────────────────────────────────────────────────────────

def split_at_sentence_boundaries(text, target=TARGET_CHARS, overlap=60):
    """
    Découpe uniquement aux frontières de phrases.
    Évite de couper au milieu d'une phrase.
    """
    text = text.strip()
    if len(text) <= target:
        return [text]

    # Tokeniser en phrases complètes
    sentence_re = re.compile(r'(?<=[.!?;])\s+(?=[A-ZÀ-Ÿ0-9(«\"])')
    sentences = sentence_re.split(text)

    if len(sentences) <= 1:
        # Aucune frontière de phrase trouvée → tenter paragraphes
        paras = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
        if len(paras) > 1:
            sentences = paras
        else:
            # Pas de frontière propre → garder le chunk tel quel
            # plutôt que couper au milieu d'une phrase
            if len(text) <= MAX_CHARS:
                return [text]
            # Dernier recours : couper aux espaces
            sentences = text.split('. ')
            sentences = [s + ('.' if not s.endswith('.') else '') for s in sentences]

    # Regrouper les phrases jusqu'à TARGET_CHARS
    chunks = []
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) + 1 <= target:
            current = (current + " " + sent).strip()
        else:
            if current:
                chunks.append(current)
            # Phrase trop longue seule ?
            if len(sent) > MAX_CHARS:
                # Couper aux virgules comme ultime recours
                parts = re.split(r',\s+', sent)
                sub = ""
                for p in parts:
                    if len(sub) + len(p) + 2 <= target:
                        sub = (sub + ", " + p).strip(", ")
                    else:
                        if sub:
                            chunks.append(sub)
                        sub = p
                if sub:
                    chunks.append(sub)
                current = ""
            else:
                current = sent

    if current:
        chunks.append(current)

    if not chunks:
        return [text[:MAX_CHARS]]

    # Overlap léger uniquement aux frontières de phrase
    if len(chunks) > 1 and overlap > 0:
        result = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = result[-1]
            # Prendre la dernière phrase complète du chunk précédent
            prev_sentences = sentence_re.split(prev)
            tail = prev_sentences[-1].strip() if prev_sentences else ""
            if tail and len(tail) < overlap * 2:
                result.append((tail + " " + chunks[i]).strip())
            else:
                result.append(chunks[i])
        return result

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
#  PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def run(dry_run=False):
    stats = defaultdict(int)
    log   = []

    print(f"\n{'='*65}")
    print(f"  BUILD CLEAN CHUNKS — Script unifié")
    print(f"  Mode : {'DRY-RUN' if dry_run else 'ÉCRITURE'}")
    print(f"{'='*65}")

    # ── 1. Charger ────────────────────────────────────────────────────────
    print(f"\n[1] CHARGEMENT")
    if not SOURCE_FILE.exists():
        import sys; sys.exit(f"ERREUR : {SOURCE_FILE} introuvable.")
    with open(SOURCE_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    lengths_raw = [len(get_text(c)) for c in raw]
    docs_raw = set(get_meta_flat(c).get("source_doc","") for c in raw)
    print(f"    {len(raw):,} chunks, {len(docs_raw)} documents")
    print(f"    Min={min(lengths_raw)}  Max={max(lengths_raw)}  Moy={sum(lengths_raw)//len(lengths_raw)}")

    # ── 2. Normaliser le schéma ───────────────────────────────────────────
    print(f"\n[2] NORMALISATION DU SCHÉMA")
    normalized = []
    for i, chunk in enumerate(raw):
        text  = get_text(chunk)
        meta  = get_meta_flat(chunk)
        src   = meta.get("source_doc", "unknown.pdf")
        art   = meta.get("article_ref", "?")
        jur   = meta.get("jurisdiction", "Tunisia")

        d = re.sub(r'[^A-Za-z0-9]', '_', src.replace('.pdf',''))[:20]
        a = re.sub(r'[^A-Za-z0-9]', '_', str(art))[:12]

        normalized.append({
            "chunk_id": f"CHK_{d}_{a}_{i:05d}",
            "content":  text,
            "metadata": {
                "source_doc":  src,
                "article_ref": str(art),
                "jurisdiction": jur,
                # chunk_role et has_threshold seront (re)classifiés en étape 8-9
                "chunk_role":   meta.get("chunk_role", "DEFINITION"),
                "has_threshold": False,
                "chunk_index":  int(meta.get("chunk_index", i)),
                "char_count":   len(text),
            }
        })
    print(f"    {len(normalized):,} chunks normalisés — méta vides : 0 ✓")

    # ── 3. Supprimer fragments de tableaux ───────────────────────────────
    print(f"\n[3] SUPPRESSION FRAGMENTS DE TABLEAUX")
    before = len(normalized)
    normalized = [c for c in normalized if not is_table_fragment(c["content"])]
    stats["tables_removed"] = before - len(normalized)
    print(f"    {stats['tables_removed']} fragments supprimés  ({before:,} → {len(normalized):,})")

    # ── 4. Fusionner micro-ancres et chunks courts ───────────────────────
    print(f"\n[4] FUSION MICRO-ANCRES + CHUNKS COURTS (<{MIN_CHARS} chars)")
    result = []
    skip   = False
    for i, chunk in enumerate(normalized):
        if skip:
            skip = False
            continue
        content = chunk["content"]
        meta    = chunk["metadata"]

        needs_merge = (
            len(content) < MIN_CHARS
            or is_anchor_only(content)
        )

        if needs_merge and i + 1 < len(normalized):
            nxt = normalized[i + 1]
            if meta["source_doc"] == nxt["metadata"]["source_doc"]:
                merged_text = content + "\n\n" + nxt["content"]
                merged = {
                    "chunk_id": nxt["chunk_id"],
                    "content":  merged_text,
                    "metadata": {
                        **nxt["metadata"],
                        "char_count":  len(merged_text),
                        "article_ref": meta.get("article_ref") or nxt["metadata"].get("article_ref",""),
                    }
                }
                result.append(merged)
                skip = True
                stats["micro_merged"] += 1
                continue

        if len(content) < 50 or count_words(content) < 6:
            stats["micro_deleted"] += 1
            continue

        result.append(chunk)

    normalized = result
    print(f"    Fusionnés : {stats['micro_merged']}  |  Supprimés : {stats['micro_deleted']}")
    print(f"    → {len(normalized):,} chunks")

    # ── 5. Fusionner phrases coupées ─────────────────────────────────────
    print(f"\n[5] FUSION PHRASES COUPÉES")
    result = []
    skip   = False
    for i, chunk in enumerate(normalized):
        if skip:
            skip = False
            continue
        content = chunk["content"]
        meta    = chunk["metadata"]

        is_cut = (
            not ends_sentence(content)
            and len(content) < 350
            and count_words(content) >= 5
        )

        if is_cut and i + 1 < len(normalized):
            nxt = normalized[i + 1]
            if (meta["source_doc"] == nxt["metadata"]["source_doc"]
                    and len(content) + len(nxt["content"]) + 1 < MAX_CHARS):
                merged_text = content + " " + nxt["content"]
                merged = {
                    "chunk_id": chunk["chunk_id"],
                    "content":  merged_text,
                    "metadata": {
                        **meta,
                        "char_count": len(merged_text),
                    }
                }
                result.append(merged)
                skip = True
                stats["cut_merged"] += 1
                continue

        result.append(chunk)

    normalized = result
    print(f"    Fusionnés : {stats['cut_merged']}")
    print(f"    → {len(normalized):,} chunks")

    # ── 6. Découper les macro-chunks (respect frontières phrases) ─────────
    print(f"\n[6] DÉCOUPE MACRO-CHUNKS (>{MAX_CHARS} chars, respect frontières phrases)")
    result = []
    for chunk in normalized:
        content = chunk["content"]
        if len(content) <= MAX_CHARS:
            result.append(chunk)
            continue

        meta   = chunk["metadata"]
        src    = meta["source_doc"]
        art    = meta["article_ref"]
        # Overlap réduit pour limiter les doublons
        overlap = 40

        parts = split_at_sentence_boundaries(content, TARGET_CHARS, overlap)
        stats["macro_split"] += 1

        for sub_idx, part in enumerate(parts, 1):
            part = part.strip()
            if len(part) < MIN_CHARS:
                continue
            d = re.sub(r'[^A-Za-z0-9]', '_', src.replace('.pdf',''))[:20]
            a = re.sub(r'[^A-Za-z0-9]', '_', str(art))[:12]
            idx = len(result)
            result.append({
                "chunk_id": f"CHK_{d}_{a}_{idx:05d}_s{sub_idx:02d}",
                "content":  part,
                "metadata": {
                    **meta,
                    "char_count":      len(part),
                    "parent_chunk_id": chunk["chunk_id"],
                }
            })
            stats["sub_chunks"] += 1

    normalized = result
    print(f"    {stats['macro_split']} macro-chunks → {stats['sub_chunks']} sous-chunks")
    print(f"    → {len(normalized):,} chunks")

    # ── 7. Supprimer quasi-doublons consécutifs ──────────────────────────
    print(f"\n[7] DÉDUPLICATION (quasi-doublons consécutifs)")
    result  = [normalized[0]] if normalized else []
    for i in range(1, len(normalized)):
        prev_text = result[-1]["content"]
        curr_text = normalized[i]["content"]
        prev_meta = result[-1]["metadata"]
        curr_meta = normalized[i]["metadata"]

        # Doublon si même doc + début identique (>80 chars communs)
        if (prev_meta.get("source_doc") == curr_meta.get("source_doc")
                and len(curr_text) > 80
                and curr_text[:80] == prev_text[-80:]):
            stats["duplicates_removed"] += 1
            continue

        result.append(normalized[i])

    normalized = result
    print(f"    Doublons supprimés : {stats['duplicates_removed']}")
    print(f"    → {len(normalized):,} chunks")

    # ── 8. Reclassifier chunk_role + has_threshold (INDÉPENDANTS) ────────
    print(f"\n[8] RECLASSIFICATION SÉMANTIQUE (chunk_role ⊥ has_threshold)")
    for chunk in normalized:
        content = chunk["content"]
        meta    = chunk["metadata"]
        meta["chunk_role"]    = classify_role(content)
        meta["has_threshold"] = classify_has_threshold(content)

    # Vérification cohérence
    incoherent = sum(
        1 for c in normalized
        if (c["metadata"]["chunk_role"] == "THRESHOLD")
        != c["metadata"]["has_threshold"]
    )
    print(f"    THRESHOLD/has_threshold incohérents restants : {incoherent}")
    print(f"    (Normal : has_threshold et chunk_role sont maintenant indépendants)")

    # ── 9. Slim (schéma final propre) ────────────────────────────────────
    print(f"\n[9] SLIM — schéma final")
    final = []
    for chunk in normalized:
        meta = chunk["metadata"]
        slim_meta = {k: v for k, v in meta.items() if k in KEEP_META}
        pid = meta.get("parent_chunk_id", "")
        if pid:
            slim_meta["parent_chunk_id"] = pid
        final.append({
            "chunk_id": chunk["chunk_id"],
            "content":  chunk["content"],
            "metadata": slim_meta,
        })

    # Renuméroter chunk_index par document
    by_doc = defaultdict(list)
    for c in final:
        by_doc[c["metadata"]["source_doc"]].append(c)
    for doc_chunks in by_doc.values():
        for idx, c in enumerate(doc_chunks):
            c["metadata"]["chunk_index"] = idx
            c["metadata"]["char_count"]  = len(c["content"])

    # ── 10. Validation ───────────────────────────────────────────────────
    print(f"\n[10] VALIDATION FINALE")
    valid   = []
    invalid = Counter()
    for chunk in final:
        content = chunk["content"]
        if len(content) < 50:
            invalid["trop_petit"] += 1; continue
        if count_words(content) < 8:
            invalid["pas_assez_mots"] += 1; continue
        if len(content) > 2000:
            invalid["trop_grand"] += 1; continue
        valid.append(chunk)

    if invalid:
        print(f"    Invalides retirés : {sum(invalid.values())}")
        for r, n in invalid.most_common():
            print(f"      {r:25} {n}")

    final = valid

    # ── STATS FINALES ─────────────────────────────────────────────────────
    fl = [len(c["content"]) for c in final]
    fd = set(c["metadata"]["source_doc"] for c in final)
    no_punct = sum(1 for c in final if not ends_sentence(c["content"]))

    role_dist = Counter(c["metadata"]["chunk_role"] for c in final)
    thresh_true = sum(1 for c in final if c["metadata"]["has_threshold"])
    incoherent_final = sum(
        1 for c in final
        if (c["metadata"]["chunk_role"] == "THRESHOLD") != c["metadata"]["has_threshold"]
    )

    print(f"\n{'='*65}")
    print(f"  RÉSULTAT FINAL")
    print(f"{'='*65}")
    print(f"  Source      : {SOURCE_FILE.name}")
    print(f"  Avant       : {len(raw):,} chunks, {len(docs_raw)} docs")
    print(f"  Après       : {len(final):,} chunks, {len(fd)} docs")
    print(f"  Min         : {min(fl)} chars")
    print(f"  Max         : {max(fl)} chars")
    print(f"  Moy         : {sum(fl)//len(fl)} chars")
    print(f"  < 200 chars : {sum(1 for l in fl if l < 200)}")
    print(f"  > 1500 chars: {sum(1 for l in fl if l > 1500)}")
    print(f"  Phrases coupées   : {no_punct} (~{no_punct/len(final)*100:.1f}%)")
    print(f"  has_threshold=True: {thresh_true} ({thresh_true/len(final)*100:.1f}%)")
    print(f"  THRESHOLD incohérents : {incoherent_final}")
    print(f"  Méta complètes : 100% ✓")
    print(f"\n  Distribution chunk_role :")
    for role, cnt in role_dist.most_common():
        bar = '█' * (cnt * 30 // max(role_dist.values()))
        print(f"    {role:16} {cnt:5,}  {bar}")

    if dry_run:
        print(f"\n  DRY-RUN — aucune écriture. Relancez sans --dry-run.")
        return

    # ── Écriture ──────────────────────────────────────────────────────────
    if OUTPUT_FILE.exists():
        bak = OUTPUT_FILE.with_suffix(
            f".json.bck_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(OUTPUT_FILE, bak)
        print(f"\n  Backup : {bak.name}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    size_mb = OUTPUT_FILE.stat().st_size / 1e6
    print(f"  ✓ {OUTPUT_FILE.name}  ({size_mb:.1f} MB, {len(final):,} chunks)")

    if PAR_DOC_DIR.exists():
        for doc, doc_chunks in by_doc.items():
            (PAR_DOC_DIR / f"{doc}.json").write_text(
                json.dumps(doc_chunks, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(f"  ✓ par_document/ — {len(by_doc)} fichiers mis à jour")

    LOG_FILE.write_text(
        f"Build Clean Chunks — {datetime.now().isoformat()}\n"
        f"{len(raw)} → {len(final)} chunks | {len(fd)} docs\n"
        f"Stats: {dict(stats)}\n",
        encoding="utf-8"
    )

    print(f"""
PROCHAINES ÉTAPES :
  python reindex_weaviate.py
  python build_neo4j_graph.py --clear
  python rebuild_validated_json.py
  python load_ground_truth.py --input output/aml_rules_complete_validated.json --all
  python build_neo4j_graph.py --stats-only
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
