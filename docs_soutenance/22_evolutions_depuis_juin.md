# 22 — Évolutions des composants déjà documentés

> Les documents 01 à 08 datent de juin. Plusieurs composants ont été corrigés ou
> enrichis depuis, **sans que ces fichiers soient mis à jour**. Ce document
> recense les écarts, pour que le rapport final ne reprenne pas des informations
> périmées.

---

## Comment lire ce document

Pour chaque document existant, on liste ce qui **a changé** depuis sa rédaction.
Ce qui n'est pas mentionné reste valable.

---

## 01 — Niveau 1, règles fixes

### Correction majeure : l'historique n'était jamais injecté

**Le problème.** Le Niveau 1 sait détecter le fractionnement (*structuring*) —
mais il a besoin de l'historique récent du client pour cela. Or l'API appelait
`check_transaction(tx)` **sans** passer `customer_history`. Résultat : le
fractionnement était **indétectable depuis la plateforme**, alors que la logique
existait et fonctionnait en test unitaire.

**La correction.** L'identifiant client est extrait **avant** l'étape 1, et
l'historique des dépôts récents (fenêtre 72 h, source Redis) est injecté dans la
transaction transmise au Niveau 1. Le champ `history_deposits_72h` est également
exposé dans la sortie de l'étape, pour être visible dans l'interface.

**Effet vérifié** : trois dépôts en espèces sous le seuil, même client, dans la
fenêtre → le troisième déclenche un `STR`.

C'est ce qui rend possible le scénario de démonstration D6.

### Correction : un modèle de question faussement accusateur

Le gabarit `TEMPLATES["SWIFT"][3]` affirmait « *high risk country {pays}* » pour
**tous** les pays, France comprise. La formulation a été rendue **neutre**
(« *cross border wire transfer destination X source of funds verification* »).

Les questions relatives aux listes noire et grise restent pilotées par les
signaux `COUNTRY_*` réellement produits par le Niveau 1 — pas par un gabarit.

### Ajout : listes FATF exposées à l'interface

Un endpoint sert les listes noire et grise au formulaire de saisie, ce qui permet
d'afficher des **alertes en direct** pendant que l'utilisateur remplit son
opération (pays sous sanction, pays sous surveillance, seuil dépassé, montant
juste sous le seuil).

---

## 02 — Planning Agent

### Ajout : les questions sont désormais explicables

`PlanningResult` expose un champ `factors`. Le déclencheur de chaque question
existait déjà dans le code (`QCandidate.factor`) mais était **jeté** lors de la
finalisation — l'information était perdue.

Chaque question est maintenant accompagnée de :

- sa **priorité** (1 à 5) ;
- sa **provenance** : signal Niveau 1, raisonnement LLM, contexte transaction, ou
  obligation de base ;
- une **explication en français** (« Déclenchée par le Niveau 1 : le montant
  dépasse le seuil de déclaration de soupçon ») ;
- la **requête corpus** effectivement exécutée.

C'est ce qui apparaît dans la page Pipeline Live et qui permet à un jury de
comprendre *pourquoi* le système pose telle question.

### Ajout : les signaux client alimentent le planning

Les signaux issus du Client Scoring Agent (`CLIENT_*`) sont fusionnés avec ceux
du Niveau 1 avant la génération des questions. Un profil PEP génère donc des
questions de vigilance renforcée qu'un profil ordinaire de même montant ne
génère pas.

---

## 03 — Retrieval Agent

C'est le composant qui a le plus évolué.

### Changement de fond : graphe **et** corpus pour chaque question

Auparavant, une question répondue par le graphe **ne descendait pas** vers le
corpus. Désormais les deux sources sont interrogées pour **chaque** question, et
les résultats sont fusionnés (règles du graphe en tête, puis extraits du corpus),
avec un marqueur de provenance par extrait.

Le coût est une latence supérieure (environ 2 à 5 secondes par question, soit
~55 secondes pour huit questions). C'est **assumé** : c'était une demande
explicite, la qualité de la couverture prime sur la vitesse.

### Ajout : synthèse des réponses

Un **appel LLM groupé** (température 0, mis en cache) produit une réponse
française de une à deux phrases **par question**, avec citation des sources.
Garde-fous : longueur bornée, repli sur la simple liste des sources en cas
d'échec.

L'interface met la réponse synthétisée en avant et replie les extraits verbatim
dans un bloc dépliable.

### Durcissement : la vérification du cache graphe

