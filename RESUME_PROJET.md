# État du projet PFE — Plateforme AML/KYC (résumé de passation)

> **Auteur** : Mariem Louhichi (ESPRIT) · **Soutenance** : septembre 2026
> **Répertoire** : `C:\Users\louhi\Downloads\projet_pfe\02_chunking_v2\`
> **Dépôt** : github.com/mariemlouhichi/financial-compliance-platform (branche `staged`)

Ce document résume l'état complet du projet pour reprendre le travail (rapport, vidéo, améliorations).

---

## 1. Vue d'ensemble

Plateforme d'aide à la décision pour la **conformité bancaire AML/KYC** (lutte anti-blanchiment),
appliquée au droit tunisien (**BCT**) et international (**FATF/GAFI**). Approche **neuro-symbolique
multi-agent** : règles déterministes + RAG agentique + ML explicable.

**Pipeline d'analyse en 8 étapes** : (0 Justificatifs docling) → 1 Règles Niveau 1 → 2 Client
Scoring → 3 Planning Agent → 4 Retrieval RAG → 5 Compliance Agent → 6 ML Fraude → 7 SAR Generator
→ 8 Decision Workflow.

**Stack** : React/TS (front) · FastAPI/Python 3.13 (API) · PostgreSQL · Neo4j · Weaviate · Redis ·
Groq LLM (Llama 3.3 70B) · embeddings BGE 768-dim · XGBoost/SHAP · Docker · Azure Container Apps.

---

## 2. Ce qui est FAIT ✅

### Cœur d'analyse (présenté en juin, consolidé)
- ✅ Pipeline neuro-symbolique complet (8 étapes)
- ✅ Anti-hallucination : gate B4 (ancrage verbatim) → **0 %**
- ✅ Graphe de connaissance Neo4j (chaîne `Rule→Chunk→LegalUnit→Document`)
- ✅ RAG hybride (BM25 + vectoriel + fusion RRF + re-ranking cross-encoder)

### Nouvelles fonctionnalités (depuis juin)
- ✅ **Persistance PostgreSQL** (dossiers/décisions, schéma hybride JSONB, repli fichier) — `api/store.py`
- ✅ **Temps réel SSE** (pipeline live + notifications) — `api/main.py` (EventBus)
- ✅ **Espace superviseur** (file priorisée + historique décisions)
- ✅ **Réentraînement RL hebdomadaire** (planificateur + détection dérive) — `retraining.py`, `api/scheduler.py`
- ✅ **Notifications email** (décision client HTML, rapport hebdo, codes) — `api/auth.py`
- ✅ **Justificatifs** (docling + vérif cohérence déterministe + LLM)
- ✅ **Simulateur** + **8 scénarios clients réels** — `api/main.py` (`REALISTIC_SCENARIOS`, ligne ~1749)

### Industrialisation & déploiement
- ✅ **Conteneurisation** : Dockerfile (API), project/Dockerfile (web nginx), docker-compose.yml
- ✅ **CI/CD** : GitHub Actions — `.github/workflows/ci.yml` + `deploy-web.yml` (build+push image vers ACR)
- ✅ **Migration cloud** : Neo4j Aura, Weaviate Cloud, Upstash Redis
- ✅ **Déploiement Azure** (Container Apps API+web, PostgreSQL Flexible, ACR) — **FAIT puis SUPPRIMÉ**
  le 2026-07-28 pour préserver le crédit (soutenance en septembre). Scripts prêts pour redéployer.
- ✅ **Comptes persistants** : 7 comptes de démo pré-chargés dans l'image — `seed_accounts.py`

### Évaluation
- ✅ **Moteur de conformité** — `evaluate_compliance.py` → `rapport_eval.md`
- ✅ **RAG (RAGAS-like)** — `evaluate_rag.py` → `rapport_rag.md`

### Documentation
- ✅ **24 fiches** de soutenance — dossier `docs_soutenance/`
- ✅ Poussé sur GitHub (branche `staged`, sans secrets)

---

## 3. Métriques clés

| Bloc | Valeurs |
|---|---|
| Décision | Accuracy **100 %** · Stabilité **100 %** (σ=0) |
| Anti-hallucination | **0 %** |
| Couverture | BCT **84 %** · FATF **94,7 %** |
| Éval RAG | Hit-Rate@5 **91,7 %** · MRR **0,72** · Faithfulness **81,4 %** · Relevancy **91,4 %** · Abstention **3/3** |
| ML fraude | PR-AUC **0,95** · F1 **0,96** |
| Corpus | 34 PDF · **6 678 chunks** · 768-dim (BGE) |

---

## 4. Ce qui RESTE à faire ⏳

- ⏳ **Rapport final** (structure de chapitres définie — voir §6)
- ⏳ **Vidéo démo** (script existant : `VIDEO_DEMO_SCRIPT.md`)
- ⏳ **Redéploiement Azure** (~1-2 h, ~1 semaine avant septembre — voir §7)
- ⏳ **Régénérer les secrets** exposés (clés Groq, mot de passe Gmail, mot de passe ACR)
- 💡 Améliorations discutées (facultatives) : scoring client ML, benchmark LLM, seuils dynamiques,
  détection de réseaux, boucle RLHF fermée

---

## 5. Emplacements des fichiers clés

```
02_chunking_v2/
├── api/
│   ├── main.py              # API FastAPI + orchestration pipeline + SSE
│   ├── auth.py              # authentification, rôles, email
│   └── store.py             # persistance PostgreSQL
├── level1_rules/            # Niveau 1 déterministe (sanctions, seuils, pays)
├── planning_agent.py        # agent de planification (questions)
├── retrieval_agent.py       # RAG hybride + re-ranking
├── compliance_agent.py      # cœur neuro-symbolique + gate anti-hallucination
├── ml_fraud_detector_v4.py  # détection de fraude ML
├── sar_generator.py         # génération du rapport SAR
├── decision_workflow.py     # machine à états + file superviseur
├── client_history_store.py  # historique client (Redis) — smurfing
├── llm_client.py            # client Groq (rotation clés × modèles)
├── retraining.py            # cycle de réentraînement RL
├── project/                 # frontend React (src/api/client.ts = appels API)
├── Dockerfile · project/Dockerfile · docker-compose.yml
├── deploy_api_azure.ps1 · deploy_web_azure.ps1 · redeploy_api_azure.ps1
├── seed_accounts.py         # comptes de démo (embarqués dans l'image)
├── evaluate_compliance.py → rapport_eval.md
├── evaluate_rag.py → rapport_rag.md
├── docs_soutenance/         # 24 fiches (base du rapport)
└── .github/workflows/       # CI/CD
```

**Secrets (gitignorés, LOCAUX)** : `cloud_env.ps1`, `groq_keys.json`, `smtp_config.json`, `platform.db`, `platform_seed.db`

---

## 6. Structure de rapport recommandée

- **Introduction générale**
- **Ch. 1 — Contexte général et état de l'art** (AML/KYC, problématique, existant, état de l'art
  RAG/neuro-symbolique/ML, méthodologie **hybride CRISP-DM + Scrum**)
- **Ch. 2 — Analyse et spécification des besoins** (acteurs 4 rôles, besoins F/NF, cas d'utilisation)
- **Ch. 3 — Conception** (architecture en couches, pipeline, couche connaissance, modèle de données)
- **Ch. 4 — Réalisation** (moteur d'analyse, RAG, ML, plateforme web, conteneurisation/CI-CD/Azure)
- **Ch. 5 — Évaluation et résultats** (conformité, RAG, ML — le différenciateur)
- **Conclusion générale et perspectives**

→ Les fiches `docs_soutenance/` se mappent directement sur les chapitres 3-5.

---

## 7. Checklist de REDÉPLOIEMENT Azure (septembre)

1. Recréer les services cloud : Weaviate sandbox (`python embed_to_weaviate.py` → 6 678 chunks) +
   Neo4j Aura (recharger le graphe) + vérifier Upstash Redis → MAJ `cloud_env.ps1`
2. Azure : `az group create` → ACR → push images → PostgreSQL Flexible → env Container Apps →
   `deploy_api_azure.ps1` + `deploy_web_azure.ps1`
3. `seed_accounts.py` (comptes) + CORS + test bout-en-bout
> Contraintes connues : Azure for Students interdit `az acr build` (→ build local + docker push) et
> la création de service principal (→ CI publie l'image, déploiement scripté). Doc complète : `docs_soutenance/24_deploiement_azure.md`.

---

## 8. Points de défense (Q/R jury)

- **« Système statique ? »** → Non, neuro-symbolique 3 niveaux. Le déterministe (Niveau 1) couvre
  les faits légaux durs ; l'intelligence est dans le RAG + ML. **Preuve : le smurfing** (3 dépôts
  sous le seuil détectés via l'historique).
- **« Vos scores sont fiables ? »** → Structure réglementaire + le ML n'aggrave jamais + validé 100 %.
- **« Vrais agents ? »** → 3 composants agentiques (Planning/Retrieval/Compliance) orchestrés de
  façon déterministe (choix assumé en domaine régulé).
- **« Fine-tuning LLM ? »** → Possible mais inutile : le RAG ancre les faits, on met à jour le corpus.
