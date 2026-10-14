# -*- coding: utf-8 -*-
"""
ingest_document.py — Ingestion d'UN règlement uploadé, à la demande (UI).

Réutilise les fonctions ÉPROUVÉES de integrate_new_documents.py
(extraction PDF, traduction FR→EN, chunking, embeddings) et n'ajoute que
l'INSERTION INCRÉMENTALE dans la collection Weaviate AMLChunk existante —
pas de full re-index. Le document devient immédiatement interrogeable par
le Retrieval Agent (RAG) pour les transactions suivantes.

Point d'entrée : ingest_pdf(pdf_path, jurisdiction, translate, progress)

NB durabilité : on insère en direct dans Weaviate (volume persistant). On ne
touche PAS au corpus maître chunks_aml_kyc.json — un `embed_to_weaviate.py
--fresh` repartirait donc du corpus d'origine (ré-uploader si besoin).
"""
from __future__ import annotations
import os
import time
from pathlib import Path

_BASE = Path(__file__).parent

# Connexion Weaviate — MÊME logique que retrieval_agent (local par défaut,
# cloud si WEAVIATE_URL est défini).
COLLECTION_NAME    = "AMLChunk"
WEAVIATE_HOST      = os.getenv("WEAVIATE_HOST",      "localhost")
WEAVIATE_HTTP_PORT = int(os.getenv("WEAVIATE_HTTP_PORT", "8088"))
WEAVIATE_GRPC_PORT = int(os.getenv("WEAVIATE_GRPC_PORT", "50051"))
WEAVIATE_URL       = os.getenv("WEAVIATE_URL", "").strip()
WEAVIATE_API_KEY   = os.getenv("WEAVIATE_API_KEY", "").strip()


def _noop(*_a, **_k):
    pass


def _connect_weaviate():
    import weaviate
    if WEAVIATE_URL:
        from weaviate.classes.init import Auth
        return weaviate.connect_to_weaviate_cloud(
            cluster_url=WEAVIATE_URL,
            auth_credentials=Auth.api_key(WEAVIATE_API_KEY),
        )
    return weaviate.connect_to_local(
        host=WEAVIATE_HOST, port=WEAVIATE_HTTP_PORT, grpc_port=WEAVIATE_GRPC_PORT,
    )


def _insert_to_weaviate(chunks_with_emb: list[dict]) -> int:
    """Ajoute les nouveaux chunks (avec vecteurs) à AMLChunk — insertion
    incrémentale (batch), sans supprimer l'existant."""
    client = _connect_weaviate()
    try:
        if not client.collections.exists(COLLECTION_NAME):
            raise RuntimeError(
                f"Collection {COLLECTION_NAME} absente — lance d'abord "
                f"embed_to_weaviate.py")
        coll = client.collections.get(COLLECTION_NAME)
        n = 0
        with coll.batch.dynamic() as batch:
            for c in chunks_with_emb:
                if "embedding" not in c or not c["embedding"]:
                    continue
                batch.add_object(
                    properties={
                        "text":          c["text"],
                        "source_doc":    c["source_doc"],
                        "article_ref":   c.get("article_ref", ""),
                        "jurisdiction":  c.get("jurisdiction", ""),
                        "chunk_role":    c.get("chunk_role", "INFORMATION"),
                        "has_threshold": bool(c.get("has_threshold", False)),
                        "chunk_index":   int(c.get("chunk_index", 0)),
                        "char_count":    int(c.get("char_count", len(c["text"]))),
                    },
                    vector=c["embedding"],
                )
                n += 1
        return n
    finally:
        client.close()


def ingest_pdf(pdf_path: str | Path, jurisdiction: str = "international",
               doc_name: str | None = None, translate: bool = True,
               progress=None) -> dict:
    """
    Ingère UN PDF réglementaire : extraction → (traduction FR→EN) → chunking
    → embeddings → insertion Weaviate. `progress(stage, detail)` est appelé à
    chaque étape (pour le suivi UI). Retourne un résumé.
    """
    progress = progress or _noop
    pdf_path = Path(pdf_path)
    doc_name = doc_name or pdf_path.name
    t0 = time.time()

    # Import PARESSEUX (l'accès à la clé Groq se fait à l'import du module —
    # on ne veut pas faire planter le démarrage de l'API si elle manque).
    import integrate_new_documents as ing

    # ── 1. Extraction ────────────────────────────────────────────────────
    progress("extraction", f"lecture de {doc_name}")
    pages = ing.extract_pdf(pdf_path)
    if not pages:
        raise RuntimeError("Aucun texte extrait du PDF (scanné/protégé ?)")
    n_pages = len(pages)

    # ── 2. Traduction FR→EN (le corpus + les embeddings sont en anglais) ──
    translated_pages: dict[str, str] = {}
    n_translated = 0
    if translate:
        items = sorted(pages.items(),
                       key=lambda x: int(x[0].split("_")[1]) if "_" in x[0] else 0)
        for idx, (pk, text) in enumerate(items, 1):
            lang = ing._detect_lang(text)
            if lang in ("FR", "AR"):
                progress("traduction", f"page {idx}/{n_pages}")
                translated_pages[pk] = ing._translate_page(text)
                n_translated += 1
            else:
                translated_pages[pk] = text
    else:
        translated_pages = pages

    # ── 3. Concaténation ─────────────────────────────────────────────────
    progress("concat", "assemblage des pages")
    concatenated = ing.step3_concatenate({jurisdiction: {doc_name: translated_pages}})

    # ── 4. Chunking (Pattern Scanner + Branch A/B) ───────────────────────
    progress("chunking", "découpage réglementaire")
    new_chunks = ing.step4_chunk(concatenated)
    if not new_chunks:
        raise RuntimeError("Aucun chunk produit (document trop court ?)")

    # ── 5. Embeddings (BGE-base-en) ──────────────────────────────────────
    progress("embedding", f"{len(new_chunks)} chunks → vecteurs")
    new_chunks = ing.step6_embed(new_chunks)

    # ── 6. Insertion Weaviate (incrémentale) ─────────────────────────────
    progress("indexation", "ajout à la base vectorielle")
    inserted = _insert_to_weaviate(new_chunks)

    # Résumé + aperçu (3 premiers chunks)
    roles: dict[str, int] = {}
    for c in new_chunks:
        roles[c.get("chunk_role", "?")] = roles.get(c.get("chunk_role", "?"), 0) + 1
    preview = [{
        "article_ref": c.get("article_ref", ""),
        "chunk_role":  c.get("chunk_role", ""),
        "text":        (c.get("text", "") or "")[:220],
    } for c in new_chunks[:3]]

    return {
        "doc_name":      doc_name,
        "jurisdiction":  jurisdiction,
        "pages":         n_pages,
        "pages_translated": n_translated,
        "chunks":        len(new_chunks),
        "chunks_indexed": inserted,
        "roles":         roles,
        "thresholds":    sum(1 for c in new_chunks if c.get("has_threshold")),
        "preview":       preview,
        "elapsed_s":     round(time.time() - t0, 1),
    }