La condition d'appariement était trop permissive (un simple `OR`), ce qui faisait
remonter des règles hors sujet. Elle a été remplacée par un **comptage de
mots-clés** : au moins deux correspondances dans le blob indexé (sujet,
contrainte, extrait, nom, description), une seule si la requête est courte.

Les statuts acceptés ont été élargis pour inclure les **règles apprises**
(`VALIDATED`, `LEARNED`), et les résultats sont triés par nombre de
correspondances puis par confiance.

**Vérifié en direct** : PEP → 7 règles, STR → 10 règles, question hors sujet →
**0 règle**. Le filtre est discriminant.

### Ajout : mode graphe-d'abord et apprentissage

Le retrieval indique désormais explicitement le chemin emprunté (`GRAPH` si le
cache répond, `CORPUS` sinon), et le mode dual persiste les règles validées dans
le graphe. Voir document 21.

---

## 04 — Compliance Agent

### Correction : les signaux client apparaissent dans l'audit

L'API transmettait `risk_signals=l1.risk_signals` (les seuls signaux du Niveau 1)
au lieu de l'ensemble fusionné. Corrigé en `risk_signals=all_signals`, dans
l'API **et** dans le test bout-en-bout.

**Effet** : les signaux `CLIENT_*` apparaissent maintenant dans la piste d'audit
et donc dans le SAR. **Aucun changement de score** — ces signaux ont un poids nul
par construction, le risque client étant déjà compté via `client_profile`. C'est
un gain de **traçabilité**, pas de scoring.

### Décision documentée : pas de double comptage du risque client

Une proposition externe suggérait d'ajouter un terme `β × score_client` au score
combiné. Vérification faite, le risque client est **déjà** intégré au score de
conformité (contribution inhérente + plancher). La proposition a été **rejetée**
comme double comptage. À assumer si la question est posée.

### Ajout : fiabilité du ML

Le SAR porte `ml_reliable` et `ml_profile_status`. Le ML ne contribue au score
combiné que s'il conclut à la fraude **et** que le profil est mature. Le calcul
de divergence a été corrigé en conséquence (voir document 10).

---

## 05 — Schéma du pipeline

### Le pipeline compte désormais une étape supplémentaire

Une **étape 0** a été ajoutée en amont : l'analyse des justificatifs joints. Son
verdict **remplace** le champ `has_justification` de la transaction, ce qui
modifie réellement le traitement en aval (voir document 16).

Le schéma à jour :

```
[0] Justificatifs  →  [1] Level 1  →  [2] Client Scoring  →  [3] Planning
                   →  [4] Retrieval  →  [5] Compliance  →  [6] ML
                   →  [7] SAR  →  [8] Decision Workflow
```

### Les états du workflow ont été complétés

Deux états ont été ajoutés à la machine : `APPROVED_BY_SUPERVISOR` et
`REJECTED_BY_SUPERVISOR`. Auparavant, la décision du superviseur ne faisait pas
avancer l'état — l'utilisateur voyait indéfiniment « en attente » alors que son
dossier était tranché.

---

## 06 — Ground truth, 49 règles

**Inchangé.** Les 49 règles validées humainement restent la référence
(`origin = human_verified`).

Ce qui a changé autour d'elles : le graphe contient désormais **aussi** des règles
apprises (`VALIDATED`, `LEARNED`), avec un ordre de priorité qui préserve la
primauté de la vérité terrain. Le comptage « règles apprises » de l'interface
correspond à la différence entre le total des règles et ces 49.

### Correction d'affichage

Les nœuds `Rule` de Neo4j ne portent pas les propriétés que le frontend
attendait (`text`, `category`, `severity`). Ils portent `component_constraint`,
`component_subject`, `chunk_role`, `source_article`, `source_text_snippet`. Une
fonction de normalisation a été ajoutée côté API — sans elle, l'interface
affichait « — » partout.

À noter : **il n'y a pas de champ `severity` sur les 49 règles de référence**.
La colonne a été remplacée par la catégorie (`chunk_role`) et la juridiction.

---

## 07 — Points 1, 2, 5

Ce document était un point d'avancement. Son contenu est désormais réparti dans
les documents thématiques :

| Sujet | Document à jour |
|---|---|
| Justificatifs | 16 |
| Workflow et file superviseur | 12 |
| Historique Redis | 20 (section flux) |
| Feedback superviseur | 21 |

---

## 08 — Arborescence

