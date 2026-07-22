"""
cleanup_chunks_v2.py — Nettoyage post-chunking intelligent
==========================================================

PROBLEME IDENTIFIE :
  5 877 chunks dont :
    Min 2 chars   | Max 19 110 chars
    < 200 chars : 1 305  (22%)  -> micro-chunks (titres, ancres, vides)
    > 1500 chars:   417  ( 7%)  -> macro-chunks (multi-regles melangees)

STRATEGIE :
  Micro-chunks :
    - Si c est une ANCRE (Article 15, Section 3...) -> FUSIONNER avec le chunk suivant
    - Si c est vide ou inutile -> SUPPRIMER
  Macro-chunks :
    - Decouper intelligemment (paragraphes -> phrases -> force)
    - Option --semantic : utiliser BGE pour decoupage semantique
  Overlap :
    - Parametre par type de document (loi=100, circulaire=120, rapport=60)

PATTERNS D ANCRE :
  Recuperes du pipeline Phase 2 (pattern_scanner.py) :
    Article N, Art. N, Recommendation N, Guideline N, Section N,
    Chapter N, Paragraph N, N. texte, N.N texte, (N) texte,
    Preambule, Segment N, Numbered N

USAGE :
  python cleanup_chunks_v2.py --dry-run          <- simule sans ecrire
  python cleanup_chunks_v2.py                    <- mode standard
  python cleanup_chunks_v2.py --semantic         <- BGE pour macro-chunks
  python cleanup_chunks_v2.py --min 100 --max 1200

APRES CE SCRIPT :
  1. python reindex_weaviate.py
  2. python build_neo4j_graph.py --clear
  3. python load_ground_truth.py --input output/aml_rules_complete_validated.json --all
"""

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


# ============================================================================
#  PARAMETRES
# ============================================================================

MIN_CHARS     = 80
MAX_CHARS     = 1400
TARGET_CHARS  = 700

CHUNKS_FILE   = "output/chunks_aml_kyc.json"
PAR_DOC_DIR   = "output/par_document"
LOG_FILE      = "output/cleanup_log.txt"

# ============================================================================
#  ANCHOR PATTERNS (du pipeline Phase 2 — pattern_scanner.py)
# ============================================================================

ANCHOR_PATTERNS = [
    # Format : (nom, regex)
    # Couvre TOUS les types d ancres detectes dans le corpus
    ("Article N",        r"^Article\s+\d+"),
    ("Art. N",           r"^Art\.\s*\d+"),
    ("Recommendation N", r"^Recommendation\s+\d+"),
    ("Guideline N",      r"^Guideline\s+\d+"),
    ("Section N",        r"^Section\s+\d+"),
    ("Chapter N",        r"^Chapter\s+\d+"),
    ("Paragraph N",      r"^Paragraph\s+\d+"),
    ("Para symbol",      r"^[§]\s*\d+"),
    ("N. texte",         r"^\d+\.\s{1,4}[A-Z]"),
    ("N.N texte",        r"^\d+\.\d+\s{1,4}[A-Z]"),
    ("N.N.N texte",      r"^\d+\.\d+\.\d+\s{1,4}[A-Z]"),
    ("(N) texte",        r"^\(\d+\)\s+[A-Z]"),
    ("Preambule",        r"^Pr[eé]ambule"),
    ("Segment N",        r"^Segment\s+\d+"),
    ("Numbered N",       r"^Numbered\s+\d+"),
    # Variantes arabes/francaises possibles
    ("Chapitre N",       r"^Chapitre\s+\d+"),
]

# ============================================================================
#  TYPE DE DOCUMENT -> OVERLAP
# ============================================================================

