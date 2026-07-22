# 20 — Simulateur de flux et scénarios de démonstration

> Comment démontrer un système AML sans données bancaires réelles, et sans
> saisir vingt formulaires à la main devant un jury.

---

## 1. Objectif

Un système de détection ne se démontre pas sur une transaction isolée. Il faut
du **flux** : des opérations qui arrivent, des clients qui reviennent, des
schémas qui se construisent sur plusieurs transactions.

Deux problèmes à résoudre :

1. Certains comportements — le fractionnement notamment — n'existent que dans une
   **séquence**. Une seule opération ne peut pas les révéler.
2. Une démonstration doit être **reproductible** et **rapide**. Saisir
   manuellement huit formulaires devant un jury est à la fois long et risqué.

D'où deux dispositifs complémentaires : un générateur de flux continu, et un
scénario scripté rattaché à de vrais comptes.

---

## 2. Le flux bancaire en direct

Un générateur produit des transactions à intervalle réglable (**15 à 120
secondes**), avec une caractéristique importante : il **réutilise les mêmes
clients**.

Ce détail n'est pas cosmétique. C'est ce qui permet :

- de constituer un **historique** dans Redis (fenêtre glissante 24-72 h) ;
- d'alimenter les **features comportementales** du ML (écart-type des montants,
  vélocité) ;
- de faire sortir le **LSTM-Autoencoder** de son état « profil nouveau », et donc
  de rendre le signal ML exploitable.

Sans clients récurrents, le ML resterait indéfiniment non calibré et le
garde-fou « profil nouveau » neutraliserait toujours sa contribution.

L'interface affiche un fil des **12 dernières arrivées**, rafraîchi en continu.

---

## 3. Le scénario clients réels

C'est le dispositif principal de démonstration, et le plus convaincant.

### Le principe

Huit opérations sont rejouées, **rattachées à trois comptes réels** de la
plateforme. Chaque opération porte :

- `source = USER` et un `owner` correspondant à un compte existant ;
- un `sender_name` de la forme `Account-<email>`, garantissant un `client_id`
  stable pour Redis et le Niveau 1.

La conséquence est que ces transactions **apparaissent réellement dans « Mes
dossiers »** de chaque client, peuvent être instruites par le superviseur, et
déclenchent de **vrais emails** de décision. Ce ne sont pas des données de
façade : elles traversent exactement le même chemin qu'une saisie manuelle.

### Les huit opérations

| # | Attendu | Ce que le cas démontre |
|---|---|---|
| 1 | `PASS` | Cas nominal — opération conforme |
| 2 | `EDD` | Vigilance renforcée déclenchée |
| 3 | `STR` | Déclaration de soupçon, **avec justificatif joint** |
| 4 | `STR` | Seuil dépassé sans justificatif |
| 5 | `PASS` | Dépôt normal (1/3) |
| 6 | `PASS` | Dépôt normal (2/3) |
| 7 | **`STR`** | **3ᵉ dépôt → fractionnement détecté** |
| 8 | `BLOCK` | Pays sous sanction — blocage |

Les opérations 5, 6 et 7 sont le cœur de la démonstration : **trois dépôts
successifs du même client**, chacun sous le seuil de déclaration. Les deux
premiers passent normalement. Le troisième déclenche un STR — non pas à cause de
son montant, mais parce que **l'historique** révèle le schéma de fractionnement.

C'est la meilleure illustration de la valeur de la mémoire glissante : aucune des
trois opérations n'est suspecte isolément.

### Les justificatifs

Trois opérations joignent un document réel issu de `test_documents/`, transmis en
base64 et analysé à l'étape 0 par docling. Cela démontre en conditions réelles
l'effet du justificatif sur la décision (voir document 16).

### Le suivi

L'interface affiche le déroulé avec, pour chaque opération, l'**attendu** et
l'**obtenu**, et une validation par comparaison. Un écart est immédiatement
visible — c'est aussi un outil de non-régression pour le développement.

---

## 4. La démo rapide (six cas)

Un second mode enchaîne les six transactions de `test_transactions_e2e.json`,
étiquetées `FRAUDE` ou `NORMALE`.