Le projet a beaucoup grossi. Principaux ajouts non listés dans le document
d'origine :

| Élément | Rôle |
|---|---|
| `api/store.py` | Couche de persistance PostgreSQL / fichiers |
| `api/auth.py` | Comptes, rôles, emails (fortement enrichi) |
| `project/` | L'application React complète (14 pages) |
| `migrate_jobs_to_postgres.py` | Migration des dossiers vers PostgreSQL |
| `test_postgres.py` | Diagnostic de connexion base |
| `generate_test_documents.py` + `test_documents/` | Jeu de justificatifs de test |
| `db_config.json`, `smtp_config.json` | Configurations locales (ignorées par Git) |
| `GUIDE_DEMO_JURY.md` | Les 14 scénarios de démonstration |
| `docker-compose.yml` | Neo4j, Weaviate, Redis **et PostgreSQL** |

---

## Synthèse des corrections de bugs notables

Ces corrections sont intéressantes à mentionner dans un rapport : elles montrent
une démarche de test et pas seulement de développement.

| Bug | Symptôme | Cause | Correction |
|---|---|---|---|
| Fractionnement invisible | Aucun STR sur 3 dépôts | Historique non injecté au Niveau 1 | Injection de la fenêtre 72 h |
| Justificatif faussement validé | Facture 12 000 valide une tx de 1 200 | Comparaison de montants par **sous-chaîne** | Parsing numérique et comparaison de **valeurs** |
| Email de décision jamais reçu | 200 OK mais rien n'arrive | Envoi conditionné à la présence d'un `@` dans le username | Résolution `username → email` en base |
| Statistiques en erreur 500 | Dashboard vide | Accès `j["steps"]` sans garde | `j.get("steps") or {}` |
| Profession à risque jamais détectée | Facteur jamais déclenché | `import_export` vs `import export` | Normalisation des libellés |
| ML saturé sur profil nouveau | Score gonflé de 5 à 12 points | Features estimées par médianes | Garde-fou `ml_reliable` |
| Règles hors sujet remontées | Cache graphe non discriminant | Condition `OR` trop permissive | Comptage de mots-clés |
| Historique perdu au redémarrage | Dossiers disparus | Stockage 100 % en mémoire | Persistance fichiers puis PostgreSQL |
| SAR PDF vide | Fenêtre blanche | `noopener` renvoie `null` + ouverture après `await` | Fenêtre ouverte dans le clic, sans `noopener` |

---

# Améliorations d'août 2026 (dernière itération)

