"""
embed_to_weaviate.py — Embedding + Indexation dans Weaviate

Ce script :
  1. Charge les chunks depuis output/chunks_aml_kyc.json
  2. Encode chaque chunk avec BAAI/bge-base-en-v1.5 (768 dims)
  3. Crée une collection "AMLChunk" dans Weaviate avec le bon schéma
  4. Importe tous les chunks avec leurs vecteurs et métadonnées en batch

PRÉREQUIS :
  - Weaviate doit tourner en Docker :
    docker compose -f docker-compose.weaviate.yml up -d
  - Les chunks doivent exister dans output/chunks_aml_kyc.json

USAGE :
  python embed_to_weaviate.py

RÉSULTAT :
  - 4 604 chunks indexés dans Weaviate avec leurs vecteurs 768d
  - Les métadonnées (source_doc, chunk_role, jurisdiction, etc.)
    sont indexées comme propriétés filtrables
  - Les embeddings sont sauvegardés localement dans
    output/chunks_with_embeddings.json (backup)
"""

import json
import os
import sys
import time

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Chemins
CHUNKS_FILE = os.path.join(
    os.path.dirname(__file__), "output", "chunks_aml_kyc.json"
)
EMBEDDINGS_BACKUP = os.path.join(
    os.path.dirname(__file__), "output", "chunks_with_embeddings.json"
)

# Weaviate
WEAVIATE_HOST = os.getenv("WEAVIATE_HOST", "localhost")
WEAVIATE_HTTP_PORT = int(os.getenv("WEAVIATE_HTTP_PORT", "8088"))
WEAVIATE_GRPC_PORT = int(os.getenv("WEAVIATE_GRPC_PORT", "50051"))
COLLECTION_NAME = "AMLChunk"

# Embedding model (même que le chunking — déjà en cache)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
BATCH_SIZE = 64  # Nombre de chunks encodés en une fois


# ═══════════════════════════════════════════════════════════════
# 1. CHARGEMENT DU MODÈLE D'EMBEDDINGS
# ═══════════════════════════════════════════════════════════════

def load_embedding_model():
    """
    Charge BAAI/bge-base-en-v1.5 via sentence-transformers.
    Le modèle est déjà en cache depuis le chunking → pas de téléchargement.
    """
    try:
        from sentence_transformers import SentenceTransformer
        print(f"🧠 Loading embedding model: {EMBEDDING_MODEL}...")
        model = SentenceTransformer(EMBEDDING_MODEL)
        dims = model.get_sentence_embedding_dimension()
        print(f"   ✅ Loaded ({dims} dimensions)\n")
        return model
    except Exception as e:
        print(f"   ❌ Failed to load model: {e}")
        print("   Run: pip install sentence-transformers")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# 2. CALCUL DES EMBEDDINGS
# ═══════════════════════════════════════════════════════════════

def compute_embeddings(chunks: list, model) -> list:
    """
    Encode tous les chunks en vecteurs 768d par batches.

    Args:
        chunks: Liste de dicts (chaque chunk avec son texte)
        model: SentenceTransformer model

    Returns:
        Liste de listes (chaque embedding = liste de 768 floats)
    """
    texts = [c["text"] for c in chunks]
    total = len(texts)
    all_embeddings = []

    print(f"📐 Computing embeddings for {total} chunks (batch_size={BATCH_SIZE})...")

    for i in range(0, total, BATCH_SIZE):
        batch_texts = texts[i:i + BATCH_SIZE]
        batch_embeddings = model.encode(
            batch_texts,
            show_progress_bar=False,
            normalize_embeddings=True,  # Required for BGE cosine similarity
            batch_size=BATCH_SIZE,
        )
        all_embeddings.extend(batch_embeddings.tolist())

        done = min(i + BATCH_SIZE, total)
        pct = done / total * 100
        print(f"   [{done}/{total}] ({pct:.0f}%)", end="\r")

    print(f"   ✅ {total} embeddings computed ({len(all_embeddings[0])} dims each)\n")
    return all_embeddings


