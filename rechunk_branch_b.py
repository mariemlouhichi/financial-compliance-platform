"""
rechunk_branch_b.py — Relance le chunking UNIQUEMENT sur les documents Branche B.

Les 20 documents Branche A (circulaires, lois, recommandations) sont déjà
correctement découpés. Ce script ne retraite que les 6 documents narratifs
qui avaient des chunks surdimensionnés, puis met à jour :
  - output/par_document/<doc>.json  (fichiers individuels)
  - output/chunks_aml_kyc.json      (fichier global recalculé)

USAGE :
  python rechunk_branch_b.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from config.settings import INPUT_FILE, OUTPUT_DIR, EMBEDDING_MODEL, STRUCTURE_THRESHOLD
from core.pattern_scanner import profile_document
from core.branch_b_semantic import chunk_branch_b
from core.role_tagger import enrich_chunk


def load_embedding_model():
    """Même logique que pipeline.py — sentence-transformers puis fallback TF-IDF."""
    try:
        from sentence_transformers import SentenceTransformer
        print(f"🧠 Loading : {EMBEDDING_MODEL}...")
        model = SentenceTransformer(EMBEDDING_MODEL)
        print(f"   ✅ Loaded ({model.get_sentence_embedding_dimension()} dims)\n")
        def embed_fn(sentences):
            return model.encode(sentences, show_progress_bar=False, normalize_embeddings=True)
        return embed_fn
    except Exception as e:
        print(f"   ⚠️  sentence-transformers unavailable: {e}")
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        print("   🔄 Fallback → TF-IDF\n")
        def embed_fn(sentences):
            return TfidfVectorizer(max_features=768).fit_transform(sentences).toarray()
        return embed_fn
    except ImportError:
        print("   ❌ No embedding model available!")
        sys.exit(1)


def main():
    start = time.time()

    # ── 1. Charger les données source ──
    print(f"📂 Loading {INPUT_FILE}...")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # ── 2. Charger le modèle ──
    embed_fn = load_embedding_model()

    # ── 3. Identifier les documents Branche B ──
    branch_b_docs = []
    for country_key, docs in data.items():
        for doc_name, text in docs.items():
            profile = profile_document(doc_name, text)
            if not profile.is_structured:
                branch_b_docs.append((country_key, doc_name, text))

    print(f"🔍 {len(branch_b_docs)} documents Branche B identifiés :\n")
    for country, name, text in branch_b_docs:
        print(f"   • {name} ({len(text):,} chars)")
    print()

    # ── 4. Re-chunker uniquement ces documents ──
    updated_docs = {}
    for country_key, doc_name, text in branch_b_docs:
        print(f"📄 Re-chunking : {doc_name}...")

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

        updated_docs[doc_name] = enriched

        # Stats
        roles = {}
        for c in enriched:
            roles[c["chunk_role"]] = roles.get(c["chunk_role"], 0) + 1
        sizes = [c["char_count"] for c in enriched]
        print(f"   ✂️  {len(enriched)} chunks | "
              f"Avg: {sum(sizes)//max(len(sizes),1)} chars | "
              f"Min: {min(sizes)} | Max: {max(sizes)} | "
              f"Roles: {roles}")

    # ── 5. Mettre à jour les fichiers par_document ──
    par_doc_dir = os.path.join(OUTPUT_DIR, "par_document")
    for doc_name, chunks in updated_docs.items():
        safe_name = doc_name.replace("/", "_").replace(" ", "_")
        doc_path = os.path.join(par_doc_dir, f"{safe_name}.json")
        with open(doc_path, "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)
        print(f"   💾 Updated : {doc_path}")

    # ── 6. Reconstruire le fichier global ──
    print(f"\n🔄 Rebuilding global chunks_aml_kyc.json...")
    all_chunks = []
    for filename in sorted(os.listdir(par_doc_dir)):
        if filename.endswith(".json"):
            filepath = os.path.join(par_doc_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                doc_chunks = json.load(f)
                all_chunks.extend(doc_chunks)

    global_path = os.path.join(OUTPUT_DIR, "chunks_aml_kyc.json")
    with open(global_path, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start
    print(f"\n{'=' * 50}")
    print(f"✅ Done in {elapsed:.1f}s")
    print(f"📊 {len(updated_docs)} documents re-chunked")
    print(f"📊 {len(all_chunks)} total chunks in global file")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
