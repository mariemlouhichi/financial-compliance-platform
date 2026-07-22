"""
integrate_new_documents.py
==========================
Integration incrementale des 7 nouveaux PDFs dans le pipeline AML/KYC.

Fait exactement les memes etapes que la Phase 1→2→3 mais UNIQUEMENT
pour les nouveaux documents, sans re-traiter les 25 originaux.

Etapes :
  [1] Extraction PDF → texte brut (PyPDF2)
  [2] Detection langue + traduction FR→EN (OpenRouter API)
  [3] Concatenation des pages
  [4] Chunking (pipeline existant : Pattern Scanner + Branch A/B)
  [5] Merge avec chunks_aml_kyc.json existant
  [6] Recalcul embeddings pour les nouveaux chunks
  [7] Mise a jour Weaviate (full re-index depuis backup + nouveaux)

USAGE :
  python integrate_new_documents.py
  python integrate_new_documents.py --skip-translation    # si deja traduit
  python integrate_new_documents.py --skip-weaviate       # chunking seul
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import requests as _requests
    _USE_REQUESTS = True
except ImportError:
    import urllib.error
    import urllib.request
    _USE_REQUESTS = False

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

REGULATIONS_BASE = Path(r"C:\Users\louhi\Downloads\projet_pfe\PFE_AML_KYC\01_regulations")

# Nouveaux fichiers a integrer (nom_fichier → (dossier, langue))
NEW_DOCUMENTS: dict[str, tuple[str, str]] = {
    # International — EN, pas de traduction
    "fatf_mvts_guidance_2016.pdf":               ("international", "EN"),
    "eu_transfer_funds_regulation_2023_1113.pdf": ("international", "EN"),
    "fatf_proliferation_financing_2021.pdf":      ("international", "EN"),
    "fatf_rba_prepaid_mobile_payments_2013.pdf":  ("international", "EN"),
    # Tunisie — FR, traduction necessaire
    "loi_2016_48_banques.pdf":                   ("tunisia", "FR"),
    "decret_2019_54_beneficiaire_effectif.pdf":  ("tunisia", "FR"),
    "bct_circulaire_2021_05_gouvernance.pdf":     ("tunisia", "FR"),
}

# Chemins
_BASE       = Path(__file__).parent
CHUNKS_FILE = _BASE / "output" / "chunks_aml_kyc.json"
BACKUP_FILE = _BASE / "output" / "chunks_with_embeddings.json"
NEW_EXTRACTED   = _BASE / "output" / "new_docs_extracted.json"
NEW_TRANSLATED  = _BASE / "output" / "new_docs_translated.json"
NEW_CHUNKS_FILE = _BASE / "output" / "new_docs_chunks.json"

# Groq API (30 RPM, 14400 RPD free tier)
# La clé n'est JAMAIS écrite en dur : elle est lue depuis la variable d'env
# GROQ_API_KEY ou depuis groq_keys.json (gitignoré), comme dans llm_client.py.
def _load_api_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if key:
        return key
    keys_file = _BASE / "groq_keys.json"
    if keys_file.exists():
        try:
            data = json.loads(keys_file.read_text(encoding="utf-8"))
            keys = data.get("keys") or []
            if keys:
                return keys[0]
        except Exception:
            pass
    raise RuntimeError(
        "Aucune clé Groq trouvée — définis GROQ_API_KEY ou crée groq_keys.json "
        "(voir groq_keys.example.json).")


API_KEY  = _load_api_key()
API_URL  = "https://api.groq.com/openai/v1/chat/completions"
MODEL    = "llama-3.3-70b-versatile"
MAX_CHARS_PER_CALL = 3000   # Groq handles larger inputs
SLEEP_BETWEEN_CALLS = 2.5   # 2.5s = ~24 RPM, safe under 30 RPM limit
MAX_RETRIES = 3

# Weaviate
WEAVIATE_HOST      = os.getenv("WEAVIATE_HOST",      "localhost")
WEAVIATE_HTTP_PORT = int(os.getenv("WEAVIATE_HTTP_PORT", "8088"))
WEAVIATE_GRPC_PORT = int(os.getenv("WEAVIATE_GRPC_PORT", "50051"))
COLLECTION_NAME    = "AMLChunk"
EMBEDDING_MODEL    = "BAAI/bge-base-en-v1.5"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sep(char="=", width=60):
    print(char * width)

def _step(n, title):
    print(f"\n[ETAPE {n}] {title}")
    print("-" * 50)


# ─────────────────────────────────────────────────────────────────────────────
# Etape 1 : Extraction PDF
# ─────────────────────────────────────────────────────────────────────────────

def extract_pdf(pdf_path: Path) -> dict[str, str]:
    """Extrait le texte page par page avec PyPDF2."""
    import PyPDF2
    pages = {}
    try:
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            total = len(reader.pages)
            for i, page in enumerate(reader.pages, 1):
                try:
                    text = page.extract_text() or "[Empty page]"
                    if text.strip():
                        pages[f"page_{i}"] = text
                    if i % 50 == 0:
                        print(f"    ... {i}/{total} pages", end="\r")
                except Exception:
                    pages[f"page_{i}"] = "[Error extracting page]"
    except Exception as e:
        print(f"  ERREUR extraction {pdf_path.name}: {e}")
    print(f"    {len(pages)} pages extraites depuis {pdf_path.name:<45}")
    return pages


def step1_extract(skip_if_exists: bool = True) -> dict:
    _step(1, "Extraction PDF")

    if skip_if_exists and NEW_EXTRACTED.exists():
        print(f"  Deja extrait → chargement depuis {NEW_EXTRACTED.name}")
        return json.loads(NEW_EXTRACTED.read_text(encoding="utf-8"))

    extracted: dict[str, dict] = {"tunisia": {}, "international": {}}

    for doc_name, (folder, _lang) in NEW_DOCUMENTS.items():
        pdf_path = REGULATIONS_BASE / folder / doc_name
        if not pdf_path.exists():
            print(f"  MANQUANT : {pdf_path}")
            continue
        print(f"\n  {doc_name}")
        pages = extract_pdf(pdf_path)
        if pages:
            extracted[folder][doc_name] = pages

    # Sauvegarder
    NEW_EXTRACTED.write_text(
        json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total = sum(len(d) for d in extracted.values())
    print(f"\n  {total} documents extraits → {NEW_EXTRACTED.name}")
    return extracted


# ─────────────────────────────────────────────────────────────────────────────
# Etape 2 : Traduction FR → EN
# ─────────────────────────────────────────────────────────────────────────────

def _detect_lang(text: str) -> str:
    if not text or len(text.strip()) < 10:
        return "EN"
    fr_chars = sum(1 for c in text if c in "éèêëàâäùûüôöœçîïÉÈÊÀÂÙÛÔÇ")
    ar_chars  = sum(1 for c in text if "؀" <= c <= "ۿ")
    if ar_chars > len(text) * 0.08:
        return "AR"
    if fr_chars >= 3:
        return "FR"
    return "EN"


def _translate_chunk(text: str, lang: str) -> str | None:
    """
    Traduit un segment de texte via Groq API (llama-3.3-70b-versatile).
    Utilise requests pour eviter les blocages Cloudflare de urllib.
    """
    instr = (
        "Translate the following regulatory text to English. "
        "Output ONLY the translated text. Preserve article numbers exactly "
        "(e.g. Article 15, Art. 3), monetary amounts (e.g. 15 000 TND, 10 000 EUR), "
        "and acronyms unchanged (AML, KYC, CDD, EDD, STR, BCT, CTAF, SWIFT, PEP, FATF)."
    )
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": f"{instr}\n\n{text}"}],
        "temperature": 0,
        "max_tokens": 800,
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _requests.post(API_URL, headers=headers, json=payload, timeout=40)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                return None
            elif resp.status_code == 429:
                wait = 30 * attempt
                print(f"\n    [429] Rate limited — attente {wait}s (tentative {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            else:
                print(f"    HTTP {resp.status_code} (tentative {attempt}/{MAX_RETRIES}): {resp.text[:80]}")
        except Exception as e:
            print(f"    Erreur tentative {attempt}/{MAX_RETRIES}: {str(e)[:80]}")
        time.sleep(2.0 * attempt)
    return None


def _translate_page(text: str) -> str:
    """Traduit une page en la decoupant en segments si necessaire."""
    if len(text) <= MAX_CHARS_PER_CALL:
        translated = _translate_chunk(text, "FR")
        time.sleep(SLEEP_BETWEEN_CALLS)
        return translated or text

    # Decouper aux sauts de ligne pour respecter les paragraphes
    lines = text.split("\n")
    segments, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 <= MAX_CHARS_PER_CALL:
            current += ("\n" if current else "") + line
        else:
            if current:
                segments.append(current)
            current = line
    if current:
        segments.append(current)

    parts = []
    for seg in segments:
        if seg.strip():
            t = _translate_chunk(seg, "FR")
            parts.append(t or seg)
            time.sleep(SLEEP_BETWEEN_CALLS)
    return "\n".join(parts)


def step2_translate(extracted: dict, skip_if_exists: bool = True) -> dict:
    _step(2, "Traduction FR → EN")

    if skip_if_exists and NEW_TRANSLATED.exists():
        print(f"  Deja traduit → chargement depuis {NEW_TRANSLATED.name}")
        return json.loads(NEW_TRANSLATED.read_text(encoding="utf-8"))

    translated: dict = {"tunisia": {}, "international": {}}
    fr_docs = {k for k, (_, lang) in NEW_DOCUMENTS.items() if lang == "FR"}

    for folder, docs in extracted.items():
        for doc_name, pages in docs.items():
            if doc_name not in fr_docs:
                # Document EN : copier directement
                translated[folder][doc_name] = pages
                print(f"  [EN] {doc_name} — {len(pages)} pages (pas de traduction)")
                continue

            # Document FR : traduire page par page
            print(f"\n  [FR] {doc_name} — {len(pages)} pages a traduire")
            translated_pages: dict[str, str] = {}
            pages_list = sorted(pages.items(), key=lambda x: int(x[0].split("_")[1]))
            total_pages = len(pages_list)

            for idx, (page_key, text) in enumerate(pages_list, 1):
                lang = _detect_lang(text)
                if lang == "EN":
                    translated_pages[page_key] = text
                    print(f"    page {idx}/{total_pages}: EN (skip)", end="\r")
                elif lang in ("FR", "AR"):
                    t = _translate_page(text)
                    translated_pages[page_key] = t
                    print(f"    page {idx}/{total_pages}: traduit      ", end="\r")
                else:
                    translated_pages[page_key] = text
                    print(f"    page {idx}/{total_pages}: inconnu      ", end="\r")

                # Sauvegarde progressive toutes les 20 pages
                if idx % 20 == 0:
                    translated[folder][doc_name] = translated_pages
                    NEW_TRANSLATED.write_text(
                        json.dumps(translated, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

            translated[folder][doc_name] = translated_pages
            print(f"\n    {doc_name}: {total_pages} pages traitees")

    NEW_TRANSLATED.write_text(
        json.dumps(translated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  Traduction terminee → {NEW_TRANSLATED.name}")
    return translated


# ─────────────────────────────────────────────────────────────────────────────
# Etape 3 : Concatenation des pages
# ─────────────────────────────────────────────────────────────────────────────

def step3_concatenate(translated: dict) -> dict:
    _step(3, "Concatenation des pages")
    concatenated: dict = {}
    for folder, docs in translated.items():
        concatenated[folder] = {}
        for doc_name, pages in docs.items():
            if not isinstance(pages, dict):
                concatenated[folder][doc_name] = pages
                continue
            sorted_pages = sorted(
                pages.items(),
                key=lambda x: int(x[0].split("_")[1]) if x[0].startswith("page_") else 9999
            )
            text = " ".join(content for _, content in sorted_pages if content.strip())
            concatenated[folder][doc_name] = text
            print(f"  {doc_name}: {len(text):,} chars")
    return concatenated


# ─────────────────────────────────────────────────────────────────────────────
# Etape 4 : Chunking des nouveaux documents
# ─────────────────────────────────────────────────────────────────────────────

def step4_chunk(concatenated: dict) -> list[dict]:
    _step(4, "Chunking (Pattern Scanner + Branch A/B)")

    sys.path.insert(0, str(_BASE))
    from core.pattern_scanner import profile_document
    from core.branch_a_structure import chunk_branch_a
    from core.branch_b_semantic import chunk_branch_b
    from core.role_tagger import enrich_chunk
    from sentence_transformers import SentenceTransformer

    print("  Chargement du modele BAAI/bge-base-en-v1.5...")
    model = SentenceTransformer(EMBEDDING_MODEL)
    def embed_fn(sentences):
        return model.encode(sentences, show_progress_bar=False, normalize_embeddings=True)
    print("  Modele charge.")

    new_chunks: list[dict] = []

    for folder, docs in concatenated.items():
        for doc_name, text in docs.items():
            if not text or len(text.strip()) < 100:
                print(f"  SKIP {doc_name}: trop court ({len(text)} chars)")
                continue

            print(f"\n  {doc_name} ({len(text):,} chars)")
            profile = profile_document(doc_name, text)
            branch = "A (Pattern)" if profile.is_structured else "B (Semantic)"
            print(f"    Score={profile.score:.1f} | {profile.best_pattern_name} | → {branch}")

            if profile.is_structured:
                raw = chunk_branch_a(text, profile.best_pattern_name, profile.best_pattern_regex, embed_fn)
            else:
                raw = chunk_branch_b(text, embed_fn)

            doc_chunks = []
            for i, chunk in enumerate(raw):
                ec = enrich_chunk(
                    text=chunk.text,
                    source_doc=doc_name,
                    article_ref=chunk.article_ref,
                    country_key=folder,
                    chunk_index=i,
                )
                doc_chunks.append(ec.to_dict())

            new_chunks.extend(doc_chunks)

            roles = {}
            for c in doc_chunks:
                roles[c["chunk_role"]] = roles.get(c["chunk_role"], 0) + 1
            thr = sum(1 for c in doc_chunks if c["has_threshold"])
            print(f"    {len(doc_chunks)} chunks | roles={roles} | thresholds={thr}")

    NEW_CHUNKS_FILE.write_text(
        json.dumps(new_chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  {len(new_chunks)} nouveaux chunks → {NEW_CHUNKS_FILE.name}")
    return new_chunks


# ─────────────────────────────────────────────────────────────────────────────
# Etape 5 : Merge avec chunks_aml_kyc.json
# ─────────────────────────────────────────────────────────────────────────────

def step5_merge(new_chunks: list[dict]) -> list[dict]:
    _step(5, "Merge avec chunks_aml_kyc.json")

    existing = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    existing_docs = {c["source_doc"] for c in existing}
    new_docs = {c["source_doc"] for c in new_chunks}

    # Supprimer les anciens chunks de memes documents (re-chunking propre)
    overlap = existing_docs & new_docs
    if overlap:
        print(f"  Suppression des chunks existants pour: {overlap}")
        existing = [c for c in existing if c["source_doc"] not in overlap]

    merged = existing + new_chunks

    # Backup de securite avant ecrasement
    backup = CHUNKS_FILE.with_suffix(".backup.json")
    backup.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    CHUNKS_FILE.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"  Avant : {len(existing):,} chunks")
    print(f"  Ajoutes: {len(new_chunks):,} chunks")
    print(f"  Total : {len(merged):,} chunks → {CHUNKS_FILE.name}")
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Etape 6 : Calcul des embeddings pour les nouveaux chunks
# ─────────────────────────────────────────────────────────────────────────────

def step6_embed(new_chunks: list[dict]) -> list[dict]:
    _step(6, "Calcul embeddings pour les nouveaux chunks")

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBEDDING_MODEL)

    texts = [c["text"] for c in new_chunks]
    total = len(texts)
    print(f"  {total} nouveaux chunks a encoder (BAAI/bge-base-en-v1.5)...")

    batch_size = 64
    all_embeddings = []
    for i in range(0, total, batch_size):
        batch = texts[i:i + batch_size]
        embs = model.encode(batch, show_progress_bar=False, normalize_embeddings=True)
        all_embeddings.extend(embs.tolist())
        done = min(i + batch_size, total)
        print(f"    [{done}/{total}] {done*100//total}%", end="\r")

    print(f"    {total}/{total} 100% termine")

    # Attacher les embeddings aux chunks
    for chunk, emb in zip(new_chunks, all_embeddings):
        chunk["embedding"] = emb

    return new_chunks


# ─────────────────────────────────────────────────────────────────────────────
# Etape 7 : Mise a jour Weaviate
# ─────────────────────────────────────────────────────────────────────────────

def step7_reindex_weaviate(new_chunks_with_embeddings: list[dict]) -> None:
    _step(7, "Mise a jour Weaviate (full re-index)")

    import weaviate
    import weaviate.classes.config as wvc

    # 1. Charger l'ancien backup (embeddings originaux)
    if not BACKUP_FILE.exists():
        print(f"  ERREUR : backup introuvable ({BACKUP_FILE})")
        print("  → Lancez d'abord embed_to_weaviate.py pour generer le backup.")
        return

    print(f"  Chargement backup ({BACKUP_FILE.stat().st_size / 1e6:.1f} MB)...")
    existing_with_emb = json.loads(BACKUP_FILE.read_text(encoding="utf-8"))

    # Supprimer les documents qui sont dans les nouveaux chunks (meme source_doc)
    new_doc_names = {c["source_doc"] for c in new_chunks_with_embeddings}
    existing_filtered = [
        c for c in existing_with_emb
        if c.get("source_doc") not in new_doc_names and "embedding" in c
    ]
    print(f"  Original apres filtrage: {len(existing_filtered):,} chunks")
    print(f"  Nouveaux: {len(new_chunks_with_embeddings):,} chunks")

    all_to_index = existing_filtered + new_chunks_with_embeddings
    print(f"  Total a indexer: {len(all_to_index):,} chunks")

    # 2. Connexion Weaviate
    print(f"\n  Connexion a Weaviate {WEAVIATE_HOST}:{WEAVIATE_HTTP_PORT}...")
    try:
        client = weaviate.connect_to_local(
            host=WEAVIATE_HOST,
            port=WEAVIATE_HTTP_PORT,
            grpc_port=WEAVIATE_GRPC_PORT,
        )
        if not client.is_ready():
            raise RuntimeError("Weaviate not ready")
    except Exception as e:
        print(f"  ERREUR connexion: {e}")
        print("  → docker compose up -d weaviate")
        return

    try:
        # 3. Recreer la collection
        if client.collections.exists(COLLECTION_NAME):
            client.collections.delete(COLLECTION_NAME)
            print(f"  Collection '{COLLECTION_NAME}' supprimee.")

        client.collections.create(
            name=COLLECTION_NAME,
            description="AML/KYC regulatory chunks with BGE-base embeddings",
            vectorizer_config=wvc.Configure.Vectorizer.none(),
            vector_index_config=wvc.Configure.VectorIndex.hnsw(
                distance_metric=wvc.VectorDistances.COSINE,
            ),
            properties=[
                wvc.Property(name="text",         data_type=wvc.DataType.TEXT),
                wvc.Property(name="source_doc",   data_type=wvc.DataType.TEXT,
                             index_filterable=True, index_searchable=True),
                wvc.Property(name="article_ref",  data_type=wvc.DataType.TEXT,
                             index_filterable=True, index_searchable=True),
                wvc.Property(name="jurisdiction", data_type=wvc.DataType.TEXT,
                             index_filterable=True),
                wvc.Property(name="chunk_role",   data_type=wvc.DataType.TEXT,
                             index_filterable=True),
                wvc.Property(name="has_threshold",data_type=wvc.DataType.BOOL,
                             index_filterable=True),
                wvc.Property(name="chunk_index",  data_type=wvc.DataType.INT),
                wvc.Property(name="char_count",   data_type=wvc.DataType.INT),
            ],
        )
        print(f"  Collection '{COLLECTION_NAME}' recreee.")

        # 4. Import batch
        collection = client.collections.get(COLLECTION_NAME)
        total = len(all_to_index)
        print(f"\n  Import de {total:,} chunks...")

        with collection.batch.dynamic() as batch:
            for i, chunk in enumerate(all_to_index):
                if "embedding" not in chunk:
                    continue
                batch.add_object(
                    properties={
                        "text":          chunk["text"],
                        "source_doc":    chunk["source_doc"],
                        "article_ref":   chunk.get("article_ref", ""),
                        "jurisdiction":  chunk.get("jurisdiction", ""),
                        "chunk_role":    chunk.get("chunk_role", "INFORMATION"),
                        "has_threshold": bool(chunk.get("has_threshold", False)),
                        "chunk_index":   int(chunk.get("chunk_index", 0)),
                        "char_count":    int(chunk.get("char_count", len(chunk["text"]))),
                    },
                    vector=chunk["embedding"],
                )
                if (i + 1) % 500 == 0:
                    pct = (i + 1) * 100 // total
                    print(f"    [{i+1:,}/{total:,}] {pct}%", end="\r")

        count = collection.aggregate.over_all(total_count=True).total_count
        print(f"\n  Weaviate: {count:,} / {total:,} chunks indexes")

        # 5. Sauvegarder le nouveau backup complet
        print("\n  Sauvegarde du nouveau backup...")
        for chunk in all_to_index:
            if "embedding" not in chunk:
                chunk["embedding"] = []
        BACKUP_FILE.write_text(
            json.dumps(all_to_index, ensure_ascii=False), encoding="utf-8"
        )
        size_mb = BACKUP_FILE.stat().st_size / 1e6
        print(f"  Backup: {BACKUP_FILE.name} ({size_mb:.1f} MB)")

    finally:
        client.close()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Integration incrementale des nouveaux documents AML/KYC"
    )
    parser.add_argument("--skip-translation", action="store_true",
                        help="Sauter la traduction (utiliser new_docs_translated.json existant)")
    parser.add_argument("--skip-weaviate",    action="store_true",
                        help="Sauter la re-indexation Weaviate (chunking seul)")
    parser.add_argument("--skip-extraction",  action="store_true",
                        help="Sauter l'extraction PDF (utiliser new_docs_extracted.json existant)")
    args = parser.parse_args()

    _sep()
    print("  AML/KYC — Integration Incrementale des Nouveaux Documents")
    _sep()
    print(f"  Nouveaux documents : {len(NEW_DOCUMENTS)}")
    for doc, (folder, lang) in NEW_DOCUMENTS.items():
        pdf = REGULATIONS_BASE / folder / doc
        status = "OK" if pdf.exists() else "MANQUANT"
        size = f"{pdf.stat().st_size/1e6:.1f}MB" if pdf.exists() else ""
        print(f"    [{status}] [{lang}] {doc} {size}")

    print()
    t0 = time.time()

    # ── Etape 1 : Extraction ──────────────────────────────────────────────
    extracted = step1_extract(skip_if_exists=args.skip_extraction)

    # ── Etape 2 : Traduction ──────────────────────────────────────────────
    if args.skip_translation:
        _step(2, "Traduction FR → EN [IGNOREE]")
        if NEW_TRANSLATED.exists():
            translated = json.loads(NEW_TRANSLATED.read_text(encoding="utf-8"))
            print(f"  Chargement depuis {NEW_TRANSLATED.name}")
        else:
            print("  ATTENTION: pas de fichier traduit, utilisation des textes bruts")
            translated = extracted
    else:
        translated = step2_translate(extracted, skip_if_exists=True)

    # ── Etape 3 : Concatenation ───────────────────────────────────────────
    concatenated = step3_concatenate(translated)

    # ── Etape 4 : Chunking ────────────────────────────────────────────────
    new_chunks = step4_chunk(concatenated)

    if not new_chunks:
        print("\nAucun nouveau chunk produit. Verifier les PDFs extraits.")
        return

    # ── Etape 5 : Merge ──────────────────────────────────────────────────
    merged = step5_merge(new_chunks)

    # ── Etapes 6+7 : Embedding + Weaviate ────────────────────────────────
    if args.skip_weaviate:
        _step(6, "Embedding [IGNORE — --skip-weaviate]")
        _step(7, "Weaviate [IGNORE — --skip-weaviate]")
    else:
        new_chunks_with_emb = step6_embed(new_chunks)
        step7_reindex_weaviate(new_chunks_with_emb)

    elapsed = time.time() - t0
    _sep()
    print(f"  Integration terminee en {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  Chunks total: {len(merged):,}")
    _sep()
    print()
    print("  Prochaine etape — reconstruire le graphe Neo4j :")
    print("    python build_neo4j_graph.py --clear")
    print()


if __name__ == "__main__":
    main()
