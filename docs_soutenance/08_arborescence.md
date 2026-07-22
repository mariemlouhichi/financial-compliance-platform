# 🗂️ Arborescence du projet — Système AML/KYC Agentic RAG

> PFE 2025/2026 — Mariem Louhichi. Working dir : `C:\Users\louhi\Downloads\projet_pfe\02_chunking_v2`.
> Description brève de chaque partie et fichier. (Mise à jour : 2026-06-27.)

```
02_chunking_v2/                    ← racine (working dir, venv Python)
│
├── 🟦 NIVEAU 1 — Règles déterministes (0% LLM)   level1_rules/
│   ├── level1_orchestrator.py     Orchestre les 4 checkers, priorité BLOCK>STR>EDD>KYC>RECORD>PASS → Level1Result
│   ├── sanctions_screening.py     Screening ONU (2807 indiv + 853 entités) + PEP, fuzzy match rapidfuzz
│   ├── risk_country_checker.py    Listes FATF (3 noires + 22 grises), résolution ISO/noms
│   ├── threshold_rules.py         Seuils BCT/FATF + détection smurfing (3+ dépôts <5k TND/72h)
│   ├── convert_un_sanctions.py    Utilitaire : convertit les données de sanctions ONU
│   └── data/                      fatf_lists.json, un_sanctions.json, peps (OpenSanctions)
│
├── 🟩 NIVEAU 2 — Agentic RAG (cœur du système)
│   ├── planning_agent.py          Reason-Act-Reflect : génère 3-8 questions réglementaires (templates + LLM)
│   ├── retrieval_agent.py         Pipeline hybride 5 étapes (cache→BM25+vecteur→RRF→expansion→reranker)
│   ├── compliance_agent.py        ⭐ Cœur neuro-symbolique dual-path v6.0 (6 phases, scoring résiduel)
│   ├── dynamic_checklist.py       Checklist FATF dynamique (pour la phase Reflect du Planning)
│   ├── client_scoring_agent.py    Scoring CLIENT structurel (KYC) + FATCA + CRS (séparés de l'AML)
│   └── llm_client.py              ResilientLLM : cache fichier + rotation clés/modèles Groq (anti-429)
│
├── 🟧 POINT 1 — Workflow post-décision (human-in-the-loop)
│   └── decision_workflow.py       Machine à états (APPROVED/BLOCKED/PENDING_*) + justificatifs sourcés
│                                  + notifications + file superviseur + graine feedback RLHF
│
├── 🟪 POINT 5 — Historique client (mémoire glissante)
│   └── client_history_store.py    Redis 24h/30-90j (+ repli mémoire) → smurfing intraday + profil comportemental
│
├── 🟥 ML FRAUD DETECTION (2ᵉ ligne statistique)
│   ├── ml_fraud_detector_v4.py    ⭐ VERSION CANONIQUE — Dual-Branch (XGB+LGB + LSTM-AE), HI-Small
│   ├── ml_fraud_detector(_v2/v3/v5).py   Versions antérieures (historique d'itérations v1→v5)
│   ├── train_demo_ml.py           Ré-entraîne v4 (LSTM+600k) + dumpe modèles & échantillon cohérents
│   ├── ml_api.py                  Service FastAPI/Swagger (/sample, /predict, /predict/explain, /model/info)
│   └── aml_ml_demo_*              Artefacts servis par l'API (xgb.json, lgb.txt, meta.json, sample.csv)
│
├── 🟨 ÉVALUATION
│   ├── evaluate_compliance.py     Harnais d'éval (décision, couverture BCT/FATF, hallucination, --dual)
│   ├── generate_scenarios.py      Générateur AUTO de vérité terrain (oracle = Niveau 1)
│   ├── evaluation_scenarios.py    24 scénarios étiquetés manuellement (cas nuancés)
│   ├── visualize_ground_truth.py  Génère docs_soutenance/06 (les 49 règles)
│   └── audit_corpus_sources.py    Audit des sources du corpus (% BCT/FATF/UE…)
│
├── 🧪 TESTS
│   ├── test_compliance_agent.py   31 tests du Compliance Agent (+ 7 intégration Docker skippés)
│   ├── test_decision_workflow.py  10 tests du workflow (Point 1)
│   ├── test_client_history_store.py  9 tests de l'historique (Point 5)
│   ├── test_integration.py        End-to-end L1→Planning→Retrieval→Compliance→Workflow (5 scénarios)
│   ├── test_reflection.py         Test de la phase Reflect du Planning
│   └── tests/test_pipeline.py     Test du pipeline de chunking (data prep)
│
├── 🎬 DÉMOS (autonomes, sans Docker)
│   ├── demo_client_integration.py    Même tx, 3 clients → décision différente (FATF RBA)
│   ├── demo_history_smurfing.py      3 dépôts séparés → smurfing détecté grâce à la mémoire
│   └── demo_agent_extraction.py      Démo d'extraction de règles par l'agent
│
├── 🏗️ PRÉPARATION DU CORPUS (Phases 1-4, déjà faites)
│   ├── pipeline.py                Orchestrateur du chunking
│   ├── core/                      branch_a_structure.py (structurel) · branch_b_semantic.py (sémantique)
│   │                              · role_tagger.py (rôle KYC/THRESHOLD…) · pattern_scanner.py
│   ├── build_clean_chunks.py / cleanup_chunks_v2.py / rechunk_branch_b.py   Construction/nettoyage chunks
│   ├── embed_to_weaviate.py / reindex_weaviate.py / search_weaviate.py      Weaviate (vecteurs BGE 768d)
│   ├── build_neo4j_graph.py / validate_neo4j_graph.py / query_neo4j.py      Graphe Neo4j
│   ├── neo4j_graph/               Module graphe : schema.py, builder.py, loaders.py, queries.py, agent_interface.py
│   ├── extract_ground_truth_v2.py / load_ground_truth.py / rebuild_validated_json.py   49 règles GT
│   ├── integrate_new_documents.py Ajout de nouveaux documents au corpus
│   └── generate_dashboard.py / weaviate_dashboard.py   Dashboards HTML d'inspection
│
├── 📚 docs_soutenance/            Dossier de soutenance
│   ├── 00_index.md                Index + chiffres clés + arguments
│   ├── 01_niveau1_regles_fixes.md
│   ├── 02_planning_agent.md
│   ├── 03_retrieval_agent.md
│   ├── 04_compliance_agent.md
│   ├── 05_schema_pipeline.md
│   ├── 06_ground_truth_49_regles.md
│   ├── 07_points_1_2_5.md         Réconciliation source-aware · workflow · historique Redis
│   └── 08_arborescence.md         (ce fichier)
│
├── 💾 output/                     Sorties générées
│   ├── aml_rules_complete_validated.json   ⚠️ 49 règles GT — NE PAS MODIFIER
│   ├── chunks_aml_kyc.json        6 678 chunks
│   ├── llm_cache.json             Cache LLM (Groq)
│   ├── notifications.jsonl        Notifications workflow (Point 1)
│   ├── supervisor_feedback.jsonl  Décisions superviseur = graine RLHF (Point 1)
│   └── rapports / logs / métriques d'éval
│
├── 🗄️ data/                       Données brutes (corpus traduit, listes)
├── ⚙️ config/settings.py          Configuration centrale
│
├── 🐳 docker-compose.yml          Weaviate (8088) + Neo4j (7687) + Redis (6379)
├── 📦 requirements.txt            Dépendances Python
├── 🔑 groq_keys.json              7 clés Groq (GITIGNORÉ — à régénérer après soutenance)
└── 📄 DOCUMENTATION_TECHNIQUE.md + rapport_*.md
```

