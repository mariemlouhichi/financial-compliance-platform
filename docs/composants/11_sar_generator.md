# 11 — SAR Generator (étape 7 du pipeline)

> Le livrable réglementaire : transformer une analyse technique en un rapport
> qu'un responsable conformité peut lire, signer et transmettre à la CTAF.

---

## 1. Objectif

Toutes les étapes précédentes produisent des objets techniques : des signaux, des
règles violées, un score, une probabilité. Personne au service conformité ne lit
cela. Le SAR Generator (*Suspicious Activity Report*) a un rôle de **traduction**
et de **synthèse** : rassembler l'ensemble des éléments dans un document unique,
hiérarchisé, où l'essentiel se lit en quelques secondes et où le détail reste
accessible.

Il produit deux formats en parallèle :

- un **Markdown** lisible et imprimable (converti en PDF sectionné dans la
  plateforme) ;
- un **JSON** complet, qui conserve tout le détail pour l'audit et
  l'exploitation machine.

---

## 2. Structure du rapport

Le rendu Markdown a été refondu pour la lisibilité — un responsable conformité
enchaîne plusieurs rapports d'affilée, l'essentiel doit tenir sur un écran.

| Section | Contenu |
|---|---|
| **En-tête** | Identifiant SAR, décision, niveau et score de risque, résumé de l'opération sur une ligne (type, montant, parties, pays, motif, justificatif oui/non) |
| **Recommandation** | La phrase d'action, mise en évidence — ex. « DÉCLARER À LA CTAF — déclaration obligatoire dans les 48 h » |
| **Synthèse** | Le paragraphe à lire : synthèse exécutive rédigée en français professionnel |
| **Scores** | Un tableau unique : conformité, ML, client, score combiné + ligne de divergence |
| **Obligations violées** | Le cœur juridique — tableau des règles enfreintes (source, article, référence FATF, sévérité) |
| **Conformité** | Nombre d'obligations vérifiées conformes + taux de couverture BCT/FATF |
| **Pattern AML** | Le schéma détecté et son explication en langage courant |
| **Client** | Identifiant, niveau de risque, marqueurs (PEP, KYC incomplet, STR antérieurs, FATCA) |
| **ML (SHAP top 3)** | Les trois features les plus contributives, interprétées |
| **Documents requis** | Liste numérotée des justificatifs à réclamer |
| **Actions** | Cases à cocher : approuver / refuser / demander documents / annoter |
| **Pied** | Banque déclarante, horodatage de génération |

Le détail complet (toutes les règles respectées, la piste d'audit intégrale, les
features SHAP au-delà du top 3) reste dans le **JSON**, volontairement écarté du
Markdown pour ne pas noyer l'essentiel.

---

## 3. La synthèse exécutive : LLM sous contrainte stricte

C'est le seul endroit du rapport où un modèle de langage rédige du texte libre.
L'approche mérite d'être expliquée, parce qu'elle est au cœur de la crédibilité
du système.

### Le principe : rédacteur, pas analyste

Le LLM ne décide rien et ne calcule rien. Il reçoit **uniquement les faits déjà
établis**, sous forme de JSON (décision, scores, règles violées, profil client,
pattern), et sa seule mission est de les mettre en prose.

Les consignes du prompt sont explicites :

- utiliser **uniquement** ces faits — n'inventer aucun chiffre, aucune loi,
  aucun nom qui n'y figure pas ;
- un seul paragraphe fluide de 4 à 7 phrases, en français professionnel ;
- pas de titre, pas de liste, pas de markdown, **aucun emoji** ;
- commencer par décrire l'opération, puis la décision et sa raison principale,
  puis le contexte client/ML ;
- terminer **exactement** par la recommandation calculée par le système.

### Les garde-fous

| Garde-fou | Rôle |
|---|---|
| `temperature = 0` | Sortie déterministe → la démonstration est rejouable à l'identique |
| Cache fichier | Le même dossier produit la même synthèse sans nouvel appel |
| Contrôle de longueur | Une réponse trop courte ou trop longue est rejetée |
| **Repli déterministe** | Si le LLM est indisponible ou hors contrainte, une synthèse générée par gabarit prend le relais |

Le champ `executive_summary_source` indique toujours si la synthèse vient du LLM
(`llm`) ou du repli (`deterministe`), et le rapport le mentionne en note. **Le
SAR sort toujours**, même sans accès au LLM : c'est une exigence de robustesse
pour une démonstration.

---

## 4. Le score combiné

Le score final agrège deux sources, et une seule règle gouverne l'agrégation :
**le ML ne peut qu'ajouter**.

