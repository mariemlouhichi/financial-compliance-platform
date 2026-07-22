# Documentation Technique — Pipeline de Chunking AML/KYC v2

## Vue d'ensemble

Ce pipeline découpe intelligemment 26 documents réglementaires AML/KYC (traduits en anglais) en chunks exploitables par un système RAG. Il combine deux approches : le découpage par patterns structurels (pour les lois et circulaires) et le chunking sémantique (pour les guides et rapports narratifs).

**Entrée** : `translated_content_v2_concatenated.json` (2 juridictions, 26 documents)
**Sortie** : `chunks_aml_kyc.json` — chaque chunk contient le texte + 7 métadonnées

**Pas besoin d'API key HuggingFace.** Le modèle `BAAI/bge-base-en-v1.5` est open-source. `sentence-transformers` le télécharge automatiquement (~440 Mo) lors du premier lancement, puis le cache localement dans `~/.cache/huggingface/`.

---

## 1. `config/settings.py` — Configuration centralisée

Tous les paramètres du pipeline sont centralisés ici. Rien n'est hardcodé dans les autres fichiers.

### Paramètres clés

| Paramètre | Valeur | Rôle |
|-----------|--------|------|
| `STRUCTURE_THRESHOLD` | 3.0 | Score au-dessus duquel un document est classé "structuré" (Branche A) |
| `MAX_CHUNK_CHARS` | 1500 | Taille max d'un chunk avant sub-chunking sémantique |
| `SEMANTIC_SIMILARITY_THRESHOLD` | 0.45 | Seuil de similarité cosinus pour la rupture de sens (Branche B) |
| `MIN_CHUNK_CHARS` | 200 | Taille min d'un chunk (en dessous, fusion avec le voisin) |
| `EMBEDDING_MODEL` | `BAAI/bge-base-en-v1.5` | Modèle d'embeddings (768 dimensions, top MTEB retrieval) |
| `DEFAULT_ROLE` | `INFORMATION` | Rôle par défaut quand aucun mot-clé ne matche |

### `ANCHOR_PATTERNS`

Liste de 6 regex qui détectent les ancres structurelles. Chaque tuple contient un nom et une regex avec un groupe de capture `(\d+)` pour extraire le numéro. Le Profiler teste **tous** les patterns et garde celui avec le meilleur score. Patterns couverts : `Article X`, `Recommendation X`, `Section X`, `Chapter X`, `Paragraph X`, et le pattern générique `1. Titre...` (documents FATF).

### `ROLE_KEYWORDS` — 8 rôles, English-only

Tous les documents sont traduits en anglais → les mots-clés sont exclusivement en anglais. Le système couvre 8 rôles au lieu de 5 dans la v1 :

| Rôle | Ce qu'il détecte | Exemples de mots-clés |
|------|------------------|----------------------|
| `SANCTION` | Pénalités, conséquences, infractions | penalty, fine, violation, enforcement, revocation |
| `OBLIGATION` | Devoirs, exigences obligatoires | shall, must, required, mandatory, compliance |
| `PROCEDURE` | Processus, étapes, vérifications | procedure, step, KYC, CDD, EDD, due diligence |
| `THRESHOLD` | Montants, seuils, délais | threshold, limit, amount, exceed, deadline, within X days |
| `DEFINITION` | Définitions juridiques, concepts | defined as, means, refers to, shall mean |
| `RED_FLAG` | Indicateurs de risque, activités suspectes | suspicious, unusual, red flag, money laundering, shell company |
| `REPORTING` | Déclarations, notifications aux autorités | SAR, report, FIU, disclosure, notify |
| `EXEMPTION` | Exceptions, dérogations, cas particuliers | exempt, unless, not applicable, derogation, notwithstanding |
| `INFORMATION` | Rôle par défaut (aucun mot-clé matché) | — |

### `THRESHOLD_PATTERNS` — Détection de seuils financiers élargie

4 patterns regex couvrant :
- **Montant + devise** (nombre PUIS devise) : `50 000 TND`, `10 000 EUR`, `500 dollars`
- **Symbole + montant** (devise PUIS nombre) : `$10,000`, `€5000`, `£200`
- **Pourcentages** : `10%`, `5.5 %`, `50%`
- **Ordres de grandeur** : `1 million`, `500 thousand`, `2 billion`

