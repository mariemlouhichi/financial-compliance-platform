# 23 — Conteneurisation et intégration continue

> Emballer la plateforme entière pour qu'elle démarre en une commande — et
> préparer le déploiement Azure.

---

## 1. Objectif

Jusqu'ici, faire tourner le projet demandait : lancer Docker pour les bases,
activer un environnement Python, démarrer uvicorn, puis démarrer Vite dans un
second terminal. C'est acceptable en développement, impossible à demander à un
jury — et incompatible avec un déploiement.

L'objectif de cette phase : **une seule commande démarre tout**, et le résultat
est déployable tel quel sur Azure.

---

## 2. Architecture cible

```
                    http://localhost:8080
                            │
                            ▼
              ┌──────────────────────────┐
              │  web  (nginx)            │
              │  • sert le bundle React  │
              │  • relaie /api → api     │
              └────────────┬─────────────┘
                           │  réseau interne Docker
              ┌────────────▼─────────────┐
              │  api  (FastAPI/uvicorn)  │
              └────────────┬─────────────┘
                           │
   ┌──────────┬────────────┼────────────┬──────────┐
   ▼          ▼            ▼            ▼          ▼
postgres    neo4j      weaviate      redis    (corpus PDF
(dossiers) (graphe)   (vecteurs)  (historique)  monté)
```

### Un seul point d'entrée public

nginx sert l'interface **et** relaie `/api/...` vers le conteneur de l'API.
Conséquence directe : le navigateur ne parle qu'à **une seule origine**, donc
**plus aucun problème de CORS**. C'est aussi la configuration la plus simple à
déployer sur Azure — une seule adresse publique à exposer.

Le frontend est construit avec `VITE_API_BASE_URL=""`, ce qui fait qu'il appelle
`/api/...` en chemin **relatif**.

---

## 3. L'image de l'API

### Le problème de la taille

Les dépendances du projet sont lourdes : `torch`, `docling`, `sentence-transformers`,
`shap`, `pandas`. Sans précaution, l'image dépasse facilement 5 Go — un vrai
problème avec un crédit Azure étudiant limité.

Trois optimisations appliquées :

**1. torch en version CPU.** Installé explicitement depuis l'index CPU **avant**
les autres dépendances :

```dockerfile
RUN pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
```

Sans cela, pip résout la variante CUDA — environ **2 Go inutiles**, puisqu'il n'y
a aucun GPU en production.

