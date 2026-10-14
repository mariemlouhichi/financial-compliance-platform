# 24 — Déploiement sur Azure

> Mettre la plateforme en ligne, sur une simple adresse web, pour que le jury la
> teste depuis son navigateur — sans Docker, sans Python, sans rien installer.

---

## ⚠️ À FAIRE EN PREMIER si tu recrées le déploiement

Le déploiement a été **supprimé volontairement** après validation, pour préserver
le crédit Azure for Students jusqu'à la soutenance. L'ancienne URL ne répond donc
plus (`DNS_PROBE_FINISHED_NXDOMAIN`) : c'est normal.

**Le piège au redéploiement.** Le fichier `.github/workflows/deploy-web.yml`
contient, ligne 33 :

```yaml
API_URL: https://aml-api.mangoisland-00189a40.francecentral.azurecontainerapps.io
```

`mangoisland-00189a40` est le suffixe **généré aléatoirement par Azure** à la
création de l'environnement Container Apps. En recréant l'environnement, ce
suffixe sera **différent**.

Cette adresse est figée dans le bundle React au moment du build. Si elle n'est pas
mise à jour :

- l'interface s'affiche normalement, page de connexion comprise ;
- **tous les appels échouent** — connexion, analyse, validation ;
- **aucun message d'erreur explicite** n'indique la cause.

**Ordre à respecter :**

1. Créer l'environnement Azure et déployer `aml-api`.
2. Relever le FQDN réel de l'API :
   ```powershell
   az containerapp show --name aml-api --resource-group $RG `
      --query properties.configuration.ingress.fqdn -o tsv
   ```
3. Reporter cette adresse dans `deploy-web.yml` ligne 33 (préfixée de `https://`).
4. **Seulement ensuite**, lancer le workflow de déploiement de l'interface.

> `redeploy_api_azure.ps1` ne couvre pas ce cas : il se contente de mettre à jour
> l'image d'une Container App **déjà existante** (`az containerapp update`).


---

## 1. Objectif

La conteneurisation (document 23) rendait le projet démarrable en une commande,
mais toujours **sur la machine de démonstration**. L'étape suivante : héberger la
plateforme sur Internet, avec une **URL publique** que n'importe qui peut ouvrir.

Contrainte de fond : l'abonnement est un **Azure for Students** (crédit limité,
quotas réduits, certaines fonctions bridées). Toutes les décisions ci-dessous en
découlent.

---

## 2. Architecture déployée

```
        https://aml-web.<...>.azurecontainerapps.io   ← le jury
                          │
                          ▼
             ┌────────────────────────┐
             │  aml-web  (nginx)      │   Container App · 0,5 vCPU / 1 Go
             │  sert le bundle React  │
             └───────────┬────────────┘
                         │  appels /api directs (HTTPS) + CORS
                         ▼
             ┌────────────────────────┐
             │  aml-api  (FastAPI)    │   Container App · 2 vCPU / 4 Go
             │  pipeline multi-agent  │
             └───────────┬────────────┘
       ┌─────────┬───────┼────────┬──────────┐
       ▼         ▼       ▼        ▼          ▼
  PostgreSQL   Neo4j  Weaviate  Redis     Groq
  Flexible     Aura    Cloud   Upstash    (LLM)
  (Azure)    (externe)(externe)(externe)
```

### Le partage des rôles

Seuls **l'API et le web** tournent sur Azure (Container Apps). Les bases
spécialisées restent sur leurs **offres gratuites externes** — c'est le choix
anticipé au document 23 : auto-héberger Neo4j, Weaviate et Redis sur Azure
consommerait crédit et quota pour rien.

| Composant | Hébergement | Pourquoi là |
|---|---|---|
| `aml-web` (front) | Azure Container Apps | Point d'entrée public |
| `aml-api` (pipeline) | Azure Container Apps | Cœur de l'analyse |
| Dossiers / transactions | **PostgreSQL Flexible Server** (Azure) | Persistance durable, proche de l'API |
| Graphe réglementaire | Neo4j Aura Free | Offre gratuite gérée |
| Vecteurs (RAG) | Weaviate Cloud | Offre gratuite gérée |
| Historique client | Redis Upstash | Offre gratuite gérée |
| Images Docker | Azure Container Registry | Registre privé du projet |

