# 14 — La plateforme web (API FastAPI + interface React)

> Le pipeline d'agents devient un produit utilisable : quatre espaces métier,
> une API, et une interface qui rend le raisonnement visible.

---

## 1. Objectif

Un moteur d'analyse qui ne tourne qu'en ligne de commande ne démontre pas
grand-chose. La plateforme web transforme le pipeline en **outil de travail** :
un client soumet une opération, un agent conformité l'instruit, un auditeur
enquête, un administrateur pilote.

Elle a aussi un rôle pédagogique assumé : rendre **visible** le raisonnement des
agents. La page Pipeline Live montre les huit étapes, les questions posées, les
extraits réglementaires retenus et les règles appliquées — ce qui serait invisible
dans une API classique.

---

## 2. Architecture générale

```
┌──────────────────────────────────────────────────────────┐
│  Navigateur — React 18 + TypeScript + Vite + Tailwind    │
│  14 pages, routage par hash, 4 espaces filtrés par rôle  │
└───────────────────────┬──────────────────────────────────┘
                        │  REST (JSON) + Bearer token
                        │  SSE (flux temps réel)
┌───────────────────────▼──────────────────────────────────┐
│  API — FastAPI / uvicorn  (port 8001)                    │
│  auth · pipeline · corpus · règles · graphe · superviseur │
└───────────────────────┬──────────────────────────────────┘
                        │
    ┌───────────┬───────┴────────┬──────────────┬──────────┐
    ▼           ▼                ▼              ▼          ▼
 PostgreSQL   Neo4j          Weaviate        Redis      SQLite
 (dossiers)  (graphe)      (vecteurs)     (historique) (comptes)
```

Chaque brique a un rôle distinct :

| Brique | Rôle | Dégradation si absente |
|---|---|---|
| **PostgreSQL** | Dossiers d'analyse, requêtes complexes | Repli automatique sur fichiers JSON |
| **Neo4j** | Graphe de connaissances, règles | Message 503 lisible sur les pages concernées |
| **Weaviate** | Recherche vectorielle | Le graphe seul répond |
| **Redis** | Historique glissant client (24-72 h) | Dégradation en mémoire |
| **SQLite** | Comptes utilisateurs | — |

Ce principe de **dégradation gracieuse** est systématique : aucune brique
manquante ne fait tomber l'application entière. C'est ce qui rend la
démonstration robuste.

---

## 3. L'API

Organisée par domaine fonctionnel :

| Domaine | Endpoints principaux |
|---|---|
| **Authentification** | `/api/auth/register`, `/verify`, `/login`, `/forgot`, `/reset`, `/profile` |
| **Pipeline** | `POST /api/analyze`, `GET /api/result/{tx_id}` |
| **Dossiers** | `GET /api/transactions`, `GET /api/transactions/query` |
| **Superviseur** | `POST /api/validate/{tx_id}`, `/api/supervisor/queue`, `/api/supervisor/history` |
| **Rapports** | `GET /api/sar/{tx_id}/markdown`, `POST /api/reports/weekly` |
| **Connaissance** | `/api/corpus/documents`, `/api/corpus/chunks`, `/api/corpus/files`, `/api/rules`, `/api/graph/stats`, `/api/graph/sample` |
| **Justificatifs** | `POST /api/justificatif/{tx_id}` |
| **Simulateur** | `/api/simulator/start`, `/stop`, `/status`, `/speedrun` |
| **Temps réel** | `GET /api/events` (SSE) |
| **Supervision** | `GET /api/health`, `GET /api/stats` |

### Le pipeline est asynchrone

`POST /api/analyze` ne bloque pas : il retourne immédiatement un `tx_id` et lance
l'analyse dans un **thread**. Le client suit ensuite la progression via
`GET /api/result/{tx_id}`. C'est nécessaire — une analyse complète prend de
quelques secondes à une minute, dominée par l'étape de retrieval.

Ce choix a une conséquence technique importante : le pipeline s'exécutant hors
de la boucle asyncio, toute notification temps réel doit franchir la frontière
thread → boucle événementielle (voir document 18).

---

## 4. Les quatre espaces

L'interface est découpée par **rôle**, pas par fonctionnalité. Chaque utilisateur
ne voit que ce qui le concerne.

### Espace utilisateur (rôle `USER`)

| Page | Rôle |
|---|---|
| **Nouvelle transaction** | Formulaire de soumission, avec alertes métier en direct |
| **Mes dossiers** | Suivi personnel, notifications d'état, historique |
| **Mon profil** | Coordonnées KYC — alimentent réellement le scoring client |

### Espace conformité & connaissance (`COMPLIANCE_OFFICER`)