# ═══════════════════════════════════════════════════════════════
# 3. SAUVEGARDE LOCALE (BACKUP)
# ═══════════════════════════════════════════════════════════════

def save_embeddings_backup(chunks: list, embeddings: list, path: str):
    """
    Sauvegarde les chunks + embeddings en JSON local.
    Utile comme backup si Weaviate doit être rechargé plus tard.
    """
    for i, chunk in enumerate(chunks):
        chunk["embedding"] = embeddings[i]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False)

    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"💾 Backup saved: {path} ({size_mb:.1f} MB)")


# ═══════════════════════════════════════════════════════════════
# 4. INDEXATION DANS WEAVIATE
# ═══════════════════════════════════════════════════════════════

def create_weaviate_collection(client):
    """
    Crée la collection AMLChunk dans Weaviate avec le schéma adapté.

    Propriétés indexées (filtrables dans les requêtes) :
      - text          : le contenu textuel du chunk
      - source_doc    : nom du document source
      - article_ref   : référence de l'article/segment
      - jurisdiction  : Tunisia / International
      - chunk_role    : SANCTION, OBLIGATION, PROCEDURE, etc.
      - has_threshold : présence de seuils financiers
      - chunk_index   : position dans le document
      - char_count    : taille du chunk
    """
    import weaviate.classes.config as wvc

    # Supprimer la collection si elle existe déjà (réindexation propre)
    if client.collections.exists(COLLECTION_NAME):
        client.collections.delete(COLLECTION_NAME)
        print(f"   🗑️  Deleted existing collection '{COLLECTION_NAME}'")

    client.collections.create(
        name=COLLECTION_NAME,
        description="AML/KYC regulatory chunks with BGE-base embeddings",
        # Pas de vectorizer — on fournit nos propres vecteurs
        vectorizer_config=wvc.Configure.Vectorizer.none(),
        # Index vectoriel HNSW (par défaut, optimal pour notre taille)
        vector_index_config=wvc.Configure.VectorIndex.hnsw(
            distance_metric=wvc.VectorDistances.COSINE,
        ),
        # Propriétés du schéma
        properties=[
            wvc.Property(
                name="text",
                data_type=wvc.DataType.TEXT,
                description="Chunk text content",
            ),
            wvc.Property(
                name="source_doc",
                data_type=wvc.DataType.TEXT,
                description="Source document filename",
                index_filterable=True,
                index_searchable=True,
            ),
            wvc.Property(
                name="article_ref",
                data_type=wvc.DataType.TEXT,
                description="Article/section reference",
                index_filterable=True,
                index_searchable=True,
            ),
            wvc.Property(
                name="jurisdiction",
                data_type=wvc.DataType.TEXT,
                description="Tunisia or International",
                index_filterable=True,
            ),
            wvc.Property(
                name="chunk_role",
                data_type=wvc.DataType.TEXT,
                description="SANCTION, OBLIGATION, PROCEDURE, etc.",
                index_filterable=True,
            ),
            wvc.Property(
                name="has_threshold",
                data_type=wvc.DataType.BOOL,
                description="Contains financial thresholds",
                index_filterable=True,
            ),
            wvc.Property(
                name="chunk_index",
                data_type=wvc.DataType.INT,
                description="Position in source document",
            ),
            wvc.Property(
                name="char_count",
                data_type=wvc.DataType.INT,
                description="Character count",
            ),
        ],
    )
    print(f"   ✅ Collection '{COLLECTION_NAME}' created\n")