> Six améliorations menées après la documentation de juin, chacune ciblée
> (« améliorer l'existant, pas refaire »). Elles renforcent trois axes : la
> **détection**, la **fiabilité prouvable**, et l'**apprentissage**.

## A1 — Détection d'anomalies non supervisée (Isolation Forest)

**Le manque.** Le ML supervisé (LightGBM) et le LSTM-Autoencoder ont besoin
d'historique. Un **nouveau client** (sans historique) ne bénéficiait d'aucun
signal comportemental fiable.

**L'ajout.** Une **branche C** dans `ml_fraud_detector_v4.py` (au *training*) :
un Isolation Forest entraîné sur les transactions normales produit un
`isolation_score` (0 = normal, 1 = atypique) — un signal **non supervisé** qui
fonctionne **pour tous les clients, même sans historique**. La feature est
intégrée au vecteur du modèle et affichée dans Pipeline Live (badge vert/ambre/
rouge).

**Effet vérifié** : séparation fraude/normal 1,40× (l'ancien autoencodeur seul
donnait 0,69×) ; PR-AUC 0,9518, F1 0,9564.

## A2 — Précision du contexte (resserrage du reranking)

**Le manque.** Le reranker gardait les 5 meilleurs extraits sans seuil de score
→ des passages peu pertinents « diluaient » le contexte transmis au LLM.

**L'ajout.** Un **seuil de score** après reranking (`RERANK_MIN_SCORE`), avec un
plancher de sécurité (`RERANK_MIN_KEEP = 2`) pour ne jamais renvoyer le vide.

**Effet mesuré** (RAGAS-like) : *Context Precision* 67,3 % → **73,3 %**,
*Faithfulness* 81,4 % → **86,3 %**, Hit-Rate stable à 91,7 %, latence divisée
par deux. Compromis assumé : 9/12 questions répondues (vs 11/12) — on préfère
**moins mais plus juste**.

## A3 — Juge de qualité des règles (validation en 2 étapes)

**Le manque.** Une règle sémantique n'était filtrée que par l'ancrage verbatim.
Un texte **descriptif** (définition, contexte) ancré mot-à-mot pouvait polluer
le graphe comme si c'était une obligation.

**L'ajout.** Un **2ᵉ garde-fou** dans `SemanticReasoner._persist_validated` : un
juge LLM (`_rule_quality_ok`, piloté par `RULE_QUALITY_JUDGE`) distingue une
**vraie obligation déontique** (« doit / ne doit pas ») d'un texte descriptif.
Repli sûr : si le juge est indisponible, on laisse passer (l'ancrage fait foi).

**Effet vérifié** : sur 4 cas de test, les 2 obligations sont gardées, les 2
textes descriptifs rejetés. Le graphe ne se peuple que de vraies règles.

## A4 — Visualisation structurée des règles + citation verbatim

**Le manque.** La page Compliance n'affichait qu'une ligne tronquée par
violation ; les obligations **respectées** (pourtant renvoyées par l'API)
n'étaient pas montrées ; aucune citation de loi visible.

**L'ajout.** Une **carte règle** structurée (composant `RuleCard`) : bandeau
typé (sévérité · catégorie · provenance 🔷 graphe / 🔶 LLM · rec. FATF · statut
cycle de vie), l'obligation, la provenance (source → article → seuil), et
surtout la **citation verbatim** ❝ … ❞ du texte de loi + barre de confiance. Les
règles sont groupées en deux sections (violations / respectées). Les règles du
graphe portent aussi leur extrait ancré (`source_text_snippet`).

En complément, un compteur **anti-hallucination** est exposé : « N règles LLM
rejetées (citation non ancrée) » — la garantie 0 % hallucination devient
**visible** à l'écran, pas seulement affirmée.

## A5 — Upload de règlements à la demande (ingestion Weaviate)

**L'ajout.** Une interface (onglet « Ajouter un règlement » de la page Corpus)
permet d'**uploader un PDF réglementaire** ; le backend le fait passer par le
**même pipeline** que les 33 documents d'origine — extraction → traduction
FR→EN si nécessaire → chunking (Pattern Scanner + Branch A/B) → embeddings
BGE → **insertion incrémentale dans Weaviate**. Le document devient
immédiatement interrogeable par le Retrieval Agent (chemin sémantique). Choix
assumé : ingestion **vectorielle uniquement** (pas d'intégration dans la partie
symbolique/Neo4j), traitée en tâche de fond avec suivi temps réel.
Fichiers : `ingest_document.py`, endpoints `/api/regulations/upload`.

## A6 — Boucle RLHF fermée (apprentissage à partir du feedback superviseur)

**Le manque.** Le feedback superviseur était capturé (`supervisor_feedback.jsonl`)
et mesuré, mais le **réentraînement ne s'exécutait pas** (étape préparée puis
laissée manuelle), et le feedback n'alimentait pas les données d'entraînement.

**L'ajout.** La boucle est **fermée** et rendue **visible** :

1. **feedback → dataset** : chaque transaction validée devient un exemple
   étiqueté (features ML persistées + label déduit du verdict humain).
2. **Réentraînement INCRÉMENTAL (warm-start)** : on **repart du modèle existant**
   (LightGBM `init_model`) et on ajoute quelques itérations de boosting sur le
   feedback + un *replay buffer* → rapide (secondes), **sans oubli catastrophique**.
3. **Garde-fous** (conformes à la politique existante) : volume minimum
   (≥ 20 étiquettes), **déploiement seulement si F1 et PR-AUC ne régressent pas**
   (sinon rollback avec sauvegarde `.bak`), et **trace auditable** par cycle.
4. **Cadence hybride** : cycle hebdomadaire + garde-fou de dérive quotidien.
5. **Panneau UI** (page Superviseur) : accord système/expert, taux de fausses
   alertes, seuil courant, bouton « Lancer un cycle » et tableau **avant/après**
   avec verdict *Déployé / Rollback*.

Le réentraînement **complet** (600k lignes IBM + LSTM) reste le chemin lourd
*offline*, nourri par le dataset accumulé.
Fichiers : `rlhf.py`, `retraining.py`, endpoints `/api/rlhf/*`.

**À dire au jury** : *« Le système apprend du feedback humain sans se dégrader —
un garde-fou peut refuser un modèle qui régresse : c'est une fonctionnalité, pas
un bug. »*
