# 🎥 SCRIPT DE TOURNAGE — Vidéo démo (prise continue)

Basé sur `GUIDE_DEMO_JURY.md` (mêmes scénarios), réorganisé en **un seul flux
chronologique** filmable sans coupe, avec exactement ce qu'il faut taper/cliquer
et une phrase de commentaire suggérée pour chaque étape.

Durée indicative totale : **12-15 minutes**. Coupe au montage si besoin (chaque
section est autonome).

---

## ⚙️ AVANT DE LANCER L'ENREGISTREMENT (hors caméra)

```powershell
docker compose up -d
.\venv\Scripts\python.exe seed_redis_history.py
.\venv\Scripts\python.exe -m uvicorn api.main:app --port 8001
cd project ; npm run dev
```

**Faire à blanc AVANT de filmer** (pour ne pas subir le chargement à froid) :
- Ouvrir `localhost:5173`, se connecter une fois, soumettre UNE transaction test
  quelconque → le premier appel docling/retrieval charge ses modèles (~30-60 s) ;
  les suivants seront rapides pendant l'enregistrement.
- Vérifier que `test_documents/` est accessible depuis le sélecteur de fichier.

**Comptes à créer avant le tournage** (via Créer un compte → code console → activer) :

| Email suggéré | Rôle | Usage |
|---|---|---|
| `user.demo@test.com` | USER | soumission transactions |
| `compliance.demo@test.com` | COMPLIANCE_OFFICER | validation superviseur |
| `admin.demo@test.com` | ADMIN | dashboard/simulateur (montrer le refus de validation) |
| `audit.demo@test.com` | AUDITOR | page Investigation |

Client émetteur utilisé partout : **`802559360`** (profil + historique Redis seedés).

---

## 🎬 PARTIE 1 — Connexion & profil KYC (~1 min 30)

**[Écran Login]**
> *« Voici Financial Compliance Platform. Je me connecte avec mon compte utilisateur — l'authentification est maison : mot de passe hashé PBKDF2, vérification par email, token signé. »*

- Se connecter avec `user.demo@test.com`
- Arrivée sur **Mes dossiers** (page d'accueil de l'espace utilisateur)

**[Aller sur « Mon profil »]**
> *« Avant de soumettre une transaction, je renseigne mes coordonnées KYC — chaque champ alimente directement le scoring client du pipeline. »*