## Résumé par couche

| Couche | Rôle | Fichiers clés |
|--------|------|---------------|
| **Niveau 1** | Faits durs déterministes (sanctions, seuils, pays) | `level1_rules/` |
| **Niveau 2** | Agentic RAG (raisonnement réglementaire) | `planning_agent`, `retrieval_agent`, `compliance_agent`, `client_scoring_agent` |
| **Niveau 3 / opérationnel** | Workflow + historique + ML | `decision_workflow`, `client_history_store`, `ml_fraud_detector_v4`, `ml_api` |
| **Support** | Éval, tests, démos, données | `evaluate_compliance`, `test_*`, `demo_*`, `output/`, `docker-compose` |

## Reste à créer (backlog)
- **SAR Generator** (Point 4) — pas encore de fichier
- **Scoring Agent** (fusion RAG + ML) — pas encore de fichier
- **API `/analyze` + dashboard React + SQLite + boucles RLHF** (Point 6)
- **Orchestration LangGraph** (Point 3)

## Infrastructure
- **Weaviate** : localhost:8088 (HTTP), 50051 (gRPC) — 6 678 vecteurs BGE 768d
- **Neo4j** : bolt://localhost:7687 — 33 docs, 2 301 LegalUnits, 6 678 Chunks
- **Redis** : localhost:6379 — historique client (Point 5)
- **venv** : `02_chunking_v2/venv/` — lancer via `venv/Scripts/python.exe`
