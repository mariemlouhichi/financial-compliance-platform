# RETRIEVAL AGENT — Pipeline hybride 5 étapes

> Support de soutenance — le moteur de récupération documentaire.

---

## 1. Positionnement

Le Retrieval Agent récupère, pour chaque question du Planning Agent, les **5 chunks réglementaires les plus pertinents** du corpus AML/KYC, en garantissant **précision** (chunks informatifs) ET **rappel** (couverture réglementaire).

```
Question réglementaire (du Planning Agent)
                │
                ▼
        RETRIEVAL AGENT (5 étapes)
                │
                ▼
   5 chunks pertinents → Compliance Agent
```

### Pourquoi un pipeline HYBRIDE ?
Aucune méthode isolée n'est optimale sur ce corpus :
| Méthode | Force | Faiblesse |
|---------|-------|-----------|
| **BM25 seul** | termes exacts (« Art. 43 », « 10 000 USD ») | aveugle à la synonymie (STR ≡ suspicious transaction report) |
| **Vectoriel seul** | similarité sémantique | peut ramener du thématiquement proche mais réglementairement faux |
| **Graphe seul** | précis sur les règles validées | ne couvre que les 49 ground truth |

→ **Solution** : un pipeline qui exploite les complémentarités.

---

## 2. Le pipeline en 5 étapes (+ Step 0 cache)

```
 Requête
   │
   ▼
 STEP 0  Cache Neo4j        ≥ 3 règles validées → SORTIE DIRECTE (bypass)
   │ cache miss
   ▼
 STEP 1  Graph pre-filter   Neo4j → docs pertinents par juridiction
   │
   ▼
 STEP 2  Hybrid Search      BM25 top-20  +  Vector top-20  (parallèle)
   │
   ▼
 STEP 3  RRF Fusion         score = Σ 1/(60 + rank) → top-15
   │
   ▼
 STEP 4  Parent Expansion   Neo4j NEXT_IN_DOC ±1 → articles voisins
   │
   ▼
 STEP 5  Reranking          BGE-reranker cross-encoder → top-5
```

---

## 3. STEP 0 — Cache Neo4j (court-circuit)

Pour les transactions récurrentes, les règles applicables sont identiques. On interroge directement les **règles ground truth validées** de Neo4j. Si **≥ 3 règles** pertinentes correspondent → le pipeline Weaviate est **entièrement court-circuité**.
→ Latence : de ~3 s (pipeline complet) à **< 200 ms** (cache hit).

---

## 4. STEP 1 — Graph pre-filter

Neo4j renvoie la liste des **documents pertinents** pour la juridiction de la transaction. Cette liste devient un **filtre** dans les requêtes Weaviate, réduisant l'espace de recherche de 6 678 chunks à quelques centaines.

---

## 5. STEP 2 — Recherche hybride (BM25 + vectorielle)

