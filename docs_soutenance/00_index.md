# 📚 Dossier de soutenance — Système AML/KYC Agentic RAG

> PFE 2025/2026 — Mariem Louhichi
> Supports détaillés par composant : objectifs, architecture, choix de conception,
> algorithmes, résultats et limites assumées.

---

## Comment utiliser ce dossier

Chaque document est autonome et suit la même trame : **à quoi sert le composant**,
**comment il est construit**, **pourquoi ces choix**, **ce qu'il produit**, et
**ce qu'il ne fait pas**. Les documents sont rédigés pour être repris directement
dans le rapport final.

⚠️ **À lire en premier si vous rédigez le rapport** : le document **22** recense
les évolutions apportées aux composants documentés en juin (01 à 08). Certains
passages de ces fichiers sont périmés.

---

## Partie I — Le cœur d'analyse (pipeline en 9 étapes)

| # | Document | Contenu |
|---|----------|---------|
| **16** | [Analyse des justificatifs](16_analyse_justificatifs.md) | **Étape 0** — le document prouve-t-il l'opération ? Docling, vérifications, verdict |
| **01** | [Niveau 1 — Règles fixes](01_niveau1_regles_fixes.md) | **Étape 1** — sanctions, risque pays, seuils BCT. La base déterministe, 0 % LLM |
| **09** | [Client Scoring Agent](09_client_scoring_agent.md) | **Étape 2** — risque structurel du client, somme pondérée explicable |
| **02** | [Planning Agent](02_planning_agent.md) | **Étape 3** — Reason-Act-Reflect, génération des questions réglementaires |
| **03** | [Retrieval Agent](03_retrieval_agent.md) | **Étape 4** — récupération hybride graphe + corpus, synthèse des réponses |
| **04** | [Compliance Agent](04_compliance_agent.md) | **Étape 5** — cœur neuro-symbolique dual-path, scoring résiduel |
| **10** | [ML Fraud Detector](10_ml_fraud_detector.md) | **Étape 6** — dual-branch + LSTM-AE, SHAP, garde-fou profil nouveau |
| **11** | [SAR Generator](11_sar_generator.md) | **Étape 7** — le rapport réglementaire, synthèse LLM sous contrainte |
| **12** | [Decision Workflow & file superviseur](12_decision_workflow_superviseur.md) | **Étape 8** — machine à états, priorités, historique des décisions |
| **05** | [Schéma du pipeline](05_schema_pipeline.md) | Vue d'ensemble visuelle des 3 niveaux |

## Partie II — La connaissance réglementaire

| # | Document | Contenu |
|---|----------|---------|
| **13** | [Couche de connaissance](13_couche_connaissance.md) | Corpus, chunking, embeddings BGE, Weaviate, graphe Neo4j |
| **06** | [Ground truth — 49 règles](06_ground_truth_49_regles.md) | Les règles validées humainement (vérité terrain) |
| **21** | [Amélioration continue](21_amelioration_continue.md) | Apprentissage de règles (opérationnel) et boucle RLHF (préparée) |

## Partie III — La plateforme

| # | Document | Contenu |
|---|----------|---------|
| **14** | [Plateforme web](14_plateforme_web.md) | Architecture API FastAPI + React, 4 espaces, zéro dépendance ajoutée |
| **15** | [Authentification et rôles](15_authentification_roles.md) | Comptes, jetons, 4 rôles, **séparation des devoirs** |
| **17** | [Persistance PostgreSQL](17_persistance_postgresql.md) | Couche d'accès à deux backends, schéma hybride, requêtes complexes |
| **18** | [Temps réel SSE](18_temps_reel_sse.md) | Push serveur, bus thread-safe, remplacement du polling |
| **19** | [Notifications email](19_notifications_email.md) | Décision au client, rapport hebdomadaire, codes d'authentification |

## Partie IV — Démonstration et suivi

