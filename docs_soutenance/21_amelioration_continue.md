# 21 — Amélioration continue : apprentissage de règles et boucle de feedback

> Ce qui apprend réellement aujourd'hui, ce qui est préparé mais pas encore
> branché — et pourquoi il faut le dire clairement.

---

## 1. Une distinction à faire d'emblée

Le système comporte **deux mécanismes d'amélioration** de nature très
différente, et il est important de ne pas les confondre devant un jury :

| Mécanisme | État | Ce qu'il fait |
|---|---|---|
| **Apprentissage de règles** (graphe Neo4j) | ✅ **Fonctionnel** | Le graphe s'enrichit à chaque transaction analysée |
| **Boucle de feedback RLHF** (collecte, mesure, décision, planification) | ✅ **Fonctionnelle** | Cycle hebdomadaire automatisé, avec garde-fous |
| **Exécution du réentraînement du modèle** | ⚠️ **Conditionnée au jeu d'entraînement** | Se déclenche si les données sont disponibles ; sinon le feedback continue de s'accumuler |

La formulation juste est donc : **la boucle est fermée et automatisée**, mais
l'étape finale — le réentraînement effectif des poids — ne s'exécute que si le
jeu d'entraînement est présent dans l'environnement. Ce qui est décidé,
mesuré et planifié fonctionne ; ce qui dépend de données volumineuses est
conditionnel.

---

## 2. L'apprentissage de règles — ce qui fonctionne

### Le principe

À chaque transaction, le Compliance Agent emprunte deux chemins en parallèle :

- le **chemin symbolique** — évaluation déterministe des règles déjà présentes
  dans le graphe ;
- le **chemin sémantique** — un LLM **extrait des règles candidates** depuis les
  extraits réglementaires récupérés.

Les règles candidates issues du second chemin ne sont pas acceptées telles
quelles. Elles passent un **gate de validation** exigeant une citation verbatim
ancrée dans un chunk réel. Celles qui passent, avec une confiance suffisante,
sont **écrites dans le graphe**.

### Les trois statuts

| Statut | Condition | Origine |
|---|---|---|
| `GROUND_TRUTH` | Validée par un humain | Les 49 règles de référence |
| `VALIDATED` | Confiance ≥ 0,65 **et** citation ancrée | Extraite automatiquement |
| `LEARNED` | Confiance ≥ 0,80 **et réutilisée ≥ 3 fois avec succès** | Promue depuis `VALIDATED` |

La promotion `VALIDATED → LEARNED` est le point intéressant : une règle ne
devient « apprise » qu'après avoir **fait ses preuves à l'usage**. Ce n'est pas
la confiance déclarée par le LLM qui fait foi, c'est la réutilisation effective.

Ordre de priorité à l'exécution : `GROUND_TRUTH` > `LEARNED` > `VALIDATED`. Une
règle validée humainement l'emporte toujours sur une règle apprise.

### L'effet observable : le graphe répond de plus en plus

Le retrieval commence par interroger le graphe (étape 0). Si le graphe couvre
déjà la question, le corpus vectoriel n'est pas sollicité.

Comme le graphe s'enrichit à chaque analyse, **la proportion de questions
répondues par le graphe augmente avec l'usage**. C'est mesurable et visible dans
l'interface : chaque question affiche sa provenance (graphe ou corpus), et le
compteur « règles apprises ajoutées au graphe » s'incrémente.

Le mécanisme de correspondance a été durci en cours de projet : une condition
trop permissive faisait remonter des règles hors sujet. Elle a été remplacée par
un **comptage de mots-clés** (au moins deux correspondances, une seule si la
requête est courte). Vérification en direct : une question sur les PEP remonte 7
règles, une question STR en remonte 10, une question hors sujet en remonte **0**.
Le filtre est donc réellement discriminant.

---

## 3. La boucle de feedback humain — ce qui est collecté

### Le FeedbackEvent

Chaque décision de superviseur produit un événement enregistré en JSON-lines
dans `output/supervisor_feedback.jsonl` :

```json
{
  "tx_id": "TX-20260721-0002",
  "agent_decision": "STR",
  "supervisor_action": "REJECT",
  "supervisor_label": "TRUE_FRAUD",
  "annotation": "seuil dépassé et absence de justificatif",
  "supervisor_id": "compliance@demo.local",
  "created_at": "…"
}
```

