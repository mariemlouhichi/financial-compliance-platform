# 10 — ML Fraud Detector (étape 6 du pipeline)

> Un second regard, statistique, sur la transaction.
> Volontairement **subordonné** aux règles : il ne peut jamais innocenter.

---

## 1. Objectif

Les règles réglementaires détectent ce qui est **écrit** : un seuil dépassé, un
pays sanctionné, un justificatif manquant. Elles ne détectent pas ce qui est
**inhabituel** : une rafale de virements vers vingt bénéficiaires différents en
une heure, un montant qui ne ressemble à rien de ce que ce compte fait
d'habitude, une opération à 3 h du matin sur un compte qui n'a jamais bougé la
nuit.

C'est le rôle du détecteur ML : apporter un signal **comportemental et
statistique**, complémentaire au symbolique.

---

## 2. Le principe directeur : le ML ne peut que renforcer

C'est le choix de conception le plus important du composant, et le plus facile à
défendre :

> **Le ML peut faire monter le risque, jamais le faire baisser.**

Concrètement, si les règles disent STR et que le ML dit « légitime », la décision
reste **STR**. Le symbolique prévaut toujours. Le ML n'a pas le droit de
rabaisser une décision réglementaire.

La raison est réglementaire, pas technique : un modèle statistique n'est pas une
source de droit. Il ne peut pas dispenser une banque d'une obligation de
déclaration. En revanche, il peut légitimement attirer l'attention sur un dossier
que les règles avaient laissé passer.

---

## 3. Architecture : dual-branch

Le pipeline d'entraînement (`ml_fraud_detector_v4.py`) combine deux branches.

```
                    ┌────────────────────────────────┐
   transaction  →   │ BRANCHE A — supervisée          │
                    │ LightGBM / XGBoost              │ → probabilité de fraude
                    │ (apprend sur les étiquettes)    │
                    └────────────────────────────────┘
                                  ▲
                                  │ reconstruction_error (feature)
                    ┌────────────────────────────────┐
   séquence des →   │ BRANCHE B — non supervisée      │
   N dernières tx   │ LSTM-Autoencoder                │
   du même compte   │ (apprend le comportement normal)│
                    └────────────────────────────────┘
```

### Branche A — supervisée

Un modèle à arbres (LightGBM, avec XGBoost en variante) entraîné sur des
transactions étiquetées fraude / normale. C'est le classifieur principal.

### Branche B — LSTM-Autoencoder

L'idée est élégante : plutôt que d'apprendre à reconnaître la fraude (qu'on
observe peu), on apprend à reproduire le **comportement normal** d'un compte.

- On construit, pour chaque transaction, la **séquence des W dernières opérations
  du même émetteur**.
- Un encodeur LSTM compresse cette séquence en un vecteur latent (16 dimensions),
  un décodeur LSTM tente de la reconstruire.
- L'**erreur de reconstruction** mesure à quel point la séquence est atypique.

Une rafale *fan-out* produit une erreur élevée parce que le LSTM n'a jamais vu ce
schéma pour ce compte. Cette erreur devient ensuite une **feature** de la branche
A — les deux branches ne votent pas séparément, la seconde nourrit la première.

---

## 4. Les features

**37 features** au total (`aml_ml_demo_meta.json`), regroupables en familles :

| Famille | Exemples | Ce qu'elles capturent |
|---|---|---|
| Montant | `amount`, `amount_log`, `amount_sqrt` | Ordre de grandeur, écrasement des extrêmes |
| Devise | `currency_mismatch` | Incohérence entre devises d'envoi et de réception |
| Format de paiement | `fmt_ACH`, `fmt_Cash`, `fmt_Cheque`, `fmt_Bitcoin`… | Canal utilisé (le cash et le crypto sont plus risqués) |
| Temporel | heure, jour de la semaine | Opérations nocturnes, week-end |
| Comportemental | écart-type des montants de l'émetteur, vélocité | Rupture par rapport aux habitudes |
| Anomalie | `reconstruction_error` | Sortie de la branche B |

Les transformations `log` et `sqrt` du montant ne sont pas décoratives : les
montants en AML suivent une distribution très étalée (de 50 TND à plusieurs
millions). Sans compression, quelques valeurs extrêmes dominent l'apprentissage.

---

## 5. Résultats mesurés

Métriques du modèle retenu — **LightGBM + LSTM-AE** :

```
PR-AUC     : 0.9528
F1         : 0.9640
Précision  : 0.9869
Rappel     : 0.9421

Matrice de confusion :
    Vrais positifs  (TP) :     976
    Faux positifs   (FP) :      13
    Faux négatifs   (FN) :      60
    Vrais négatifs  (TN) : 118 952
```

Lecture métier de ces chiffres :

- **Précision 98,7 %** — quand le modèle crie à la fraude, il a raison 99 fois sur
  100. C'est ce qui compte pour un service conformité : peu de faux positifs, donc
  peu de temps perdu.