| # | Document | Contenu |
|---|----------|---------|
| **20** | [Simulateur et scénarios](20_simulateur_scenarios.md) | Flux continu, scénario clients réels, 14 cas de démonstration |
| **23** | [Conteneurisation et CI](23_conteneurisation_ci.md) | Images Docker, nginx + SSE, docker-compose complet, GitHub Actions |
| **08** | [Arborescence du projet](08_arborescence.md) | Structure des dossiers et fichiers |
| **07** | [Points 1, 2, 5 (historique)](07_points_1_2_5.md) | Ancien point d'avancement — contenu réparti dans les docs thématiques |
| **22** | [**Évolutions depuis juin**](22_evolutions_depuis_juin.md) | **Ce qui a changé dans les docs 01-08** + synthèse des bugs corrigés |

---

## Les chiffres clés

```
── Moteur de conformité ──────────────────────────────
Accuracy décisionnelle       : 100/100   (cible ≥ 85%)   ✅
Couverture BCT contraignante :  84,1 %                   ⚖️
Couverture FATF (référence)  :  94,7 %   (cible ≥ 90%)   ✅
Taux d'hallucination         :   0,00 %  (cible < 2%)    ✅
Stabilité décisionnelle      : 100 %     (σ = 0)         ✅

── Base de connaissance ──────────────────────────────
Documents PDF                :     34    (17 TN + 17 int.)
Chunks indexés               :  6 678
Dimension embedding          :    768    (BAAI/bge-base-en-v1.5)
Règles vérité terrain        :     49    (human_verified)

── Détection ML ──────────────────────────────────────
Features                     :     37
PR-AUC                       :  0,9528
F1                           :  0,9640
Précision                    :  0,9869
Rappel                       :  0,9421
```

---

## Les arguments forts

1. **Neuro-symbolique** — le déterministe tranche les faits durs (sanctions,
   seuils), le LLM raisonne sur le contextuel, et **le déterministe prévaut
   toujours**.
2. **Anti-hallucination prouvé** — 0 % grâce à la chaîne d'ancrage
   `Rule → Chunk → LegalUnit → Document` : une règle sans preuve verbatim n'est
   pas retenue.
3. **BCT-first** — le système s'appuie sur le droit tunisien contraignant, la
   FATF n'étant qu'une taxonomie de référence. Les deux couvertures sont
   distinguées.
4. **Le ML ne peut que renforcer** — un modèle statistique n'est pas une source
   de droit : il peut aggraver une décision, jamais l'innocenter.
5. **Séparation des devoirs** — contrôlée **côté serveur** (403), pas seulement
   par un bouton masqué.
6. **Le système apprend** — le graphe s'enrichit à chaque analyse et répond de
   plus en plus sans solliciter le corpus.

---

## Les honnêtetés à assumer

- **La couverture-preuve réelle est d'environ 28 %** en production : le reste est
  couvert symboliquement, sans citation verbatim rattachée.
- **Les poids de scoring sont une calibration**, pas une source réglementaire.
  Seule la *structure* de la formule est réglementaire (FATF/Bâle).
- **La boucle RLHF n'est pas fermée** : le feedback est collecté et structuré,
  le réentraînement automatique reste à brancher (cadence retenue :
  hebdomadaire, avec garde-fou anti-dérive).
- **Le rapport hebdomadaire n'est pas encore planifié** automatiquement.
- **Le ML est entraîné sur un jeu public** (IBM AML), pas sur des données
  bancaires tunisiennes réelles.
- **Pas d'OCR** : les justificatifs scannés ne sont pas lus.

---

## Commandes utiles

```powershell
cd C:\Users\louhi\Downloads\projet_pfe\02_chunking_v2

# ── Tout en conteneurs (recommandé) ──────────────────────
docker compose up -d --build
#    Interface : http://localhost:8080
#    API       : http://localhost:8001

# ── Ou en développement (rechargement à chaud) ───────────
.\venv\Scripts\Activate.ps1
docker compose up -d postgres neo4j weaviate redis
python -m uvicorn api.main:app --port 8001
cd project ; npm run dev

# Évaluation complète (100 scénarios + hallucination)
python evaluate_compliance.py --generated --stress-semantic 20 --output resultats_eval.json --markdown rapport_eval.md

# Diagnostics
python test_postgres.py      # connexion base
python test_smtp.py          # envoi email
```

---

*Dossier de soutenance — mis à jour le 22 juillet 2026.*