Le champ décisif est `supervisor_label` — le **verdict humain sur la décision de
la machine** :

| Verdict | Signification pour l'apprentissage |
|---|---|
| `CORRECT` | Le système a bien jugé |
| `FALSE_POSITIVE` | Fausse alerte — le système a signalé à tort |
| `TRUE_FRAUD` | Fraude confirmée — l'alerte était justifiée |

C'est exactement la matière première d'un apprentissage par renforcement à partir
de retours humains : des paires (décision du modèle, jugement de l'expert).

### Ce que ces données permettent déjà

Sans réentraînement, elles alimentent des **indicateurs de qualité** visibles dans
l'espace Audit : nombre de décisions instruites, faux positifs annotés, fraudes
confirmées. C'est une mesure directe de la performance perçue du système par ses
utilisateurs.

---

## 4. La boucle automatisée (`retraining.py` + `api/scheduler.py`)

Le cycle se déroule en cinq temps, et produit à chaque exécution un rapport
auditable dans `output/retraining/`.

```
1. AGRÉGATION   lire supervisor_feedback.jsonl, joindre aux dossiers persistés
2. MESURE       taux d'accord système/expert, taux de fausses alertes
3. DÉCISION     réentraîner seulement si volume suffisant OU dérive avérée
4. GARDE-FOU    ne déployer que si les métriques s'améliorent
5. TRACE        rapport horodaté + état persistant
```

### La politique de déclenchement

| Condition | Décision |
|---|---|
| Moins de **20 étiquettes** | **Pas de réentraînement** — l'échantillon n'est pas significatif |
| Taux de fausses alertes > **30 %** | **Réentraînement anticipé** — dérive avérée |
| Sinon | Cycle hebdomadaire nominal |

Le seuil de volume est le point important : réentraîner sur huit décisions
ferait apprendre du bruit. Le système **refuse explicitement** de le faire et
le justifie dans son rapport, plutôt que de produire un modèle instable.

### Le planificateur

Trois tâches, sans dépendance supplémentaire (un thread et une boucle de
réveil suffisent pour une cadence hebdomadaire) :

| Tâche | Cadence | Rôle |
|---|---|---|
| `retraining` | dimanche 02 h | Cycle complet d'amélioration continue |
| `drift_check` | quotidien 03 h | Garde-fou : déclenche un cycle anticipé si dérive |
| `weekly_report` | vendredi 17 h | Envoi automatique du rapport hebdomadaire |

Deux propriétés de robustesse :

- **L'état est persisté** : un redémarrage ne rejoue pas une tâche déjà faite
  dans la période, et ne la saute pas non plus. Si le serveur était éteint à
  l'heure prévue, la tâche s'exécute au redémarrage (rattrapage).
- **Aucune exception ne remonte** : une tâche en échec est journalisée, le
  planificateur ne peut pas faire tomber l'API.

Deux endpoints permettent de l'observer et de le démontrer :
`GET /api/scheduler/status` et `POST /api/scheduler/run/{tâche}`.

### Résultat observé sur les données réelles

Exécution du cycle sur les 8 décisions superviseur enregistrées :

```
Étiquettes exploitables : 8
  décision correcte     : 2
  fausse alerte         : 1
  fraude confirmée      : 5
Accord système/expert   : 87,5 %
Taux de fausses alertes : 12,5 %   (seuil d'alerte 30 %)

Réentraînement : NON
  motif : volume insuffisant (8 étiquettes pour un minimum de 20)
```

C'est exactement le comportement attendu : le système mesure, conclut que
l'échantillon est trop faible, et s'abstient — en le justifiant.

### Ce qui reste conditionnel

L'**exécution effective du réentraînement** requiert le jeu d'entraînement
(le dataset IBM, non embarqué dans le conteneur pour des raisons de taille).
Quand il est absent, le cycle le signale explicitement (`statut : IGNORÉ`) et
le feedback continue de s'accumuler pour le cycle suivant. Le
**rechargement à chaud** du modèle dans l'API reste également à câbler.

---

## 5. La cadence de réentraînement recommandée : hebdomadaire

Une question s'est posée : réentraîner tous les **3 jours** ou toutes les
**semaines** ? La recommandation retenue est **hebdomadaire**, pour des raisons
qui tiennent à la nature du signal.

**Pourquoi pas tous les 3 jours.** Le signal d'apprentissage, ce sont les
décisions **validées par un superviseur** — un volume faible par nature. En trois
jours, on n'accumule pas assez d'étiquettes pour que la mise à jour soit
statistiquement significative. Le modèle apprendrait surtout du **bruit**, avec
trois conséquences : instabilité des prédictions, dérive du seuil de décision
d'une version à l'autre, et perte de confiance des analystes qui verraient le
comportement changer sans raison apparente.

**Pourquoi l'hebdomadaire.** Une semaine donne un volume d'étiquettes
suffisant, s'aligne sur le rythme naturel du reporting réglementaire (le rapport
hebdomadaire existe déjà), et reste largement assez réactif pour l'AML — où les
schémas évoluent sur des mois, pas sur des heures.

