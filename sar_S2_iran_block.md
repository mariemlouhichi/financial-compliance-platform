# 📋 DÉCLARATION DE SOUPÇON — SAR-AD41E58A

**Date de génération** : 2026-06-28T01:33:04  
**Banque déclarante**  : Banque Tunisienne (PFE 2025-2026)  
**Décision système**   : **BLOCK**  
**Niveau de risque**   : 🔴 **CRITIQUE** (score combiné 100.0/100)  

## 🎯 Recommandation pour l'admin

> **REFUSER — Sanctions ou interdictions BCT/ONU violées**

---

## 📊 Scores détaillés

| Composant | Score | Décision |
|---|---|---|
| **Compliance Agent** (règles BCT/FATF) | 100/100 | BLOCK |
| **ML Fraud Detector** (LightGBM + LSTM-AE) | 97/100 | FRAUDE_SUSPECTÉE |
| **Client Scoring** (profil structurel) | — | FAIBLE |
| **Score combiné** (formule pondérée) | **100/100** | **CRITIQUE** |

*Formule : `max(score_compliance, 0.6 × compliance + 0.4 × ml)` — Le ML ne peut jamais rabaisser une décision règle.*

## 💳 Détails de la transaction

| Champ | Valeur |
|---|---|
| Type | SWIFT |
| Montant | **21,800 USD** |
| Émetteur | Eric Badege (Congo) |
| Bénéficiaire | Iran National Bank (Iran) |
| Motif |  |
| Justification | ❌ NON |
| Horodatage | 2026-05-19T03:15:00 |

## 👤 Profil client (risque structurel)

| Critère | Valeur |
|---|---|
| Identifiant | CLT-S2-001 |
| Niveau de risque | **FAIBLE** (score 10/100) |
| PEP (Personne Politiquement Exposée) | ✅ non |
| KYC complet | ✅ OUI |
| STR antérieurs | ✅ aucun |

## ⚖️ Obligations VIOLÉES

**3 obligation(s) réglementaire(s) non respectée(s)** :

| # | Source | Article | FATF | Sévérité | Texte |
|---|---|---|---|---|---|
| 1 | ⚖️ BCT `bct_circulaire_2017_08_aml.pdf` | Article 43 | R6 ✓✓ | **CRITICAL** | Transactions avec pays sous sanctions ONU interdites — gel automatique des fonds... |
| 2 | 🌐 FATF `fatf_recommendations_2024.pdf` | Recommendation 19 | R19 | **CRITICAL** | Iran figure sur la liste FATF des juridictions à haut risque (countermeasures)... |
| 3 | ⚖️ BCT `bct_circulaire_2006_19.pdf` | Article 12 | R20 | **HIGH** | Obligation de déclaration de soupçon à la CTAF dans les 48h pour toute transacti... |

## ✅ Obligations RESPECTÉES

**5 obligation(s) vérifiée(s) et conforme(s)** :

| # | Source | Article | FATF | Statut |
|---|---|---|---|---|
| 1 | ⚖️ BCT `bct_circulaire_2017_08` | Art.43 | R6 | ✅ CONFORME |
| 2 | ⚖️ BCT `bct_circulaire_2006_19` | Art.5 | R12 | ✅ CONFORME |
| 3 | ⚖️ BCT `loi_2003_75` | Art.7 | R10 | ✅ CONFORME |
| 4 | ⚖️ BCT `bct_circulaire_2017_08` | Art.45 | R11 | ✅ CONFORME |
| 5 | 🌐 FATF `fatf_recommendations_2024` | R.16 | R16 | ✅ CONFORME |

**Couverture réglementaire :**
- FATF : 94.7%
- BCT (contraignant) : 84.1%

## 🤖 Analyse Machine Learning (LightGBM + LSTM-AE)

**Probabilité de fraude** : 97.0/100  
**Décision ML** : FRAUDE_SUSPECTÉE

### Facteurs explicatifs (SHAP — Top 5)

| Rang | Feature | Valeur | Impact SHAP | Pousse vers | Interprétation métier |
|---|---|---|---|---|---|
| 1 | `reconstruction_error` | 6.2 | +13.40 | 🔴 FRAUDE | Séquence temporelle anormale détectée par l'autoencoder LSTM |
| 2 | `currency_mismatch` | 1 | +8.10 | 🔴 FRAUDE | Conversion de devise (layering possible) |
| 3 | `is_night` | 1 | +3.20 | 🔴 FRAUDE | Transaction effectuée la nuit |
| 4 | `sender_fanout_ratio` | 0.85 | +2.10 | 🔴 FRAUDE | Émetteur diversifie vers de nombreux récepteurs (pattern smurfing) |
| 5 | `amount_vs_sender_mean` | 4.5 | +1.70 | 🔴 FRAUDE | Montant anormal par rapport à l'historique de l'émetteur |

*Lecture : SHAP > 0 pousse vers FRAUDE, SHAP < 0 pousse vers NORMAL.*

## 📎 Documents requis pour la décision

Pour valider ou refuser cette transaction, l'admin doit obtenir les documents suivants du client :

1. Justification économique impérative (refus si absent)
2. Documentation du donneur d'ordre et du bénéficiaire effectif
3. Formulaire goAML pour transmission CTAF dans les 48h

## 📝 Synthèse narrative

La transaction SWIFT d'un montant de 21,800 USD de Eric Badege (Congo) vers Iran National Bank (Iran) a été évaluée par le système AML/KYC.

**Décision système** : BLOCK (score combiné 100.0/100, niveau CRITIQUE).

**Profil client** : niveau de risque structurel FAIBLE (score 10/100). PEP : non. KYC : complet. STR antérieurs : non.

**3 obligation(s) violée(s)** :
  • [⚖️BCT] bct_circulaire_2017_08_aml.pdf Article 43 (réf. FATF R6) : Transactions avec pays sous sanctions ONU interdites — gel automatique des fonds requis
  • [FATF] fatf_recommendations_2024.pdf Recommendation 19 (réf. FATF R19) : Iran figure sur la liste FATF des juridictions à haut risque (countermeasures)
  • [⚖️BCT] bct_circulaire_2006_19.pdf Article 12 (réf. FATF R20) : Obligation de déclaration de soupçon à la CTAF dans les 48h pour toute transaction suspecte

**Analyse ML** : probabilité de fraude 97.0/100 (FRAUDE_SUSPECTÉE).
Facteurs ML dominants :
  • reconstruction_error (FRAUDE) : Séquence temporelle anormale détectée par l'autoencoder LSTM
  • currency_mismatch (FRAUDE) : Conversion de devise (layering possible)
  • is_night (FRAUDE) : Transaction effectuée la nuit

**Recommandation** : REFUSER — Sanctions ou interdictions BCT/ONU violées

---

## 🛂 Actions admin

- [ ] **APPROUVER** — Transaction conforme, exécuter
- [ ] **REFUSER** — Bloquer la transaction définitivement
- [ ] **DEMANDER DOCUMENTS** — Suspendre en attente de justificatifs (voir section 7)
- [ ] **ANNOTER** — Faux positif / vraie fraude / autre commentaire

---

*Rapport généré automatiquement par le système AML/KYC Agentic RAG v6.0 — Mariem Louhichi, PFE 2025-2026*