# 13 — La couche de connaissance (corpus, chunking, embeddings, graphe)

> Comment 34 PDF réglementaires deviennent une base interrogeable par un agent.

---

## 1. Objectif

Un agent RAG ne vaut que ce que vaut sa base documentaire. Cette couche est
l'infrastructure de connaissance du système : elle transforme des textes
réglementaires bruts (circulaires BCT, loi tunisienne, recommandations FATF,
lignes directrices EBA) en deux représentations complémentaires :

- une **base vectorielle** pour la recherche sémantique (« trouve-moi ce qui
  ressemble à cette question ») ;
- un **graphe de connaissances** pour la recherche structurée et le raisonnement
  symbolique (« quelles règles s'appliquent à ce type d'opération, dans cette
  juridiction ? »).

Les deux ne sont pas redondantes : elles répondent à des questions différentes.

---

## 2. Le corpus

**34 documents PDF**, répartis en deux ensembles :

| Ensemble | Volume | Contenu |
|---|---|---|
| **Tunisie** | 17 documents | Circulaires BCT, loi 2015-26 AML/CFT, réglementation bancaire, réglementation des changes, rapports CTAF |
| **International** | 17 documents | Recommandations FATF, lignes directrices EBA, documents Bâle, guidances sectorielles |

Cette séparation n'est pas cosmétique : elle porte la distinction
**contraignant / de référence** qui structure tout le système. Le droit tunisien
oblige une banque en Tunisie ; la FATF fournit une taxonomie internationale de
référence. Le système ne les mélange jamais dans ses taux de couverture.

---

## 3. Le chunking

### Le problème

Un PDF réglementaire de 200 pages ne peut pas être envoyé tel quel à un moteur de
recherche sémantique. Il faut le découper. Mais un découpage naïf (tous les 500
caractères) coupe au milieu des articles et détruit exactement ce qui fait la
valeur d'un texte juridique : sa **structure articulaire**.

### L'approche retenue

Le découpage respecte la structure du document — articles, alinéas, numérotations
— plutôt qu'une longueur fixe. Chaque chunk conserve dans ses métadonnées son
ancrage :

```json
{
  "chunk_id": "...",
  "content":  "texte de l'unité réglementaire",
  "metadata": {
    "source_doc": "bct_circulaire_2017_08_aml.pdf",
    "article_ref": "Article 14",
    "jurisdiction": "Tunisia",
    "chunk_role": "OBLIGATION"
  }
}
```

**Résultat : 6 678 chunks** indexés.

Le champ `chunk_role` est important : il classe la nature de l'unité
(`OBLIGATION`, `KYC`, `THRESHOLD`, `SANCTIONS`, `REPORTING`, `RISK_ASSESSMENT`,
`RED_FLAG`). C'est ce qui permettra ensuite de filtrer « donne-moi les
obligations de seuil » sans relire tout le corpus.

---

## 4. Les embeddings

**Modèle : `BAAI/bge-base-en-v1.5` — 768 dimensions.**

Pourquoi celui-là :

- il est **open source et exécutable localement**, sans appel à une API payante
  — un critère important pour un projet reproductible et pour la confidentialité
  des données bancaires ;
- il est spécifiquement entraîné pour la **recherche** (retrieval), pas seulement
  pour la similarité générale ;
- ses performances sur les *benchmarks* de retrieval sont solides pour une taille
  de modèle raisonnable (`base`, pas `large`), ce qui le rend utilisable sur une
  machine de développement.

Les vecteurs sont stockés dans **Weaviate** (conteneur Docker, port 8088), qui
assure la recherche par similarité et le mode hybride (vectoriel + BM25 lexical).

---

## 5. Le graphe de connaissances (Neo4j)

C'est la partie la plus structurante, et celle qui distingue ce projet d'un RAG
classique.

### Le schéma

**Nœuds :**

| Label | Rôle |
|---|---|
| `Document` | Un texte réglementaire source |
| `LegalUnit` | Une unité juridique (article, alinéa) |
| `Chunk` | Un fragment indexé, rattaché à son unité |
| `Rule` | Une règle exploitable par le moteur de décision |
| `Jurisdiction` | Tunisie, international… |
| `RoleCategory` | Nature de l'obligation |
| `TransactionType` | SWIFT, virement local, dépôt espèces… |

**Relations :**

| Relation | Sens |
|---|---|
| `FROM_DOCUMENT` | Un chunk/unité provient d'un document |
| `REALIZES` | Un chunk réalise une unité juridique |
| `SUPPORTED_BY` | Une règle est appuyée par un chunk (**la preuve**) |
| `DERIVED_FROM` | Une règle dérive d'une unité juridique |
| `NEXT_IN_DOC` | Ordre de lecture dans le document |
| `UNDER_JURISDICTION` | Rattachement juridictionnel |
| `APPLIES_TO` | La règle s'applique à un type d'opération |
| `HAS_ROLE` | Catégorisation de l'unité |

### La chaîne d'ancrage — le point clé anti-hallucination

