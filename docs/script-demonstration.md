# 🎥 Script de tournage — Vidéo de démonstration

> Flux chronologique filmable d'une traite. Chaque partie est autonome : coupe au
> montage si besoin.
> **Durée cible : 15 à 18 minutes.**

---

## ⚙️ AVANT D'ENREGISTRER (hors caméra)

### 1. Démarrer la plateforme

```powershell
docker compose up -d
docker compose ps          # les 6 services doivent être Up
```

> ⚠️ **Ne lance PAS `uvicorn` ni `npm run dev`** — tout tourne en conteneurs.
> L'adresse est **http://localhost:8090** (le 8080 est pris par Oracle).

### 2. Préchauffer — indispensable

Le premier appel charge les modèles en mémoire : **~16 s**, puis ~5 s. À faire
**avant** de filmer, sinon la caméra tourne pendant l'attente :

- Se connecter une fois à http://localhost:8090
- Soumettre **une transaction bidon** et laisser l'analyse aller à son terme
- Amorcer l'historique comportemental :
  ```powershell
  .\venv\Scripts\python.exe seed_redis_history.py
  ```

### 3. Comptes nécessaires

| Rôle | Usage dans la vidéo |
|---|---|
| **USER** | soumission des transactions |
| **COMPLIANCE_OFFICER** | file superviseur, validation |
| **ADMIN** | montrer le **refus** de validation (séparation des devoirs) |
| **AUDITOR** | page Investigation |

> 💡 Pour la **partie email**, utilise un compte USER dont tu peux **ouvrir la
> boîte mail à l'écran**. C'est le moment le plus convaincant de la démo.

### 4. Fenêtres à préparer

- **Fenêtre A** : navigateur, session USER
- **Fenêtre B** : navigateur (mode privé), session COMPLIANCE_OFFICER
- **Fenêtre C** : ta boîte mail
- Client émetteur utilisé partout : **`802559360`**

---

## 🎬 PARTIE 1 — Ouverture (~1 min)

**[Page de connexion]**

> *« Financial Compliance Platform : un système multi-agent qui décide, pour chaque
> opération bancaire, si elle est conforme, si elle exige une vigilance renforcée,
> ou si elle doit faire l'objet d'une déclaration de soupçon — en citant la règle
> qui justifie la décision. »*

> *« Le point de départ : un modèle de machine learning seul ne cite aucun article
> de loi ; un moteur de règles seul ne détecte pas les comportements inhabituels.
> Ce projet combine les deux, avec une règle stricte — le déterministe prévaut
> toujours. »*

- Se connecter en **USER**

---

## 🎬 PARTIE 2 — Le profil KYC alimente le scoring (~1 min)

**[Mon profil]**

> *« Avant toute transaction, l'utilisateur renseigne ses coordonnées KYC. Chaque
> champ alimente réellement le scoring client du pipeline — ce n'est pas un
> formulaire décoratif. »*

- Montrer la jauge de complétion et les 5 sections
- Pointer un champ à risque (profession, PEP) : *« celui-ci pèse dans le score »*

---

## 🎬 PARTIE 3 — Transaction conforme + justificatif (~2 min)

**[Nouvelle transaction]**

| Champ | Valeur |
|---|---|
| Type | Virement local |
| Client émetteur | `802559360` → **Vérifier** |
| Montant | `2500` · TND |
| Pays | Tunisia → Tunisia |
| Bénéficiaire | `Ste Services Plus` |
| Motif | `Paiement facture électricité` |
| RIB émetteur | `12345678901234567890` |
| Justificatif | ☑ + `test_documents/recu_paiement_2500_tnd.txt` |

→ **Analyser**

**[Pipeline Live — le cœur de la démonstration]**

> *« Voici ce qui rend le système explicable : les étapes sont visibles, avec leur
> durée réelle. »*

