# 📋 DÉCLARATION DE SOUPÇON — SAR-FA14FDF6

**Date de génération** : 2026-06-28T01:33:04  
**Banque déclarante**  : Banque Tunisienne (PFE 2025-2026)  
**Décision système**   : **STR**  
**Niveau de risque**   : 🟠 **ÉLEVÉ** (score combiné 78.6/100)  

## 🎯 Recommandation pour l'admin

> **DÉCLARER À LA CTAF — Soupçon de blanchiment, déclaration obligatoire dans les 48h**

---

## 📊 Scores détaillés

| Composant | Score | Décision |
|---|---|---|
| **Compliance Agent** (règles BCT/FATF) | 75/100 | STR |
| **ML Fraud Detector** (LightGBM + LSTM-AE) | 84/100 | FRAUDE_SUSPECTÉE |
| **Client Scoring** (profil structurel) | — | MOYEN |
| **Score combiné** (formule pondérée) | **79/100** | **ÉLEVÉ** |

*Formule : `max(score_compliance, 0.6 × compliance + 0.4 × ml)` — Le ML ne peut jamais rabaisser une décision règle.*

## 💳 Détails de la transaction

| Champ | Valeur |
|---|---|
| Type | DEPOT_ESPECES |
| Montant | **4,700 TND** |
| Émetteur | Mohamed Trabelsi (Tunisia) |
| Bénéficiaire | Banque BCT (Tunisia) |
| Motif | Dépôt en espèces |
| Justification | ❌ NON |
| Horodatage | 2026-05-18T16:45:00 |

## 👤 Profil client (risque structurel)

| Critère | Valeur |
|---|---|
| Identifiant | CLT-S3-002 |
| Niveau de risque | **MOYEN** (score 45/100) |
| PEP (Personne Politiquement Exposée) | ✅ non |
| KYC complet | ❌ INCOMPLET |
| STR antérieurs | ⚠️ OUI |

**Facteurs de risque détectés** : prior_strs, incomplete_kyc

## ⚖️ Obligations VIOLÉES

**2 obligation(s) réglementaire(s) non respectée(s)** :

| # | Source | Article | FATF | Sévérité | Texte |
|---|---|---|---|---|---|
| 1 | ⚖️ BCT `bct_circulaire_2006_19.pdf` | Article 8 | R20 ✓✓ | **HIGH** | Détection de fractionnement : 3 dépôts sous 5 000 TND en 24h pour le même client... |
| 2 | ⚖️ BCT `loi_2015_26.pdf` | Article 107 | R20 | **HIGH** | Déclaration de soupçon à la CTAF obligatoire pour toute opération inhabituelle... |

## ✅ Obligations RESPECTÉES

**4 obligation(s) vérifiée(s) et conforme(s)** :

| # | Source | Article | FATF | Statut |
|---|---|---|---|---|
| 1 | ⚖️ BCT `bct_circulaire_2017_08` | Art.43 | R20 | ✅ CONFORME |
| 2 | ⚖️ BCT `loi_2003_75` | Art.7 | R10 | ✅ CONFORME |
| 3 | ⚖️ BCT `bct_circulaire_2006_19` | Art.10 | R10 | ✅ CONFORME |
| 4 | ⚖️ BCT `bct_circulaire_2017_08` | Art.45 | R11 | ✅ CONFORME |

**Couverture réglementaire :**
- FATF : 92.0%
- BCT (contraignant) : 85.0%

## 🤖 Analyse Machine Learning (LightGBM + LSTM-AE)

**Probabilité de fraude** : 84.0/100  
**Décision ML** : FRAUDE_SUSPECTÉE

### Facteurs explicatifs (SHAP — Top 5)

| Rang | Feature | Valeur | Impact SHAP | Pousse vers | Interprétation métier |
|---|---|---|---|---|---|
| 1 | `sender_fanout_ratio` | 0.92 | +11.20 | 🔴 FRAUDE | Émetteur diversifie vers de nombreux récepteurs (pattern smurfing) |
| 2 | `is_round_100` | 0 | +4.30 | 🔴 FRAUDE | Indicateur statistique |
| 3 | `is_cash` | 1 | +3.80 | 🔴 FRAUDE | Indicateur statistique |
| 4 | `reconstruction_error` | 2.1 | +2.90 | 🔴 FRAUDE | Séquence temporelle anormale détectée par l'autoencoder LSTM |
| 5 | `amount_vs_sender_mean` | 1.6 | +1.50 | 🔴 FRAUDE | Montant anormal par rapport à l'historique de l'émetteur |

*Lecture : SHAP > 0 pousse vers FRAUDE, SHAP < 0 pousse vers NORMAL.*

## 📎 Documents requis pour la décision

Pour valider ou refuser cette transaction, l'admin doit obtenir les documents suivants du client :

1. Justificatif économique de l'opération (facture, contrat)
2. Dossier KYC complet (CIN, justificatif domicile)
3. Formulaire goAML pour transmission CTAF

## 📝 Synthèse narrative

La transaction DEPOT_ESPECES d'un montant de 4,700 TND de Mohamed Trabelsi (Tunisia) vers Banque BCT (Tunisia) a été évaluée par le système AML/KYC.

**Décision système** : STR (score combiné 78.6/100, niveau ÉLEVÉ).

**Profil client** : niveau de risque structurel MOYEN (score 45/100). PEP : non. KYC : INCOMPLET. STR antérieurs : OUI.

**2 obligation(s) violée(s)** :
  • [⚖️BCT] bct_circulaire_2006_19.pdf Article 8 (réf. FATF R20) : Détection de fractionnement : 3 dépôts sous 5 000 TND en 24h pour le même client
  • [⚖️BCT] loi_2015_26.pdf Article 107 (réf. FATF R20) : Déclaration de soupçon à la CTAF obligatoire pour toute opération inhabituelle

**Analyse ML** : probabilité de fraude 84.0/100 (FRAUDE_SUSPECTÉE).
Facteurs ML dominants :
  • sender_fanout_ratio (FRAUDE) : Émetteur diversifie vers de nombreux récepteurs (pattern smurfing)
  • is_round_100 (FRAUDE) : Indicateur statistique
  • is_cash (FRAUDE) : Indicateur statistique

**Recommandation** : DÉCLARER À LA CTAF — Soupçon de blanchiment, déclaration obligatoire dans les 48h

---

## 🛂 Actions admin

- [ ] **APPROUVER** — Transaction conforme, exécuter
- [ ] **REFUSER** — Bloquer la transaction définitivement
- [ ] **DEMANDER DOCUMENTS** — Suspendre en attente de justificatifs (voir section 7)
- [ ] **ANNOTER** — Faux positif / vraie fraude / autre commentaire

---

*Rapport généré automatiquement par le système AML/KYC Agentic RAG v6.0 — Mariem Louhichi, PFE 2025-2026*