Devises couvertes : TND, TDN, DT, EUR, USD, GBP, MAD, DZD, EGP, SAR, AED, QAR, KWD, BHD, OMR, CHF, JPY, CNY/RMB, et les formes textuelles (dinars, dollars, euros, pounds, dirhams, riyals, yuan, yen).

---

## 2. `core/pattern_scanner.py` — Le Profiler (Étape 1)

Scanne le document et calcule un **Score de Structure** pour décider quelle branche utiliser.

### La formule

```
SCORE = Fréquence × Incrémentation × (0.5 + Régularité × 0.5)
```

**Les 3 critères :**

- **Fréquence (F)** : Nombre total d'occurrences du pattern trouvé dans le texte.
- **Incrémentation (I)** : Les numéros se suivent-ils ? [1,2,3,4] → I=1.0 (parfait). [1,3,7,12] → I=0.0 (pas de séquence). Calculé comme la proportion d'incréments de +1 entre les numéros triés et dédupliqués.
- **Régularité (R)** : Les ancres sont-elles espacées uniformément dans le texte ? Mesuré via le Coefficient de Variation (CV = écart-type / moyenne) des écarts en caractères. R = 1/(1+CV). Un CV faible = espacement régulier = R proche de 1.

**Pourquoi multiplier les trois ?** Un seul critère ne suffit pas. Un document pourrait citer "Article" 100 fois (F élevé) sans aucun ordre (I=0) — c'est du texte qui référence des articles, pas un document structuré par articles. La multiplication garantit que les trois conditions sont remplies simultanément.

**Exemple concret** avec `bct_circulaire_2017_08_aml.pdf` : F=77, I=0.919, R=0.522 → Score = 77 × 0.919 × (0.5 + 0.522 × 0.5) = **53.86** → bien au-dessus du seuil 3.0 → Branche A.

---

## 3. `core/branch_a_structure.py` — Branche A : Pattern Chunking

Pour les documents **structurés** : circulaires BCT, lois, recommandations GAFI.

### Étape 1+2 : `split_by_pattern`