Commenter en descendant :
- **Justificatifs** → *« le document est lu, pas juste coché »*
- **Niveau 1** → PASS, aucun signal
- **Client Scoring** → risque FAIBLE
- **Planning** → *« pour chaque question, le système affiche POURQUOI il la pose »*
- **Retrieval** → *« chaque question interroge d'abord le graphe, puis le corpus »* (badges 📗 / 🔎)
- **Compliance** → PASS
- **ML** → *« profil mature grâce à l'historique : le ML est réellement évalué »*
- **SAR** → ouvrir le rapport, montrer la **synthèse en français**, puis **PDF**
- **Workflow** → état **APPROUVÉ**

---

## 🎬 PARTIE 4 — Le justificatif, vraiment vérifié ⭐⭐ (~3 min)

> **C'est la séquence la plus démonstrative. Ne la coupe pas.**

### 4a — Seuil dépassé, sans preuve → STR

| Champ | Valeur |
|---|---|
| Type | SWIFT · `12000` USD |
| Client | `802559360` |
| Pays | Tunisia → France |
| Bénéficiaire | `Societe Lumiere SARL` |
| Motif | `Paiement facture export` |
| Justificatif | ☐ **décoché** |

→ **Attendu : STR**
> *« Au-dessus du seuil réglementaire et sans aucune preuve, la déclaration de
> soupçon est obligatoire. »*

### 4b — Même transaction, avec la vraie facture

Mêmes champs, **☑ + `facture_export_12000_usd.pdf`**

→ **Attendu : le document est lu (docling extrait le tableau), montant 12 000 USD ✓,
bénéficiaire ✓, date ✓ → RECEVABLE → décision EDD** au lieu de STR.

> *« Le justificatif n'est pas une case à cocher : c'est un document réellement
> extrait et confronté à la transaction — montant exact, parties, date. »*

### 4c — Document hors-sujet ⭐

Mêmes champs, **☑ + `attestation_travail.docx`**

→ **Attendu : NON RECEVABLE** (aucun montant) → traitée comme non justifiée → **STR**

> *« Et si quelqu'un joint n'importe quel document pour cocher la case ? Le système
> le rejette et maintient la déclaration. »*

**💡 À dire ici** : *« Un bug réel a été corrigé sur ce point : la vérification
comparait les montants comme du texte, si bien qu'une facture de 12 000 validait
une transaction de 1 200. Les montants sont désormais comparés en valeur. »*

---

## 🎬 PARTIE 5 — Word et tableaux (~45 s)

| Champ | Valeur |
|---|---|
| Virement local · `15000` TND | Bénéficiaire `Bureau Etudes Manar SARL` |
| Motif | `Acompte contrat prestation` |
| Justificatif | ☑ + `contrat_prestation_15000_tnd.docx` |

→ **RECEVABLE**
> *« Docling lit aussi les fichiers Word, y compris leurs tableaux — ici
> l'échéancier de paiement. »*

---

## 🎬 PARTIE 6 — Le fractionnement ⭐ (~1 min 30)

3 soumissions successives, **même client**, **Dépôt espèces** :

| # | Montant | Attendu |
|---|---|---|
| 1 | `4800` TND | PASS |
| 2 | `4500` TND | PASS |
| 3 | `4900` TND | **STR + [SMURFING]** |

> *« Chaque dépôt est parfaitement légal isolément. C'est la mémoire glissante qui
> révèle trois dépôts sous le seuil en 72 heures. Aucune des trois opérations n'est
> suspecte seule — c'est la séquence qui l'est. »*

---

## 🎬 PARTIE 7 — Listes FATF (~1 min 30)

**7a — Liste noire** : Pays destination **Iran**
→ Alerte **rouge en direct dans le formulaire**, puis décision **BLOCK**
> *« L'alerte apparaît avant même la soumission. »*

**7b — Liste grise** : Pays destination **Algeria**
→ Bandeau ambre → **EDD**

---

## 🎬 PARTIE 8 — File superviseur priorisée ⭐ (~2 min)

**[Fenêtre B — COMPLIANCE_OFFICER → File superviseur]**

> *« Côté conformité, la file n'est pas un simple ordre d'arrivée : elle est pilotée
> par le risque. »*