| Page | Rôle |
|---|---|
| **File superviseur** | Instruction et validation des dossiers |
| **Rapports & SAR** | Historique, export CSV, rapports PDF |
| **Règles extraites** | Les 49 règles de référence + les règles apprises |
| **Graphe de connaissances** | Exploration du graphe Neo4j |
| **Corpus réglementaire** | Les 34 PDF sources et leur structure |

### Espace administration (`ADMIN`)

| Page | Rôle |
|---|---|
| **Dashboard** | Indicateurs, répartitions, évolution de l'activité |
| **Flux bancaire en direct** | Simulateur de flux et scénarios de démonstration |

### Espace audit (`AUDITOR`)

| Page | Rôle |
|---|---|
| **Investigation** | Recherche transversale SQL, piste d'audit, indicateurs qualité |

La page **Pipeline Live** est transversale : elle est accessible à tous les rôles
autorisés, puisqu'elle est l'explication du raisonnement.

---

## 5. Un choix structurant : zéro dépendance ajoutée

Le frontend n'utilise que React, TypeScript, Vite, Tailwind et `lucide-react`
pour les icônes. **Aucune bibliothèque de graphiques, de routage ou de rendu
Markdown n'a été ajoutée.** Tout ce qui manquait a été écrit à la main :

| Composant maison | Remplace |
|---|---|
| `charts.tsx` — `BarList`, `Donut`, `AreaChart`, `ScoreGauge` | Recharts / Chart.js |
| `GraphView.tsx` — rendu *force-directed* SVG déterministe | vis.js / cytoscape |
| `router.tsx` — routage par hash | React Router |
| `Markdown.tsx` — rendu Markdown + export PDF | react-markdown |

Pourquoi ce parti pris :

- **maîtrise totale du rendu** — les couleurs de statut (PASS vert, STR rouge…)
  sont cohérentes partout, ce qu'une bibliothèque générique impose difficilement ;
- **poids et sécurité** — moins de dépendances, moins de surface d'attaque et un
  *bundle* léger ;
- **reproductibilité** — le projet s'installe sans surprise de version.

Le coût est réel : il a fallu écrire ces composants. Le bénéfice est un rendu
homogène et un projet qui ne dépend pas d'un écosystème mouvant.

⚠️ **Piège rencontré** : `lucide-react` est figé en version 0.344. Certaines
icônes récentes (`IdCard`, `BriefcaseBusiness`) n'existent pas et un import
manquant casse **tout le bundle** (page blanche). Toute nouvelle icône doit être
vérifiée avant usage.

---

## 6. Thème clair / sombre

Le mode sombre est implémenté par **surcouche CSS** plutôt qu'en modifiant les
pages : une classe `.dark` sur `<html>` déclenche environ 67 règles qui
réassignent les utilitaires récurrents (fonds, textes, bordures, champs de
saisie). Aucune page n'a été touchée.

Le thème est persisté dans le navigateur et appliqué **avant le rendu**, pour
éviter le flash blanc au chargement. La bascule est une icône seule (soleil/lune)
placée dans l'en-tête de page, à côté des actions.

---

## 7. Ce que l'interface rend visible

C'est l'apport pédagogique du projet, et le meilleur support de démonstration :

- **les huit étapes** du pipeline avec leur durée réelle ;
- **les questions réglementaires** générées, chacune avec son déclencheur et son
  explication (« pourquoi le système pose cette question ») ;
- **les extraits réglementaires** récupérés par question, avec leur source, leur
  article et le texte verbatim ;
- **la provenance** de chaque réponse : graphe ou corpus ;
- **les règles appliquées** et les obligations violées, avec citation ;
- **la contribution ML** avec ses trois features SHAP interprétées ;
- **la divergence** entre chemin réglementaire et chemin statistique.

Un jury peut donc vérifier le raisonnement, pas seulement constater un verdict.

---

## 8. Limites assumées

- **Instance unique** : l'état en mémoire (dossiers chargés, bus d'événements)
  suppose un seul processus serveur. Un passage à plusieurs instances demanderait
  un cache et un bus partagés (Redis).
- **Pas encore conteneurisée de bout en bout** : `docker-compose` couvre les
  bases de données, pas encore l'API ni le frontend.
- **Interface en français uniquement**, cohérent avec le contexte (BCT, CTAF).

---

## 9. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `api/main.py` | API FastAPI, pipeline, endpoints métier |
| `api/auth.py` | Authentification, profils, emails |
| `api/store.py` | Couche de persistance des dossiers |
| `project/src/pages/*.tsx` | Les 14 pages |
| `project/src/components/Layout.tsx` | Barre latérale, en-tête, thème |
| `project/src/components/charts.tsx` | Graphiques SVG maison |
| `project/src/api/client.ts` | Client HTTP + flux SSE |
| `project/src/auth.ts` | Session, rôles, pages autorisées |
