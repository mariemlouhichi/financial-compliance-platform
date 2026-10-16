# PLANNING AGENT — Reason-Act-Reflect

> Support de soutenance — le générateur de questions réglementaires.

---

## 1. Positionnement

Le Planning Agent est le **premier composant actif du Niveau 2**. Sa mission : transformer une transaction + les signaux du Niveau 1 en un **ensemble de requêtes de recherche réglementaire** optimales pour interroger le corpus.

```
Level1Result (decision + risk_signals) + transaction
                          │
                          ▼
                 PLANNING AGENT (Reason-Act-Reflect)
                          │
                          ▼
              3 à 8 questions de recherche priorisées
                          │
                          ▼  → Retrieval Agent
```

### Le problème central
Le corpus est multilingue (traduit de l'arabe/français vers l'anglais réglementaire). Une requête naïve comme `"obligation KYC pour PEP"` ne retourne rien, alors que `"politically exposed persons enhanced vigilance source of funds"` retourne les bons chunks. Le rôle du Planning Agent est de **construire ces requêtes expertes automatiquement**.

---

## 2. L'architecture Reason-Act-Reflect (inspirée de ReAct, Yao et al. 2022)

```
┌─────────────────────────────────────────────────────┐
│  REASON (plan)                                       │
│   Couche 1 — Déterministe (toujours)                 │
│     templates + contexte + signaux + historique      │
│   Couche 2 — LLM (seulement si risk_signals ≠ ∅)     │
│     raisonnement holistique                          │
└──────────────────┬──────────────────────────────────┘
                   │ 3-8 questions
                   ▼
┌─────────────────────────────────────────────────────┐
│  ACT (orchestrateur)                                 │
│     RetrievalPipeline.retrieve(q) × N                │
└──────────────────┬──────────────────────────────────┘
                   │ chunks
                   ▼
┌─────────────────────────────────────────────────────┐
│  REFLECT (gap analysis)                              │
│     le LLM évalue la couverture vs checklist FATF    │
│     → 0-2 nouvelles questions si gaps                │
└──────────────────┬──────────────────────────────────┘
                   │ gaps ?  Oui → RE-ACT (2ᵉ recherche)
                   │         Non → Compliance Agent
```

---

## 3. PHASE REASON — Couche 1 (déterministe)

### Sous-étape 1 — Templates par type de transaction
Des fonctions lambda paramétriques génèrent des requêtes en injectant les valeurs de la transaction. Exemple SWIFT :
```python
lambda t: f"KYC identification requirements international wire transfer "
          f"{t['sender_country']} to {t['receiver_country']} SWIFT"
lambda t: f"STR reporting threshold SWIFT transfer {t['amount']} {t['currency']} "
          f"without economic justification obligations"
```
→ **Priorité 2 (BASE)** : filet de sécurité garantissant qu'au moins 3 dimensions fondamentales sont toujours couvertes.

