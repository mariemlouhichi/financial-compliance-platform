# 16 — Analyse des justificatifs

> Vérifier qu'un document **prouve réellement** l'opération déclarée — et pas
> seulement qu'un fichier a été déposé.

---

## 1. Objectif

Quand une opération est mise en attente pour justification, le client dépose une
facture, un contrat ou une attestation. La question n'est pas « un fichier a-t-il
été fourni ? » mais **« ce document prouve-t-il cette transaction précise ? »**.

Un système naïf coche la case dès qu'un PDF arrive. Le nôtre lit le document,
en extrait les faits, et les confronte à la transaction. Un document hors sujet
est **rejeté**, et l'opération reste bloquée.

---

## 2. Le document influence réellement la décision

C'est le point le plus important : l'analyse du justificatif n'est pas un affichage
décoratif, elle est **intégrée au pipeline**.

```
Étape 0 — analyse des justificatifs joints
        │
        ▼
   verdict : recevable / non recevable
        │
        ▼
   REMPLACE le champ has_justification de la transaction
        │
        ▼
Étapes 1 à 8 — le Niveau 1 et le Compliance Agent traitent l'opération
              comme réellement (non) justifiée
```

Concrètement : joindre une facture qui ne correspond pas fait basculer
`has_justification` à **faux**, ce qui peut déclencher une déclaration de
soupçon. Le document ne « décore » pas le dossier, il en change l'issue.

---

## 3. Extraction du texte

Les documents arrivent en PDF, DOCX ou TXT. La chaîne d'extraction est hiérarchisée :

| Format | Extracteur principal | Repli |
|---|---|---|
| PDF | **docling** | `pypdf` |
| DOCX | **docling** | `python-docx` |
| TXT | décodage direct | — |

### Pourquoi docling

Une facture, c'est presque toujours un **tableau**. Les extracteurs classiques
aplatissent les tableaux en une bouillie de mots où les montants perdent leur
colonne. Docling préserve la structure et restitue un Markdown propre
(`## FACTURE`, lignes de tableau), ce qui rend les montants et les libellés
exploitables.

### Le garde-fou de latence

Docling charge un modèle de mise en page d'environ 500 Mo. Le **premier**
téléchargement a pris 47 minutes sur une connexion lente — ce qui a d'abord été
pris pour un problème de performance. Une fois le modèle en cache : **4,3 s à
froid, ~1 s à chaud**.

Pour que ce ne soit jamais bloquant, l'extraction docling tourne dans un
exécuteur avec un **délai maximum de 90 secondes**. En cas d'échec **ou** de
dépassement, le système bascule automatiquement sur `pypdf`. La qualité baisse,
mais l'analyse aboutit toujours.

L'OCR est **désactivé** (`do_ocr=False`) : les documents scannés ne sont pas
traités. C'est un choix de performance assumé, et une perspective identifiée.

---

## 4. Les vérifications déterministes

Avant tout appel à un modèle de langage, le système effectue des contrôles
mécaniques et vérifiables.

### Le montant — un piège corrigé

**Le bug initial.** La vérification cherchait le montant comme **sous-chaîne**
dans les chiffres normalisés du document. Résultat : une facture de
**12 000 USD** validait une transaction de **1 200 USD**, parce que la suite
`1200` est bien contenue dans `1200000`.

Ce cas a été détecté lors d'un test réel de l'utilisatrice, et c'est un bon
exemple de bug silencieux : le système répondait « conforme » avec assurance.

**Le correctif.** Une fonction `_amounts_in_text()` **parse chaque token
numérique** du document (séparateurs de milliers en espace, point ou virgule ;
décimales sur un ou deux chiffres) et compare les **valeurs** à ±0,01 près :

```
1 200,00        → 1200.0
12.000,00       → 12000.0
12 000 USD      → 12000.0
```

La comparaison porte désormais sur des nombres, plus sur du texte.

### Les parties

