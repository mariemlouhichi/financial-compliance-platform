# SCHÉMA DU PIPELINE COMPLET — Architecture 3 niveaux

> Support de soutenance — vue d'ensemble visuelle.

---

## 1. Vue d'ensemble (de la transaction à la décision)

```
╔══════════════════════════════════════════════════════════════════════╗
║                      TRANSACTION FINANCIÈRE                            ║
║       (type, montant, devise, émetteur, bénéficiaire, pays…)          ║
╚═══════════════════════════════════╤══════════════════════════════════╝
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│  NIVEAU 1 — Règles fixes déterministes  (0% LLM, < 100 ms)            │
│  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌────────────────┐          │
│  │Sanctions │ │Risque Pays│ │ Seuils   │ │ Multi-facteurs │          │
│  │ ONU+PEP  │ │   FATF    │ │  BCT     │ │   suspicion    │          │
│  └──────────┘ └───────────┘ └──────────┘ └────────────────┘          │
│  → décision : BLOCK | STR | EDD | KYC | RECORD | PASS                 │
│  → risk_signals : [SANCTION_*, COUNTRY_*, THRESHOLD_STR, …]           │
└───────────────────────────────────┬──────────────────────────────────┘
                                    │ risk_signals + decision
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│  NIVEAU 2 — Agentic RAG  (LLM conditionnel)                          │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  PLANNING AGENT  (Reason-Act-Reflect)                         │    │
│  │  → 3-8 questions réglementaires priorisées                    │    │
│  └────────────────────────────┬─────────────────────────────────┘    │
│                               │ questions                             │
│                               ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  RETRIEVAL AGENT  (pipeline hybride 5 étapes)                 │    │
│  │  Cache → Pre-filter → BM25+Vector → RRF → Expansion → Rerank  │    │
│  │  → 5 chunks réglementaires les plus pertinents                │    │
│  └────────────────────────────┬─────────────────────────────────┘    │
│                               │ chunks                                │
│                               ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  COMPLIANCE AGENT  (neuro-symbolique dual-path, 6 phases)     │    │
│  │  Symbolique ∥ Sémantique → Réconciliation → Scoring résiduel  │    │
│  └────────────────────────────┬─────────────────────────────────┘    │
└───────────────────────────────┼──────────────────────────────────────┘
                                │ ComplianceDecision
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  NIVEAU 3 — Génération SAR                                            │
│  Rapport de soupçon avec provenance complète (Rule → Chunk → Doc)     │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Zoom sur le Compliance Agent (les 6 phases)

```
  Transaction + risk_signals + chunks + checklist FATF
      │
      ▼  PHASE 1 — SYMBOLIQUE
      │   Règles Neo4j (GT) + signaux HARD (violation) / SOFT (risque ≤30%)
      │
      ▼  PHASE 2 — GAP ANALYSIS
      │   gaps = checklist − couvert_symbolique
      │
      ▼  PHASE 3 — SÉMANTIQUE (LLM, gaps seulement)
      │   Gate → Triage → Pass1 → Validation snippet → Gate B4 → Pass2 → Persistance
      │
      ▼  PHASE 4 — RÉCONCILIATION
      │   accord → cross-validation ; contradiction → revue humaine (symbolique prévaut)
      │
      ▼  PHASE 5 — SCORING RÉSIDUEL
      │   Résiduel = Inhérent × (1 − Contrôles)   [FATF/Bâle]
      │
      ▼  PHASE 6 — DÉCISION + AUDIT
      │
  ComplianceDecision { décision, score, violations (BCT-first),
                       couvertures, flags revue, audit_trail }
```

---

## 3. Le principe « 3 zones » (mode production dual)

```
┌────────────────┬──────────────────────────┬─────────────────────────┐
│   ZONE         │   Recommandations         │   Chemin                │
├────────────────┼──────────────────────────┼─────────────────────────┤
│ FAITS DURS     │ R6 (sanctions)            │ SYMBOLIQUE SEUL         │
│                │ R7 (prolifération)        │ (lookup déterministe,   │
│                │ R19 (pays noir), seuils   │  le LLM n'apporte rien) │
├────────────────┼──────────────────────────┼─────────────────────────┤
│ CONTEXTUEL     │ R10, R12, R13, R14,       │ SYMBOLIQUE + SÉMANTIQUE │
│                │ R16, R20, R24…            │ en parallèle →          │
│                │                           │ réconciliation          │
├────────────────┼──────────────────────────┼─────────────────────────┤
│ DÉCISION       │ —                         │ SYMBOLIQUE PRÉVAUT      │
│ FINALE         │                           │ (le déterministe gagne) │
└────────────────┴──────────────────────────┴─────────────────────────┘
```

---

## 4. Les 3 couvertures (argument méthodologique)

```
Symbolique (générique)   100%   ████████████████████  « le corpus connaît l'obligation »
BCT contraignante         98%   ███████████████████░  « source tunisienne contraignante »
Adossée à une preuve   ~78-82%  ████████████████░░░░  « preuve vérifiée pour CETTE tx »

→ La couverture générique SURESTIME.
→ La couverture adossée à une preuve est la mesure HONNÊTE.
```

---

## 5. Infrastructure technique

```
┌──────────────┬──────────────────────────┬──────────────────────────┐
│ Composant    │ Technologie              │ Rôle                     │
├──────────────┼──────────────────────────┼──────────────────────────┤
│ Vectoriel    │ Weaviate (port 8088)     │ BM25 + recherche cosinus │
│ Graphe       │ Neo4j (7687)             │ règles, NEXT_IN_DOC      │
│ Embedding    │ BAAI/bge-base-en-v1.5    │ 768d                     │
│ Reranker     │ BAAI/bge-reranker-base   │ cross-encoder            │
│ LLM          │ Groq (rotation 5 modèles)│ Planning + Compliance    │
│ Sanctions    │ rapidfuzz + JSON         │ ONU/PEP O(1) + fuzzy     │
└──────────────┴──────────────────────────┴──────────────────────────┘
```

---

## 6. Résultats globaux (100 scénarios)

```
┌─────────────────────────────┬──────────┬─────────┬────────┐
│ Métrique                    │ Résultat │ Cible   │ Statut │
├─────────────────────────────┼──────────┼─────────┼────────┤
│ Accuracy décisionnelle      │ 100/100  │ ≥ 85%   │   ✅   │
│ Couverture BCT contraignante│ 84.1%    │   —     │   ⚖️   │
│ Couverture FATF (référence) │ 94.7%    │ ≥ 90%   │   ✅   │
│ Taux d'hallucination        │ 0.00%    │ < 2%    │   ✅   │
│ Stabilité (accord inter-runs│ 100%     │ 100%    │   ✅   │
└─────────────────────────────┴──────────┴─────────┴────────┘
```

---

*Fin — Schéma du pipeline.*