Découpe le texte selon les ancres détectées. Chaque match d'ancre démarre un nouveau chunk. Le texte avant la première ancre est conservé comme "Préambule" s'il dépasse 100 caractères (en dessous, c'est probablement un en-tête sans valeur). La référence (ex: "Article 15") est extraite automatiquement.

### Étape 3 : `sub_chunk_semantic`

Si un chunk dépasse `MAX_CHUNK_CHARS` (1500), on ne coupe pas arbitrairement — on risquerait de casser une règle au milieu. Le mécanisme :
1. Découper le chunk en phrases
2. Encoder chaque phrase en vecteur (via `BAAI/bge-base-en-v1.5`)
3. Calculer la similarité cosinus entre phrases consécutives
4. Trouver les points de rupture avec un seuil **adaptatif** : `moyenne - 1 écart-type` (seules les ruptures les plus marquées)
5. Les sous-chunks sont suffixés : `Article 15-a`, `Article 15-b`, `Article 15-c`

### `split_into_sentences`

Regex `(?<=[.!?;])\s+(?=[A-ZÀ-Ü0-9])` — coupe après ponctuation suivie d'un espace + majuscule. Évite les faux positifs sur `n°2017-08` ou `art. 15`.

---

## 4. `core/branch_b_semantic.py` — Branche B : Semantic Chunking

Pour les documents **narratifs** sans structure claire : guides Wolfsberg, rapports CTAF, mémos.

### `chunk_branch_b` — 4 étapes

1. **Découpage en phrases** : Même regex que la Branche A.
2. **Encodage vectoriel** : Chaque phrase est transformée en vecteur 768 dimensions via `BAAI/bge-base-en-v1.5`. Le modèle est entraîné avec du contrastive learning, optimisé pour le retrieval — il capte bien la proximité sémantique entre passages juridiques.
3. **Détection de rupture de sens** : Pour chaque paire de phrases consécutives, on calcule la similarité cosinus. Quand elle descend sous 0.45, c'est un changement de sujet → on coupe.
4. **Fusion des micro-chunks** : `_merge_small_chunks` colle les chunks de moins de 200 caractères à leur voisin. Un fragment isolé trop court n'a pas assez de contexte pour le retriever.

### `_cosine_similarity`

Implémentée à la main (produit scalaire / produit des normes) sans dépendance numpy. Cela rend le code léger et portable.

---

## 5. `core/role_tagger.py` — Enrichissement & Classification

Transforme un chunk brut en chunk exploitable par le RAG, avec 7 métadonnées.

### `classify_role` — Scoring par mots-clés (8 rôles)

Pour chaque rôle, on compte combien de ses mots-clés apparaissent dans le texte du chunk. Le rôle avec le score le plus élevé gagne. Si aucun mot-clé ne matche → `INFORMATION` (rôle par défaut).

Exemple : le texte *"Banks shall ensure compliance with these requirements"* matche `shall` (1 hit OBLIGATION), `ensure` (1 hit OBLIGATION), `compliance` (1 hit OBLIGATION), `requirements` (1 hit OBLIGATION) → 4 hits OBLIGATION → classé `OBLIGATION`.

### `detect_threshold`

4 patterns regex cherchent des montants avec devise, des symboles monétaires, des pourcentages, et des ordres de grandeur. Quand `has_threshold=true`, le RAG sait que ce chunk contient des seuils concrets — utile pour les requêtes comme *"What is the minimum reporting amount?"*.

### `EnrichedChunk` — Format de sortie

```json
{
  "text": "Banks shall ensure compliance...",
  "source_doc": "bct_circulaire_2017_08_aml.pdf",
  "article_ref": "Article 15",
  "jurisdiction": "Tunisia",
  "chunk_role": "OBLIGATION",
  "has_threshold": false,
  "chunk_index": 14,
  "char_count": 892
}
```

---

## 6. `pipeline.py` — L'orchestrateur

### `load_embedding_model` — Chaîne de fallback

1. **`sentence-transformers`** avec `BAAI/bge-base-en-v1.5` (768 dims, meilleure qualité). Les embeddings sont normalisés (`normalize_embeddings=True`, requis par BGE pour le cosine similarity). **Pas besoin d'API key** — le modèle est téléchargé automatiquement la première fois.
2. **TF-IDF fallback** via sklearn (fonctionne offline, 768 features pour matcher les dimensions BGE, qualité moindre mais suffisante pour le chunking).
3. **Arrêt avec message clair** si aucun modèle n'est disponible.

### `process_document`

Pour chaque document : Profiler → routage vers Branche A ou B → enrichissement de chaque chunk. Affiche les stats en temps réel (score, nombre de chunks, distribution des 8 rôles, nombre de seuils détectés).

### `save_results`

Deux sorties : `chunks_aml_kyc.json` (fichier global) et un fichier par document dans `output/par_document/` (utile pour débugger ou inspecter un document spécifique).

---

## 7. `tests/test_pipeline.py` — 27 tests unitaires

Couvre chaque composant. Peut tourner avec `pytest` ou directement avec `python tests/test_pipeline.py` (runner intégré sans dépendance).

Tests couverts : incrémentalité (séquence parfaite, trous, vide), régularité (uniforme, chaotique), classification structuré/narratif, découpage par patterns, cosine similarity, fusion des micro-chunks, classification des 8 rôles (un test par rôle + INFORMATION), détection de seuils (dinars, USD, EUR, pourcentages, absence), mapping des juridictions.

---

## 8. `Dockerfile` + `docker-compose.yml`

Le Dockerfile part de `python:3.11-slim`, installe les dépendances, et copie le code. Le `docker-compose.yml` monte deux volumes (`data/` et `output/`) pour la persistance et expose toutes les variables d'environnement modifiables sans toucher au code :

| Variable | Défaut | Rôle |
|----------|--------|------|
| `STRUCTURE_THRESHOLD` | 3.0 | Seuil Profiler |
| `MAX_CHUNK_CHARS` | 1500 | Taille max chunk |
| `SIMILARITY_THRESHOLD` | 0.45 | Seuil rupture sémantique |
| `EMBEDDING_MODEL` | BAAI/bge-base-en-v1.5 | Modèle d'embeddings |

---

## Lancement

```bash
# 1. Copier le JSON dans data/
cp translated_content_v2_concatenated.json data/

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Lancer les tests (optionnel)
python tests/test_pipeline.py

# 4. Lancer le pipeline
python pipeline.py

# OU avec Docker
docker compose up --build
```