---

## 3. La chaîne de ressources Azure

Créées dans cet ordre, chacune dépendant de la précédente :

1. **Groupe de ressources** `aml-kyc-rg` (région *France Central*).
2. **Container Registry** `amlkyclouhichi2026` — le registre privé des images.
3. **PostgreSQL Flexible Server** `aml-pg-louhichi2026` (PostgreSQL 16, palier
   *Burstable B1ms*, base `amlkyc`), pare-feu ouvert aux services Azure, **SSL
   obligatoire**.
4. **Environnement Container Apps** `aml-env` — le socle qui héberge les deux
   applications.
5. **Application API**, puis **application web**.

Un détail propre aux abonnements neufs : chaque service doit être **enregistré
une fois** (`az provider register`) avant utilisation — `Microsoft.ContainerRegistry`,
`Microsoft.App`, `Microsoft.DBforPostgreSQL`, `Microsoft.OperationalInsights`.
Sans cela, la création échoue sur `MissingSubscriptionRegistration`.

---

## 4. Livrer l'image de l'API — contourner les blocages

C'est la partie qui a demandé le plus d'ingénierie, à cause de deux contraintes
cumulées : un abonnement bridé et une connexion instable.

### ACR Tasks est interdit

L'approche standard — `az acr build`, qui construit l'image **dans le cloud** —
échoue sur Azure for Students : `TasksOperationsNotAllowed`. La construction
distante n'est pas autorisée sur ce type d'abonnement.

**Solution** : construire l'image **en local** (elle l'était déjà, document 23)
puis la **pousser** vers le registre. L'image de l'API pèse 3,9 Go décompressée
mais ne transfère que **~892 Mo** de couches compressées. Surtout, `docker push`
**reprend couche par couche** : une coupure ne fait pas tout recommencer.

```powershell
az acr login --name amlkyclouhichi2026
docker tag 02_chunking_v2-api:latest amlkyclouhichi2026.azurecr.io/aml-api:latest
docker push amlkyclouhichi2026.azurecr.io/aml-api:latest
```

### Deux pièges rencontrés (et corrigés)

- **`.dockerignore` et le slash final.** Le compresseur d'`az acr build` n'honore
  pas les motifs de dossier écrits `venv/` — seulement `venv` sans slash. Écrit
  avec le slash, le dossier `venv` (1,9 Go) partait dans le contexte. Corrigé en
  retirant les slashs finaux : contexte de **691 Mo → 29 Mo**.
- **Le push du frontend qui casse.** Docker Desktop ajoute par défaut un
  **manifeste d'attestation** (provenance/SBOM) : un blob supplémentaire dont
  l'échec laissait le tag incomplet dans le registre. Deux réglages ont fiabilisé
  le push sur connexion faible :
  - `docker build --provenance=false` → image simple, sans ce manifeste ;
  - `"max-concurrent-uploads": 1` (Docker Engine) → les couches partent **une à
    une**, chacune avec toute la bande passante, au lieu de se disputer le lien et
    de rompre le tuyau (*broken pipe*).

---

## 5. La liaison web ↔ API — une architecture différente du local

En local, nginx sert le front **et** relaie `/api` : une seule origine, aucun
CORS (document 23). Sur Container Apps, le web et l'API sont **deux applications
distinctes, avec deux URL distinctes** — le relais interne `api:8001` n'existe
plus.

Deux options se présentaient :

