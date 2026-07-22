# NIVEAU 1 — Règles fixes déterministes

> Support de soutenance — la première ligne de défense, 0% LLM.

---

## 1. Positionnement

Le Niveau 1 est la **première couche de défense, entièrement déterministe**. Aucun LLM n'intervient. Son rôle : filtrer les violations les plus graves (sanctions, seuils légaux, pays à risque) avec une **fiabilité de 100% et une latence < 100 ms**.

```
        TRANSACTION
            │
            ▼
   ┌──────────────────────────────────────────────┐
   │  NIVEAU 1 — Règles Python fixes (0% LLM)      │
   │  Sanctions · Risque pays · Seuils · Multi-fact│
   └──────────────────────────────────────────────┘
            │
            ▼
   Level1Result { decision, violations, risk_signals, details }
            │
            ▼  risk_signals → alimentent le Niveau 2 (Planning Agent)
```

**Justification du 0% LLM** : une décision impliquant une sanction ONU ou un seuil légal doit être **100% reproductible et auditable** par un régulateur. Un LLM peut halluciner ou varier. Une comparaison `montant > seuil` donne toujours le même résultat, traçable et défendable.

---

## 2. L'orchestrateur — 5 vérifications par ordre de priorité

```
PASS(1) < RECORD(2) < KYC(3) < EDD(4) < STR(5) < BLOCK(6)
```
La décision retenue est **toujours la plus haute priorité** obtenue parmi les 5 vérifications.

```
Transaction
   ├─ [1] Sanctions ONU + PEPs ──────── BLOCK si match
   ├─ [2] Multi-facteurs suspicion ──── STR si ≥ 2 facteurs
   ├─ [3] Seuils réglementaires BCT ─── RECORD/KYC/EDD/STR
   ├─ [4] Juste-sous-seuil ──────────── signal AMOUNT_JUST_BELOW
   └─ [5] Risque pays FATF ──────────── BLOCK (noir) / EDD (gris)
   = max(priorités) → Level1Result
```

---

## 3. Module 1 — Screening des sanctions (`sanctions_screening.py`)

### Sources chargées au démarrage
- **Liste consolidée ONU** : 2 807 individus + 853 entités (résolutions UNSCR 1267, 1373…).
- **PEPs** : base OpenSanctions (schéma FollowTheMoney : Occupancy → Person).
- **Journal d'audit** : chaque vérification tracée (timestamp, nom, décision, source, confiance).

### Algorithme en 5 étapes
1. **Validation entrée** : ≥ 4 caractères, ≥ 3 lettres (rejette les codes/identifiants → évite les faux positifs).
2. **Correspondance exacte O(1)** : dictionnaire indexé par nom normalisé (minuscules, sans accents).
3. **Correspondance floue** : `rapidfuzz.token_sort_ratio` sur les 2 807 individus. Le tri alphabétique des tokens rend robuste à l'ordre des prénoms/noms. **Seuil = 90/100** (calibré pour minimiser les faux négatifs sans excès de faux positifs).
4. **Résolution d'alias** : remonte la chaîne `alias_of` jusqu'à l'enregistrement canonique.
5. **Résultat structuré** : `SanctionResult(is_sanctioned, match_type, confidence, source, reason)`.

> L'émetteur ET le bénéficiaire sont vérifiés indépendamment. Un match → `BLOCK` immédiat.

---

## 4. Module 2 — Risque pays FATF (`risk_country_checker.py`)

### Données (`data/fatf_lists.json`, maj 2026-02-13)
- **Liste noire (3 pays)** : Corée du Nord (KP), Iran (IR), Myanmar (MM).
- **Liste grise (22 pays)** : surveillance renforcée FATF.

### Résolution en cascade
```
Pays soumis → code ISO direct ? → alias (DPRK→KP, Burma→MM) ?
            → nom normalisé exact ? → sous-chaîne ? → sinon UNKNOWN → PASS
```

### Décisions
| Catégorie | Décision | Recommandation FATF | Signal |
|-----------|----------|--------------------|--------|
| Liste noire | **BLOCK** | R7 (contre-mesures) | `COUNTRY_BLACK_*` |
| Liste grise | **EDD** | R19 (surveillance accrue) | `COUNTRY_GREY_*` |
| Aucune liste | PASS | — | — |

> Vérifié sur le pays émetteur ET le pays destination.

---

## 5. Module 3 — Seuils réglementaires BCT (`threshold_rules.py`)

### Philosophie : seuils codés en dur (pas en base)
1. **Auditabilité** : un seuil dans le code source est visible et versionnable (git).
2. **Immutabilité** : aucun bug de base ne peut altérer un seuil légal.
3. **Performance** : aucune requête réseau pour un cas ultra-fréquent.

Chaque seuil est annoté de sa **base légale exacte**.