Les noms de l'émetteur et du bénéficiaire sont recherchés de façon **tokenisée**
(insensible à la casse et aux variations d'espacement), pour tolérer les
différences de formatage entre le formulaire et le document.

### Les dates

Détection par expression régulière, avec vérification que le document est
**récent** (moins d'un an). Une facture périmée n'est pas une preuve valable pour
une opération d'aujourd'hui.

---

## 5. La revue par LLM

En complément, un modèle de langage lit le document extrait et rend un avis
structuré en JSON :

```json
{
  "conforme": true,
  "verifications": ["…"],
  "resume": "…"
}
```

Son rôle est d'apporter le **jugement contextuel** que les regex ne savent pas
faire : le document est-il du bon type ? Le libellé de la prestation est-il
cohérent avec le motif déclaré ?

---

## 6. La règle de verdict — durcie

Le verdict final n'est pas le verdict du LLM. C'est une **conjonction** :

```
recevable  =  avis_LLM_favorable
              ET  montant exact présent
              ET  (émetteur cité  OU  bénéficiaire cité)
```

**Ce qui a changé.** Le repli initial acceptait « montant + date récente », ce
qui était trop laxiste : une facture au bon montant mais entre deux tiers
inconnus passait. Désormais la date seule ne prouve rien — il faut le montant
exact **et** au moins une des deux parties.

Résultats vérifiés après correction :

| Cas | Verdict |
|---|---|
| Transaction 1 200 USD, facture 12 000 USD | **rejet** |
| Transaction 12 000 USD, facture 12 000 USD au nom du bénéficiaire | **recevable** |
| Bon montant mais parties inconnues | **rejet** |
| Facture périmée (plus d'un an) | **rejet** |

### Un comportement à savoir expliquer

Le contrôle « émetteur présent » se base sur le **profil de l'utilisateur
connecté**, pas sur un nom lu dans le document. Si la cliente dépose la facture
d'un tiers, la vérification « émetteur cité » échoue légitimement. Ce n'est pas
un bug : le document doit rattacher l'opération à **son** auteur.

---

## 7. Effet sur le workflow

Un justificatif recevable **débloque** le dossier :

```
PENDING_DOCUMENTS  ──(document recevable)──▶  PENDING_REVIEW
```

L'opération repart alors vers la file superviseur pour décision humaine. Le
déblocage est immédiat et visible côté client dans « Mes dossiers ».

Plusieurs documents peuvent être joints (facture **et** contrat) : ils sont
accumulés, chacun analysé, et le dossier est recevable dès qu'un document
probant est fourni.

---

## 8. Choix technique : base64 plutôt que multipart

Les fichiers sont transmis **encodés en base64 dans le corps JSON**, et non en
`multipart/form-data`.

La raison est pragmatique : le multipart exige la dépendance `python-multipart`.
Le base64 fonctionne avec le JSON natif de FastAPI, sans rien ajouter — cohérent
avec la ligne « zéro dépendance superflue » du projet. Le coût est un surpoids
d'environ 33 % sur la taille transmise, négligeable pour des justificatifs de
quelques centaines de kilo-octets.

---

## 9. Jeu de documents de test

Six documents ont été **générés sans bibliothèque externe** (PDF en syntaxe
native, DOCX en ZIP + XML avec de vrais tableaux `w:tbl` pour tester docling) :

| Document | Cas testé |
|---|---|
| `facture_export_12000_usd.pdf` | Cas nominal avec tableau |
| `contrat_prestation_15000_tnd.docx` | Échéancier, extraction DOCX |
| `attestation_origine_fonds_9000_tnd.pdf` | Origine des fonds |
| `attestation_travail.docx` | **Non probant** — aucun montant |
| `facture_perimee_2023.pdf` | Cas limite — date trop ancienne |
| `recu_paiement_2500_tnd.txt` | Format texte simple |

---

## 10. Limites assumées

- **Pas d'OCR** : un justificatif photographié ou scanné n'est pas lu.
- **Pas de détection de falsification** : le système vérifie la cohérence du
  contenu, pas l'authenticité du document.
- **Documents en français et en anglais** : d'autres langues n'ont pas été testées.

---

## 11. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `api/main.py` | `_extract_doc_text`, `_deterministic_checks`, `_amounts_in_text`, `_llm_review_justificatif`, `_maybe_unblock` |
| `generate_test_documents.py` | Génération du jeu de documents de test |
| `test_documents/` | Les six documents de démonstration |
| `project/src/pages/PipelineLive.tsx` | `JustificatifCard` — dépôt et résultat |
| `project/src/pages/Submit.tsx` | Dépôt multiple à la soumission |
