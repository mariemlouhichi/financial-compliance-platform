# LE COMPLIANCE AGENT — Support de soutenance

> PFE AML/KYC Agentic RAG — Mariem Louhichi — 2025/2026
> Document de préparation : explication étape par étape, résultats, et questions de jury.

---

## 1. Positionnement : que fait le Compliance Agent ?

Le Compliance Agent est le **cœur du Niveau 2 (Agentic RAG)**. Il reçoit une transaction déjà pré-filtrée par le Niveau 1 (déterministe) + des chunks réglementaires récupérés par le Retrieval Agent, et produit une **décision de conformité explicable et traçable**.

```
Transaction + risk_signals (L1) + chunks (Retrieval) + checklist FATF
                          │
                          ▼
                 COMPLIANCE AGENT  (neuro-symbolique dual-path)
                          │
                          ▼
   ComplianceDecision { décision, score, violations, couvertures,
                        flags revue humaine, audit_trail }
```

**Architecture neuro-symbolique « dual-path »** : deux chemins de raisonnement en parallèle.

| Chemin | Quoi | Fiabilité |
|--------|------|-----------|
| **Symbolique** | Règles déterministes (Neo4j + signaux Niveau 1) | 100%, zéro hallucination |
| **Sémantique** | Extraction LLM des obligations depuis les chunks | Flexible, mais validé contre le texte source |

**Thèse centrale** : le déterministe pour les faits durs, le LLM pour le contextuel, et **le déterministe gagne toujours** en cas de conflit.

---

## 2. Le pipeline en 6 phases (vue d'ensemble)

```
PHASE 1  Symbolique          → règles déterministes + signaux HARD/SOFT
PHASE 2  Gap analysis        → checklist − couvert = écarts à explorer
PHASE 3  Sémantique (LLM)    → extraction + validation anti-hallucination (gaps seulement)
PHASE 4  Réconciliation      → fusion symbolique + sémantique, contradictions → revue humaine
PHASE 5  Scoring résiduel    → Résiduel = Inhérent × (1 − Contrôles)
PHASE 6  Décision + audit    → PASS/REVIEW/EDD/STR/BLOCK + traçabilité
```

---

## 3. PHASE 1 — Chemin symbolique (`SymbolicReasoner`)

Deux sources alimentent le raisonnement déterministe.

### (a) Les signaux HARD / SOFT du Niveau 1
- **HARD** = fait avéré → **violation directe** (certitude 1.0).
  Ex : `SANCTION_UN_INDIVIDUAL`→R6, `COUNTRY_BLACK`→R19, `THRESHOLD_STR`→R20.
- **SOFT** = indicateur de risque → **contribution au score**, jamais une violation.
  Ex : montant rond, heure atypique, motif vague. **Plafonnés à 30%** du risque (principe de **non-domination** : aucun indicateur faible ne peut tout faire basculer).