Montrer :
- Les **badges de priorité** : `HAUTE` (STR/BLOCK), `MOYENNE` (EDD), `NORMALE`
- L'**ancienneté** : « en attente 3 j »
- **Le point clé à souligner** :

> *« Regardez : ce STR arrivé il y a une minute passe devant cet EDD qui attend
> depuis quatre jours. C'est le comportement métier correct — une déclaration de
> soupçon a un délai réglementaire de 48 heures, une diligence renforcée peut
> attendre. Un simple FIFO aurait fait l'inverse. »*

- Faire défiler jusqu'à **l'historique des décisions** en bas de page
> *« Chaque décision passée est tracée : qui, quoi, quand, et pourquoi. »*

---

## 🎬 PARTIE 9 — Décision + email au client ⭐⭐ (~2 min)

> **Le moment le plus concret de la démo. Prépare la fenêtre C (boîte mail).**

**[Fenêtre B]** Sélectionner le dossier STR → instruire :
- **Verdict** : `🔴 Fraude confirmée`
- **Annotation** *(obligatoire)* : `Seuil dépassé et absence de justificatif probant.`
- → **Refuser**

> *« L'annotation est obligatoire : une décision sans motif n'est pas défendable
> devant un contrôle. »*

**[Fenêtre C — boîte mail]** Rafraîchir

→ **L'email est arrivé** : bandeau rouge, récapitulatif de l'opération, et
**le motif du refus**.

> *« Le client est notifié automatiquement, avec la raison exacte du refus. La
> justification saisie par le contrôleur devient l'explication donnée au client :
> l'exigence d'audit interne et la transparence externe sont le même texte. »*

---

## 🎬 PARTIE 10 — Temps réel ⭐ (~1 min)

> **Mettre les fenêtres A et B côte à côte à l'écran.**

- **Fenêtre B** : File superviseur affichée
- **Fenêtre A** : soumettre une transaction qui déclenchera un STR

→ **Dès la fin de l'analyse, le dossier apparaît dans la fenêtre B**, sans
rafraîchissement, avec son badge `HAUTE` et « en attente 1 min ».

> *« Aucune interrogation périodique : le serveur pousse la notification aux
> interfaces connectées. Le superviseur voit le dossier arriver instantanément. »*

---

## 🎬 PARTIE 11 — Séparation des devoirs ⭐ (~1 min)

**[Se connecter en ADMIN]**

> *« L'administrateur a tous les droits techniques. Peut-il valider un dossier
> réglementaire ? »*

- Montrer que la **File superviseur est absente** de son menu
- *(Optionnel, très parlant)* montrer le **403** renvoyé par l'API

> *« Non — et le contrôle est côté serveur, pas seulement un bouton masqué. C'est
> un principe de contrôle interne : celui qui administre le système ne doit pas
> pouvoir approuver ses propres opérations. »*

---

## 🎬 PARTIE 12 — Investigation & recherche SQL ⭐ (~1 min 30)

**[Se connecter en AUDITOR → Investigation]**

> *« L'auditeur dispose d'une recherche transversale exécutée en base. »*

Saisir : **Montant min `10000`** + **Décision `STR`** + **Période 30 jours**

→ Pointer le badge : **« N résultat(s) · moteur : PostgreSQL »**

> *« Ce badge n'est pas décoratif : la requête s'exécute réellement en SQL sur des
> colonnes indexées, pas dans le navigateur. C'est ce qui permet de répondre à une
> question de contrôle du type "toutes les opérations de plus de 10 000 déclarées
> ce mois-ci". »*

- Montrer aussi les **indicateurs de qualité** : faux positifs annotés, fraudes confirmées

---

## 🎬 PARTIE 13 — La connaissance réglementaire (~2 min)

**[COMPLIANCE_OFFICER]**

