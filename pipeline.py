"""
pipeline.py — Point d'entrée principal du Pipeline de Chunking AML/KYC v2

v2 CHANGES :
  - Embedding model : BAAI/bge-base-en-v1.5 (768 dims, optimized for retrieval)
  - 8 roles classification (SANCTION, OBLIGATION, PROCEDURE, THRESHOLD,
    DEFINITION, RED_FLAG, REPORTING, EXEMPTION, INFORMATION)
  - English-only keywords
  - Expanded currency detection

USAGE :
  python pipeline.py           ← local (VS Code)
  docker compose up --build    ← Docker
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from config.settings import INPUT_FILE, OUTPUT_DIR, EMBEDDING_MODEL, MAX_CHUNK_CHARS
from core.pattern_scanner import profile_document
from core.branch_a_structure import chunk_branch_a
from core.branch_b_semantic import chunk_branch_b
from core.role_tagger import enrich_chunk


# ═══════════════════════════════════════════════════════════════
# EMBEDDING MODEL LOADING
# ═══════════════════════════════════════════════════════════════

def load_embedding_model():
    """
    Load the embedding model with fallback chain :
      1. sentence-transformers with BAAI/bge-base-en-v1.5 (best quality)
      2. TF-IDF fallback (works offline, lower quality)
      3. Exit with clear error message

    For BGE models, we normalize embeddings for cosine similarity.
    """
    # ── Attempt 1 : sentence-transformers (BAAI/bge-base-en-v1.5) ──
    try:
        from sentence_transformers import SentenceTransformer
        print(f"🧠 Loading embedding model : {EMBEDDING_MODEL}...")
        model = SentenceTransformer(EMBEDDING_MODEL)
        print(f"   ✅ Model loaded ({model.get_sentence_embedding_dimension()} dims)\n")

        def embed_fn(sentences):
            return model.encode(
                sentences,
                show_progress_bar=False,
                normalize_embeddings=True  # Required for BGE cosine similarity
            )
        return embed_fn

    except Exception as e:
        print(f"   ⚠️  sentence-transformers unavailable : {e}")

    # ── Attempt 2 : TF-IDF fallback ──
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        print("   🔄 Fallback → TF-IDF (sklearn)\n")

        def embed_fn(sentences):
            vectorizer = TfidfVectorizer(max_features=768)
            return vectorizer.fit_transform(sentences).toarray()
        return embed_fn

    except ImportError:
        pass

    # ── No model available ──
    print("   ❌ No embedding model available!")
    print("   Install : pip install sentence-transformers")
    print("   Or :      pip install scikit-learn")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# DOCUMENT PROCESSING
# ═══════════════════════════════════════════════════════════════

def process_document(doc_name, text, country_key, embed_fn):
    """Full pipeline for ONE document : Profile → Chunk → Enrich."""
    profile = profile_document(doc_name, text)
    branch = "A (Pattern)" if profile.is_structured else "B (Semantic)"
    print(f"   📊 Score={profile.score} | "
          f"Pattern={profile.best_pattern_name} (×{profile.frequency}) | "
          f"→ Branch {branch}")

    if profile.is_structured:
        raw_chunks = chunk_branch_a(
            text, profile.best_pattern_name,
            profile.best_pattern_regex, embed_fn
        )
    else:
        raw_chunks = chunk_branch_b(text, embed_fn)

    enriched = []
    for i, chunk in enumerate(raw_chunks):
        ec = enrich_chunk(
            text=chunk.text,
            source_doc=doc_name,
            article_ref=chunk.article_ref,
            country_key=country_key,
            chunk_index=i,
        )
        enriched.append(ec.to_dict())

    return enriched


# ═══════════════════════════════════════════════════════════════
# SAVE RESULTS
# ═══════════════════════════════════════════════════════════════

def save_results(all_chunks, doc_chunks, output_dir):
    """Save to JSON : global file + per-document files."""
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "par_document"), exist_ok=True)

    global_path = os.path.join(output_dir, "chunks_aml_kyc.json")
    with open(global_path, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)
    print(f"\n💾 {len(all_chunks)} chunks → {global_path}")

    for doc_name, chunks in doc_chunks.items():
        safe_name = doc_name.replace("/", "_").replace(" ", "_")
        doc_path = os.path.join(output_dir, "par_document", f"{safe_name}.json")
        with open(doc_path, "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)

    print(f"📁 {len(doc_chunks)} individual files → {output_dir}/par_document/")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    start = time.time()

    print(f"📂 Loading {INPUT_FILE}...")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    total_docs = sum(len(docs) for docs in data.values())
    print(f"   → {len(data)} jurisdictions, {total_docs} documents\n")

    embed_fn = load_embedding_model()

    all_chunks = []
    doc_chunks = {}

    for country_key, docs in data.items():
        print(f"{'=' * 60}")
        print(f"🌍 Jurisdiction : {country_key.upper()} ({len(docs)} documents)")
        print(f"{'=' * 60}")

        for doc_name, text in docs.items():
            print(f"\n📄 {doc_name} ({len(text):,} chars)")

            chunks = process_document(doc_name, text, country_key, embed_fn)
            all_chunks.extend(chunks)
            doc_chunks[doc_name] = chunks

            # Stats by role
            roles = {}
            for c in chunks:
                roles[c["chunk_role"]] = roles.get(c["chunk_role"], 0) + 1
            thresholds = sum(1 for c in chunks if c["has_threshold"])
            print(f"   ✂️  {len(chunks)} chunks | Roles: {roles} | Thresholds: {thresholds}")

    save_results(all_chunks, doc_chunks, OUTPUT_DIR)

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print(f"⏱️  Pipeline completed in {elapsed:.1f}s")
    print(f"📊 Total : {len(all_chunks)} chunks from {len(doc_chunks)} documents")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
