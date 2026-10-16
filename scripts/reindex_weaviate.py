"""
reindex_weaviate.py — Réindexation depuis chunks_aml_kyc.json (version corrigée).

LOGIQUE :
  1. Charge chunks_aml_kyc.json  (source de vérité — après slim + cleanup)
  2. Calcule les embeddings BGE-base (sentence-transformers)
  3. Importe dans Weaviate
  4. Sauvegarde chunks_with_embeddings.json (backup pour futures réindexations rapides)

USAGE :
  python reindex_weaviate.py                  ← recalcule tout (recommandé après slim/cleanup)
  python reindex_weaviate.py --use-backup     ← charge embeddings depuis backup si nb chunks OK
"""

import argparse
import json
import os
import sys
import time

# ── CHEMINS ───────────────────────────────────────────────────────────────────
BASE_DIR          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR        = os.path.join(BASE_DIR, "output")
CHUNKS_FILE       = os.path.join(OUTPUT_DIR, "chunks_aml_kyc.json")      # SOURCE DE VÉRITÉ
EMBEDDINGS_BACKUP = os.path.join(OUTPUT_DIR, "chunks_with_embeddings.json")

# ── CONFIG WEAVIATE ────────────────────────────────────────────────────────────
WEAVIATE_HOST      = os.getenv("WEAVIATE_HOST",      "localhost")
WEAVIATE_HTTP_PORT = int(os.getenv("WEAVIATE_HTTP_PORT", "8088"))
WEAVIATE_GRPC_PORT = int(os.getenv("WEAVIATE_GRPC_PORT", "50051"))
COLLECTION_NAME    = "AMLChunk"

# ── CONFIG EMBEDDING ───────────────────────────────────────────────────────────
EMBEDDING_MODEL    = "BAAI/bge-base-en-v1.5"
BATCH_EMBED        = 64      # phrases par batch BGE
BATCH_IMPORT       = 200     # objets par batch Weaviate


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_content(chunk: dict) -> str:
    """Lit le contenu peu importe le champ utilisé (content ou text)."""
    return (chunk.get("content") or chunk.get("text", "")).strip()

def get_meta(chunk: dict) -> dict:
    """Retourne les métadonnées (imbriquées ou plates)."""
    return chunk.get("metadata", chunk)