DOC_TYPE_PATTERNS = {
    "law":            [r"(?i)loi",     r"(?i)law"],
    "circular":       [r"(?i)circulaire", r"(?i)circular"],
    "directive":      [r"(?i)directive",  r"(?i)amld"],
    "recommendation": [r"(?i)fatf",       r"(?i)recommendation"],
    "guidance":       [r"(?i)guidance",   r"(?i)guide", r"(?i)principles"],
    "report":         [r"(?i)rapport",    r"(?i)report"],
    "regulation":     [r"(?i)reglement",  r"(?i)regulation", r"(?i)reglementation"],
}

OVERLAP_BY_TYPE = {
    "law":            100,   # Lois : contexte inter-articles important
    "circular":       120,   # Circulaires BCT : tres denses
    "directive":      100,   # Directives EU
    "recommendation":  80,   # FATF
    "guidance":        80,   # Guides divers
    "report":          60,   # Rapports annuels : moins denses
    "regulation":     100,   # Reglementations
    "other":           80,
}

# ============================================================================
#  MOTS-CLES METIER (validation post-cleanup)
# ============================================================================

BUSINESS_KEYWORDS = [
    "bank", "financial", "institution", "customer", "client",
    "transaction", "amount", "report", "declaration", "sanction",
    "obligation", "due diligence", "risk", "money laundering",
    "terrorist", "financing", "suspicious", "threshold", "seuil",
    "vigilance", "compliance", "audit", "fund", "account",
    "transfer", "wire", "swift", "mandat", "deposit",
    "kyc", "aml", "cft", "edd", "cdd", "str", "dos", "pep",
    "ctaf", "bct", "fatf", "ofac",
]


# ============================================================================
#  UTILITAIRES
# ============================================================================

def get_meta(chunk):
    return chunk.get("metadata", chunk)


def get_content(chunk):
    return (chunk.get("content", "") or chunk.get("text", "")).strip()


def count_words(text):
    return len(re.findall(r'\w+', text))


def detect_doc_type(doc_name):
    for dtype, patterns in DOC_TYPE_PATTERNS.items():
        for p in patterns:
            if re.search(p, doc_name):
                return dtype
    return "other"


def get_overlap(source_doc):
    dtype = detect_doc_type(source_doc)
    return OVERLAP_BY_TYPE.get(dtype, 80)


def is_anchor(text):
    """Detecte si le texte est une ancre structurelle (Article 15, Section 3...)."""
    text = text.strip()
    for name, pattern in ANCHOR_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return True, name
    return False, None


def classify_micro(chunk):
    """
    Classifie un micro-chunk :
      'anchor'  -> fusionner avec le voisin suivant
      'empty'   -> supprimer
      'normal'  -> garder tel quel (faux positif)
    """
    content = get_content(chunk)

    if len(content) >= MIN_CHARS and count_words(content) >= 15:
        return "normal"

    # Verifier si c est une ancre
    is_anc, anc_type = is_anchor(content)
    if is_anc:
        return "anchor"

    # Verifier si le contenu a au moins quelques mots significatifs
    if count_words(content) < 5:
        return "empty"

    # Chunk court mais avec du contenu (ex: phrase isolee)
    if len(content) < 30:
        return "empty"

    return "anchor"  # En cas de doute, fusionner plutot que supprimer


# ============================================================================
#  FUSION DES MICRO-CHUNKS (ancres)
# ============================================================================