Champs à remplir :
| Champ | Valeur |
|---|---|
| Nom complet | *(déjà pré-rempli)* |
| Nationalité | Tunisia |
| Pays de résidence | Tunisia |
| Profession | Commerce de détail |
| Type de compte | Particulier |
| Date d'ouverture du compte | (une date > 90 jours dans le passé) |
| STR antérieures | 0 |
| PEP | ☐ décoché *(pour l'instant — réactivé en Partie 5)* |
| FATCA US person | ☐ décoché |

→ Cliquer **Enregistrer mon profil** → bandeau vert de confirmation.

---

## 🎬 PARTIE 2 — Transaction normale + justificatif TXT (~1 min 30)

**[Nouvelle transaction]**
> *« Je soumets une transaction bénigne, avec son justificatif joint directement au formulaire — plus besoin de le déclarer, il est vérifié. »*

| Champ | Valeur |
|---|---|
| Type | Virement local |
| Client émetteur | `802559360` → cliquer Vérifier |
| Montant | 2500 |
| Devise | TND |
| Pays émetteur | Tunisia |
| Pays destination | Tunisia |
| Bénéficiaire | Ste Services Plus |
| Motif | Paiement facture électricité |
| RIB émetteur | 12345678901234567890 *(20 chiffres — voir Partie 6 pour la validation)* |
| Justificatif fourni | ☑ coché → joindre `test_documents/recu_paiement_2500_tnd.txt` |

→ **Analyser la transaction**

**[Pipeline Live — commenter chaque étape en direct]**
> *« Je montre les 8 étapes en temps réel. »*
- Level 1 : PASS, aucun signal
- Client Scoring : risque FAIBLE
- Planning : *« Le système explique pourquoi il pose chaque question — ici uniquement des vérifications standard. »*
- Retrieval : *« Chaque question interroge d'abord le graphe de connaissances, puis le corpus si besoin — badges 📗 et 🔎. »*
- Compliance : décision PASS
- ML : *« Profil mature grâce à l'historique — le ML est réellement évalué, pas neutralisé. »*
- SAR : ouvrir le rapport → montrer la **synthèse exécutive en français**, cliquer **Télécharger PDF**
- Workflow : état **APPROUVED**

---

## 🎬 PARTIE 3 — Seuil dépassé : déclaration vs justificatif vérifié ⭐ (~2 min 30)

### 3a — Sans justificatif → STR
> *« Maintenant un virement international au-dessus du seuil réglementaire, sans aucune preuve. »*

| Champ | Valeur |
|---|---|
| Type | SWIFT |
| Client émetteur | `802559360` |
| Montant | 12000 |
| Devise | USD |
| Pays émetteur | Tunisia |
| Pays destination | France |
| Bénéficiaire | Societe Lumiere SARL |
| Motif | Paiement facture export |
| IBAN bénéficiaire | *(laisser vide pour l'instant)* |
| Justificatif fourni | ☐ décoché, aucun fichier |

→ Analyser → **Attendu : décision STR** (seuil >10 000 USD sans justificatif).
> *« Sans preuve, la déclaration ne suffit pas : le système déclare le soupçon obligatoirement. »*

### 3b — Même transaction, AVEC la facture ⭐
> *« Je refais exactement la même transaction, mais cette fois je joins la vraie facture. »*

Mêmes champs, **Justificatif fourni ☑** + joindre `facture_export_12000_usd.pdf`

→ Analyser → **Attendu : le document est extrait (docling lit le tableau de marchandises), vérifié — montant 12 000 USD ✓, compte ✓, bénéficiaire ✓, date ✓ → RECEVABLE → décision EDD** (au lieu de STR).
> *« Le justificatif n'est plus une case à cocher : c'est un document réellement lu et vérifié — montant, parties, date — puis jugé par un LLM contraint aux faits. »*

### 3c — Document hors-sujet (anti-fraude déclarative) ⭐
> *« Et si un utilisateur joint n'importe quel document juste pour cocher la case ? »*

Mêmes champs que 3a, **Justificatif fourni ☑** + joindre `attestation_travail.docx`

→ Analyser → **Attendu : NON RECEVABLE (aucun montant dans le document) → traitée comme non justifiée → STR malgré la case cochée.**
> *« Le système ne se laisse pas tromper par une case cochée sans preuve réelle. »*

---

## 🎬 PARTIE 4 — Contrat Word + tableau (vitrine docling) (~1 min)

| Champ | Valeur |
|---|---|
| Type | Virement local |
| Client émetteur | `802559360` |
| Montant | 15000 |
| Devise | TND |
| Bénéficiaire | Bureau Etudes Manar SARL |
| Motif | Acompte contrat prestation |
| Justificatif fourni | ☑ + joindre `contrat_prestation_15000_tnd.docx` |

→ Analyser → **Attendu : RECEVABLE**, panneau d'analyse affiche « type détecté : contrat ».
> *« Docling extrait aussi les fichiers Word, y compris leurs tableaux — ici l'échéancier de paiement. »*

---

## 🎬 PARTIE 5 — Smurfing : la mémoire Redis ⭐ (~1 min 30)

> *« Le fractionnement est invisible transaction par transaction — c'est la mémoire glissante qui le révèle. »*

3 soumissions successives, **même client `802559360`**, type **Dépôt espèces** :

| # | Montant | Devise | Résultat attendu |
|---|---|---|---|
| 1 | 4800 | TND | PASS |
| 2 | 4500 | TND | PASS |
| 3 | 4900 | TND | **STR + violation [SMURFING]** |

> *« Chaque dépôt est légal isolément. C'est Redis qui détecte 3 dépôts sous 5 000 TND en 72 heures. »*

---

## 🎬 PARTIE 6 — RIB/IBAN dynamique (~45 s)

> *« La validation des coordonnées bancaires s'adapte au pays sélectionné, en direct. »*

- Pays destination **France** → taper un IBAN de 20 caractères → **erreur affichée : « IBAN français : FR + 25 → 27 caractères »**
- Pays destination **Tunisia** → taper des lettres dans le RIB → **erreur : 20 chiffres exigés**

---

## 🎬 PARTIE 7 — Listes FATF : noir et gris (~1 min 30)

### 7a — Liste noire (blocage dur)
| Champ | Valeur |
|---|---|
| Type | SWIFT |
| Montant | 20000 USD |
| Pays destination | Iran |

> *« Dès la saisie du pays, l'alerte rouge apparaît. »* → Analyser → **BLOCK**.
> *« Aucun raisonnement LLM ne peut passer outre : le symbolique prévaut toujours sur les faits durs. »*

### 7b — Liste grise (vigilance renforcée)
| Champ | Valeur |
|---|---|
| Type | SWIFT |
| Montant | 8000 USD |
| Pays destination | Algeria |
| Justificatif | ☐ décoché |

→ Analyser → **Attendu : EDD** (signal COUNTRY_GREY → questions ciblées au Planning).

---

## 🎬 PARTIE 8 — Profil PEP : risque structurel (~1 min 30)

> *« Retour sur mon profil pour illustrer l'approche par les risques FATF. »*

- **Mon profil** → cocher **PEP** → type **Étrangère** → Enregistrer
- **Nouvelle transaction** : SWIFT · 8000 USD · Tunisia → France (transaction en soi anodine)

→ Analyser → **Attendu : Client Scoring ÉLEVÉ (pep_foreign +35) → EDD**, malgré une transaction bénigne.
> *« Même une opération anodine déclenche une diligence renforcée si le client est structurellement à risque — c'est l'approche par les risques de la recommandation FATF R12. »*

*(Optionnel : décocher le PEP après cette démo pour ne pas polluer les tests suivants.)*

---

## 🎬 PARTIE 9 — Déblocage a posteriori d'un dossier EDD (~1 min)

> *« Un dossier en attente de justificatifs peut être débloqué après coup. »*

- Aller dans **Mes dossiers** → ouvrir un dossier « En cours — justificatifs » (ex. celui de la Partie 8, ou un virement 9000 TND vers `Ste El Amen Distribution`)
- Dans Pipeline Live → carte **Justificatifs** → uploader `attestation_origine_fonds_9000_tnd.pdf`

→ **Attendu : RECEVABLE → dossier bascule en PENDING_REVIEW**.

---

## 🎬 PARTIE 10 — Mes dossiers : filtres et graphiques (~45 s)

> *« L'espace utilisateur permet de suivre, filtrer et rechercher ses dossiers. »*

- Montrer le **filtre par décision** (chips PASS/STR/EDD…)
- Montrer la **recherche** (taper un montant ou un pays)
- Montrer les **2 graphiques** : évolution mensuelle + répartition des décisions
- Montrer une **notification** (ex. « justificatifs requis »)

---

## 🎬 PARTIE 11 — Séparation des rôles + piste d'audit ⭐ (~2 min)

> *« Le rôle Utilisateur n'a pas accès au rapport SAR ni au corpus réglementaire — ce sont des documents internes conformité. »* *(montrer la sidebar simplifiée)*

**Se déconnecter → connexion ADMIN (`admin.demo@test.com`)**
> *« Un administrateur technique voit le dashboard et le simulateur… »*
- Ouvrir la file superviseur → **elle n'apparaît pas dans le menu**
- *(Optionnel, si tu veux le montrer explicitement)* : tenter un appel direct à `/api/validate/{id}` → 403.
> *« … mais ne peut jamais valider un dossier réglementaire. C'est la séparation des devoirs (segregation of duties) — un DSI ne doit pas pouvoir approuver une déclaration de soupçon. »*

**Se déconnecter → connexion COMPLIANCE_OFFICER (`compliance.demo@test.com`)**
> *« Seul ce rôle peut instruire les dossiers. »*
- File superviseur → ouvrir le dossier de la Partie 3b (EDD) → **Approuver** avec annotation `CORRECT`
- Ouvrir le dossier de la Partie 3c (STR malgré case cochée) → **Rejeter** avec label `TRUE_FRAUD`
> *« Chaque décision alimente une boucle de feedback — la graine du RLHF. »*

**Se déconnecter → connexion AUDITOR (`audit.demo@test.com`)**
> *« L'auditeur dispose d'un espace d'investigation dédié, pas d'une simple lecture seule. »*
- Page **Investigation** : montrer la recherche transversale, la piste d'audit (qui a décidé quoi et quand), les indicateurs de qualité (faux positifs annotés, temps moyen)

---

## 🎬 PARTIE 12 — Graphe, Règles, Corpus (~1 min 30, rôle COMPLIANCE_OFFICER)

> *« La mémoire réglementaire n'est pas statique — elle grossit à l'usage. »*

- **Graphe de connaissances** : montrer les compteurs (nœuds/relations), le graphe SVG interactif (chaîne Règle → Chunk → Article → Document)
- **Règles extraites** : montrer les 49 règles ground truth + les règles apprises, cliquer une règle → montrer la citation verbatim (chaîne d'ancrage anti-hallucination)
- **Corpus réglementaire** : montrer un PDF prévisualisé en direct + la recherche hybride

---

## 🎬 PARTIE 13 — Simulateur (optionnel, ~1 min)

> *« Un simulateur joue le rôle du flux bancaire en continu, pour peupler la plateforme en conditions réalistes. »*

- Espace admin → Simulateur → démarrer (intervalle 30 s) → montrer une transaction apparaître dans le Dashboard avec la source 🤖

---

## 📎 Récapitulatif — documents utilisés

| Document | Utilisé dans |
|---|---|
| `recu_paiement_2500_tnd.txt` | Partie 2 |
| `facture_export_12000_usd.pdf` | Partie 3b |
| `attestation_travail.docx` | Partie 3c |
| `contrat_prestation_15000_tnd.docx` | Partie 4 |
| `attestation_origine_fonds_9000_tnd.pdf` | Partie 9 |
| `facture_perimee_2023.pdf` | non utilisé ici — réserve pour question du jury sur les cas limites (date périmée) |

## ⚠️ Pièges à éviter pendant le tournage
1. Toujours utiliser `802559360` comme client émetteur (documents + historique cohérents).
2. Saisir les noms de bénéficiaires **exactement** comme dans le tableau (recherche textuelle dans le document).
3. Décocher le PEP après la Partie 8 si tu continues à tester d'autres scénarios ensuite.
4. Le premier upload docling de la session est plus lent (~30-60 s) — le faire à blanc avant de lancer l'enregistrement.
5. Couper au montage les temps d'attente du pipeline (5-15 s par étape) si tu ne veux pas les garder en direct.