**2. Séparation des dépendances.** `requirements.txt` ne contient que le
**runtime** ; `requirements-dev.txt` ajoute `optuna` (recherche
d'hyperparamètres), `torchvision` et `pytest`, qui n'ont rien à faire dans
l'image de production.

Au passage, cette phase a révélé que `requirements.txt` était **gravement
incomplet** : il ne mentionnait ni FastAPI, ni uvicorn, ni docling, ni torch.
Le projet n'aurait pas pu être réinstallé ailleurs. Il a été reconstitué à
partir des dépendances réellement utilisées, avec les versions validées.

**3. Un `.dockerignore` strict.** C'est le levier le plus efficace :

| Exclu | Poids | Pourquoi |
|---|---|---|
| `venv/` | 1 887 Mo | Réinstallé dans l'image |
| `data/entities.ftm.json` | 853 Mo | Source brute OpenSanctions — sert **uniquement** à construire `peps.json`, qui lui est embarqué (83 Mo) |
| `output/chunks_with_embeddings.json` | 114 Mo | Les vecteurs sont déjà dans Weaviate |
| `project/node_modules/` | 139 Mo | Réinstallé par `npm ci` |
| Modèles ML non utilisés | ~35 Mo | Seule la famille `aml_ml_demo_*` est chargée |

**Le contexte de build passe d'environ 3 Go à ~120 Mo.**

### Ce qui n'est pas dans l'image

Deux catégories, volontairement montées en volume :

- **Les secrets** — `groq_keys.json`, `smtp_config.json`, `platform.db`. Un secret
  dans une image est un secret publié dès que l'image est partagée.
- **Le corpus PDF** (68 Mo) — il se trouve hors du contexte de build
  (`PFE_AML_KYC/01_regulations`). Plutôt que d'élargir le contexte, il est monté
  en lecture seule. Le code a été adapté : `REG_DIR` est désormais une variable
  d'environnement.

### Bonnes pratiques appliquées

- **Utilisateur non privilégié** (`amlapp`, UID 1000) — le conteneur ne tourne
  pas en root.
- **`libgomp1`** installé : LightGBM et XGBoost en ont besoin à l'exécution, et
  l'oubli produit une erreur obscure au premier appel du modèle.
- **Sonde de santé** sur `/api/health`, avec un `start-period` de **120 s** : le
  chargement des listes de sanctions et des 660 000 PEP prend une trentaine de
  secondes, un conteneur serait déclaré défaillant à tort.
- **Cache Hugging Face en volume** : sans lui, les modèles d'embedding et de mise
  en page seraient retéléchargés à chaque redémarrage.

---

## 4. L'image du frontend

Construction en **deux étapes** :

```dockerfile
FROM node:20-alpine AS build     # npm ci → typecheck → vite build
FROM nginx:alpine                # ne garde que /dist
```

L'image finale ne contient **ni Node, ni `node_modules`** — uniquement les
fichiers statiques compilés, quelques mégaoctets.

Le `typecheck` est exécuté **pendant le build** : une erreur de typage empêche
la production d'un artefact. C'est un garde-fou simple et efficace.

### La configuration nginx — le piège du SSE

Le point qui aurait pu casser silencieusement le temps réel : **nginx met en
tampon les réponses par défaut**. Appliqué à un flux SSE, cela signifie que les
événements sont retenus jusqu'à saturation du buffer — le push instantané ne
fonctionnerait plus, sans aucune erreur visible.

Le flux `/api/events` a donc son propre bloc, déclaré **avant** `/api/` :

```nginx
location /api/events {
    proxy_pass http://api:8001;
    proxy_http_version 1.1;
    proxy_buffering off;          # indispensable
    gzip off;                     # ne pas compresser un flux continu
    proxy_set_header Connection '';
    proxy_read_timeout 24h;       # le flux reste ouvert
}
```

Autres réglages : `client_max_body_size 25m` (les justificatifs transitent en
base64, ce qui gonfle la taille d'environ 33 %) et `proxy_read_timeout 300s` sur
`/api/` (une analyse complète peut durer une minute).

---

## 5. Démarrage

```bash
# Tout construire et démarrer
docker compose up -d --build

# Interface :  http://localhost:8080
# API        :  http://localhost:8001
```

Services démarrés : `postgres`, `neo4j`, `weaviate`, `redis`, `api`, `web`.

L'API attend que PostgreSQL, Redis et Neo4j soient **effectivement prêts**
(`depends_on` avec `condition: service_healthy`), pas seulement démarrés.

Commandes utiles :

```bash
docker compose ps               # état et santé des services
docker compose logs -f api      # suivi des journaux de l'API
docker compose down             # arrêt (données conservées)
docker compose down -v          # arrêt + SUPPRESSION des volumes
```

---

## 6. L'intégration continue

Un workflow GitHub Actions (`.github/workflows/ci.yml`) avec trois jobs,
volontairement **rapides et sans infrastructure** :

| Job | Ce qu'il vérifie |
|---|---|
| **frontend** | `npm ci` → `typecheck` → `build`, et publie le bundle |
| **backend** | Compilation de tous les modules Python, cohérence des fichiers de dépendances, validation de `docker-compose` |
| **docker-web** | Construit l'image du frontend, la démarre et vérifie qu'elle répond |

### Choix assumés

- **On ne réinstalle pas torch/docling dans la CI.** Cela ajouterait plusieurs
  minutes à chaque exécution pour un gain faible : la compilation détecte déjà
  toute erreur de syntaxe.
- **L'image de l'API n'est pas construite dans la CI.** Elle nécessite les
  modèles ML et les listes de sanctions, trop volumineux pour un dépôt Git. Elle
  est construite localement et au moment du déploiement.
- **Les tests nécessitant Neo4j/Weaviate/PostgreSQL restent manuels.** Les
  démarrer dans la CI serait possible mais lourd ; ce n'est pas le meilleur
  usage du temps à ce stade.

Le job `backend` inclut un contrôle utile : il vérifie qu'aucun paquet n'est
déclaré à la fois dans `requirements.txt` et `requirements-dev.txt` (risque de
conflit de versions), et que les dépendances essentielles sont bien présentes.

---

## 7. Corrections apportées par cette phase

La conteneurisation force à expliciter ce qui était implicite. Trois problèmes
réels ont été révélés :

| Problème | Conséquence en conteneur | Correction |
|---|---|---|
| `requirements.txt` incomplet | Image inutilisable | Reconstitué (22 paquets runtime) |
| Connexion Neo4j codée en dur (`bolt://localhost`) à un endroit | L'API n'aurait pas joint le graphe | Variables d'environnement, comme l'autre point de connexion |
| `REG_DIR` en chemin absolu relatif au projet | Corpus PDF introuvable | Variable d'environnement `REG_DIR` |

Un piège de configuration a également été évité : dans le réseau Docker,
Weaviate écoute sur le port **8080**, alors que `8088` n'est que le mapping vers
l'hôte. Utiliser 8088 depuis l'API en conteneur aurait échoué.

Un `SyntaxWarning` présent depuis longtemps (séquence d'échappement invalide
dans le docstring de `api/main.py`) a aussi été corrigé — il polluait chaque
démarrage et aurait pollué les journaux de la CI.

---

## 8. Ce qui reste avant Azure

- **Bus d'événements par processus** : le temps réel SSE suppose une instance
  unique. Plusieurs instances nécessiteraient un broker partagé (Redis pub/sub,
  déjà présent dans l'infrastructure).
- **Neo4j, Weaviate et Redis auto-hébergés** consommeraient crédit et quota. Sur
  Azure for Students, il est préférable d'utiliser les offres gratuites externes
  (Neo4j Aura Free, Weaviate Cloud, Redis Cloud) et de ne déployer que `api` et
  `web` en Container Apps avec PostgreSQL Flexible Server.
- **Les secrets** devront passer par la configuration Azure, pas par des fichiers
  montés.

---

## 9. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `Dockerfile` | Image de l'API (torch CPU, utilisateur non root, healthcheck) |
| `.dockerignore` | Réduction du contexte de 3 Go à ~120 Mo |
| `requirements.txt` | Dépendances runtime (reconstituées) |
| `requirements-dev.txt` | Entraînement et tests |
| `project/Dockerfile` | Build Vite en 2 étapes → nginx |
| `project/nginx.conf` | SPA + relais API + **configuration SSE** |
| `project/.dockerignore` | Exclusion de `node_modules` |
| `docker-compose.yml` | Les 6 services, volumes, dépendances de santé |
| `.github/workflows/ci.yml` | Intégration continue |