def merge_anchors_with_neighbors(chunks):
    """
    Fusionne les micro-chunks ancres avec le chunk SUIVANT du meme document.

    Logique :
      chunk[i] = "Article 15"          (ancre, 10 chars)
      chunk[i+1] = "The bank must..."  (contenu, 400 chars)
      -> resultat = "Article 15\n\nThe bank must..."  (410 chars)

    Le chunk fusionné hérite du chunk_id du contenu (le plus long),
    et l article_ref de l ancre (car c est la reference structurelle).
    """
    if not chunks:
        return chunks

    result = []
    skip_next = False

    for i, chunk in enumerate(chunks):
        if skip_next:
            skip_next = False
            continue

        content = get_content(chunk)
        cls = classify_micro(chunk)

        if cls == "empty":
            # Vrai micro vide -> supprimer
            continue

        if cls == "anchor" and len(content) < MIN_CHARS:
            # C est une ancre -> fusionner avec le chunk suivant
            if i + 1 < len(chunks):
                next_chunk = chunks[i + 1]
                next_meta  = get_meta(next_chunk)
                meta       = get_meta(chunk)

                # Verifier que c est le meme document
                if meta.get("source_doc") == next_meta.get("source_doc"):
                    # Fusionner
                    merged_content = content + "\n\n" + get_content(next_chunk)
                    merged_chunk = dict(next_chunk)  # copie du suivant
                    merged_chunk["content"] = merged_content
                    if "text" in merged_chunk:
                        merged_chunk["text"] = merged_content

                    # Mettre a jour les metadonnees
                    merged_meta = get_meta(merged_chunk)
                    # Garder l article_ref de l ancre si elle est plus specifique
                    anchor_ref = meta.get("article_ref", "")
                    if anchor_ref and len(anchor_ref) > 2:
                        merged_meta["article_ref"] = anchor_ref
                    merged_meta["char_count"]  = len(merged_content)
                    merged_meta["word_count"]  = count_words(merged_content)
                    merged_meta["merged_from_anchor"] = True
                    merged_meta["anchor_text"] = content

                    result.append(merged_chunk)
                    skip_next = True
                    continue
                else:
                    # Document different -> supprimer l ancre isolee
                    continue
            else:
                # Dernier chunk = ancre isolee -> supprimer
                continue

        # Chunk normal -> garder
        result.append(chunk)

    return result


# ============================================================================
#  DECOUPE DES MACRO-CHUNKS
# ============================================================================

def split_text(text, target=TARGET_CHARS, overlap=80):
    """Decoupe un texte long en sous-chunks coherents."""
    text = text.strip()
    if len(text) <= target:
        return [text]

    # Strategie 1 : paragraphes
    paras = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    if len(paras) >= 2:
        chunks, current = [], ""
        for para in paras:
            if len(current) + len(para) + 2 <= target:
                current = (current + "\n\n" + para).strip()
            else:
                if current:
                    chunks.append(current)
                if len(para) > target:
                    chunks.extend(_split_by_sentences(para, target))
                else:
                    current = para
        if current:
            chunks.append(current)
        if chunks and all(len(c) <= MAX_CHARS for c in chunks):
            return _apply_overlap(chunks, overlap)

    # Strategie 2 : phrases
    return _apply_overlap(_split_by_sentences(text, target), overlap)


def _split_by_sentences(text, target):
    """Decoupe en phrases."""
    pat = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9\(])')
    sents = pat.split(text)
    if len(sents) <= 1:
        sents = re.split(r'[;]\s+', text)
    if len(sents) <= 1:
        step = max(1, target - 80)
        return [text[i:i+target] for i in range(0, len(text), step)]

    chunks, current = [], ""
    for sent in sents:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) + 1 <= target:
            current = (current + " " + sent).strip()
        else:
            if current:
                chunks.append(current)
            if len(sent) > target:
                step = max(1, target - 80)
                for i in range(0, len(sent), step):
                    chunks.append(sent[i:i+target])
                current = ""
            else:
                current = sent
    if current:
        chunks.append(current)
    return chunks if chunks else [text[:target]]


def _apply_overlap(chunks, overlap):
    """Ajoute un chevauchement contextuel entre chunks consecutifs."""
    if len(chunks) <= 1 or overlap <= 0:
        return chunks
    result = [chunks[0]]
    for i in range(1, len(chunks)):
        tail = result[-1][-overlap:].strip()
        result.append((tail + " " + chunks[i]).strip())
    return result


