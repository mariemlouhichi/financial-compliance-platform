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
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "chunks_aml_kyc.json"
)
EMBEDDINGS_BACKUP = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "chunks_with_embeddings.json"
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

    # REPRISE : par défaut, si la collection existe déjà on la CONSERVE — un
    # import interrompu (connexion instable) peut ainsi être repris en
    # relançant le script, sans tout réindexer. Les UUID déterministes
    # (voir index_to_weaviate) garantissent l'absence de doublons.
    # Passer --fresh pour forcer une réindexation propre depuis zéro.
    if client.collections.exists(COLLECTION_NAME):
        if "--fresh" in sys.argv:
            client.collections.delete(COLLECTION_NAME)
            print(f"   🗑️  Collection '{COLLECTION_NAME}' supprimée (--fresh)")
        else:
            deja = client.collections.get(COLLECTION_NAME).aggregate.over_all(
                total_count=True).total_count
            print(f"   ♻️  Collection '{COLLECTION_NAME}' déjà présente "
                  f"({deja} objets) — reprise (upsert). --fresh pour repartir de zéro.")
            return

    # Type d'index vectoriel selon la cible :
    #  • Weaviate Cloud gratuit (« Cost Optimized ») n'autorise QUE `hfresh`,
    #    or le client 4.10.4 ne connaît que hnsw/flat/dynamic → on crée alors
    #    la collection par un appel REST direct imposant hfresh.
    #  • En local (Docker), on garde HNSW via le client typé.
    _sur_cloud = bool(os.getenv("WEAVIATE_URL", "").strip())
    if _sur_cloud:
        _create_collection_cloud_hfresh()
        return

    _index_vectoriel = wvc.Configure.VectorIndex.hnsw(
        distance_metric=wvc.VectorDistances.COSINE)

    client.collections.create(
        name=COLLECTION_NAME,
        description="AML/KYC regulatory chunks with BGE-base embeddings",
        # Pas de vectorizer — on fournit nos propres vecteurs
        vectorizer_config=wvc.Configure.Vectorizer.none(),
        vector_index_config=_index_vectoriel,
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


def _create_collection_cloud_hfresh():
    """Crée la collection sur Weaviate Cloud via l'API REST, avec l'index
    `hfresh` (seul autorisé par le profil gratuit). Le client Python 4.10.4
    ne sait créer que hnsw/flat/dynamic ; on contourne par un POST direct.
    Distance COSINE (embeddings BGE normalisés). Idempotent : si la classe
    existe déjà, on ne fait rien (la reprise s'appuie dessus).
    """
    import requests
    url = os.environ["WEAVIATE_URL"].rstrip("/")
    key = os.environ.get("WEAVIATE_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}

    # Déjà présente ? (reprise)
    r = requests.get(f"{url}/v1/schema/{COLLECTION_NAME}", headers=headers,
                     timeout=60)
    if r.status_code == 200:
        print(f"   ♻️  Collection '{COLLECTION_NAME}' déjà présente (cloud) — reprise")
        return

    def prop(nom, dtype, filt=False, search=False, desc=""):
        return {"name": nom, "dataType": [dtype], "description": desc,
                "indexFilterable": filt, "indexSearchable": search}

    classe = {
        "class": COLLECTION_NAME,
        "description": "AML/KYC regulatory chunks with BGE-base embeddings",
        "vectorizer": "none",
        "vectorIndexType": "hfresh",        # imposé par le profil gratuit
        "vectorIndexConfig": {"distance": "cosine"},
        "properties": [
            # text DOIT être searchable : la recherche hybride fait du BM25
            # (mots-clés) dessus. Sans indexSearchable, le gRPC échoue.
            prop("text", "text", filt=True, search=True,
                 desc="Chunk text content"),
            prop("source_doc", "text", filt=True, search=True),
            prop("article_ref", "text", filt=True, search=True),
            prop("jurisdiction", "text", filt=True),
            prop("chunk_role", "text", filt=True),
            prop("has_threshold", "boolean", filt=True),
            prop("chunk_index", "int"),
            prop("char_count", "int"),
        ],
    }
    resp = requests.post(f"{url}/v1/schema", headers=headers, json=classe,
                         timeout=90)
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Création REST échouée (HTTP {resp.status_code}) : {resp.text[:300]}")
    print(f"   ✅ Collection '{COLLECTION_NAME}' créée sur le cloud (index hfresh)\n")


def index_to_weaviate(client, chunks: list, embeddings: list):
    """
    Importe tous les chunks dans Weaviate, de façon RÉSILIENTE.

    Sur une connexion instable, le batch gRPC dynamique perd silencieusement
    des objets (ils n'apparaissent même pas dans failed_objects). On procède
    donc en PLUSIEURS PASSES : à chaque passe on relit ce qui est réellement
    présent côté serveur (UUID déterministes) et on ne renvoie QUE le manque,
    en lots de taille fixe et à faible concurrence (plus fiable). On s'arrête
    quand le compte serveur atteint la cible.
    """
    import time
    from weaviate.util import generate_uuid5
    collection = client.collections.get(COLLECTION_NAME)
    total = len(chunks)

    # Pré-calcul : (uuid, propriétés, vecteur) pour chaque chunk.
    tous = []
    for chunk, emb in zip(chunks, embeddings):
        tous.append((
            str(generate_uuid5(chunk["chunk_id"])),
            {
                "text": chunk["text"],
                "source_doc": chunk["source_doc"],
                "article_ref": chunk["article_ref"],
                "jurisdiction": chunk["jurisdiction"],
                "chunk_role": chunk["chunk_role"],
                "has_threshold": chunk["has_threshold"],
                "chunk_index": chunk["chunk_index"],
                "char_count": chunk["char_count"],
            },
            emb,
        ))

    print(f"📤 Indexing {total} chunks into Weaviate (multi-passes)...")

    for passe in range(1, 9):
        # Ce qui est DÉJÀ présent côté serveur (source de vérité).
        existants = set()
        try:
            for obj in collection.iterator(return_properties=[]):
                existants.add(str(obj.uuid))
        except Exception as e:
            print(f"   ⚠️  lecture des UUID existants partielle ({type(e).__name__})")

        manquants = [t for t in tous if t[0] not in existants]
        if not manquants:
            print(f"   ✅ tous les objets sont présents ({len(existants)})")
            break

        print(f"   passe {passe} : {len(existants)} présents · {len(manquants)} à envoyer")
        try:
            with collection.batch.fixed_size(batch_size=200,
                                             concurrent_requests=2) as batch:
                for uid, props, vec in manquants:
                    batch.add_object(uuid=uid, properties=props, vector=vec)
            fails = collection.batch.failed_objects
            if fails:
                print(f"      {len(fails)} objet(s) rejeté(s) — réessayés à la passe suivante")
        except Exception as e:
            print(f"      interruption ({type(e).__name__}) — on repart de ce qui est passé")
        time.sleep(6)   # laisser l'index HFRESH se stabiliser avant de recompter

    final = collection.aggregate.over_all(total_count=True).total_count
    if final >= total:
        print(f"   ✅ {final}/{total} chunks indexés\n")
    else:
        print(f"   ⚠️  {final}/{total} seulement — RELANCE la commande pour "
              f"envoyer le reste (reprise automatique)\n")
    return final


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

    # ── 2. Embeddings : RÉUTILISER le calcul précédent si possible ──
    #  Recalculer 6 678 vecteurs prend une dizaine de minutes de CPU pour un
    #  résultat identique. Si le fichier de sauvegarde existe et correspond au
    #  même nombre de chunks, on le relit — utile notamment pour réindexer un
    #  cluster Weaviate Cloud recréé après expiration du sandbox.
    embeddings = None
    if "--recompute" not in sys.argv and os.path.exists(EMBEDDINGS_BACKUP):
        try:
            print(f"♻️  Réutilisation des embeddings : {EMBEDDINGS_BACKUP}")
            with open(EMBEDDINGS_BACKUP, "r", encoding="utf-8") as f:
                sauvegarde = json.load(f)
            items = sauvegarde if isinstance(sauvegarde, list) \
                else sauvegarde.get("chunks", [])
            if len(items) == len(chunks) and items and "embedding" in items[0]:
                embeddings = [it["embedding"] for it in items]
                # Le fichier de sauvegarde porte les champs PLATS attendus par
                # l'indexation (text, source_doc, …), contrairement à
                # chunks_aml_kyc.json (imbriqué). On aligne donc chunks dessus.
                chunks = items
                print(f"   → {len(embeddings)} vecteurs relus "
                      f"({len(embeddings[0])} dimensions) — calcul évité\n")
            else:
                print("   → sauvegarde incompatible, recalcul nécessaire\n")
        except Exception as e:
            print(f"   → relecture impossible ({e}), recalcul\n")

    if embeddings is None:
        model = load_embedding_model()
        embeddings = compute_embeddings(chunks, model)
        # ── 3. Sauvegarder le backup local ──
        save_embeddings_backup(chunks, embeddings, EMBEDDINGS_BACKUP)

    # ── 4. Connecter à Weaviate et indexer ──
    import weaviate

    # Cloud si WEAVIATE_URL est défini (migration vers Weaviate Cloud),
    # sinon conteneur local — même script pour les deux cibles.
    _CLOUD_URL = os.getenv("WEAVIATE_URL", "").strip()
    if _CLOUD_URL:
        from weaviate.classes.init import Auth, AdditionalConfig, Timeout
        print(f"\n🔌 Connecting to Weaviate Cloud at {_CLOUD_URL}...")
        # Connexion RÉSILIENTE : sur une connexion instable, l'établissement
        # TCP/TLS échoue par intermittence (ConnectTimeout). On réessaie
        # plusieurs fois, et skip_init_checks=True allège chaque tentative
        # (pas de ping gRPC ni de /meta à faire échouer au démarrage).
        client = None
        derniere = None
        for tentative in range(1, 9):
            try:
                client = weaviate.connect_to_weaviate_cloud(
                    cluster_url=_CLOUD_URL,
                    auth_credentials=Auth.api_key(
                        os.getenv("WEAVIATE_API_KEY", "").strip()),
                    additional_config=AdditionalConfig(
                        timeout=Timeout(init=90, query=120, insert=600)),
                    skip_init_checks=True,
                )
                client.is_ready()   # force un aller-retour REST une fois connecté
                print(f"   ✅ connecté (tentative {tentative})")
                break
            except Exception as e:
                derniere = e
                print(f"   … tentative {tentative} échouée "
                      f"({type(e).__name__}), nouvel essai dans 5 s")
                try:
                    if client:
                        client.close()
                except Exception:
                    pass
                client = None
                time.sleep(5)
        if client is None:
            print(f"\n❌ Connexion impossible après plusieurs tentatives : {derniere}")
            print("   Vérifie ta connexion et relance — l'import reprend sans doublon.")
            sys.exit(1)
    else:
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
