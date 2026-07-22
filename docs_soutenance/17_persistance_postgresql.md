# 17 — Persistance des dossiers en PostgreSQL

> Passer d'un stockage en fichiers JSON à une vraie base transactionnelle —
> sans jamais casser le fonctionnement local.

---

## 1. Le problème à résoudre

Au départ, les dossiers d'analyse vivaient **en mémoire**. Un redémarrage
d'uvicorn effaçait tout l'historique : l'utilisatrice se reconnectait et ne
retrouvait plus ses dossiers. Une première correction les a écrits dans
`output/jobs/{tx_id}.json`, ce qui a résolu la perte de données.

Mais deux limites subsistaient :

1. **Aucune requête possible.** Répondre à « toutes les transactions de plus de
   10 000 USD en juillet » imposait de charger et de parcourir tous les fichiers.
   Pas de recherche par client, période ou décision.

2. **Incompatible avec un déploiement conteneurisé.** Sur Azure Container Apps,
   le système de fichiers d'un conteneur est **éphémère** : à chaque
   redémarrage ou redéploiement, `output/jobs/` disparaîtrait. Les données du
   jury seraient perdues en cours de démonstration.

D'où la migration vers **PostgreSQL**, faite **avant** le déploiement — précisément
pour ne pas avoir à ré-architecturer une fois en ligne.

---

## 2. Le principe : une couche d'accès à deux backends

Plutôt que de remplacer brutalement les fichiers par du SQL, le code introduit une
**couche d'abstraction** (`api/store.py`) avec deux implémentations
interchangeables :

```
              ┌──────────────────────────────┐
   API  ────▶ │  STORE  (sélection AUTO)     │
              └───────────┬──────────────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
  PostgresJobStore                 FileJobStore
  (base transactionnelle)          (repli JSON, dev local)
```

La sélection est **automatique au démarrage** :

| Situation | Backend retenu |
|---|---|
| Connexion PostgreSQL disponible | **PostgreSQL** |
| Driver absent, base injoignable, aucune config | **Fichiers JSON** (repli) |

Le point essentiel : `get_store()` **ne lève jamais d'exception**. Driver
manquant, base éteinte, mauvais mot de passe — tout bascule proprement sur le
repli avec un message clair. L'API démarre toujours.

C'est ce qui permet de travailler sans PostgreSQL installé : rien ne casse, on
perd seulement les requêtes SQL.

---

## 3. Le schéma

Un compromis délibéré entre le relationnel et le document :

```sql
CREATE TABLE jobs (
    tx_id       TEXT PRIMARY KEY,
    owner       TEXT,
    source      TEXT,
    status      TEXT,
    decision    TEXT,
    risk_level  TEXT,
    state       TEXT,
    tx_type     TEXT,
    amount      NUMERIC,
    currency    TEXT,
    created_at  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ DEFAULT now(),
    data        JSONB NOT NULL
);

CREATE INDEX idx_jobs_owner    ON jobs (owner);
CREATE INDEX idx_jobs_created  ON jobs (created_at);
CREATE INDEX idx_jobs_decision ON jobs (decision);
```

### Pourquoi ce modèle hybride

Un dossier d'analyse est un objet **profondément imbriqué** : huit étapes, chacune
avec ses sorties, ses règles, ses extraits réglementaires, son SAR. Le normaliser
en une quinzaine de tables aurait produit des jointures lourdes pour un gain
discutable.

La solution retenue :

- **Colonnes en clair** pour ce qui sert aux requêtes (montant, décision, client,
  date, état) — indexées, donc rapides ;
- **Blob `JSONB`** pour l'objet complet — reconstruction fidèle du dossier, sans
  perte, sans schéma rigide à faire évoluer à chaque ajout d'étape.

C'est le meilleur des deux mondes : on interroge en SQL, on restitue en JSON.

Les fichiers volumineux (justificatifs encodés en base64) sont **exclus** avant
écriture, dans les deux backends.

### Écriture idempotente

Chaque enregistrement est un **UPSERT** (`INSERT … ON CONFLICT (tx_id) DO
UPDATE`). Un dossier peut donc être persisté à chaque changement d'état sans
risque de doublon, et la migration peut être relancée sans effet de bord.

---

## 4. Configuration

Trois sources, par ordre de priorité :

