# 12 — Decision Workflow & file superviseur (étape 8 du pipeline)

> La dernière étape : transformer une décision d'analyse en **actions
> opérationnelles**, et confier au superviseur humain ce qui doit lui revenir.

---

## 1. Objectif

Une décision `STR` n'est pas une fin en soi : c'est le début d'un processus.
Il faut mettre le dossier en attente, réclamer des justificatifs, notifier le
client, placer l'opération dans une file de validation humaine, et garder la
trace de qui a tranché quoi.

Le Decision Workflow est la machine à états qui orchestre tout cela. Sans lui,
le système produirait un verdict… et rien ne se passerait.

---

## 2. La machine à états

```
                    décision du Compliance Agent
                              │
        ┌──────────┬──────────┼──────────┬───────────────┐
        ▼          ▼          ▼          ▼               ▼
      PASS      REVIEW       EDD        STR            BLOCK
        │          │          │          │               │
        ▼          └────┬─────┘          ▼               ▼
    APPROVED           │           PENDING_REVIEW     BLOCKED
   (exécutable)        │          (file superviseur)  (opération refusée)
                       ▼
            justificatifs manquants ?
                  oui │   │ non
                      ▼   ▼
        PENDING_DOCUMENTS   PENDING_REVIEW
        (on réclame au client)
                      │
        document recevable déposé
                      ▼
              PENDING_REVIEW
                      │
              décision superviseur
                ┌─────┴─────┐
                ▼           ▼
    APPROVED_BY_        REJECTED_BY_
     SUPERVISOR          SUPERVISOR
```

Les six états (`TxState`) :

| État | Signification |
|---|---|
| `APPROVED` | Opération conforme, exécutable |
| `BLOCKED` | Opération refusée d'office (sanction, liste noire) |
| `PENDING_DOCUMENTS` | En attente de justificatifs du client |
| `PENDING_REVIEW` | En attente d'une décision humaine |
| `APPROVED_BY_SUPERVISOR` | Validée par le Compliance Officer |
| `REJECTED_BY_SUPERVISOR` | Refusée par le Compliance Officer |

Le point important : **une décision automatique n'est jamais définitive quand
elle est défavorable**. `STR`, `EDD` et `REVIEW` passent tous par un humain.
Seul `PASS` s'exécute sans intervention, et seul `BLOCK` refuse sans discussion
— parce qu'il correspond à une interdiction légale (pays sous sanction).

---

## 3. La file superviseur

### Elle est dérivée, pas stockée séparément

Un choix de conception qui simplifie beaucoup : la file n'est pas une table à
part qu'il faudrait synchroniser. Elle est **calculée** à partir des dossiers :

> file = dossiers terminés, dont l'état commence par `PENDING`, et qui n'ont pas
> encore de décision superviseur.

Conséquence directe : depuis que les dossiers sont persistés en PostgreSQL
(voir document 17), **la file survit aux redémarrages du serveur** sans code
supplémentaire. C'était l'un des manques identifiés — il est résolu par la
persistance, pas par une file dédiée.

### Priorisation par le risque

La file n'est pas un simple FIFO. Chaque dossier reçoit une **priorité** dérivée
de la décision système :

| Décision | Priorité | Rang de tri |
|---|---|---|
| `STR`, `BLOCK` | **HAUTE** | 0 |
| `EDD` | **MOYENNE** | 1 |
| autres (`REVIEW`, `KYC`, `RECORD`) | **NORMALE** | 2 |

Le tri combine ensuite deux critères, dans cet ordre :

```
trier par (rang_de_priorité, date_de_création croissante)
```

Autrement dit : **la priorité prime, puis l'ancienneté**. Un STR arrivé il y a
une minute passe devant un EDD qui attend depuis quatre jours.

C'est le comportement métier correct : en AML, une déclaration de soupçon a un
délai réglementaire court (48 h pour la CTAF), alors qu'une diligence renforcée
peut légitimement attendre. Un FIFO strict aurait fait exactement l'inverse.

**Observé en démonstration :**

| Dossier | Priorité | Ancienneté | Position |
|---|---|---|---|
| TX-20260722-0002 (STR) | HAUTE | 1 min | 1er |
| TX-20260718-0003 (EDD) | MOYENNE | 4 j | 2e |

### Ancienneté affichée

