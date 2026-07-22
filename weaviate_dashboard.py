"""
weaviate_dashboard.py — Diagnostic et Visualisation de la collection Weaviate

Affiche un résumé complet de la collection AMLChunk :
  - Nombre total de chunks
  - Distribution par rôle (SANCTION, OBLIGATION, PROCEDURE, etc.)
  - Distribution par juridiction (Tunisia vs International)
  - Distribution par document source
  - Top 5 documents avec le plus de chunks
  - Statistiques de taille des chunks
  - Exemples de chunks

Idéal pour prendre des captures d'écran pour le rapport PFE.

USAGE :
  python weaviate_dashboard.py
"""

import weaviate
import json


WEAVIATE_PORT = 8088
COLLECTION_NAME = "AMLChunk"


def main():
    client = weaviate.connect_to_local(port=WEAVIATE_PORT)

    try:
        collection = client.collections.get(COLLECTION_NAME)

        # ═══════════════════════════════════════════════════
        # 1. TOTAL COUNT
        # ═══════════════════════════════════════════════════
        total = collection.aggregate.over_all(total_count=True)
        print("=" * 60)
        print(f"  WEAVIATE DASHBOARD — Collection: {COLLECTION_NAME}")
        print("=" * 60)
        print(f"\n  📊 Total chunks indexed: {total.total_count}")

        # ═══════════════════════════════════════════════════
        # Collect ALL data in a single pass (efficient)
        # ═══════════════════════════════════════════════════
        role_counts = {}
        jurisdiction_counts = {}
        doc_counts = {}
        sizes = []
        threshold_count = 0
        sample_chunks = []

        for obj in collection.iterator():
            p = obj.properties
            role = p.get("chunk_role", "UNKNOWN")
            jur = p.get("jurisdiction", "Unknown")
            doc = p.get("source_doc", "Unknown")
            size = p.get("char_count", 0)

            role_counts[role] = role_counts.get(role, 0) + 1
            jurisdiction_counts[jur] = jurisdiction_counts.get(jur, 0) + 1
            doc_counts[doc] = doc_counts.get(doc, 0) + 1
            sizes.append(size)

            if p.get("has_threshold", False):
                threshold_count += 1

            # Collect first 3 chunks as samples
            if len(sample_chunks) < 3:
                sample_chunks.append(p)

        total_count = sum(role_counts.values())

        # ═══════════════════════════════════════════════════
        # 2. DISTRIBUTION BY ROLE
        # ═══════════════════════════════════════════════════
        print(f"\n{'─' * 60}")
        print("  📋 Distribution by chunk_role:")
        print(f"{'─' * 60}")

        sorted_roles = sorted(role_counts.items(), key=lambda x: x[1], reverse=True)
        for role, count in sorted_roles:
            pct = count / total_count * 100
            bar = "█" * int(pct / 2)
            print(f"  {role:<15} {count:>5}  ({pct:5.1f}%)  {bar}")

        # ═══════════════════════════════════════════════════
        # 3. DISTRIBUTION BY JURISDICTION
        # ═══════════════════════════════════════════════════
        print(f"\n{'─' * 60}")
        print("  🌍 Distribution by jurisdiction:")
        print(f"{'─' * 60}")

        for jur, count in sorted(jurisdiction_counts.items()):
            pct = count / total_count * 100
            print(f"  {jur:<20} {count:>5}  ({pct:5.1f}%)")

        # ═══════════════════════════════════════════════════
        # 4. TOP DOCUMENTS
        # ═══════════════════════════════════════════════════
        print(f"\n{'─' * 60}")
        print("  📄 Top 10 documents by chunk count:")
        print(f"{'─' * 60}")

        sorted_docs = sorted(doc_counts.items(), key=lambda x: x[1], reverse=True)
        for doc, count in sorted_docs[:10]:
            print(f"  {count:>5} chunks  ← {doc}")

        # ═══════════════════════════════════════════════════
        # 5. CHUNK SIZE STATISTICS
        # ═══════════════════════════════════════════════════
        print(f"\n{'─' * 60}")
        print("  📏 Chunk size statistics:")
        print(f"{'─' * 60}")

        if sizes:
            sizes.sort()
            avg = sum(sizes) // len(sizes)
            median = sizes[len(sizes) // 2]
            p10 = sizes[int(len(sizes) * 0.1)]
            p90 = sizes[int(len(sizes) * 0.9)]

            print(f"  Min:      {min(sizes):>6} chars")
            print(f"  P10:      {p10:>6} chars")
            print(f"  Median:   {median:>6} chars")
            print(f"  Average:  {avg:>6} chars")
            print(f"  P90:      {p90:>6} chars")
            print(f"  Max:      {max(sizes):>6} chars")

            brackets = {
                "< 200": sum(1 for s in sizes if s < 200),
                "200-500": sum(1 for s in sizes if 200 <= s < 500),
                "500-1000": sum(1 for s in sizes if 500 <= s < 1000),
                "1000-1500": sum(1 for s in sizes if 1000 <= s < 1500),
                "> 1500": sum(1 for s in sizes if s >= 1500),
            }
            print(f"\n  Size distribution:")
            for bracket, count in brackets.items():
                pct = count / len(sizes) * 100
                bar = "█" * int(pct / 2)
                print(f"  {bracket:<12} {count:>5}  ({pct:5.1f}%)  {bar}")

        # ═══════════════════════════════════════════════════
        # 6. SAMPLE CHUNKS
        # ═══════════════════════════════════════════════════
        print(f"\n{'─' * 60}")
        print("  📝 Sample chunks (first 3):")
        print(f"{'─' * 60}")

        for p in sample_chunks:
            print(f"\n  [{p['chunk_role']}] {p['source_doc']}")
            print(f"  Ref: {p['article_ref']} | {p['jurisdiction']} | {p['char_count']} chars")
            text = p['text'][:200] + "..." if len(p['text']) > 200 else p['text']
            print(f"  Text: {text}")

        # ═══════════════════════════════════════════════════
        # 7. THRESHOLD STATS
        # ═══════════════════════════════════════════════════
        print(f"\n{'─' * 60}")
        print("  💰 Financial threshold detection:")
        print(f"{'─' * 60}")

        pct = threshold_count / total_count * 100
        print(f"  Chunks with thresholds:  {threshold_count} / {total_count} ({pct:.1f}%)")

        print(f"\n{'=' * 60}")
        print(f"  ✅ Weaviate is healthy — {total.total_count} chunks ready for RAG")
        print(f"{'=' * 60}")

    finally:
        client.close()


if __name__ == "__main__":
    main()