1. **`DATABASE_URL`** (variable d'environnement) —
   `postgresql://user:pass@host:5432/dbname`. C'est le format standard, et celui
   que fournit Azure : le déploiement ne demandera aucune modification de code.
2. **`db_config.json`** à la racine — pratique en local, ignoré par Git.
3. **Rien** → repli fichiers.

Un **délai de connexion de 3 secondes** est imposé : si PostgreSQL est configuré
mais éteint, le démarrage bascule vite sur le repli au lieu de se figer.

Le service est déclaré dans `docker-compose.yml` (`postgres:16-alpine`, volume
persistant, `healthcheck`), donc disponible en une commande.

---

## 5. Les requêtes complexes

C'est le bénéfice concret de la migration. La couche expose une méthode `query()`
traduite en **SQL indexé** côté PostgreSQL, et en filtrage mémoire équivalent
côté repli — même sémantique, deux implémentations.

Exposée par l'API :

```
GET /api/transactions/query
      ?min_amount=10000
      &decision=STR
      &client=mariem
      &date_from=2026-07-01
      &date_to=2026-07-31
```

| Filtre | Traduction SQL |
|---|---|
| `min_amount` | `amount >= …` |
| `decision` | `decision = …` |
| `client` | `owner ILIKE '%…%'` (recherche partielle) |
| `date_from` / `date_to` | `created_at BETWEEN …` |

Deux détails soignés :

- une date de fin au format jour seul (`2026-07-31`) est étendue à
  `23:59:59`, sinon la journée entière serait exclue ;
- l'endpoint est **réservé** aux rôles audit et conformité.

La réponse indique le backend utilisé :

```json
{ "count": 4, "backend": "PostgreSQL", "results": [ … ] }
```

### Dans l'interface

La page **Investigation** (espace audit) est branchée dessus : les filtres
Client / Montant minimum / Période / Décision déclenchent une vraie requête
serveur, et un badge affiche **« N résultat(s) · moteur : PostgreSQL »**.

Ce badge n'est pas décoratif — c'est la preuve visuelle, vérifiable en direct,
que la recherche s'exécute en base et non dans le navigateur.

---

## 6. Migration de l'existant

Un script dédié importe les dossiers déjà présents en fichiers :

```bash
python migrate_jobs_to_postgres.py
```

Il est **non destructif** (les fichiers restent en place) et **idempotent**
(relançable sans risque, grâce à l'UPSERT). Sur l'installation de test :
**15 dossiers migrés**.

---

## 7. Vérification

Trois niveaux, du plus simple au plus probant :

1. **Au démarrage** — le journal affiche
   `[STORE] ✅ persistance PostgreSQL active`, puis
   `15 dossier(s) rechargé(s) depuis PostgreSQL`.

2. **Test isolé** — `python test_postgres.py` vérifie la configuration, le
   driver, la connexion, un aller-retour écriture/lecture et une requête filtrée.

3. **Contre-vérification directe en base** — la preuve définitive :

   ```bash
   docker exec aml_postgres psql -U aml -d amlkyc -c \
     "SELECT count(*) FROM jobs WHERE amount >= 10000 AND decision = 'STR';"
   ```

   Si le nombre correspond à celui du badge dans l'interface pour les mêmes
   filtres, la chaîne complète est validée. C'est le test qui a été effectué :
   **4 résultats des deux côtés**.

---

## 8. Un bug révélé par la migration

Le dossier de test inséré par `test_postgres.py` ne contenait pas de clé `steps`.
L'endpoint `/api/stats` y accédait sans garde (`j["steps"]`) et renvoyait une
erreur 500 sur tout le Dashboard.

Deux corrections :

- **le vrai correctif** — `stats` utilise désormais `j.get("steps") or {}` :
  **aucun dossier, même malformé, ne peut plus faire tomber les statistiques** ;
- **l'hygiène** — le script de test supprime son dossier de test à la fin, via
  une méthode `delete()` ajoutée aux deux backends.

C'est un effet secondaire utile de la migration : elle a exposé une fragilité qui
existait déjà mais n'avait jamais été déclenchée.

---

## 9. Limites assumées

- **Lecture depuis la mémoire.** Les dossiers sont chargés en mémoire au démarrage
  et servis depuis là ; PostgreSQL assure la durabilité et les requêtes complexes.
  Ce modèle convient à une instance unique ; plusieurs instances exigeraient de
  lire directement en base.
- **Pas de migrations versionnées** (type Alembic) : le schéma est créé par
  `CREATE TABLE IF NOT EXISTS`. Suffisant ici, insuffisant pour un vrai cycle de
  vie applicatif.
- **Pas de politique de rétention ni d'archivage** des dossiers anciens.

---

## 10. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `api/store.py` | `FileJobStore`, `PostgresJobStore`, `get_store()`, `query()` |
| `api/main.py` | `STORE`, `_persist_job`, `_load_jobs`, `/api/transactions/query` |
| `migrate_jobs_to_postgres.py` | Import unique des dossiers fichiers |
| `test_postgres.py` | Diagnostic de connexion isolé |
| `docker-compose.yml` | Service `postgres` + volume + healthcheck |
| `db_config.json` | Identifiants locaux (ignoré par Git) |
| `project/src/pages/Audit.tsx` | Recherche SQL côté serveur |
