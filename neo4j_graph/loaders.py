"""
neo4j_graph/loaders.py
======================
Chargement des chunks pour la couche structurelle du graphe.

Ce module ne charge QUE les chunks. Les règles (couche 2) sont créées
dynamiquement par l'agent Phase 6 via neo4j_graph/agent_interface.py.

Utilité :
  • load_chunks() — lit chunks_aml_kyc.json (4 604 chunks de la Phase 2)
  • Garantit chunk_id pour chaque chunk
  • Calcule weaviate_ref (clé de bridge vers Weaviate Phase 5)
"""

import json
import re
from pathlib import Path


def load_chunks(file_path: str | Path) -> list[dict]:
    """
    Charge chunks_aml_kyc.json et garantit qu'un chunk_id existe pour chaque chunk.

    Format attendu (sortie de la Phase 2) :
      [
        {
          "chunk_id": "TN_BCT_2017_08_AML_ART015_01",   ← peut être absent
          "content":  "Article 15: ...",
          "metadata": {
            "source_doc":        "bct_circulaire_2017_08_aml.pdf",
            "article_ref":       "Article 15",
            "jurisdiction":      "Tunisia",
            "chunk_role":        "OBLIGATION",
            "has_threshold":     false,
            "chunk_index":       14,
            "char_count":        487,
            "transaction_types": ["VIREMENT_LOCAL", "SWIFT"],
            "topics":            ["KYC", "STR"],
            "is_sub_chunk":      false,
            ...
          }
        },
        ...
      ]
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Fichier chunks introuvable : {path}\n"
            "  → Lancez d'abord pipeline.py pour générer chunks_aml_kyc.json"
        )

    with open(path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    # Garantit chunk_id + harmonise l'accès aux métadonnées
    for i, chunk in enumerate(chunks):
        # Support des deux formats : metadata imbriqué ou plat
        meta = chunk.get("metadata", chunk)

        if "chunk_id" not in chunk:
            src = re.sub(r"[^A-Za-z0-9]", "_", meta.get("source_doc", "unk"))[:20]
            art = re.sub(r"[^A-Za-z0-9]", "_", meta.get("article_ref", ""))[:15]
            chunk["chunk_id"] = f"CHK_{src}_{art}_{i:05d}"

    print(f"  {len(chunks):,} chunks charges depuis {path.name}")
    return chunks


def chunk_meta(chunk: dict) -> dict:
    """
    Helper : retourne les métadonnées d'un chunk, peu importe le format
    (metadata imbriqué ou plat).
    """
    return chunk.get("metadata", chunk)