La validation est faite par **famille de décisions** plutôt que par égalité
stricte :

| Étiquette | Décisions acceptées |
|---|---|
| `FRAUDE` | `EDD`, `STR`, `BLOCK` |
| `NORMALE` | `PASS`, `REVIEW`, `KYC`, `RECORD` |

C'est méthodologiquement correct : ce qui compte n'est pas que le système
prononce exactement `STR` plutôt que `EDD`, mais qu'il **classe du bon côté** de
la frontière entre « à traiter » et « à laisser passer ».

Chaque résultat est cliquable et renvoie vers le Pipeline Live correspondant.

---

## 5. Le guide de démonstration

Un document séparé (`GUIDE_DEMO_JURY.md`) décrit **14 scénarios** prêts à jouer,
avec pour chacun les valeurs exactes à saisir et le résultat attendu :

| Scénario | Ce qu'il montre |
|---|---|
| D1 | Cas nominal |
| D2 | Dépassement de seuil → STR |
| D3 | **Justificatif vérifié** — le document débloque le dossier |
| D4 | **Document hors sujet** — rejeté, le dossier reste bloqué |
| D5 | Extraction DOCX par docling |
| D6 | **Fractionnement** sur trois dépôts |
| D7 | Liste noire FATF (Iran) → blocage |
| D8 | Liste grise (Algérie) → vigilance renforcée |
| D9 | Montant juste sous le seuil (9 900) |
| D10 | Profil PEP |
| D11 | Déblocage a posteriori par justificatif |
| D12 | Validation RIB/IBAN |
| D13 | **Séparation des devoirs** + piste d'audit |
| D14 | Facture périmée — cas limite de date |

Les scénarios D3, D4, D6 et D13 sont ceux qui font le plus d'effet, parce qu'ils
montrent le système en train de **raisonner** plutôt que d'appliquer un seuil.

### Préparation

- Bénéficiaires exacts requis (les noms sont vérifiés) :
  `Societe Lumiere SARL`, `Bureau Etudes Manar SARL`, `Ste El Amen Distribution`.
- Client de démonstration : `802559360`.
- `seed_redis_history.py` amorce l'historique comportemental.
- Quatre comptes, un par rôle, pour démontrer la séparation des devoirs.

---

## 6. Pourquoi ce dispositif est défendable

Une objection possible du jury : « vos données sont générées, donc votre
démonstration est truquée ».

La réponse tient en trois points :

1. **Les transactions générées traversent le pipeline complet**, sans raccourci.
   Aucune décision n'est écrite à l'avance : les attendus servent à *vérifier* le
   système, pas à le contraindre.
2. **Les scénarios sont rattachés à de vrais comptes**, avec de vrais emails et de
   vraies validations humaines. Le chemin est identique à celui d'une saisie
   manuelle.
3. **Les modèles ML sont entraînés sur un jeu public reconnu** (IBM AML), pas sur
   les données du simulateur. Le simulateur sert à l'**exécution** de la
   démonstration, pas à l'entraînement.

Ce qui manque — et qu'il faut assumer — ce sont des données bancaires
tunisiennes réelles, inaccessibles pour un projet étudiant pour des raisons
évidentes de confidentialité.

---

## 7. Limites assumées

- **Pas d'historique de simulation** : les sessions passées du générateur ne sont
  pas conservées pour analyse comparative.
- **Diversité limitée** : le générateur produit une variété de cas plus étroite
  qu'un flux bancaire réel.
- **Le fractionnement démontré est simple** (trois dépôts identiques) ; les
  schémas réels sont plus subtils.

---

## 8. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `api/main.py` | `REALISTIC_SCENARIOS`, `_SCN_USERS`, endpoints simulateur |
| `test_transactions_e2e.json` | Les six cas de la démo rapide |
| `generate_scenarios.py`, `evaluation_scenarios.py` | Génération de scénarios |
| `seed_redis_history.py` | Amorçage de l'historique comportemental |
| `client_history_store.py` | Mémoire glissante Redis (24-72 h) |
| `GUIDE_DEMO_JURY.md` | Les 14 scénarios détaillés |
| `project/src/pages/Simulator.tsx` | Flux continu, scénario, arrivées en direct |