- **Rappel 94,2 %** — il rate environ 6 % des fraudes. Acceptable **parce qu'il
  n'est pas seul** : les règles déterministes attrapent des cas que le ML manque.
- **PR-AUC** est reportée plutôt que ROC-AUC parce que les classes sont très
  déséquilibrées (1 036 fraudes contre ~119 000 normales). Dans ce contexte, la
  ROC-AUC est trop flatteuse ; la PR-AUC est la mesure honnête.

Le **seuil de décision** est extrêmement haut (≈ 0,99999983). Ce n'est pas une
anomalie : c'est la conséquence d'un modèle très confiant sur un jeu déséquilibré.
Le seuil a été optimisé pour maximiser le F1, pas fixé arbitrairement à 0,5.

---

## 6. Le garde-fou « profil nouveau »

C'est un correctif important apporté en cours de projet, et un bon exemple
d'honnêteté méthodologique.

**Le problème.** Les features comportementales (écart-type des montants,
vélocité, erreur de reconstruction) nécessitent un historique. Pour un client
sans passé, elles sont remplies par des **médianes**. Le modèle recevait donc des
valeurs plausibles mais fictives, et sortait régulièrement une probabilité
saturée à 100 % — qui gonflait ensuite le score combiné de 5 à 12 points.

**La correction.** Le SAR porte désormais deux attributs, `ml_reliable` et
`ml_profile_status`. Si le client n'a pas assez d'historique (**seuil : 5
transactions sur 30 jours**) :

- la probabilité ML est **affichée** (transparence) ;
- elle est **exclue** du score combiné ;
- elle est **exclue** du calcul de divergence RAG↔ML, qui passe à
  « NON ÉVALUÉE » plutôt que de produire une fausse alerte de désaccord ;
- l'interface affiche un bandeau explicite : *« Client sans historique : les
  features comportementales sont estimées → la probabilité n'est pas calibrée sur
  ce cas. »*

C'est exactement ce qu'on observe sur la transaction de démonstration
TX-20260722-0002 : le ML annonce 100 % de fraude, et le système l'ignore
proprement en s'appuyant sur les règles L1 + RAG.

---

## 7. Explicabilité — SHAP

Une probabilité brute n'est pas exploitable par un analyste conformité. Chaque
prédiction est donc accompagnée de ses **trois principales contributions SHAP**,
traduites en langage métier via un dictionnaire (`FEAT_FR`).

Exemple réel affiché dans l'interface :

```
heure de l'opération   ↗ FRAUDE
jour de la semaine     ↗ FRAUDE
fmt_Reinvestment       ↗ FRAUDE
```

Chaque feature indique le **sens** de sa contribution. C'est ce qui permet à
l'analyste de juger si l'alerte tient debout — et, ici, de constater qu'elle
repose surtout sur des features temporelles, ce qui est un signe de **biais
temporel** du modèle plutôt que d'un vrai signal de fraude. Le garde-fou
« profil nouveau » a donc bien fait son travail en l'écartant.

---

## 8. Divergence RAG ↔ ML

Le SAR calcule un indicateur de **concordance** entre le chemin réglementaire et
le chemin statistique :

| Situation | Divergence | Conséquence |
|---|---|---|
| ML fraude **et** règles alertent (EDD/STR/BLOCK) | **FAIBLE** | Décision robuste, les deux concordent |
| ML légitime **et** règles ne alertent pas | **FAIBLE** | Décision robuste |
| ML fraude **mais** règles conformes | **ÉLEVÉE** | Revue humaine — schéma nouveau non couvert, ou biais du modèle |
| ML légitime **mais** règles STR/BLOCK | **ÉLEVÉE** | Le symbolique prévaut, décision maintenue |
| Profil nouveau | **NON ÉVALUÉE** | ML ignoré, décision sur règles seules |

Cette divergence est un outil de **supervision du modèle** : une divergence
élevée récurrente signale soit un angle mort des règles, soit une dérive du ML.

---

## 9. Limites assumées

- **Le modèle est entraîné sur un jeu public** (IBM AML), pas sur des données
  bancaires tunisiennes réelles. Les distributions diffèrent nécessairement.
- **Biais temporel** : les features heure/jour ressortent souvent dans SHAP, ce
  qui traduit une particularité du jeu d'entraînement plus qu'une réalité métier.
  C'est identifié et assumé, et c'est précisément pourquoi le ML est subordonné.
- **Pas de réentraînement automatique à ce stade** : les décisions du superviseur
  sont collectées (voir document 21) mais la boucle de réentraînement planifié
  reste à brancher.

---

## 10. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `ml_fraud_detector_v4.py` | Pipeline d'entraînement dual-branch + LSTM-AE + SHAP |
| `train_demo_ml.py` | Entraînement du modèle de démonstration |
| `calibrate_ml.py` | Calibration du seuil de décision |
| `aml_ml_demo_meta.json` | 37 features, seuil, métriques, architecture |
| `aml_ml_demo_lgb.txt` / `_xgb.json` | Modèles sérialisés |
| `ml_api.py` | Service d'inférence |