def split_macro(chunk, global_idx, overlap=80):
    """Decoupe un macro-chunk et herite les metadonnees."""
    content    = get_content(chunk)
    meta       = get_meta(chunk)
    source_doc = meta.get("source_doc", chunk.get("source_doc", "unknown.pdf"))
    article_ref= meta.get("article_ref", chunk.get("article_ref", "?"))
    parts      = split_text(content, TARGET_CHARS, overlap)

    result = []
    for sub_idx, part in enumerate(parts, 1):
        part = part.strip()
        if len(part) < MIN_CHARS:
            continue
        doc_clean = re.sub(r'[^A-Za-z0-9]', '_', source_doc.replace('.pdf',''))[:20]
        art_clean = re.sub(r'[^A-Za-z0-9]', '_', str(article_ref))[:12]
        new_id = f"CHK_{doc_clean}_{art_clean}_{global_idx:05d}_s{sub_idx:02d}"

        new_chunk = {
            "chunk_id": new_id,
            "content":  part,
            "metadata": {
                **meta,
                "char_count":        len(part),
                "word_count":        count_words(part),
                "is_sub_chunk":      True,
                "parent_chunk_id":   chunk.get("chunk_id", ""),
                "sub_chunk_index":   sub_idx,
                "sub_chunk_total":   len(parts),
                "cleanup_version":   "v2",
            }
        }
        result.append(new_chunk)
    return result


# ============================================================================
#  DECOUPE SEMANTIQUE OPTIONNELLE (BGE)
# ============================================================================

def split_macro_semantic(chunk, global_idx, embed_fn, overlap=80, threshold=0.45):
    """
    Decoupe un macro-chunk en utilisant BGE pour detecter les ruptures semantiques.
    Fallback sur split_macro() si BGE n est pas disponible.
    """
    content = get_content(chunk)
    # Decouper en phrases
    sentences = re.split(r'(?<=[.!?])\s+', content)
    if len(sentences) < 3:
        return split_macro(chunk, global_idx, overlap)

    try:
        import numpy as np
        embeddings = embed_fn(sentences)
        # Calculer la similarite cosinus entre phrases consecutives
        similarities = []
        for i in range(len(embeddings) - 1):
            sim = float(np.dot(embeddings[i], embeddings[i+1]) /
                       (np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[i+1]) + 1e-8))
            similarities.append(sim)

        # Trouver les points de rupture (similarite < seuil adaptatif)
        if similarities:
            p25 = float(np.percentile(similarities, 25))
            break_threshold = min(threshold, p25)
        else:
            break_threshold = threshold

        # Regrouper les phrases entre les points de rupture
        groups, current_group = [], [sentences[0]]
        for i, sim in enumerate(similarities):
            if sim < break_threshold and len(" ".join(current_group)) >= MIN_CHARS:
                groups.append(" ".join(current_group))
                current_group = [sentences[i + 1]]
            else:
                current_group.append(sentences[i + 1])
        if current_group:
            groups.append(" ".join(current_group))

        # Verifier les tailles et re-decouper si necessaire
        final_parts = []
        for group in groups:
            if len(group) > MAX_CHARS:
                final_parts.extend(split_text(group, TARGET_CHARS, overlap))
            elif len(group) >= MIN_CHARS:
                final_parts.append(group)

        if not final_parts:
            return split_macro(chunk, global_idx, overlap)

        # Construire les sous-chunks
        meta       = get_meta(chunk)
        source_doc = meta.get("source_doc", "unknown.pdf")
        article_ref= meta.get("article_ref", "?")
        result = []
        for sub_idx, part in enumerate(final_parts, 1):
            doc_clean = re.sub(r'[^A-Za-z0-9]', '_', source_doc.replace('.pdf',''))[:20]
            art_clean = re.sub(r'[^A-Za-z0-9]', '_', str(article_ref))[:12]
            new_id = f"CHK_{doc_clean}_{art_clean}_{global_idx:05d}_s{sub_idx:02d}"
            result.append({
                "chunk_id": new_id,
                "content":  part.strip(),
                "metadata": {
                    **meta,
                    "char_count":      len(part.strip()),
                    "word_count":      count_words(part.strip()),
                    "is_sub_chunk":    True,
                    "parent_chunk_id": chunk.get("chunk_id", ""),
                    "sub_chunk_index": sub_idx,
                    "sub_chunk_total": len(final_parts),
                    "cleanup_version": "v2_semantic",
                }
            })
        return result

    except Exception as e:
        print(f"      BGE split echoue: {e} -> fallback textuel")
        return split_macro(chunk, global_idx, overlap)


