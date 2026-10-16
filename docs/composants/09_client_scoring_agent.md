# 09 — Client Scoring Agent (étape 2 du pipeline)

> Le profil du client, évalué avant même de regarder le contenu réglementaire.
> 100 % déterministe, aucun LLM.

---

## 1. Objectif

Une même transaction n'a pas le même sens selon qui l'exécute. Un virement de
50 000 TND émis par un commerçant établi depuis dix ans ne se lit pas comme le
même virement émis par un compte ouvert la semaine dernière au nom d'une société
d'import-export dont le bénéficiaire effectif est inconnu.

Le Client Scoring Agent répond donc à une question simple : **quel est le risque
structurel porté par ce client, indépendamment de l'opération en cours ?**

Ce score alimente ensuite deux choses :
- il devient un **facteur du score de conformité** (contribution + plancher) ;
- il **enrichit les questions** posées par le Planning Agent (un PEP déclenche
  des questions de vigilance renforcée que le montant seul ne déclencherait pas).

---

## 2. Place dans le pipeline

```
[1] Level 1 (règles fixes)
        │
        ▼
[2] CLIENT SCORING  ←── profil KYC (base clients ou profil de l'utilisateur connecté)
        │   score 0-100 + niveau FAIBLE / MOYEN / ÉLEVÉ + signaux CLIENT_*
        ▼
[3] Planning Agent (les signaux client deviennent des questions)
```

Le profil provient de deux sources, dans cet ordre de priorité :

1. **Le profil de l'utilisateur connecté** (page « Mon profil » de la plateforme) —
   c'est le cas nominal en démonstration : la cliente saisit ses coordonnées, elles
   alimentent réellement le scoring.
2. **La base clients** `clients_db.json` (5 009 profils) si le client est connu
   par son identifiant ou son RIB.

Si le client est totalement inconnu, le système ne bloque pas : il applique un
profil neutre et le signale (`profil nouveau`), ce qui a des conséquences plus
loin sur le ML (voir document 10).

---

## 3. Choix de conception

### Somme pondérée plutôt qu'un modèle appris

Le scoring client n'utilise **pas** de machine learning, et c'est volontaire.
Un régulateur doit pouvoir demander « pourquoi ce client est-il classé ÉLEVÉ ? »
et obtenir une réponse en une phrase. Une somme pondérée de facteurs nommés
donne exactement cela : chaque point de score est traçable à une règle citée.

Chaque facteur porte donc un **poids documenté et défendable**, adossé à une
source (FATF R1, R10, R12, R13 ; EBA GL 2021/02).

### Calibration : un facteur fort ne suffit pas

Les poids ont été calibrés selon un principe simple et explicable :

> **un seul facteur fort classe en MOYEN ; il en faut au moins deux pour ÉLEVÉ.**