- **Corpus réglementaire** → 34 PDF sources, prévisualisation
- **Règles extraites** → les 49 règles de référence + **la citation verbatim**
> *« Chaque règle est reliée à son texte source. C'est ce qui explique un taux
> d'hallucination de 0 % : une règle sans preuve n'est pas retenue. »*
- **Graphe de connaissances** → la chaîne `Rule → Chunk → LegalUnit → Document`
> *« Et le graphe s'enrichit : chaque analyse peut y ajouter des règles validées,
> si bien qu'il répond de plus en plus souvent sans solliciter le corpus. »*

---

## 🎬 PARTIE 14 — Amélioration continue (~1 min)

> *« Les décisions du superviseur ne sont pas perdues : elles alimentent une boucle
> d'apprentissage automatisée. »*

| Tâche planifiée | Cadence |
|---|---|
| Cycle de réentraînement | dimanche 02 h |
| Contrôle de dérive | quotidien 03 h |
| Rapport hebdomadaire par email | vendredi 17 h |

> *« Le point important est la politique de déclenchement : le système refuse
> explicitement de réentraîner en dessous de vingt étiquettes — sur un échantillon
> plus faible, il apprendrait du bruit. Et un contrôle quotidien déclenche un cycle
> anticipé si le taux de fausses alertes dérive. »*

*(Optionnel)* Déclencher le **rapport hebdomadaire** et le montrer arriver dans la
boîte mail.

---

## 🎬 PARTIE 15 — Clôture : les limites assumées (~1 min)

> **Ne saute pas cette partie. Annoncer ses limites vaut mieux que se les faire
> pointer.**

> *« Pour finir, ce que le système ne fait pas : la couverture par preuve verbatim
> est d'environ 28 % en production, le reste étant couvert symboliquement. Les
> poids de scoring sont une calibration, pas une source réglementaire. Le modèle
> est entraîné sur un jeu de données public, pas sur des données bancaires réelles.
> Et les justificatifs scannés ne sont pas lus, faute d'OCR. »*

> *« Tout cela est documenté — vingt-quatre fiches techniques accompagnent le
> projet, chacune avec sa section de limites. »*

---

## ⚠️ Pièges à éviter pendant le tournage

| Piège | Comment l'éviter |
|---|---|
| **Attente au premier appel** | Préchauffer avant de filmer (voir setup) |
| Lancer `uvicorn` / `npm run dev` | Inutile : tout est en conteneurs |
| Se tromper d'adresse | C'est **8090**, pas 8080 ni 5173 |
| Bénéficiaire inexact | Les noms sont vérifiés : `Societe Lumiere SARL`, `Bureau Etudes Manar SARL`, `Ste El Amen Distribution` |
| Fractionnement non détecté | Bien utiliser **le même client** et **Dépôt espèces** |
| File superviseur vide | Créer un STR non tranché avant la partie 8 |
| Email non reçu | Vérifier les **spams**, et utiliser un compte dont tu ouvres la boîte |
| PEP resté coché | Le décocher après un scénario PEP, sinon il fausse les suivants |

---

## 📎 Documents utilisés

| Fichier | Partie | Rôle |
|---|---|---|
| `recu_paiement_2500_tnd.txt` | 3 | Cas nominal |
| `facture_export_12000_usd.pdf` | 4b | Justificatif **valide** (tableau) |
| `attestation_travail.docx` | 4c | Document **hors-sujet** |
| `contrat_prestation_15000_tnd.docx` | 5 | Word avec tableau |
| `attestation_origine_fonds_9000_tnd.pdf` | réserve | Origine des fonds |
| `facture_perimee_2023.pdf` | réserve | Cas limite : date trop ancienne |

---

## 🎯 Les 6 moments qui font la différence

Si tu dois raccourcir, garde **ceux-là** :

1. **4b vs 4c** — le document vérifié contre le document hors-sujet
2. **Partie 6** — le fractionnement révélé par la mémoire
3. **Partie 8** — le STR qui double l'EDD plus ancien
4. **Partie 9** — l'email reçu par le client avec le motif
5. **Partie 11** — l'administrateur qui ne peut pas valider
6. **Partie 13** — la citation verbatim derrière chaque règle
