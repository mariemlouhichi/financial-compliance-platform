"""
audit_corpus_sources.py — Le Retrieval Agent utilise-t-il TOUTES les sources ?
==============================================================================
Audit empirique : (1) ce qui est réellement indexé dans Weaviate (par
juridiction + par document), (2) ce que le Retrieval Agent renvoie vraiment
sur des requêtes représentatives → BCT/CTAF apparaissent-ils, ou seulement FATF ?
"""
import sys
from collections import Counter

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

import warnings; warnings.filterwarnings("ignore")


def classify_source(doc: str) -> str:
    d = (doc or "").lower()
    if d.startswith("bct_") or "reglementation" in d:        return "BCT"
    if d.startswith("ctaf") or "loi_" in d or "decret" in d: return "Tunisie (CTAF/loi)"
    if d.startswith("fatf"):                                 return "FATF"
    if d.startswith("amld") or d.startswith("eu_") or d.startswith("eba"): return "UE/EBA"
    if d.startswith("basel") or d.startswith("wolfsberg"):   return "Basel/Wolfsberg"
    return "autre"


# ── 1. CE QUI EST INDEXÉ DANS WEAVIATE ──────────────────────────────────
print("="*70)
print("  1. CONTENU RÉEL DE L'INDEX WEAVIATE (ce que le retrieval peut voir)")
print("="*70)
import weaviate
client = weaviate.connect_to_local(host="localhost", port=8088, grpc_port=50051)
try:
    col = client.collections.get("AMLChunk")
    by_jur, by_src, by_fam = Counter(), Counter(), Counter()
    total = 0
    for obj in col.iterator(return_properties=["jurisdiction", "source_doc"]):
        p = obj.properties
        total += 1
        by_jur[p.get("jurisdiction", "?")] += 1
        src = p.get("source_doc", "?")
        by_src[src] += 1
        by_fam[classify_source(src)] += 1

    print(f"\n  Total chunks indexés : {total}")
    print(f"\n  Par juridiction :")
    for k, n in by_jur.most_common():
        print(f"     {k:<16} {n:>5}  ({n/total:.0%})")
    print(f"\n  Par FAMILLE de source (régulateur) :")
    for k, n in by_fam.most_common():
        print(f"     {k:<22} {n:>5}  ({n/total:.0%})")
    print(f"\n  Top 12 documents indexés :")
    for doc, n in by_src.most_common(12):
        print(f"     {n:>5}  {doc}")
finally:
    client.close()


# ── 2. CE QUE LE RETRIEVAL RENVOIE VRAIMENT ─────────────────────────────
print("\n" + "="*70)
print("  2. SOURCES RÉELLEMENT RENVOYÉES PAR LE RETRIEVAL AGENT (top-8)")
print("="*70)
from retrieval_agent import RetrievalPipeline
pipe = RetrievalPipeline(verbose=False)

queries = [
    ("STR reporting threshold local transfer Tunisia BCT obligations", "VIREMENT_LOCAL"),
    ("cash deposit declaration threshold suspicious structuring", "DEPOT_ESPECES"),
    ("customer due diligence identification verification obligations", "SWIFT"),
    ("money transfer operator MVTS obligations identification", "MANDAT"),
    ("politically exposed persons enhanced vigilance", "SWIFT"),
]
try:
    global_fam = Counter()
    for q, tt in queries:
        res = pipe.retrieve(query=q, tx_type=tt, top_k=8, skip_cache=True)
        fam = Counter(classify_source(r.source_doc) for r in res)
        for k, v in fam.items():
            global_fam[k] += v
        fam_str = ", ".join(f"{k}:{v}" for k, v in fam.most_common())
        print(f"\n  [{tt}] {q[:48]}")
        print(f"     → {fam_str}")
        for r in res[:3]:
            print(f"       · {r.source_doc}  ({r.article_ref})")

    print("\n" + "-"*70)
    print("  BILAN — familles de sources dans TOUS les résultats retrieval :")
    tot = sum(global_fam.values())
    for k, n in global_fam.most_common():
        print(f"     {k:<22} {n:>3}  ({n/tot:.0%})")
finally:
    pipe.close()

print("\n" + "="*70)