C'est ce qui évite qu'un client soit sur-classé pour une seule caractéristique
(être PEP domestique n'est pas en soi une présomption de blanchiment), tout en
faisant remonter les profils cumulant plusieurs signaux.

---

## 4. Les facteurs et leurs poids

Implémentés dans `RiskFactorScorer.WEIGHTS` (`client_scoring_agent.py`) :

| Facteur | Poids | Justification |
|---|---|---|
| `pep_foreign` | **35** | PEP étranger — risque maximal (FATF R12) |
| `pep_domestic` | **20** | PEP domestique — risque élevé mais moindre |
| `high_risk_country` | **25** | Résidence/nationalité en pays liste noire FATF |
| `prior_strs` | **20** | Déclarations de soupçon antérieures |
| `high_risk_profession` | **18** | Activité cash-intensive ou opaque |
| `incomplete_kyc` | **15** | Dossier d'identification incomplet |
| `correspondent_account` | **15** | Compte de correspondance (FATF R13) |
| `grey_country` | **12** | Pays sous surveillance renforcée FATF |
| `no_beneficial_owner` | **12** | Bénéficiaire effectif non identifié |
| `new_relationship` | **10** | Relation d'affaires < 90 jours |
| `trust_account` | **10** | Structure potentiellement opaque |

Le score brut est **borné à 100**.

**Seuils de niveau** (`LEVEL_THRESHOLDS`) :

```
score ≥ 50  →  ÉLEVÉ
score ≥ 25  →  MOYEN
sinon       →  FAIBLE
```

### Professions à risque

La liste `HIGH_RISK_PROFESSIONS` couvre les secteurs reconnus comme
cash-intensifs ou opaques : casino et jeux, crypto-monnaies, marchands d'art et
antiquaires, métaux précieux, immobilier, import-export, bureaux de change,
armement, pétrole et industries extractives.

Un détail qui a son importance : la comparaison se fait sur des libellés
**normalisés avec espaces** (`import export`, `real estate`). Un bug corrigé en
cours de projet venait de là — la base stockait `import_export` avec un
underscore, et le facteur ne se déclenchait donc jamais.

---

## 5. Algorithme

```
score(profil) :
    composants = {}
    audit     = []

    pour chaque facteur applicable au profil :
        poids = WEIGHTS[facteur]
        composants[facteur] = poids
        audit.append("facteur (+poids) — raison lisible")

    score = min(somme(composants), 100)
    niveau = ÉLEVÉ si score ≥ 50, MOYEN si ≥ 25, sinon FAIBLE
    retourner (score, niveau, composants, audit)
```

Le point important est la **piste d'audit** : chaque facteur retenu produit une
ligne lisible du type
`pep_foreign (+35.0) — client identifié comme personne politiquement exposée étrangère`.
C'est cette liste qui se retrouve dans le rapport SAR et dans l'interface, et qui
rend la décision explicable sans lire le code.

---

## 6. Articulation avec le Compliance Agent

Le score client **n'est pas** ajouté une seconde fois au score final du SAR — ce
serait un double comptage. Il agit à deux endroits, dans le Compliance Agent :

- **Contribution inhérente** : un client ÉLEVÉ ajoute une part au risque inhérent
  (`CLIENT_RISK_CONTRIB`, +0,15).
- **Plancher de décision** (`CLIENT_FLOOR`) : un client ÉLEVÉ impose un score
  minimum de 45, un client MOYEN un minimum de 25. Autrement dit, un profil à
  risque ne peut pas produire un score de conformité quasi nul, même sur une
  opération anodine.

C'est un point à assumer devant un jury : *le risque client est déjà intégré au
score de conformité, il n'est volontairement pas recompté au niveau du SAR.*

---

## 7. Résultats et comportement observé

- Sur les scénarios de démonstration, le scoring distingue correctement les trois
  niveaux et les signaux `CLIENT_*` remontent dans la piste d'audit du SAR.
- Les signaux client sont transmis au Planning Agent : c'est ce qui fait qu'un
  dossier PEP génère des questions de vigilance renforcée absentes d'un dossier
  ordinaire de même montant.
- Les facteurs `CLIENT_*` apparaissent dans l'audit du Compliance Agent avec un
  **poids nul** dans les signaux souples — c'est intentionnel et documenté dans le
  code : le risque structurel est compté via `client_profile`, pas deux fois.

---

## 8. Limites assumées

- **Les poids sont une calibration, pas une source réglementaire.** La FATF impose
  une approche par les risques, pas un barème chiffré. Ce qui est réglementaire,
  c'est la *structure* (facteurs de risque client, vigilance renforcée pour les
  PEP), pas la valeur « 35 ».
- **La qualité du score dépend de la qualité du profil.** Un profil KYC incomplet
  produit mécaniquement un score partiel — le système le signale via le facteur
  `incomplete_kyc` plutôt que de faire semblant.
- **Pas d'apprentissage.** Le scoring ne s'adapte pas automatiquement ; toute
  évolution des poids est une décision humaine tracée dans le code.

---

## 9. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `client_scoring_agent.py` | `RiskFactorScorer`, profils, calcul du score |
| `clients_db_loader.py` | Chargement et normalisation des 5 009 profils |
| `clients_db.json` | Base clients (identifiant + RIB comme clés) |
| `api/auth.py` | Profil KYC de l'utilisateur connecté (`get_user_raw_profile`) |