# ============================================================================
#  VALIDATION POST-CLEANUP
# ============================================================================

def validate_chunk(chunk):
    """
    Verifie qu un chunk est exploitable pour le RAG.
    Retourne (is_valid, reason).
    """
    content = get_content(chunk)

    if len(content) < 50:
        return False, "trop_petit"
    if len(content) > 2000:
        return False, "trop_grand"

    words = re.findall(r'\w+', content)
    if len(words) < 10:
        return False, "pas_assez_mots"

    content_lower = content.lower()
    has_keyword = any(kw in content_lower for kw in BUSINESS_KEYWORDS)
    if not has_keyword:
        return False, "aucun_mot_cle_metier"

    return True, "ok"


# ============================================================================
#  PIPELINE PRINCIPAL
# ============================================================================

def run_cleanup(chunks_path, par_doc_dir, dry_run=False, use_semantic=False):
    log_lines = [
        f"Cleanup demarré : {datetime.now().isoformat()}",
        f"Parametres : MIN={MIN_CHARS} MAX={MAX_CHARS} TARGET={TARGET_CHARS}",
        f"Mode : {'DRY-RUN' if dry_run else 'SEMANTIC' if use_semantic else 'TEXTUEL'}",
        "=" * 60, ""
    ]

    print(f"\n{'='*62}")
    print(f"  NETTOYAGE POST-CHUNKING v2")
    print(f"  MIN={MIN_CHARS}  MAX={MAX_CHARS}  TARGET={TARGET_CHARS}")
    print(f"  Mode : {'DRY-RUN' if dry_run else 'SEMANTIC (BGE)' if use_semantic else 'TEXTUEL'}")
    print(f"{'='*62}\n")

    # ── 1. Charger ────────────────────────────────────────────────────────────
    print(f"[1] Chargement : {chunks_path}")
    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    total_before = len(chunks)
    lengths = [len(get_content(c)) for c in chunks]
    print(f"    {total_before:,} chunks charges")
    print(f"    Min={min(lengths)}  Max={max(lengths)}  Moy={sum(lengths)//len(lengths)}")

    # ── 2. Classifier chaque chunk ────────────────────────────────────────────
    print(f"\n[2] Classification des chunks")
    micro_anchors = []
    micro_empties = []
    macro_chunks  = []
    normal_chunks = []

    for i, chunk in enumerate(chunks):
        content = get_content(chunk)
        if len(content) > MAX_CHARS:
            macro_chunks.append((i, chunk))
        elif len(content) < MIN_CHARS or count_words(content) < 15:
            cls = classify_micro(chunk)
            if cls == "anchor":
                micro_anchors.append((i, chunk))
            elif cls == "empty":
                micro_empties.append((i, chunk))
            else:
                normal_chunks.append((i, chunk))
        else:
            normal_chunks.append((i, chunk))

    print(f"    Normal            : {len(normal_chunks):,}")
    print(f"    Micro-ANCRES      : {len(micro_anchors):,}  (seront FUSIONNEES avec voisin)")
    print(f"    Micro-VIDES       : {len(micro_empties):,}  (seront SUPPRIMEES)")
    print(f"    Macro (>{MAX_CHARS} chars) : {len(macro_chunks):,}  (seront DECOUPEES)")

    # Exemples d ancres detectees
    if micro_anchors:
        print(f"\n    Exemples d'ancres detectees :")
        for _, ch in micro_anchors[:5]:
            c = get_content(ch)[:60]
            is_anc, anc_type = is_anchor(get_content(ch))
            print(f"      [{str(anc_type or 'UNKNOWN'):20}] \"{c}\"")

    # Exemples de vides
    if micro_empties:
        print(f"\n    Exemples de chunks vides :")
        for _, ch in micro_empties[:5]:
            c = get_content(ch)[:60]
            print(f"      \"{c}\"")

    # Documents les plus impactes
    macro_by_doc = Counter(get_meta(c).get("source_doc","?") for _, c in macro_chunks)
    anchor_by_doc = Counter(get_meta(c).get("source_doc","?") for _, c in micro_anchors)
    empty_by_doc = Counter(get_meta(c).get("source_doc","?") for _, c in micro_empties)

    print(f"\n    Documents avec le plus de macro-chunks :")
    for doc, cnt in macro_by_doc.most_common(5):
        dtype = detect_doc_type(doc)
        ovlp  = OVERLAP_BY_TYPE.get(dtype, 80)
        print(f"      {doc[:45]:45} {cnt:>4}  (type={dtype}, overlap={ovlp})")

    log_lines.append(f"Classification : {len(normal_chunks)} normal, "
                     f"{len(micro_anchors)} ancres, {len(micro_empties)} vides, "
                     f"{len(macro_chunks)} macro")

    # ── DRY-RUN ───────────────────────────────────────────────────────────────
    if dry_run:
        n_from_macro = sum(
            max(1, len(split_text(get_content(c), TARGET_CHARS,
                                  get_overlap(get_meta(c).get("source_doc","")))))
            for _, c in macro_chunks
        )
        est_total = len(normal_chunks) + len(micro_anchors) + n_from_macro
        print(f"\n{'='*62}")
        print(f"  DRY-RUN : Estimation du resultat")
        print(f"{'='*62}")
        print(f"  Ancres fusionnees avec voisins : ~{len(micro_anchors)}")
        print(f"  Vides supprimes               : -{len(micro_empties)}")
        print(f"  Macro decoupes                : {len(macro_chunks)} -> ~{n_from_macro}")
        print(f"  Total estime                  : ~{est_total:,}")
        print(f"\n  Relance sans --dry-run pour appliquer.")
        return

    # ── 3. Backup ─────────────────────────────────────────────────────────────
    backup = chunks_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(chunks_path, backup)
    print(f"\n[3] Backup : {backup}")

    # ── 4. Charger BGE si mode semantique ─────────────────────────────────────
    embed_fn = None
    if use_semantic:
        print(f"\n[3.5] Chargement du modele BGE pour decoupe semantique...")
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("BAAI/bge-base-en-v1.5")
            def embed_fn(sentences):
                return model.encode(sentences, show_progress_bar=False, normalize_embeddings=True)
            print(f"      BGE charge (768 dims)")
        except ImportError:
            print(f"      sentence-transformers non installe -> fallback textuel")
            use_semantic = False

    # ── 5. Traitement — fusionner ancres + supprimer vides ────────────────────
    print(f"\n[4] Fusion des micro-ancres avec voisins...")
    # On travaille sur la liste complete, dans l ordre original
    merged = merge_anchors_with_neighbors(chunks)
    anchors_merged = total_before - len(merged) + len(micro_empties)
    print(f"    {total_before} -> {len(merged)} chunks (ancres fusionnees)")

    # ── 6. Traitement — decouper les macro-chunks ─────────────────────────────
    print(f"\n[5] Decoupe des macro-chunks...")
    final = []
    macro_split_count  = 0
    new_sub_count      = 0

    for chunk in merged:
        content = get_content(chunk)
        if len(content) > MAX_CHARS:
            meta       = get_meta(chunk)
            source_doc = meta.get("source_doc", "")
            overlap    = get_overlap(source_doc)
            idx        = len(final)

            if use_semantic and embed_fn:
                subs = split_macro_semantic(chunk, idx, embed_fn, overlap)
            else:
                subs = split_macro(chunk, idx, overlap)

            if subs:
                final.extend(subs)
                macro_split_count += 1
                new_sub_count     += len(subs)
                log_lines.append(f"SPLIT {chunk.get('chunk_id','')} "
                                 f"({len(content)} chars) -> {len(subs)} sous-chunks "
                                 f"(doc={source_doc}, overlap={overlap})")
            else:
                final.append(chunk)
        else:
            final.append(chunk)

    print(f"    Macro decoupes : {macro_split_count} -> {new_sub_count} sous-chunks")

    # ── 7. Validation post-cleanup ────────────────────────────────────────────
    print(f"\n[6] Validation post-cleanup...")
    valid_chunks   = []
    invalid_chunks = []

    for chunk in final:
        is_valid, reason = validate_chunk(chunk)
        if is_valid:
            valid_chunks.append(chunk)
        else:
            invalid_chunks.append((chunk, reason))

    if invalid_chunks:
        reason_counts = Counter(r for _, r in invalid_chunks)
        print(f"    Chunks invalides retires : {len(invalid_chunks)}")
        for reason, cnt in reason_counts.most_common():
            print(f"      {reason:25} {cnt:>4}")
        log_lines.append(f"VALIDATION : {len(invalid_chunks)} retires")
        for ch, r in invalid_chunks[:10]:
            log_lines.append(f"  INVALIDE [{r}] {get_content(ch)[:80]}")
    else:
        print(f"    Tous les chunks sont valides")

    # Garantir chunk_id partout
    for i, c in enumerate(valid_chunks):
        if not c.get("chunk_id"):
            m = get_meta(c)
            doc = re.sub(r'[^A-Za-z0-9]', '_', m.get("source_doc","unk").replace('.pdf',''))[:20]
            art = re.sub(r'[^A-Za-z0-9]', '_', str(m.get("article_ref",""))[:12])
            c["chunk_id"] = f"CHK_{doc}_{art}_{i:05d}"

    # ── 8. Ecriture ───────────────────────────────────────────────────────────
    print(f"\n[7] Ecriture : {chunks_path}")
    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(valid_chunks, f, ensure_ascii=False, indent=2)
    print(f"    {len(valid_chunks):,} chunks sauvegardes")

    # Mettre a jour par_document/
    par_dir = Path(par_doc_dir)
    if par_dir.exists():
        print(f"\n[8] Mise a jour : {par_doc_dir}/")
        by_doc = defaultdict(list)
        for c in valid_chunks:
            src = get_meta(c).get("source_doc", "unknown.pdf")
            by_doc[src].append(c)

        updated = 0
        for doc_file in par_dir.glob("*.json"):
            with open(doc_file, encoding="utf-8") as f:
                old = json.load(f)
            if not old:
                continue
            src_doc = get_meta(old[0]).get("source_doc", "")
            if src_doc in by_doc:
                with open(doc_file, "w", encoding="utf-8") as f:
                    json.dump(by_doc[src_doc], f, ensure_ascii=False, indent=2)
                updated += 1
        print(f"    {updated} fichiers par_document mis a jour")

    # ── 9. Stats finales ──────────────────────────────────────────────────────
    final_lens = [len(get_content(c)) for c in valid_chunks]
    under200 = sum(1 for l in final_lens if l < 200)
    over1500 = sum(1 for l in final_lens if l > 1500)

    print(f"\n{'='*62}")
    print(f"  RESULTAT NETTOYAGE v2")
    print(f"{'='*62}")
    print(f"  Avant            : {total_before:,} chunks")
    print(f"  Apres            : {len(valid_chunks):,} chunks")
    print(f"  Ancres fusionnees: {anchors_merged}")
    print(f"  Vides supprimes  : {len(micro_empties)}")
    print(f"  Macro decoupes   : {macro_split_count} -> {new_sub_count}")
    print(f"  Invalides retires: {len(invalid_chunks)}")
    print(f"  Min     : {min(final_lens):,} chars    (etait {min(lengths):,})")
    print(f"  Max     : {max(final_lens):,} chars    (etait {max(lengths):,})")
    print(f"  Moy     : {sum(final_lens)//len(final_lens):,} chars    "
          f"(etait {sum(lengths)//len(lengths):,})")
    print(f"  < 200   : {under200}  ({under200/len(valid_chunks)*100:.1f}%)"
          f"     (etait {sum(1 for l in lengths if l<200)} = "
          f"{sum(1 for l in lengths if l<200)/len(lengths)*100:.1f}%)")
    print(f"  > 1500  : {over1500}  ({over1500/len(valid_chunks)*100:.1f}%)"
          f"     (etait {sum(1 for l in lengths if l>1500)} = "
          f"{sum(1 for l in lengths if l>1500)/len(lengths)*100:.1f}%)")
    print(f"  Backup  : {backup}")
    print(f"{'='*62}")

    # ── 10. Log ───────────────────────────────────────────────────────────────
    log_lines.extend([
        "", "=" * 60,
        f"RESULTAT : {total_before} -> {len(valid_chunks)} chunks",
        f"Min={min(final_lens)} Max={max(final_lens)} Moy={sum(final_lens)//len(final_lens)}",
        f"<200: {under200} ({under200/len(valid_chunks)*100:.1f}%)",
        f">1500: {over1500} ({over1500/len(valid_chunks)*100:.1f}%)",
    ])
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    print(f"\n  Log detaille : {LOG_FILE}")

    print(f"""
PROCHAINES ETAPES :

  1. Re-indexer Weaviate :
       python reindex_weaviate.py

  2. Reconstruire Neo4j :
       python build_neo4j_graph.py --clear

  3. Recharger les regles ground truth :
       python load_ground_truth.py \\
         --input output/aml_rules_complete_validated.json --all

  4. Verifier :
       python build_neo4j_graph.py --stats-only
""")