def load_chunks(path: str) -> list[dict]:
    """Charge et valide chunks_aml_kyc.json."""
    if not os.path.exists(path):
        sys.exit(
            f"ERREUR : fichier introuvable : {path}\n"
            "  → Vérifiez que output/chunks_aml_kyc.json existe."
        )
    size_mb = os.path.getsize(path) / 1e6
    print(f"[1] Chargement : {os.path.basename(path)}  ({size_mb:.1f} MB)")
    with open(path, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    if not chunks:
        sys.exit("ERREUR : chunks_aml_kyc.json est vide.")
    print(f"    {len(chunks):,} chunks chargés")
    return chunks


# ══════════════════════════════════════════════════════════════════════════════
#  EMBEDDINGS
# ══════════════════════════════════════════════════════════════════════════════

def load_model():
    """Charge le modèle BGE-base."""
    try:
        from sentence_transformers import SentenceTransformer
        print(f"[2] Chargement modèle : {EMBEDDING_MODEL}")
        model = SentenceTransformer(EMBEDDING_MODEL)
        print(f"    Modèle prêt (dim=768)")
        return model
    except ImportError:
        sys.exit(
            "ERREUR : sentence-transformers non installé.\n"
            "  → pip install sentence-transformers"
        )


def compute_embeddings(model, chunks: list[dict]) -> list[list[float]]:
    """Calcule les embeddings par batch avec barre de progression."""
    total  = len(chunks)
    texts  = [get_content(c) for c in chunks]
    result = []

    print(f"[3] Calcul embeddings  ({total:,} chunks, batch={BATCH_EMBED})")
    t0 = time.time()

    for i in range(0, total, BATCH_EMBED):
        batch_texts = texts[i : i + BATCH_EMBED]
        vecs = model.encode(
            batch_texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        result.extend(vecs.tolist())
        pct = min(i + BATCH_EMBED, total) / total * 100
        elapsed = time.time() - t0
        print(f"    [{min(i+BATCH_EMBED, total):,}/{total:,}]  {pct:.0f}%  ({elapsed:.0f}s)", end="\r")

    print(f"    [{total:,}/{total:,}]  100%  ({time.time()-t0:.0f}s)         ")
    return result


def try_load_backup(chunks: list[dict]) -> list[list[float]] | None:
    """
    Charge les embeddings depuis backup SI le nombre de chunks correspond.
    Retourne None si le backup est absent ou désynchronisé.
    """
    if not os.path.exists(EMBEDDINGS_BACKUP):
        return None
    size_mb = os.path.getsize(EMBEDDINGS_BACKUP) / 1e6
    print(f"[2] Backup trouvé : {os.path.basename(EMBEDDINGS_BACKUP)}  ({size_mb:.1f} MB)")
    with open(EMBEDDINGS_BACKUP, "r", encoding="utf-8") as f:
        backup = json.load(f)

    if len(backup) != len(chunks):
        print(f"    ⚠ Désynchronisé : backup={len(backup):,}  vs  courant={len(chunks):,}")
        print(f"    → Recalcul des embeddings obligatoire.")
        return None

    if "embedding" not in backup[0]:
        print(f"    ⚠ Backup sans embeddings → recalcul.")
        return None

    print(f"    ✓ Backup synchronisé ({len(backup):,} chunks) — embeddings réutilisés")
    return [c["embedding"] for c in backup]


def save_backup(chunks: list[dict], embeddings: list[list[float]]):
    """Sauvegarde chunks + embeddings pour futures réindexations rapides."""
    print(f"[4] Sauvegarde backup : {os.path.basename(EMBEDDINGS_BACKUP)}")
    backup_data = []
    for chunk, emb in zip(chunks, embeddings):
        meta = get_meta(chunk)
        backup_data.append({
            "chunk_id":    chunk.get("chunk_id", ""),
            "text":        get_content(chunk),          # Weaviate attend "text"
            "embedding":   emb,
            "source_doc":  meta.get("source_doc", ""),
            "article_ref": meta.get("article_ref", ""),
            "jurisdiction":meta.get("jurisdiction", ""),
            "chunk_role":  meta.get("chunk_role", "OTHER"),
            "has_threshold":meta.get("has_threshold", False),
            "chunk_index": meta.get("chunk_index", 0),
            "char_count":  meta.get("char_count", len(get_content(chunk))),
        })
    with open(EMBEDDINGS_BACKUP, "w", encoding="utf-8") as f:
        json.dump(backup_data, f, ensure_ascii=False)
    size_mb = os.path.getsize(EMBEDDINGS_BACKUP) / 1e6
    print(f"    Sauvegardé ({size_mb:.1f} MB)")


# ══════════════════════════════════════════════════════════════════════════════
#  WEAVIATE
# ══════════════════════════════════════════════════════════════════════════════

def connect_weaviate():
    try:
        import weaviate
    except ImportError:
        sys.exit("Installez weaviate-client :  pip install 'weaviate-client==4.10.4'")

    print(f"\n[5] Connexion Weaviate  {WEAVIATE_HOST}:{WEAVIATE_HTTP_PORT}")
    try:
        client = weaviate.connect_to_local(
            host=WEAVIATE_HOST,
            port=WEAVIATE_HTTP_PORT,
            grpc_port=WEAVIATE_GRPC_PORT,
        )
        if not client.is_ready():
            raise RuntimeError("Weaviate not ready")
        print(f"    Connecté.")
        return client
    except Exception as e:
        sys.exit(
            f"Impossible de se connecter : {e}\n"
            "  → docker compose up -d   puis attendre ~20s"
        )


def recreate_collection(client):
    """Supprime et recrée la collection AMLChunk."""
    import weaviate.classes.config as wvc

    if client.collections.exists(COLLECTION_NAME):
        client.collections.delete(COLLECTION_NAME)
        print(f"    Collection '{COLLECTION_NAME}' supprimée.")

    client.collections.create(
        name=COLLECTION_NAME,
        description="AML/KYC regulatory chunks — BGE-base-en-v1.5 embeddings",
        vectorizer_config=wvc.Configure.Vectorizer.none(),
        vector_index_config=wvc.Configure.VectorIndex.hnsw(
            distance_metric=wvc.VectorDistances.COSINE,
        ),
        properties=[
            wvc.Property(name="text",          data_type=wvc.DataType.TEXT),
            wvc.Property(name="chunk_id",      data_type=wvc.DataType.TEXT,
                         index_filterable=True),
            wvc.Property(name="source_doc",    data_type=wvc.DataType.TEXT,
                         index_filterable=True, index_searchable=True),
            wvc.Property(name="article_ref",   data_type=wvc.DataType.TEXT,
                         index_filterable=True, index_searchable=True),
            wvc.Property(name="jurisdiction",  data_type=wvc.DataType.TEXT,
                         index_filterable=True),
            wvc.Property(name="chunk_role",    data_type=wvc.DataType.TEXT,
                         index_filterable=True),
            wvc.Property(name="has_threshold", data_type=wvc.DataType.BOOL,
                         index_filterable=True),
            wvc.Property(name="chunk_index",   data_type=wvc.DataType.INT),
            wvc.Property(name="char_count",    data_type=wvc.DataType.INT),
        ],
    )
    print(f"    Collection '{COLLECTION_NAME}' créée.")
    return client.collections.get(COLLECTION_NAME)


def import_chunks(collection, chunks: list[dict], embeddings: list[list[float]]):
    """Import batch dans Weaviate."""
    total = len(chunks)
    print(f"\n[6] Import  ({total:,} chunks, batch={BATCH_IMPORT})")
    t0 = time.time()
    errors = 0

    with collection.batch.dynamic() as batch:
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            meta = get_meta(chunk)
            try:
                batch.add_object(
                    properties={
                        "text":          get_content(chunk),
                        "chunk_id":      chunk.get("chunk_id", ""),
                        "source_doc":    meta.get("source_doc", ""),
                        "article_ref":   meta.get("article_ref", ""),
                        "jurisdiction":  meta.get("jurisdiction", ""),
                        "chunk_role":    meta.get("chunk_role", "OTHER"),
                        "has_threshold": bool(meta.get("has_threshold", False)),
                        "chunk_index":   int(meta.get("chunk_index", 0)),
                        "char_count":    int(meta.get("char_count", len(get_content(chunk)))),
                    },
                    vector=emb,
                )
            except Exception:
                errors += 1

            if (i + 1) % 500 == 0 or (i + 1) == total:
                pct = (i + 1) / total * 100
                print(f"    [{i+1:,}/{total:,}]  {pct:.0f}%  ({time.time()-t0:.0f}s)", end="\r")

    print(f"    [{total:,}/{total:,}]  100%  ({time.time()-t0:.0f}s)         ")
    if errors:
        print(f"    ⚠ {errors} erreurs d'import")
    return errors


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Réindexation Weaviate depuis chunks_aml_kyc.json")
    parser.add_argument("--use-backup", action="store_true",
                        help="Réutiliser les embeddings du backup si synchronisé")
    args = parser.parse_args()

    total_start = time.time()
    print(f"\n{'='*60}")
    print(f"  RÉINDEXATION WEAVIATE — Source : chunks_aml_kyc.json")
    print(f"{'='*60}\n")

    # 1. Charger les chunks courants
    chunks = load_chunks(CHUNKS_FILE)

    # 2. Embeddings : backup ou recalcul
    embeddings = None
    if args.use_backup:
        embeddings = try_load_backup(chunks)

    if embeddings is None:
        model      = load_model()
        embeddings = compute_embeddings(model, chunks)
        save_backup(chunks, embeddings)
    else:
        # Numéro d'étape cohérent
        pass

    # 3. Weaviate
    client = connect_weaviate()
    try:
        collection = recreate_collection(client)
        errors     = import_chunks(collection, chunks, embeddings)

        # Vérification finale
        count   = collection.aggregate.over_all(total_count=True).total_count
        elapsed = time.time() - total_start
        status  = "✓ OK" if count == len(chunks) else f"⚠ PARTIEL ({count}/{len(chunks)})"

        print(f"\n{'='*60}")
        print(f"  RÉSULTAT")
        print(f"{'='*60}")
        print(f"  Chunks indexés  : {count:,} / {len(chunks):,}")
        print(f"  Erreurs import  : {errors}")
        print(f"  Durée totale    : {elapsed:.0f}s")
        print(f"  Statut          : {status}")
        print(f"{'='*60}")
        print(f"\n  Test :")
        print(f'  python search_weaviate.py "KYC requirements for PEPs"')

    finally:
        client.close()


if __name__ == "__main__":
    main()