1. Reconfigurer nginx pour relayer vers l'URL publique de l'API (proxy HTTPS,
   gestion de l'en-tête `Host`, SNI) ;
2. Faire **appeler l'API directement** par le navigateur, et gérer le CORS.

**Choix : l'option 2**, plus simple et moins fragile. Le front est reconstruit
avec `VITE_API_BASE_URL` = l'URL de l'API Azure (figée dans le bundle), et le
**CORS est activé au niveau de l'ingress** de l'API :

```powershell
az containerapp ingress cors enable --name aml-api --resource-group aml-kyc-rg `
  --allowed-origins "*" --allowed-methods "*" --allowed-headers "*"
```

L'avantage décisif : **aucune reconstruction de l'image de l'API** (les 892 Mo
n'ont pas à repartir). Le CORS se règle par une commande, côté plateforme, sans
toucher au code. L'authentification se faisant par jeton `Authorization: Bearer`
(et non par cookie), un CORS ouvert suffit et reste sans risque de session.

---

## 6. Les secrets

Aucun secret ne se trouve dans l'image ni dans le dépôt Git. Ils sont **injectés
comme secrets Container Apps** (chiffrés au repos) et lus, au moment du
déploiement, depuis les fichiers **locaux gitignorés** :

| Secret | Source locale |
|---|---|
| `DATABASE_URL` (PostgreSQL) | `cloud_env.ps1` |
| Neo4j / Weaviate / Redis | `cloud_env.ps1` |
| Clés Groq (LLM) | `groq_keys.json` |
| Identifiants SMTP (email) | `smtp_config.json` |

L'application reçoit **14 variables d'environnement**, dont **6 secrets** référencés
par `secretref:`. Le même mécanisme d'interpolation que docker-compose (document 23)
sert ici : le code n'a pas changé, seule la **source** des valeurs diffère.

Le déploiement est scripté (`deploy_api_azure.ps1`, `deploy_web_azure.ps1`) :
rejouable, relisible, et sans secret écrit en dur.

---

## 7. Résultat

La plateforme est **entièrement en ligne**. Les journaux de démarrage confirment
que tous les services cloud sont joints :

```
[STORE] ✅ persistance PostgreSQL active
OK PEPs: 660 179 entrées · UN: 2 807 · FATF: 3 black, 22 grey
✅ Redis connecté (rediss://…upstash.io)
/api/health → {"neo4j": true, ...}  → 200 OK
[SCHED] ✅ planificateur démarré
```

- **Web (jury)** : `https://aml-web.<env>.francecentral.azurecontainerapps.io`
- **API** : `https://aml-api.<env>.francecentral.azurecontainerapps.io`

Le jury ouvre une adresse, se connecte, et déroule le pipeline complet — sans
aucune installation.

---

## 8. Limites et points à finaliser

Dans l'esprit des honnêtetés assumées du dossier :

- **Comptes utilisateurs à rendre permanents.** Les comptes sont dans
  `platform.db` (SQLite), volontairement hors de l'image (c'est un fichier
  sensible). Sur Azure, la base démarre donc **vide**, et sur stockage
  **éphémère** : les comptes doivent être recréés en ligne, puis persistés — soit
  par un **montage Azure Files**, soit en **pré-chargeant** des comptes vérifiés
  dans l'image. C'est le dernier réglage avant la soutenance.
- **Email sortant.** Azure restreint parfois le SMTP sortant sur les abonnements
  étudiants. Le code a un repli — le code de vérification s'affiche alors dans les
  **journaux serveur** (mode démo, document 19) — mais l'envoi réel est à
  confirmer sur la plateforme.
- **Offres gratuites à surveiller.** Le bac à sable Weaviate expire (~14 jours) et
  l'instance Neo4j Aura se met en pause après inactivité : à réveiller/vérifier la
  veille de la soutenance.
- **Une seule instance.** `min = max = 1` réplique : le temps réel SSE suppose un
  processus unique (document 23). Suffisant pour une démonstration.
- **Secrets à régénérer** après la soutenance (Groq, mot de passe d'application
  Gmail, identifiants des bases cloud).

---

## 9. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `deploy_api_azure.ps1` | Déploiement de l'API (image + 14 variables dont 6 secrets) |
| `deploy_web_azure.ps1` | Build + push + déploiement du front, activation du CORS |
| `cloud_env.ps1` | Identifiants des services cloud (gitignoré) |
| `sync_docker_env.py` | Génération du `.env` docker à partir de `cloud_env.ps1` |
| `test_cloud.py` | Vérification des connexions Neo4j / Weaviate / Redis |
| `.dockerignore` | Motifs sans slash final (compatibles Azure CLI) |

---

*Suite directe du document 23 — la conteneurisation préparait ce déploiement.*