### BM25 (lexical)
Modèle probabiliste basé sur la fréquence des termes pondérée par leur rareté (TF-IDF étendu). Excellent pour les **termes réglementaires exacts** (numéros d'articles, montants, acronymes).

### Vectorielle (sémantique)
- Modèle d'embedding : **BAAI/bge-base-en-v1.5** (768 dimensions, normalisé L2).
- Similarité **cosinus** entre le vecteur de la requête et les vecteurs des chunks.
- **Cache LRU** (128 entrées) : une requête déjà encodée n'est pas recalculée.

### Complémentarité
| Requête | BM25 | Vectoriel |
|---------|------|-----------|
| "Art. 43 BCT 2017" | ✅ Excellent | ⚠️ Moyen |
| "obligation of reporting suspicious" | ⚠️ Moyen | ✅ Excellent |
| "STR threshold 10000 USD" | ✅ | ✅ |

---

## 6. STEP 3 — Reciprocal Rank Fusion (RRF)

### La formule (Cormack et al., 2009)
```
score(d) = Σ  1 / (k + rang_i(d))     pour chaque système i ∈ {BM25, vectoriel}
           i
avec k = 60 (standard empirique)
```

### Pourquoi RRF plutôt qu'une moyenne pondérée ?
- **Pas de calibration des poids** : les scores BM25 (non bornés) et cosinus ([0,1]) sont sur des échelles incomparables. RRF n'utilise que le **rang**, pas le score brut.
- **Robustesse** : un document en tête des deux listes accumule la contribution maximale.

### Exemple
| Chunk | Rang BM25 | Rang Vectoriel | RRF Score |
|-------|-----------|---------------|-----------|
| A | 1 | 3 | 1/61 + 1/63 = **0.0322** |
| B | 5 | 1 | 1/65 + 1/61 = **0.0318** |

Le chunk A, en tête des deux, obtient le meilleur score combiné. → top-15 retenus.

---

## 7. STEP 4 — Parent Expansion (enrichissement contextuel)

Les articles de loi sont conditionnels (« sous réserve de l'article précédent… »). On récupère les articles **immédiatement voisins** (`±1 NEXT_IN_DOC`) dans le graphe Neo4j → chaque chunk est enrichi d'un `expanded_context` (max 4 voisins).
**Optimisation** : 1 requête Cypher batch (UNWIND) au lieu de N requêtes (anti-pattern N+1 corrigé).

---

## 8. STEP 5 — Reranking cross-encoder

### Bi-encodeur vs cross-encodeur
- **Bi-encodeur (Step 2)** : encode requête et document **séparément**, puis mesure la similarité. Rapide, mais interactions limitées.
- **Cross-encodeur (Step 5)** : reçoit la **paire (requête, document) concaténée**, capture les interactions fines par attention croisée. Beaucoup plus précis, mais O(N).

### Architecture en deux temps
Le bi-encodeur filtre rapidement les 6 678 chunks ; le cross-encodeur (**BAAI/bge-reranker-base**) réordonne précisément les **15 candidats** présélectionnés → **top-5 finaux**. La paire d'entrée inclut le contexte voisin (Parent Expansion) pour les articles conditionnels.

---

## 9. Le résultat
```python
@dataclass
class RetrievalResult:
    text, source_doc, article_ref, jurisdiction, chunk_role,
    has_threshold, score, rank,
    retrieval_path,      # "rrf+reranked" | "bm25+reranked" | "cache"
    expanded_context     # articles voisins
```

---

## 10. Résultats (validés sur le scénario S2 — SWIFT vers Iran)
- Couverture FATF : **80%** (8/10 recommandations) sur le cas le plus critique.
- Latence : **< 3 s** sans cache, **< 200 ms** avec cache hit.
- Résilience : retry avec backoff exponentiel (3 tentatives) sur Weaviate/Neo4j.

---

## 11. Questions probables du jury

**Q1 — Pourquoi combiner BM25 et vectoriel ?**
> Parce qu'ils sont complémentaires : BM25 excelle sur les termes réglementaires exacts (numéros d'articles, montants) mais est aveugle à la synonymie ; le vectoriel capture le sens mais peut ramener du thématiquement proche mais faux. La fusion exploite les deux.

**Q2 — Pourquoi RRF et pas une moyenne pondérée des scores ?**
> Parce que les scores BM25 (non bornés) et cosinus ([0,1]) sont sur des échelles incomparables — les moyenner exigerait une calibration fragile. RRF n'utilise que le rang relatif, ce qui est robuste et sans paramètre à régler (k=60 est le standard).

**Q3 — C'est quoi un cross-encoder, et pourquoi seulement à la fin ?**
> Un cross-encoder lit la paire (requête, document) ensemble et capture les interactions fines, donc très précis mais coûteux (une passe par document). On ne peut pas l'appliquer aux 6 678 chunks. On l'applique donc uniquement aux 15 candidats présélectionnés par le bi-encodeur, plus rapide.

**Q4 — À quoi sert le Parent Expansion ?**
> Les articles de loi sont conditionnels : un article peut dépendre du précédent (« sous réserve de l'article 42 »). Un chunk isolé peut être mal interprété. On récupère donc les articles voisins (±1) dans le graphe Neo4j pour donner le contexte complet au reranker et au Compliance Agent.

**Q5 — Le retrieval ne se base-t-il que sur la FATF ?**
> Non. L'index est à 48% BCT, et le retrieval renvoie 55% de sources tunisiennes. Pour un virement local, il ramène des articles BCT et de la loi 2016-48, pas de la FATF. Seuls les cas sans équivalent tunisien (ex. MVTS) ramènent de la guidance FATF, ce qui est correct.

---

*Fin — Retrieval Agent v2.*