# ============================================================================
#  MAIN
# ============================================================================

def main():
    global MIN_CHARS, MAX_CHARS, TARGET_CHARS, LOG_FILE
    parser = argparse.ArgumentParser(
        description="Nettoyage post-chunking v2 : fusion ancres, decoupe macro, validation"
    )
    parser.add_argument("--chunks",   default=CHUNKS_FILE)
    parser.add_argument("--par-doc",  default=PAR_DOC_DIR)
    parser.add_argument("--log",      default=LOG_FILE)
    parser.add_argument("--dry-run",  action="store_true",
                        help="Simule sans ecrire")
    parser.add_argument("--semantic", action="store_true",
                        help="Utiliser BGE pour decoupe semantique des macro-chunks")
    parser.add_argument("--min",      type=int, default=MIN_CHARS,
                        help=f"Taille min (defaut: {MIN_CHARS})")
    parser.add_argument("--max",      type=int, default=MAX_CHARS,
                        help=f"Taille max (defaut: {MAX_CHARS})")
    parser.add_argument("--target",   type=int, default=TARGET_CHARS,
                        help=f"Taille cible sous-chunks (defaut: {TARGET_CHARS})")
    args = parser.parse_args()

    MIN_CHARS    = args.min
    MAX_CHARS    = args.max
    TARGET_CHARS = args.target
    LOG_FILE     = args.log

    if not Path(args.chunks).exists():
        sys.exit(f"Fichier introuvable : {args.chunks}")

    run_cleanup(args.chunks, args.par_doc,
                dry_run=args.dry_run,
                use_semantic=args.semantic)


if __name__ == "__main__":
    main()
