"""
neo4j_graph — Phase 4 du système AML/KYC Agentic RAG.

ARCHITECTURE À 2 COUCHES
────────────────────────

  Couche 1 — STRUCTURELLE (construite immédiatement depuis chunks_aml_kyc.json)
    Représente l'intégralité du corpus réglementaire.
    Nœuds : Document, LegalUnit, Chunk
    Référentiels : Jurisdiction, RoleCategory, TransactionType

  Couche 2 — RÈGLES (vide au démarrage, peuplée par l'agent Phase 6)
    Cache intelligent de règles extraites par l'agent et validées contre
    le texte source. Chaque règle conserve la traçabilité vers les chunks
    qui la prouvent.
    Nœuds : Rule (avec propriétés origin / confidence / validated_against_source)

Modules :
  schema           — contraintes d'unicité + index B-Tree
  loaders          — chargement et normalisation de chunks_aml_kyc.json
  builder          — construction de la couche structurelle
  agent_interface  — API pour les agents Phase 6 (upsert/validate/query rules)
  queries          — requêtes Cypher pour Phase 5/6 (retrieval) et Phase 7 (eval)
"""

__version__ = "3.0.0"
