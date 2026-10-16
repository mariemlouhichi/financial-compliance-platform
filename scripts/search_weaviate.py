"""
search_weaviate.py — Test de recherche sémantique dans Weaviate

Permet de tester rapidement le RAG en posant des questions
et en récupérant les chunks les plus pertinents.

USAGE :
  python search_weaviate.py "What are the KYC requirements?"
  python search_weaviate.py "sanctions for money laundering" --role SANCTION
  python search_weaviate.py "reporting obligations" --jurisdiction Tunisia --limit 5
"""

import sys
import os
import json
import argparse

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
COLLECTION_NAME = "AMLChunk"


def main():
    parser = argparse.ArgumentParser(description="Search AML chunks in Weaviate")
    parser.add_argument("query", help="Search query in natural language")
    parser.add_argument("--limit", type=int, default=5, help="Number of results (default: 5)")
    parser.add_argument("--role", help="Filter by chunk_role (e.g. SANCTION, OBLIGATION)")
    parser.add_argument("--jurisdiction", help="Filter by jurisdiction (e.g. Tunisia, International)")
    parser.add_argument("--source", help="Filter by source document name")
    args = parser.parse_args()

    # ── Encode the query ──
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBEDDING_MODEL)
    query_vector = model.encode(args.query, normalize_embeddings=True).tolist()

    # ── Connect to Weaviate ──
    import weaviate
    from weaviate.classes.query import Filter

    client = weaviate.connect_to_local(port=8088)

    try:
        collection = client.collections.get(COLLECTION_NAME)

        # ── Build filters ──
        filters = None
        filter_parts = []

        if args.role:
            filter_parts.append(Filter.by_property("chunk_role").equal(args.role))
        if args.jurisdiction:
            filter_parts.append(Filter.by_property("jurisdiction").equal(args.jurisdiction))
        if args.source:
            filter_parts.append(Filter.by_property("source_doc").contains_any([args.source]))

        if len(filter_parts) == 1:
            filters = filter_parts[0]
        elif len(filter_parts) > 1:
            filters = Filter.all_of(filter_parts)

        # ── Search ──
        results = collection.query.near_vector(
            near_vector=query_vector,
            limit=args.limit,
            filters=filters,
            return_metadata=["distance"],
        )

        # ── Display results ──
        print(f'\nQuery: "{args.query}"')
        if args.role:
            print(f"   Filter: role={args.role}")
        if args.jurisdiction:
            print(f"   Filter: jurisdiction={args.jurisdiction}")
        print(f"   Results: {len(results.objects)}\n")

        for i, obj in enumerate(results.objects):
            p = obj.properties
            d = obj.metadata.distance
            print("-" * 60)
            print(f"  #{i+1}  [{p['chunk_role']}]  {p['source_doc']}")
            print(f"       {p['article_ref']}  |  {p['jurisdiction']}  |  "
                  f"distance: {d:.4f}  |  {p['char_count']} chars")
            print(f"       threshold: {'yes' if p['has_threshold'] else 'no'}")
            print()
            text = p['text']
            if len(text) > 300:
                text = text[:300] + "..."
            print(f"  {text}")
            print()

    finally:
        client.close()


if __name__ == "__main__":
    main()