```
score_combiné = score_conformité
                + contribution_ML        (seulement si ML = FRAUDE ET profil mature)
                + contribution_client    (déjà intégrée au score de conformité)

           puis : garde-fou max() — le combiné ne descend jamais
                  sous le score de conformité
```

Trois cas où la contribution ML est **nulle** :

1. le ML conclut « légitime » ;
2. le profil est nouveau (`ml_reliable = False`) ;
3. le ML est indisponible.

Le niveau de risque (`FAIBLE / MOYEN / ÉLEVÉ / CRITIQUE`) est ensuite dérivé du
score combiné.

### Pourquoi le risque client n'est pas recompté

Un plan externe proposait d'ajouter un terme `β × score_client` au score du SAR.
Vérification faite dans le code du Compliance Agent, le risque client y est
**déjà** intégré (contribution inhérente + plancher de décision). L'ajouter au
niveau du SAR aurait été un **double comptage** — exactement l'anti-pattern que
le code documente et évite ailleurs. La proposition a donc été rejetée, et c'est
un bon point à assumer si la question tombe.

---

## 5. Couverture BCT et FATF

Le rapport affiche deux taux de couverture distincts :

- **couverture BCT** — la part des obligations *contraignantes* du droit tunisien
  effectivement vérifiées ;
- **couverture FATF** — la part des recommandations de référence couvertes.

Les deux sont distinguées volontairement. La FATF est une **taxonomie de
référence** internationale ; c'est la BCT et la loi tunisienne qui sont
**contraignantes** pour une banque en Tunisie. Afficher un seul chiffre agrégé
laisserait croire à une conformité qui n'a pas la même valeur juridique.

Sur la transaction de démonstration TX-20260722-0002 : **75 % BCT / 100 % FATF**.

---

## 6. Patterns AML

Le SAR reprend le schéma détecté par le classifieur de patterns et l'accompagne
d'une explication en langage courant :

| Pattern | Signification |
|---|---|
| `STRUCTURING` | Fractionnement des montants sous le seuil de déclaration |
| `FAN_OUT` | Un émetteur vers de nombreux destinataires |
| `FAN_IN` | De nombreux émetteurs vers un seul compte |
| `LAYERING` | Empilement d'opérations destiné à brouiller la traçabilité |
| `HIGH_RISK_ROUTE` | Route géographique à risque |
| `NIGHT_BURST` | Rafale d'opérations nocturnes |
| `NORMAL` | Aucun schéma suspect identifié |

---

## 7. Un choix de forme : aucun emoji

Le rendu Markdown a été entièrement débarrassé des emojis (titres, statuts,
badges de sévérité, indicateurs SHAP). La raison est simple et assumée : un
rapport réglementaire destiné à être transmis à une autorité doit avoir l'aspect
d'un document professionnel. Les pictogrammes décoratifs trahissent une
génération automatique et nuisent à la crédibilité du livrable.

Les remplacements : `oui`/`non` pour les statuts, `[ ]` pour les cases d'action,
`BCT`/`Int.` pour les badges de source, `(recoupée)` pour les règles
cross-validées. La consigne « aucun emoji » a également été ajoutée au prompt de
la synthèse LLM, pour qu'il n'en réintroduise pas.

---

## 8. Diffusion du rapport

- **Dans la plateforme** : le SAR s'ouvre dans une fenêtre d'impression mise en
  forme (sections, tableaux, citations) permettant l'enregistrement en PDF. Le
  téléchargement du Markdown brut a été retiré : il était illisible pour un
  utilisateur métier.
- **Sur disque** : chaque analyse écrit `sars/sar_{tx_id}.md` et
  `sars/sar_{tx_id}.json`.
- **Accès** : le bouton PDF n'apparaît que pour les décisions réellement
  déclaratives (STR, BLOCK), et le SAR est masqué au rôle utilisateur — c'est un
  document interne de conformité.

---

## 9. Limites assumées

- La synthèse LLM, même contrainte, reste une reformulation : c'est le JSON qui
  fait foi en cas de contrôle.
- La couverture-preuve réelle en production (~28 %) est inférieure à la
  couverture générique affichée ; le reste est couvert symboliquement. C'est
  documenté et assumé plutôt que masqué.

---

## 10. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `sar_generator.py` | `SARReport`, `SARGenerator`, synthèse LLM + repli, rendu Markdown/JSON |
| `pattern_classifier.py` | Détection du schéma AML |
| `llm_client.py` | Client LLM avec rotation de clés, cache et repli |
| `sars/` | Rapports générés (`.md` + `.json`) |
| `project/src/components/Markdown.tsx` | Rendu et export PDF côté plateforme |