### Sous-étape 2 — Analyse contextuelle (10 conditions)
| Condition | Priorité | Requête générée |
|-----------|----------|-----------------|
| Motif vague | 3 | déclaration sans justification économique |
| SWIFT sans justificatif | 4 | STR pour virement sans justificatif, délai CTAF |
| Banque intermédiaire | 3 | correspondent banking due diligence |
| Billets neufs (dépôt) | 4 | cash deposit new bills suspicious |
| Opérateur MVTS à risque | 3 | obligations MVTS pour l'opérateur |
| Retrait cash (mandat) | 3 | cash withdrawal identification |
| Passeport cross-border | 3 | non-resident enhanced due diligence |
| Heure atypique (23h-5h) | 3 | out of business hours suspicious |
| Montant [85%-100%[ du seuil | 4 | structuring avoidance just below threshold |
| Montant rond | 2 | round amount money laundering pattern |

### Sous-étape 3 — Questions par signaux Niveau 1
Chaque signal mappe vers une requête prioritaire (priorité corrélée à la sévérité) :
| Signal | Priorité | Thème |
|--------|----------|-------|
| `SANCTION_UN_*` | 5 (CRIT) | asset freeze, blocking obligations |
| `SANCTION_PEP_*` | 5 (CRIT) | PEP enhanced due diligence, senior approval |
| `COUNTRY_BLACK_*` | 5 (CRIT) | FATF blacklist countermeasures |
| `COUNTRY_GREY_*` | 4 (HAUT) | FATF grey list increased monitoring |
| `THRESHOLD_STR` | 4 (HAUT) | STR filing procedure, CTAF deadline |
| `SMURFING_PATTERN` | 4 (HAUT) | structured transactions below threshold |

> Maximum 2 questions issues des signaux (évite la saturation du quota MAX = 8).

### Sous-étape 4 — Questions par historique client
6 conditions (si historique fourni) : STR antérieurs, vélocité 24h, montant vs moyenne 30j, nouveau bénéficiaire, transaction précédente rejetée, nouveau compte + montant élevé.

---

## 4. PHASE REASON — Couche 2 (LLM, conditionnelle)

### Déclenchement conditionnel
Le LLM n'est activé **que si `risk_signals ≠ ∅`**. Pour une transaction normale (S1), la couche déterministe suffit → **aucun appel LLM** → coût et latence minimisés.

### Raisonnement holistique
L'instruction clé : *« Analyse la COMBINAISON des facteurs, pas chacun isolément. Un SWIFT de 9 800 USD vers l'Iran à 3h du matin depuis un compte de 30 jours n'est pas '3 signaux séparés' — c'est un pattern cohérent d'évasion de sanctions avec structuring. »*
→ Le LLM génère 1-2 questions **cross-factor** que les templates ne peuvent pas produire.

### Calibration du vocabulaire (`VOCAB_CALIBRATION`)
Le corpus est de l'anglais réglementaire traduit. On enseigne au LLM les termes qui marchent :
| Terme interdit (0 résultat) | Terme correct |
|------------------------------|---------------|
| "smurfing" | "structured transactions below threshold" |
| "blanchiment" | "money laundering indicators" |
| "PEP obligations" | "politically exposed persons enhanced vigilance" |
| "déclaration de soupçon" | "suspicious transaction report filing" |

Sans cette calibration, le LLM utilise le jargon courant qui ne matche pas le corpus traduit.

---

## 5. Finalisation — Tri + déduplication sémantique
1. **Tri** : candidats triés par priorité décroissante (5 → 1).
2. **Déduplication Jaccard** : un candidat est écarté si son recouvrement Jaccard avec une question déjà retenue ≥ **0.60**.
```
Jaccard(a, b) = |tokens communs| / min(|tokens(a)|, |tokens(b)|)
```
→ Élimine les paraphrases tout en gardant les questions thématiquement proches mais lexicalement différentes. Résultat : **3 à 8 questions**.

---

## 6. PHASE REFLECT — Analyse des lacunes post-retrieval
Après le retrieval, le LLM évalue si les chunks récupérés couvrent les **dimensions FATF obligatoires** du type de transaction (checklist dynamique). Il retourne :
```json
{"covered": ["R10","R20"], "gaps": [{"dimension":"R16","question":"...","priority":4}]}
```
Si `is_sufficient = False` → **2ᵉ vague de retrieval** ciblée sur les dimensions manquantes.

---

## 7. Résultats
- Génère 3 à 8 questions priorisées par transaction.
- LLM appelé **conditionnellement** (uniquement si signaux de risque) → économie.
- Auto-correction : la phase Reflect détecte les gaps et relance une recherche ciblée.

---

## 8. Questions probables du jury

**Q1 — En quoi est-ce « agentique » et pas un simple template ?**
> Trois caractéristiques agentiques : (1) le LLM raisonne sur la combinaison des facteurs, pas champ par champ ; (2) la phase Reflect évalue la qualité du retrieval et déclenche une seconde recherche si des dimensions manquent (auto-correction) ; (3) le nombre de recherches s'adapte au risque, il n'est pas fixe.

**Q2 — Pourquoi le LLM n'est-il pas toujours appelé ?**
> Par design : pour une transaction sans signal de risque, la couche déterministe (templates + contexte) suffit. On réserve le LLM aux cas où des signaux justifient un raisonnement de niveau supérieur. Ça minimise coût et latence.

**Q3 — Pourquoi cette calibration de vocabulaire ?**
> Le corpus est traduit de l'arabe/français vers l'anglais réglementaire. Le jargon courant (« smurfing », « blanchiment ») ne matche pas le texte traduit. On enseigne au LLM les termes formels qui retournent des résultats. Sans ça, le retrieval échoue silencieusement.

**Q4 — Comment évitez-vous les questions redondantes ?**
> Une déduplication sémantique par similarité de Jaccard (seuil 0.60) : deux questions partageant > 60% de leurs mots sont considérées comme redondantes, on n'en garde qu'une. On conserve les questions thématiquement proches mais lexicalement distinctes car elles ramènent des chunks différents.

**Q5 — La phase Reflect ne risque-t-elle pas de boucler indéfiniment ?**
> Non : elle génère au maximum 0-2 nouvelles questions et déclenche une seule 2ᵉ vague (Round 2). Ce n'est pas une boucle infinie mais un raffinement borné.

---

*Fin — Planning Agent v4.*