Chaque dossier indique depuis combien de temps il attend (`age_hours`, affiché en
minutes, heures ou jours). C'est l'indicateur qui permet de repérer un dossier
oublié — un EDD à « 4 j » saute aux yeux.

---

## 4. La décision du superviseur

### Séparation des devoirs

La validation d'un dossier réglementaire est réservée au rôle
**Compliance Officer**. Un administrateur technique, même avec tous les droits
système, ne peut pas approuver ou refuser une opération.

Le contrôle est fait **côté serveur** (HTTP 403), pas seulement en masquant un
bouton dans l'interface. C'est un principe d'audit : une règle qui n'existe que
dans le frontend n'est pas une règle.

### Ce que le superviseur saisit

| Champ | Rôle |
|---|---|
| **Action** | `APPROVE` / `REJECT` / `ANNOTATE` |
| **Verdict RLHF** | Son jugement sur la décision du système |
| **Annotation** | Justification écrite — **obligatoire** |

L'annotation obligatoire n'est pas une contrainte gratuite : c'est ce qui rend la
piste d'audit exploitable. Une décision sans motif n'est pas défendable devant un
contrôle.

Les trois verdicts possibles ont été reformulés pour être compréhensibles sans
connaître le vocabulaire du machine learning :

| Valeur technique | Libellé affiché |
|---|---|
| `CORRECT` | La décision est correcte — le système a bien jugé |
| `FALSE_POSITIVE` | Fausse alerte — le système a signalé à tort une opération légitime |
| `TRUE_FRAUD` | Fraude confirmée — l'alerte était justifiée |

### Ce qui se déclenche ensuite

1. Un **FeedbackEvent** est écrit dans `output/supervisor_feedback.jsonl`
   (graine de l'amélioration continue — voir document 21).
2. L'**état du dossier avance** (`APPROVED_BY_SUPERVISOR` ou
   `REJECTED_BY_SUPERVISOR`), ce qui met à jour l'espace client.
3. Le dossier est **persisté** en base.
4. Un **email** part vers le propriétaire du dossier — approbation en vert,
   refus en rouge **avec le motif** (voir document 19).
5. Un **événement temps réel** est poussé aux interfaces connectées
   (voir document 18).

---

## 5. L'historique des décisions

Le superviseur dispose désormais d'un historique consultable directement dans son
espace : chaque décision passée avec son dossier, l'action, le verdict RLHF,
l'auteur, l'horodatage et la justification.

Cet historique sert trois usages :

- **opérationnel** — retrouver ce qui a été décidé sur un dossier similaire ;
- **audit** — la traçabilité « qui, quoi, quand, pourquoi » exigée par un
  contrôle ;
- **qualité** — c'est la matière première des indicateurs de faux positifs et de
  fraudes confirmées affichés dans l'espace Audit.

---

## 6. Ce qui a été construit récemment

| Ajout | Apport |
|---|---|
| Priorités HAUTE / MOYENNE / NORMALE | La file reflète l'urgence réglementaire |
| Tri par urgence côté serveur | Plus de filtrage dans le navigateur |
| Ancienneté affichée | Repérage des dossiers oubliés |
| Historique des décisions | Traçabilité dans l'outil du superviseur |
| File persistée (via PostgreSQL) | Survit aux redémarrages |
| Rafraîchissement temps réel | Un nouveau dossier apparaît sans recharger |

---

## 7. Limites assumées

- **Pas d'affectation par analyste** : la file est commune, il n'y a pas encore de
  notion de « dossier assigné à X ». Dans une banque réelle, ce serait nécessaire.
- **Pas d'escalade automatique** : un dossier qui dépasse un délai n'est pas
  remonté automatiquement à un niveau supérieur ; il est seulement visible par son
  ancienneté.
- **Pas de SLA formalisé** : le délai réglementaire (48 h pour un STR) est
  rappelé dans le SAR mais n'est pas décompté automatiquement.

---

## 8. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `decision_workflow.py` | `TxState`, `DecisionWorkflow`, `SupervisorQueue`, `FeedbackEvent` |
| `api/main.py` | `/api/validate/{tx_id}`, `/api/supervisor/queue`, `/api/supervisor/history` |
| `project/src/pages/Supervisor.tsx` | File priorisée, détail du dossier, bloc décision, historique |
| `output/supervisor_feedback.jsonl` | Journal des décisions (format JSON-lines) |