```
Rule ──SUPPORTED_BY──▶ Chunk ──REALIZES──▶ LegalUnit ──FROM_DOCUMENT──▶ Document
```

Cette chaîne est ce qui rend chaque règle **traçable jusqu'à son texte source**.
Quand le système affirme « obligation violée : seuil STR dépassé », on peut
remonter mécaniquement jusqu'au paragraphe exact de la circulaire BCT qui le dit,
et afficher la **citation verbatim**.

C'est cette traçabilité, et non un post-traitement, qui explique le taux
d'hallucination de 0 % : une règle sans chaîne d'ancrage valide n'est pas
retenue.

### Les statuts de règle

Le graphe ne contient pas que les règles validées manuellement. Il apprend.

| Statut | Origine | Condition |
|---|---|---|
| `GROUND_TRUTH` | Validée humainement (`origin = human_verified`) | Les **49 règles** de la vérité terrain |
| `VALIDATED` | Extraite par l'agent, validée automatiquement | Confiance ≥ 0,65 + citation ancrée |
| `LEARNED` | Validée puis **réutilisée avec succès ≥ 3 fois** | Confiance ≥ 0,80 |

Ordre de priorité à l'usage : `GROUND_TRUTH` > `LEARNED` > `VALIDATED`.

Ce mécanisme est le cœur de l'apprentissage du système : **le graphe s'enrichit à
chaque transaction analysée**, et répond de plus en plus souvent sans avoir
besoin d'interroger le corpus vectoriel (voir document 21).

---

## 6. Pourquoi deux bases plutôt qu'une

C'est une question de jury probable. La réponse tient en une phrase :

> **Le vecteur trouve ce qui ressemble ; le graphe sait ce qui s'applique.**

- La recherche vectorielle est excellente pour retrouver un passage pertinent à
  partir d'une question formulée librement — mais elle ne sait pas dire si une
  règle est contraignante, à quelle juridiction elle appartient, ou si elle a
  déjà été validée.
- Le graphe apporte la structure, la traçabilité et le raisonnement symbolique —
  mais il ne sait pas répondre à une question qu'on ne lui a pas encore apprise.

Le système utilise donc **le graphe d'abord** (rapide, structuré, traçable) et ne
descend vers le corpus vectoriel que si le graphe ne couvre pas la question.

---

## 7. Le pipeline d'ingestion

```
01_regulations/*.pdf  (34 documents)
        │
        ▼  extraction + traduction si nécessaire
   texte structuré
        │
        ▼  chunking respectant la structure articulaire
   6 678 chunks  (chunk_id, content, metadata)
        │
        ├────────▶ embeddings BGE 768d ────▶ Weaviate  (recherche sémantique)
        │
        └────────▶ construction du graphe ─▶ Neo4j    (structure + traçabilité)
```

Scripts correspondants :

| Script | Rôle |
|---|---|
| `build_clean_chunks.py` / `cleanup_chunks_v2.py` | Découpage et nettoyage |
| `embed_to_weaviate.py` | Calcul des embeddings + indexation |
| `build_neo4j_graph.py` | Construction du graphe |
| `load_ground_truth.py` | Injection des 49 règles validées |
| `integrate_new_documents.py` | Ajout d'un nouveau texte au corpus |
| `validate_neo4j_graph.py` | Contrôles de cohérence du graphe |

---

## 8. Consultation dans la plateforme

La couche de connaissance n'est pas une boîte noire : elle est explorable depuis
l'interface.

- **Corpus réglementaire** — bibliothèque des 34 PDF (prévisualisation intégrée),
  structure articulaire par document, recherche hybride.
- **Règles extraites** — les 49 règles de vérité terrain et les règles apprises,
  avec pour chacune sa citation verbatim et ses preuves.
- **Graphe de connaissances** — statistiques réelles (nœuds par label, relations
  par type), visualisation d'un sous-graphe, requêtes Cypher prédéfinies.

La visualisation du graphe est un composant SVG maison (rendu *force-directed*
avec disposition déterministe), parce que le navigateur Neo4j refuse d'être
embarqué dans une iframe.

---

## 9. Limites assumées

- **La couverture-preuve réelle est d'environ 28 %** en production : le reste des
  obligations est couvert symboliquement, sans citation verbatim rattachée. C'est
  perfectible avec un corpus plus riche, et c'est annoncé plutôt que masqué.
- **Pas de veille réglementaire automatique** : l'ajout d'un nouveau texte BCT ou
  FATF est manuel (`integrate_new_documents.py`). Un watcher automatique
  (détection → diff → ré-indexation) reste une perspective.
- **L'extraction PDF n'est pas parfaite** : certains tableaux complexes restent
  difficiles à structurer proprement.

---

## 10. Chiffres à retenir

```
Documents PDF        :     34   (17 Tunisie + 17 international)
Chunks indexés       :  6 678
Dimension embedding  :    768   (BAAI/bge-base-en-v1.5)
Règles vérité terrain:     49   (origin = human_verified)
Couverture FATF      :  94,7 %
Couverture BCT       :  84,1 %
Taux d'hallucination :   0,00 %
```
