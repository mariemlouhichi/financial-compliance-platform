# Plateforme AML/KYC — Surveillance multi-agent des opérations financières

> Projet de fin d'études 2025/2026 — Mariem Louhichi
> Système neuro-symbolique de détection du blanchiment d'argent, appliqué à la
> réglementation bancaire tunisienne (BCT, CTAF) et aux standards FATF.

---

## Le problème

Une banque doit décider, pour chaque opération, si elle est conforme, si elle
exige une vigilance renforcée, ou si elle doit faire l'objet d'une déclaration de
soupçon. Cette décision doit être **justifiée juridiquement** — pas seulement
statistiquement — et **traçable** devant un contrôle.

Un modèle de machine learning seul ne peut pas y répondre : il ne cite aucun
article de loi. Un moteur de règles seul ne détecte pas les comportements
inhabituels. Ce projet combine les deux, avec une règle stricte : **le
déterministe prévaut toujours**.

---

## L'approche

Chaque transaction traverse un pipeline de **9 étapes** :

```
[0] Justificatifs   analyse documentaire — le document prouve-t-il l'opération ?
[1] Niveau 1        règles fixes : sanctions, pays à risque, seuils BCT (0 % LLM)
[2] Client Scoring  risque structurel du client (somme pondérée explicable)
[3] Planning Agent  génération des questions réglementaires à poser
[4] Retrieval       recherche hybride : graphe de connaissances + corpus vectoriel
[5] Compliance      cœur neuro-symbolique — règles appliquées, obligations violées
[6] ML              détection comportementale (LightGBM + LSTM-Autoencoder)
[7] SAR             rapport de soupçon rédigé et traçable
[8] Workflow        machine à états + file de validation humaine
```

### Trois principes de conception

**Le ML ne peut que renforcer.** Un modèle statistique n'est pas une source de
droit : il peut aggraver une décision, jamais innocenter une opération que les
règles ont signalée.

**Aucune règle sans preuve.** Chaque règle appliquée est reliée à son texte
source par une chaîne d'ancrage
`Rule → Chunk → LegalUnit → Document`, avec citation verbatim. C'est ce qui
explique un **taux d'hallucination de 0 %**.

**Le droit contraignant d'abord.** La réglementation tunisienne (BCT, loi
2015-26) oblige ; la FATF est une taxonomie de référence. Les deux taux de
couverture sont mesurés séparément.

---

## Résultats

```
Accuracy décisionnelle        : 100/100      (cible ≥ 85 %)
Couverture BCT contraignante  :  84,1 %
Couverture FATF (référence)   :  94,7 %      (cible ≥ 90 %)
Taux d'hallucination          :   0,00 %     (cible < 2 %)

Modèle ML — PR-AUC 0,9528 · F1 0,9640 · précision 0,9869 · rappel 0,9421
Base de connaissance — 34 documents, 6 678 chunks, 49 règles de référence
```

---

## Architecture

```
Interface React (nginx)  →  API FastAPI  →  ┬─ PostgreSQL  (dossiers d'analyse)
   14 pages, 4 espaces      pipeline        ├─ Neo4j       (graphe réglementaire)
   par rôle                 multi-agent     ├─ Weaviate    (recherche vectorielle)
                                            └─ Redis       (historique client)
```

Quatre espaces cloisonnés par rôle : **utilisateur** (soumission et suivi),
**conformité** (instruction et validation), **audit** (investigation, lecture
seule), **administration** (pilotage). La validation d'un dossier réglementaire
est réservée au Compliance Officer — contrôle appliqué **côté serveur**.

---

## Démarrage rapide

### Tout en conteneurs

```bash
cp groq_keys.example.json groq_keys.json      # renseigner une clé Groq
cp smtp_config.example.json smtp_config.json  # optionnel (emails)

docker compose up -d --build
```

- Interface : http://localhost:8090
- API : http://localhost:8001

### En développement

```bash
python -m venv venv && source venv/bin/activate   # ou .\venv\Scripts\Activate.ps1
pip install -r requirements.txt

docker compose up -d postgres neo4j weaviate redis
python -m uvicorn api.main:app --port 8001

cd project && npm install && npm run dev
```

---

## Données non incluses

Trois jeux de données sont exclus du dépôt pour des raisons de taille :

| Fichier | Taille | Comment l'obtenir |
|---|---|---|
| `data/entities.ftm.json` | 853 Mo | Jeu PEP OpenSanctions (`wd_peps`), téléchargeable publiquement |
| `data/peps.json` | 83 Mo | Généré : `python build_peps_json.py` |
| `output/chunks_with_embeddings.json` | 114 Mo | Régénéré : `python embed_to_weaviate.py` |

Le corpus réglementaire (34 PDF) se place dans un dossier pointé par la variable
d'environnement `REG_DIR`.

L'application **démarre sans ces fichiers** : chaque composant absent est
signalé et dégradé proprement (la persistance bascule sur des fichiers JSON, les
pages dépendant de Neo4j affichent un message explicite).

---

## Documentation

Le dossier [`docs_soutenance/`](docs_soutenance/) contient 24 documents décrivant
chaque composant : objectifs, architecture, choix de conception, algorithmes,
résultats et **limites assumées**.

Commencer par [`docs_soutenance/00_index.md`](docs_soutenance/00_index.md).

---

## Limites assumées

Ce projet ne prétend pas être un produit bancaire. Les principales limites, toutes
documentées :

- La **couverture-preuve réelle** en production est d'environ 28 % ; le reste des
  obligations est couvert symboliquement.
- Les **poids de scoring sont une calibration**, pas une source réglementaire.
- La **boucle d'apprentissage par retour humain est automatisée** (cycle
  hebdomadaire planifié, refus de réentraîner sous 20 étiquettes, garde-fou de
  dérive quotidien) ; seule l'**exécution** du réentraînement dépend de la
  disponibilité du jeu d'entraînement.
- Le modèle ML est entraîné sur un **jeu de données public** (IBM AML), pas sur
  des données bancaires réelles.
- **Pas d'OCR** : les justificatifs scannés ne sont pas lus.

---

## Sécurité

Aucun secret n'est versionné. Les fichiers `groq_keys.json`, `smtp_config.json`,
`db_config.json`, `platform.db` et la clé de signature des jetons sont exclus par
`.gitignore` — des fichiers `*.example.json` documentent leur structure.

Les mots de passe présents dans `docker-compose.yml` sont des identifiants de
**développement local** ; ils doivent être remplacés pour tout déploiement réel.

---

*PFE 2025/2026 — ESPRIT*