def index_to_weaviate(client, chunks: list, embeddings: list):
    """
    Importe tous les chunks dans Weaviate en batch avec leurs vecteurs.

    Utilise le batch dynamique de Weaviate v4 qui ajuste automatiquement
    la taille des batches pour optimiser le débit.
    """
    collection = client.collections.get(COLLECTION_NAME)

    print(f"📤 Indexing {len(chunks)} chunks into Weaviate...")

    failed_count = 0
    with collection.batch.dynamic() as batch:
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            batch.add_object(
                properties={
                    "text": chunk["text"],
                    "source_doc": chunk["source_doc"],
                    "article_ref": chunk["article_ref"],
                    "jurisdiction": chunk["jurisdiction"],
                    "chunk_role": chunk["chunk_role"],
                    "has_threshold": chunk["has_threshold"],
                    "chunk_index": chunk["chunk_index"],
                    "char_count": chunk["char_count"],
                },
                vector=embedding,
            )

            if (i + 1) % 500 == 0:
                print(f"   [{i + 1}/{len(chunks)}]", end="\r")

        # Vérifier les erreurs
        failed = collection.batch.failed_objects
        if failed:
            failed_count = len(failed)
            print(f"\n   ⚠️  {failed_count} objects failed to import")
            print(f"   First error: {failed[0]}")

    success = len(chunks) - failed_count
    print(f"   ✅ {success}/{len(chunks)} chunks indexed successfully\n")
    return success


# ═══════════════════════════════════════════════════════════════
# 5. VÉRIFICATION
# ═══════════════════════════════════════════════════════════════

def verify_weaviate(client):
    """
    Vérifie que l'indexation a fonctionné en lançant une requête de test.
    """
    collection = client.collections.get(COLLECTION_NAME)

    # Compter les objets
    count = collection.aggregate.over_all(total_count=True)
    print(f"🔍 Verification:")
    print(f"   Total objects in Weaviate: {count.total_count}")

    # Test de recherche sémantique
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBEDDING_MODEL)
    query_vector = model.encode(
        "What are the KYC requirements for politically exposed persons?",
        normalize_embeddings=True,
    ).tolist()

    results = collection.query.near_vector(
        near_vector=query_vector,
        limit=3,
        return_metadata=["distance"],
    )

    print(f"\n   📝 Test query: 'KYC requirements for PEPs'")
    print(f"   Top 3 results:")
    for i, obj in enumerate(results.objects):
        props = obj.properties
        dist = obj.metadata.distance
        print(f"   {i+1}. [{props['chunk_role']}] {props['source_doc']} — "
              f"{props['article_ref']} (distance: {dist:.4f})")
        print(f"      {props['text'][:120]}...")
        print()


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    start = time.time()

    # ── 1. Charger les chunks ──
    print(f"📂 Loading chunks from {CHUNKS_FILE}...")
    with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    print(f"   → {len(chunks)} chunks loaded\n")

    # ── 2. Charger le modèle et calculer les embeddings ──
    model = load_embedding_model()
    embeddings = compute_embeddings(chunks, model)

    # ── 3. Sauvegarder le backup local ──
    save_embeddings_backup(chunks, embeddings, EMBEDDINGS_BACKUP)

    # ── 4. Connecter à Weaviate et indexer ──
    import weaviate

    print(f"\n🔌 Connecting to Weaviate at {WEAVIATE_HOST}:{WEAVIATE_HTTP_PORT}...")
    client = weaviate.connect_to_local(
        host=WEAVIATE_HOST,
        port=WEAVIATE_HTTP_PORT,
        grpc_port=WEAVIATE_GRPC_PORT,
    )

    try:
        if not client.is_ready():
            print("   ❌ Weaviate is not ready!")
            print("   Run: docker compose -f docker-compose.weaviate.yml up -d")
            sys.exit(1)
        print("   ✅ Connected\n")

        # Créer la collection
        create_weaviate_collection(client)

        # Indexer les chunks
        index_to_weaviate(client, chunks, embeddings)

        # Vérifier
        verify_weaviate(client)

    finally:
        client.close()

    elapsed = time.time() - start
    print(f"{'=' * 50}")
    print(f"⏱️  Done in {elapsed:.1f}s")
    print(f"📊 {len(chunks)} chunks embedded + indexed in Weaviate")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