**Le compromis proposé.** Réentraînement **complet hebdomadaire**, doublé d'un
**garde-fou quotidien** qui ne déclenche un réentraînement anticipé que si une
dérive est mesurée : taux de faux positifs au-delà d'un seuil, ou nombre minimum
de nouvelles étiquettes atteint. On garde la stabilité de l'hebdomadaire tout en
conservant la capacité de réagir vite.

---

## 6. Architecture cible

```
Décisions superviseur
        │
        ▼
supervisor_feedback.jsonl  ────┐
                                │
Transactions analysées  ────────┤
(PostgreSQL)                    │
                                ▼
                    Constitution du jeu incrémental
                                │
                                ▼
                    Réentraînement + recalibration
                                │
                                ▼
                    Évaluation vs modèle courant
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
            amélioration              dégradation
                    │                       │
                    ▼                       ▼
        déploiement du modèle       conservation du modèle
        + rechargement à chaud      courant + alerte
```

L'étape d'**évaluation comparative** est essentielle : un réentraînement ne doit
jamais être déployé automatiquement s'il dégrade les métriques. C'est le
garde-fou qui empêche la boucle de se dégrader toute seule.

---

## 7. Ce qu'il faut dire au jury

Une formulation honnête et défendable :

> « Le système comporte deux boucles d'apprentissage. La première, l'enrichissement
> du graphe de connaissances, est **opérationnelle** : chaque transaction analysée
> peut ajouter des règles validées, et le graphe répond de plus en plus souvent
> sans solliciter le corpus. La seconde, l'exploitation des retours du
> superviseur, est **automatisée** : un planificateur exécute chaque semaine un
> cycle qui agrège le feedback, mesure l'accord système/expert et le taux de
> fausses alertes, puis décide s'il y a lieu de réentraîner — avec un refus
> explicite quand l'échantillon est trop faible, et un garde-fou quotidien
> déclenchant un cycle anticipé en cas de dérive. Seule l'exécution finale du
> réentraînement reste conditionnée à la disponibilité du jeu d'entraînement,
> trop volumineux pour être embarqué. »

Cette formulation a deux avantages : elle est exacte, et elle montre que la
politique de déclenchement a été **pensée** — c'est elle qu'un jury interrogera,
bien plus que la mécanique du réentraînement lui-même.

---

## 8. Limites assumées

- **La boucle RLHF n'est pas fermée** (point principal ci-dessus).
- **Pas de versionnage des modèles** : pas de suivi d'expériences ni de retour
  arrière automatisé.
- **Pas de détection de dérive** (*drift*) sur les données d'entrée.
- **Les règles apprises ne sont pas revalidées périodiquement** : une règle
  promue `LEARNED` le reste, même si le texte source évolue.

---

## 9. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `decision_workflow.py` | `FeedbackEvent`, `SupervisorQueue.record_decision` |
| `output/supervisor_feedback.jsonl` | Journal des décisions humaines |
| `compliance_agent.py` | `RuleStatus`, seuils de confiance, `_persist_validated` |
| `retrieval_agent.py` | `graph_lookup`, vérification du cache graphe |
| `neo4j_graph/` | Persistance des règles apprises |
| `ml_fraud_detector_v4.py`, `train_demo_ml.py` | Entraînement hors ligne (actuel) |
| `project/src/pages/Audit.tsx` | Indicateurs de qualité issus du feedback |
