# 🎓 GUIDE DE DÉMONSTRATION — JURY

Documents de test : dossier `test_documents/` (générés par `generate_test_documents.py`).
Tous sont **cohérents** avec les transactions ci-dessous (montant + n° de compte
`802559360` + noms + dates récentes) — l'analyse documentaire les reconnaîtra.

## ⚙️ Préparation (une fois, avant la démo)

```powershell
docker compose up -d                                  # Neo4j + Weaviate + Redis
.\venv\Scripts\python.exe seed_redis_history.py       # historique du client 802559360
.\venv\Scripts\python.exe -m uvicorn api.main:app --port 8001
cd project ; npm run dev
```

**Comptes à créer** (Créer un compte → code console uvicorn → activer) :
| Rôle | Usage dans la démo |
|---|---|
| USER | soumettre les transactions (espace utilisateur) |
| COMPLIANCE_OFFICER | file superviseur — **seul rôle autorisé à valider** (segregation of duties) |
| ADMIN | dashboard, simulateur (il ne PEUT PAS valider → montrer le 403) |
| AUDITOR | page Investigation (piste d'audit) |

Client émetteur pour toutes les démos : **`802559360`** (profil + historique Redis seedés).

---

## 🎬 Scénarios (dans l'ordre de présentation conseillé)

### D1 — Transaction normale (le système laisse passer)
- **VIREMENT_LOCAL · 2 500 TND · Tunisia → Tunisia** · motif « Paiement facture électricité » · justificatif coché + joindre `recu_paiement_2500_tnd.txt`
- **Attendu** : document RECEVABLE · L1 PASS · décision PASS · état APPROUVED · ML `PROFIL_MATURE` (grâce au seed) → le ML est évalué, pas neutralisé.

### D2 — Seuil STR sans justificatif (la déclaration ne suffit pas)
- **SWIFT · 12 000 USD · Tunisia → France** · bénéficiaire `Societe Lumiere SARL` · motif « Paiement facture export » · case justificatif **décochée**, aucun fichier
- **Attendu** : L1 → seuil SWIFT >10 000 USD sans justificatif → **STR** · file superviseur.

### D3 — Même transaction AVEC la facture (le justificatif est VÉRIFIÉ, pas déclaré) ⭐
- Même transaction que D2, justificatif coché + joindre **`facture_export_12000_usd.pdf`**
- **Attendu** : étape 0 → docling extrait la facture (tableau de marchandises), montant 12 000 USD ✓, compte 802559360 ✓, bénéficiaire ✓, date ✓ → **RECEVABLE** → la transaction devient « justifiée » → décision **EDD** au lieu de STR.
- **Phrase jury** : « le justificatif n'est plus déclaratif : le document est lu, vérifié champ par champ, et jugé par un LLM contraint aux faits ».

### D4 — Document HORS-SUJET (anti-fraude déclarative) ⭐
- Même transaction que D2, justificatif coché + joindre **`attestation_travail.docx`** (aucun montant, aucun lien avec l'opération)
- **Attendu** : NON RECEVABLE (montant absent ❌) → traitée comme **non justifiée** → **STR** malgré la case cochée.

### D5 — Contrat Word avec tableau (vitrine docling)
- **VIREMENT_LOCAL · 15 000 TND · Tunisia → Tunisia** · bénéficiaire `Bureau Etudes Manar SARL` · motif « Acompte contrat prestation » + joindre **`contrat_prestation_15000_tnd.docx`**
- **Attendu** : docling parse le .docx et son **tableau échéancier** · montant/parties/dates reconnus → RECEVABLE. Montre l'analyse sémantique (type détecté : contrat).

### D6 — Smurfing : la mémoire Redis ⭐
3 soumissions successives, même client `802559360`, type **DEPOT_ESPECES** :
1. **4 800 TND** → RECORD/PASS
2. **4 500 TND** → RECORD/PASS
3. **4 900 TND** → **STR + violation [SMURFING]** (3+ dépôts < 5 000 TND en 72 h)
- **Phrase jury** : « chaque dépôt est légal isolément — c'est la mémoire glissante Redis qui révèle le fractionnement ».

### D7 — Liste noire FATF (blocage dur)
- **SWIFT · 20 000 USD · Tunisia → Iran**
- **Attendu** : alerte **rouge dès le formulaire** · L1 → **BLOCK** (aucun LLM ne peut passer outre : « le symbolique prévaut »).

### D8 — Liste grise FATF (vigilance renforcée)
- **SWIFT · 8 000 USD · Tunisia → Algeria** · justificatif décoché
- **Attendu** : alerte ambre au formulaire · signal `COUNTRY_GREY` → questions EDD ciblées au Planning → décision EDD.

### D9 — Structuring à l'unité
- **VIREMENT_LOCAL · 9 900 TND** (99 % du seuil)
- **Attendu** : alerte « juste sous le seuil » au formulaire · signal `AMOUNT_JUST_BELOW_THRESHOLD` · pattern SAR **STRUCTURING**.

### D10 — Profil client PEP (risque structurel)
- Compte USER : **Mon profil** → cocher PEP « Étrangère » → enregistrer, puis soumettre **SWIFT 8 000 USD → France** (bénigne)
- **Attendu** : Client Scoring **ÉLEVÉ** (pep_foreign +35) → plancher de diligence → **EDD** même sur transaction anodine (approche par les risques FATF R12).

### D11 — Déblocage d'un dossier EDD par justificatif a posteriori
- Reprendre un dossier **« En cours — justificatifs »** (ex. D8 ou un VIREMENT 9 000 TND vers `Ste El Amen Distribution`) → Pipeline Live → carte **Justificatifs** → uploader **`attestation_origine_fonds_9000_tnd.pdf`**
- **Attendu** : RECEVABLE → dossier bascule **PENDING_REVIEW** (revue superviseur).

### D12 — RIB/IBAN dynamique
- Dans le formulaire : pays destination **France** → saisir un IBAN de 20 caractères → **erreur « IBAN français : FR + 25 → 27 caractères »** (soumission bloquée) ; pays **Tunisia** → RIB à lettres → refusé (20 chiffres exigés).

### D13 — Segregation of duties + piste d'audit ⭐
1. Connecté **ADMIN** : la « File superviseur » n'apparaît plus dans le menu (et l'API renvoie 403).
2. Connecté **COMPLIANCE_OFFICER** : file superviseur → approuver D3 avec annotation (`CORRECT`) et rejeter D4 (`TRUE_FRAUD`) → feedback RLHF enregistré.
3. Connecté **AUDITOR** : page **Investigation** → piste d'audit (qui / quoi / quand / annotation) + indicateurs qualité.

### D14 — Cas limite (pour les questions du jury)
- Joindre **`facture_perimee_2023.pdf`** à une SWIFT 12 000 USD : montant ✓ noms ✓ mais **datée de 2023** → la date récente échoue et le LLM doit relever la péremption. Bon support pour discuter des limites/du jugement LLM.

---

## 📎 Correspondance documents ↔ transactions

| Document | Format | Transaction associée | Rôle dans la démo |
|---|---|---|---|
| `facture_export_12000_usd.pdf` | PDF + tableau | SWIFT 12 000 USD → France (`Societe Lumiere SARL`) | justificatif RECEVABLE (D3) |
| `contrat_prestation_15000_tnd.docx` | Word + tableau | VIREMENT 15 000 TND (`Bureau Etudes Manar SARL`) | vitrine docling .docx (D5) |
| `attestation_origine_fonds_9000_tnd.pdf` | PDF | VIREMENT/EDD 9 000 TND (`Ste El Amen Distribution`) | déblocage a posteriori (D11) |
| `attestation_travail.docx` | Word | n'importe laquelle | document NON PROBANT (D4) |
| `facture_perimee_2023.pdf` | PDF | SWIFT 12 000 USD | cas limite date (D14) |
| `recu_paiement_2500_tnd.txt` | TXT | VIREMENT 2 500 TND (`Ste Services Plus`) | cas nominal (D1) |

⚠️ **Conseils** : (1) tester D3 une fois AVANT la démo — le premier passage docling charge ses
modèles (~30-60 s), les suivants sont rapides ; (2) le champ « Client émetteur » doit contenir
`802559360` pour que les documents matchent ; (3) saisir les bénéficiaires exactement comme
dans le tableau ci-dessus (la vérification cherche les noms dans le document).