### Tableau des seuils
| Type | Montant | Condition | Décision | Base légale |
|------|---------|-----------|----------|-------------|
| VIREMENT_LOCAL | > 10 000 TND | — | **STR** | BCT Circulaire 2017-08, Art. 43 |
| SWIFT | > 10 000 USD | sans justificatif | **STR** | BCT 2017-08 + FATF R16 |
| SWIFT | > 10 000 USD | avec justificatif | **EDD** | BCT 2017-08, Art. 43 |
| SWIFT | > 15 000 EUR | — | **EDD** | FATF Recommandation 16 (Travel Rule) |
| DEPOT_ESPECES | > 3 000 TND | — | **RECORD** | BCT Circulaire 2006-19, Art. 7 |
| DEPOT_ESPECES | > 5 000 TND | — | **STR** | BCT Circulaire 2006-19, Art. 8 |
| MANDAT | > 1 000 TND | — | **KYC** | Décret-Loi 2021-25, Art. 37 |
| MANDAT | > 3 000 TND | + motif suspect | **STR** | Décret-Loi 2021-25, Art. 37 |

### Conversion de devises
Tout montant en devise étrangère est converti en TND (taux de référence BCT) **avant** application des seuils → comparabilité inter-devises. *(Limite documentée : taux fixes, pas un flux temps réel.)*

### Détection du fractionnement (smurfing)
**Condition** : ≥ 3 dépôts espèces, chacun < 5 000 TND, dans une fenêtre glissante de **72 heures**. Inspiré de la recommandation FATF n°20 sur le structuring. → signal `SMURFING_PATTERN` + décision STR.

---

## 6. Module 4 — Détection multi-facteurs

Aucun facteur isolé ne suffit ; leur **combinaison** constitue une présomption. 7 indicateurs :

| Identifiant | Description |
|-------------|-------------|
| `MOTIVE_VAGUE` | Motif < 5 car., absent, ou générique (don, gift, personal…) |
| `CASH_WITHDRAWAL` | Transaction vers retrait espèces |
| `HIGH_RISK_OPERATOR` | WESTERN_UNION, MONEYGRAM, WAFACASH, RIA |
| `AMOUNT_ROUND` | Montant rond multiple de 500/1000 ≥ 1000 |
| `NEW_RECEIVER` | Premier envoi vers ce bénéficiaire |
| `UNUSUAL_HOUR` | Transaction entre 23h et 5h |
| `MOTIVE_EXPLICIT_SUSPECT` | Termes suspects explicites |

**Règle** : motif explicitement suspect **OU ≥ 2 facteurs** → `is_suspicious = True` → contribue au déclenchement STR.

---

## 7. La sortie : `Level1Result`

```python
@dataclass
class Level1Result:
    decision:     str    # "BLOCK" | "STR" | "EDD" | "KYC" | "RECORD" | "PASS"
    violations:   list   # textes lisibles (→ SAR Niveau 3)
    risk_signals: list   # codes structurés (→ Planning Agent Niveau 2)
    details:      dict   # résultats bruts par module (audit)
```

Le champ **`risk_signals`** est l'interface critique vers le Niveau 2 : il encode les résultats déterministes dans un vocabulaire standardisé que le Planning Agent utilise pour construire ses recherches.

---

## 8. Résultats
- **8/8 scénarios de test passés.**
- Latence : < 10 ms (exact), < 50 ms (fuzzy sur 2 807 individus).
- 0% LLM → 100% reproductible et auditable.

---

## 9. Questions probables du jury

**Q1 — Pourquoi coder les seuils en dur plutôt qu'en base de données ?**
> Pour l'auditabilité (visible dans git), l'immutabilité (aucun bug de base ne peut altérer un seuil légal) et la performance. Chaque seuil est annoté de sa base légale exacte (circulaire, article).

**Q2 — Comment gérez-vous les variantes orthographiques d'un nom sanctionné ?**
> Trois niveaux : correspondance exacte O(1), puis fuzzy matching (rapidfuzz token_sort_ratio, seuil 90), puis résolution d'alias. Le tri des tokens rend robuste à l'ordre prénom/nom (« Mohammed Ali Hassan » ≡ « Hassan Mohammed Ali »).

**Q3 — Pourquoi un seuil fuzzy de 90 et pas 80 ou 95 ?**
> Calibré empiriquement : en dessous, trop de faux positifs opérationnels ; au-dessus, risque de manquer une variante. 90 minimise les faux négatifs (sécurité) sans saturer les analystes.

**Q4 — Le seuil cash de 5000 TND n'a-t-il pas été abrogé en 2026 ?**
> L'article 45 de la loi de finances 2019 (criminalisation pénale du cash > 5000 TND) a été abrogé par l'article 54 de la loi de finances 2026. MAIS les obligations de déclaration AML restent dans les circulaires BCT (2006-19, 2017-08), toujours en vigueur. Mon Niveau 1 référence les circulaires BCT, pas l'article pénal abrogé — le système est à jour.

**Q5 — Pourquoi séparer le Niveau 1 du reste ?**
> Parce que les sanctions et les seuils ne tolèrent aucune ambiguïté : ils doivent être 100% reproductibles. Le RAG (Niveau 2) n'intervient que là où un raisonnement contextuel est réellement nécessaire. C'est la séparation entre « tolérance zéro » et « jugement ».

---

*Fin — Niveau 1 v2.*