### (b) Les règles actives de Neo4j (49 ground truth + règles apprises)
- Chaque règle reçoit une **recommandation FATF déterministe** (`map_to_fatf_rec`, table de mots-clés fixe).
- **Principe BCT-first** : si plusieurs règles couvrent une même rec, on garde la **source contraignante tunisienne** (BCT/loi/CTAF) plutôt que le standard FATF.
- Les règles ground truth sont en **couverture seule** (elles attestent l'obligation, sans violation automatique — une obligation de CDD ne se « viole » pas par un seuil).

---

## 4. PHASE 2 — Gap analysis : comment garantir la checklist ?

```
gaps = checklist_requise − couvert_par_le_symbolique
```
Les écarts définissent **ce que le LLM doit travailler**. Si le symbolique couvre tout → **pas d'appel LLM** (économie + zéro risque).

### ⚠️ Réponse honnête : on ne « prouve » pas une checklist à 100%
Aucun régulateur ne publie « la liste exhaustive des obligations d'un virement SWIFT ». C'est une limite de TOUT système de conformité. Ce qu'on garantit : **sourcée, filtrée selon un critère documenté, auditable.**

### Comment elle est construite (`dynamic_checklist.py`)
À partir des **40 Recommandations FATF (2023)**, filtrées :
```
40 recommandations FATF
   ├── 16 HAUTEMENT pertinentes (actionnables au niveau transaction)  → retenues
   ├──  7 MOYENNEMENT pertinentes (contextuelles)                     → retenues
   └── 17 NON pertinentes (coopération internationale, institutionnel) → exclues
   = 23 recommandations retenues
```
Chaque rec = **titre officiel exact** + mots-clés de couverture. Ex :
```python
10: ("Customer due diligence", ["CDD","identification","beneficial owner",...], "HIGH")
16: ("Wire transfers", ["wire transfer","travel rule","originator",...], "HIGH")
```

### Les 3 garanties de défendabilité
1. **Sourcée** : titres/numéros tirés littéralement du texte FATF 2023.
2. **Critère de filtrage documenté** : exclusion du purement institutionnel (non actionnable sur une transaction).
3. **Par type de transaction** : un SWIFT ≠ un dépôt espèces (la checklist s'adapte).

> **Réponse jury** : « La checklist est dérivée des 40 Recommandations FATF officielles, filtrée selon un critère documenté (les 23 actionnables au niveau transactionnel), chaque entrée cite son titre officiel. Elle est auditable et évolutive. C'est une approche par les risques, pas une prétention d'exhaustivité parfaite. »
>
> ❌ Ne jamais dire « 100% correcte ». ✅ Dire « sourcée, filtrée, auditable ».

---

## 5. PHASE 3 — Chemin sémantique (LLM, sur les gaps uniquement)

### C'est quoi un « snippet » et « verbatim » ?
- **Snippet** = **court extrait** (≤ 80 caractères) : la **phrase exacte du chunk** qui prouve la règle. C'est la **citation / preuve**.
- **Verbatim** = **mot pour mot, à l'identique** : la chaîne existe **exactement** dans le corpus.

**Exemple :**
```
Chunk : "Article 43: Any transaction exceeding 10,000 USD without economic
         justification must be reported to CTAF within 5 days."

LLM → rule_text       : "Report transactions over 10,000 USD to CTAF"
      source_snippet  : "exceeding 10,000 USD without economic justification"
                        ↑ VERBATIM (présent mot pour mot) → admis
```
Si le LLM avait écrit `"transactions above 10000 dollars"` → **pas verbatim** → rejeté.

### Les 7 étapes
1. **Compliance Gate** : filtre déterministe des chunks (rejette définitions, chunks d'un autre type de tx). Économise les appels LLM.
2. **Triage TOP-12** : score par autorité de la source + rôle + pertinence aux gaps.
3. **Pass 1 (extraction)** : le LLM extrait les obligations applicables, **snippet verbatim ≤ 80 car. obligatoire**, + seuil, rec FATF, sévérité. Sortie JSON. Prompt : *« le snippet DOIT être verbatim, le seuil DOIT apparaître comme un nombre, n'invente aucune règle »*.
4. **Validation anti-hallucination** (mécanique, pas par le LLM) :
   - **Verbatim** : snippet présent mot pour mot → score 1.0
   - **Sinon trigrammes** : ≥ 60% des groupes de 3 mots consécutifs présents → score 0.6
   - **Sinon** : score 0 → hallucination potentielle
   - Le seuil littéral est aussi vérifié (`10000` doit apparaître dans le chunk).
5. **Gate B4** : `applicable = [r for r in candidates if r.snippet_ok]`. Une règle n'est **ADMISE que si son snippet est ancré**. Toute règle non sourçable est **REJETÉE**. → hallucination **10.26% → 0%**.
6. **Pass 2 (violations)** : distingue « s'applique » de « est violée ». Déterministe d'abord (seuil en équivalent USD), LLM pour le qualitatif (instruction : *« en cas de doute → violated=false »*). Downgrade des violations sémantiques de conf < 0.65.
7. **Persistance R5** : une règle validée **verbatim** est écrite dans Neo4j (MERGE + hash SHA256 + `use_count`). Après **3 réutilisations** → promue « LEARNED ». **Le graphe s'enrichit transaction après transaction.**

---

## 6. PHASE 4 — Réconciliation (`ReconciliationEngine`)

Objectif : fusionner symbolique + sémantique **sans créer de fausses contradictions**.

### Étape 1 — Regrouper le symbolique par rec FATF
```python
sym_by_fatf = {R20: [règle_seuil_STR], R10: [règle_CDD], ...}
```

### Étape 2 — Similarité de Jaccard sur le texte
```
Jaccard(A, B) = |mots communs| / |mots totaux|
Ex : "report suspicious transaction" vs "suspicious transaction report filing"
     communs = 3, totaux = 4 → Jaccard = 0.75 → ≥ 0.30 → MÊME règle
```

### Étape 3 — Les 3 cas
- **Cas A — Accord** (même rec, Jaccard ≥ 0.30, même verdict) → **cross-validation** : confiance +0.10, provenance = RECONCILED. Le déterministe ET le LLM convergent = signal de fiabilité.
- **Cas B — Contradiction réelle** (même règle, verdicts opposés, LLM a tranché) → **flag de revue humaine**. **Le symbolique prévaut.** On ne masque pas le désaccord.
- **Cas C — Complément** (même rec, texte différent) → deux règles distinctes sous la même recommandation (ex. sous R20 : « seuil STR » vs « fractionnement »). **Pas une contradiction.** On ajoute la règle (si conf ≥ 0.5).

**Pourquoi ça compte (fix C3)** : avant, le système confondait deux règles R20 différentes et criait « contradiction » à tort. Jaccard a éliminé ces **fausses contradictions** (scénario S3 smurfing).

---

## 7. PHASE 5 — Scoring résiduel : sur quelle source ?

**Réponse en deux parties : le cadre est réglementaire, la calibration est justifiée.**

### Partie 1 — La STRUCTURE : source réglementaire solide
```
Risque Résiduel = Risque Inhérent × (1 − Efficacité des Contrôles)
Score = Résiduel × 100
```
Formule **canonique de l'approche par les risques**, non inventée :
- **FATF Guidance on the Risk-Based Approach** (2014, 2021) : inherent / control effectiveness / residual.
- **Comité de Bâle — Sound Management of Risks related to ML/FT** (d353).
- **EBA Guidelines 2021/02** (facteurs de risque).
- Éditeurs AML du marché : azakaw, AMLYZE, ACAMS, Flagright.

→ **Inattaquable** : c'est la formule standard FATF/Bâle.

### Partie 2 — Les POIDS : calibration justifiée (PAS un copier-coller)
Aucun régulateur ne publie les poids exacts — volontaire : la FATF impose le **cadre**, chaque institution **calibre** dedans.

| Paramètre | Valeur | Justification |
|-----------|--------|---------------|
| Sévérité CRITIQUE | 1.00 | Sanctions / financement terrorisme = blocage légal immédiat |
| Sévérité HAUTE | 0.70 | Obligation STR légale, PEP |
| Sévérité MOYENNE | 0.40 | KYC/CDD, MVTS, EDD |
| Sévérité BASSE | 0.15 | Record keeping, procédural |
| Plafond contrôles | **80%** | **Bâle : aucune banque n'a un contrôle parfait** (source réelle) |
| Saturation | 3 violations CRITIQUES | Au-delà, risque déjà maximal |
| Plancher Niveau 1 | STR≥65, EDD≥45, KYC≥25, RECORD≥15 | Garantit qu'on ne descend jamais sous l'obligation légale |
| Non-domination SOFT | ≤ 30% | AMLYZE : un indicateur faible seul ne décide pas |

### Les overrides
- Niveau 1 = BLOCK → **BLOCK absolu** (non négociable).
- Violation CRITIQUE **symbolique** → BLOCK. **Une règle LLM seule ne bloque JAMAIS** (symbolique prévaut).

> **Réponse jury** : « Le score repose sur la formule canonique FATF/Bâle : Résiduel = Inhérent × (1 − Efficacité des contrôles) — structure réglementaire non négociable. Les pondérations exactes sont une calibration, car aucun régulateur ne publie de poids chiffrés (laissé à l'institution dans le cadre RBA). Chaque poids est documenté et justifié par un critère. Le plafond de 80% est un principe explicite de Bâle. »
>
> ❌ Ne jamais prétendre que les poids viennent d'une circulaire. ✅ Assumer la calibration en la justifiant — c'est plus crédible.

---

## 8. PHASE 6 — Décision + audit
Mapping score → décision : `PASS < REVIEW < EDD < STR < BLOCK`.
Sortie complète : violations (citant **l'article BCT en premier** ⚖️, FATF en référence), couvertures (BCT contraignante / FATF / preuve), flags de revue humaine, et **audit trail** de chaque phase.

---

## 9. Les améliorations clés

| Code | Problème corrigé | Effet |
|------|------------------|-------|
| **A1** | La sévérité n'avait aucun effet sur le score | Saturation à 3 violations CRITIQUES |
| **B1** | Snippet validé sur 70% des mots → hallucinations passaient | Verbatim OU trigrammes consécutifs |
| **B4** | Règles non sourçables admises | **Gate snippet ancré → hallucination 10.26% → 0%** |
| **C1** | Devise différente → règle ignorée (angle mort) | Comparaison en équivalent USD |
| **C2** | Justificatif annulait la violation (bypass) | Le justificatif **atténue** (demi-poids) |
| **C3** | Fausses contradictions (deux règles R20 confondues) | Matching Jaccard |
| **E1** | 49 règles GT invisibles (décalage de schéma Neo4j) | Couverture FATF 30% → 95% |
| **BCT-first** | Métrique 100% FATF, BCT invisible | Couverture contraignante BCT explicite |
| **Dual 3 zones** | LLM appliqué aveuglément | Faits durs = symbolique seul ; contextuel = parallèle |
| **Symbolique prévaut** | Une règle LLM pouvait déclencher un BLOCK | Seul un fait dur déterministe bloque |

---

## 10. Les résultats obtenus

**100 scénarios générés automatiquement** (oracle Niveau 1 déterministe — pas d'étiquetage manuel) :

| Métrique | Résultat | Cible PFE |
|----------|----------|-----------|
| **Accuracy décisionnelle** | **100/100** (matrice diagonale) | ≥ 85% ✅ |
| **Couverture BCT contraignante** | **84.1%** | — ⚖️ |
| **Couverture FATF (référence)** | **94.7%** | ≥ 90% ✅ |
| **Taux d'hallucination** | **0.00%** | < 2% ✅ |
| **Stabilité** (accord inter-runs) | **100%** (σ=0) | 100% ✅ |

**Les 3 couvertures distinctes (argument méthodologique) :**
```
Symbolique (générique)   100%   « le corpus connaît l'obligation »
BCT contraignante         98%   « source tunisienne contraignante »
Adossée à une preuve   ~78-82%  « preuve vérifiée pour CETTE transaction »
```
→ La couverture générique **surestime**. La couverture adossée à une preuve est la **mesure honnête**.

**Anti-hallucination avant/après gate B4** : 10.26% → **0%**, couverture-preuve quasi inchangée (78.5% → 77%) → on a retiré du **bruit non sourçable**, pas du **signal**.

---

## 11. Questions probables du jury (Q&A)

**Q1 — Pourquoi neuro-symbolique et pas juste un LLM ?**
> Un régulateur n'accepte pas une décision de blocage fondée sur une hallucination potentielle. Les faits durs (sanctions, listes noires, seuils) sont tranchés par du code déterministe à 100%. Le LLM n'intervient que sur le contextuel, et uniquement avec une preuve littérale du texte source. Le symbolique prévaut en cas de conflit.

**Q2 — Comment garantissez-vous l'absence d'hallucination ?**
> Par un gate de validation : une règle extraite n'est admise que si son snippet est retrouvé littéralement dans le corpus (verbatim ou trigrammes ≥ 60%). Mesuré : 0% sur 71 règles. Avant : 10.26%. Et la couverture-preuve n'a quasi pas baissé → on a éliminé du bruit, pas du signal.

**Q3 — Votre accuracy de 100%, n'est-ce pas du surapprentissage ?**
> Non : les scénarios sont étiquetés automatiquement par le Niveau 1 déterministe (le droit BCT/FATF en code), pas à la main. Le test vérifie que l'agent ne descend jamais sous le plancher réglementaire sur 100 cas couvrant les frontières de seuils. C'est un test de sûreté à l'échelle, pas une mémorisation.

**Q4 — Pourquoi la FATF alors que vous êtes en Tunisie ?**
> Je ne m'appuie PAS que sur la FATF. Corpus = 48% BCT, Niveau 1 = 100% BCT, retrieval = 55% sources tunisiennes. La FATF est une taxonomie de référence que la BCT transpose. Chaque violation cite l'article BCT en premier (⚖️). Ma couverture contraignante BCT est mesurée à 84%.

**Q5 — Le justificatif lève-t-il une alerte ?**
> Non, il l'atténue. Avant, un justificatif annulait la violation (bypass trivial). Maintenant la violation reste détectée, le justificatif réduit son poids de 50% et augmente l'efficacité des contrôles. Un justificatif ne fait jamais disparaître une obligation.

**Q6 — Comment scorez-vous le risque ?**
> Formule standard FATF/Bâle : Résiduel = Inhérent × (1 − Efficacité des contrôles). L'inhérent agrège les violations pondérées ; les contrôles le réduisent, plafonnés à 80% (Bâle : pas de contrôle parfait). Un plancher Niveau 1 garantit qu'on ne descend jamais sous l'obligation légale.

**Q7 — Si le LLM et les règles se contredisent ?**
> Le symbolique prévaut systématiquement, la contradiction est remontée pour revue humaine. J'ai distingué une vraie contradiction (même règle, verdicts opposés) d'un simple complément (deux obligations distinctes sous la même rec), via la similarité de Jaccard — ça a éliminé les fausses contradictions.

**Q8 — Votre système apprend-il ?**
> Oui. Toute règle sémantique validée verbatim est persistée dans Neo4j et devient disponible pour le chemin symbolique (plus rapide, plus fiable). Promotion « LEARNED » après 3 réutilisations. La couverture symbolique croît dans le temps.

**Q9 — Le coût / la dépendance au LLM ?**
> Le LLM est appelé conditionnellement (gaps contextuels seulement), jamais sur les faits durs. Cache déterministe (même prompt → réponse réutilisée) + rotation 7 clés × 5 modèles. En mode production dual, le LLM ne traite que ~28% des dimensions.

**Q10 — Quelle est la limite de votre approche ?** *(montre ta lucidité)*
> Deux honnêtetés : (1) la couverture-preuve en production est ~28% — le reste reste couvert symboliquement, acceptable mais perfectible avec un corpus plus riche ; (2) l'oracle Niveau 1 teste la consistance avec le plancher légal, pas une correction totalement indépendante — d'où le jeu expert complémentaire de 24 scénarios annotés.

**Q11 — Comment garantir que la checklist d'obligations est complète ?**
> On ne peut pas la prouver exhaustive — c'est une limite de toute conformité. Elle est dérivée des 40 Recommandations FATF officielles, filtrée selon un critère documenté (23 actionnables), chaque entrée cite son titre officiel, et elle est évolutive. Approche par les risques, pas prétention d'exhaustivité.

**Q12 — Vos poids de scoring viennent d'où ?**
> La structure (Résiduel = Inhérent × (1−Contrôles)) est la formule réglementaire FATF/Bâle. Les poids exacts sont une calibration documentée — aucun régulateur ne publie de chiffres, c'est laissé à l'institution dans le cadre RBA. Chaque poids est justifié par un critère ; le plafond 80% est un principe de Bâle.

---

*Fin du support — Compliance Agent v6.0.*
