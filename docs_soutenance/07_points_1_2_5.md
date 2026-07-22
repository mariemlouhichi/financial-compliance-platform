# 📋 État du projet — Points 1, 2, 5 (faits)

> Document de mise à jour / handoff. À donner à Claude (ou au jury) pour être à jour.
> Date : 2026-06-26. Répertoire : `C:\Users\louhi\Downloads\projet_pfe\02_chunking_v2\`.
> Tests via `venv/Scripts/python.exe -m pytest`.

## 🔖 Contexte
Backlog de 6 points restants. **Faits : Point 2, Point 1, Point 5.**
Reste : Point 4 (ML + SAR), Point 6 (SQLite / dashboard / RLHF), Point 3 (LangGraph).

---

## ✅ POINT 2 — Réconciliation source-aware (« Option complète »)

**Problème** : dans le Compliance Agent, la réconciliation symbolique↔sémantique était
indexée sur `fatf_rec` seul. Crainte : « le système ne traite que la FATF ». Réalité :
`fatf_rec` est un **index de classification**, pas un filtre de source (la vraie source est
dans `source_doc` / `is_binding`). On voulait rendre la réconciliation **consciente de la source**.

**Fait** (fichier modifié : `compliance_agent.py`) :
- `ComplianceRule` : 2 nouveaux champs → `cross_source_validated` (corroboré par une **autre**
  famille de source) + `corroborating_families` (liste des familles qui confirment).
- `reconcile()` : **garde** le regroupement par `fatf_rec` (préserve la corroboration
  **inter-sources** BCT↔FATF = preuve indépendante forte) MAIS la **clé secondaire**
  `(fatf_rec, source_family, ref)` au complément + dédup → une obligation BCT et une obligation
  FATF distinctes sous la même rec ne sont **jamais fusionnées**. Lors d'un match cross-validé,
  on enregistre les familles et on marque `cross_source_validated` si elles diffèrent.
- `ComplianceDecision.source_coverage` (nouveau champ : `rec → [familles]`) + stat
  `cross_source_validated`.
- `_print` : affiche `🔗 confirmé inter-sources` sur les violations + `Confirmées par ≥2
  familles : R19(BCT+UE)`.

**Décisions inchangées** (boost de confiance maintenu à +0.10).

**Validé** : 3 cas (inter-source BCT↔FATF ; complément fractionnement BCT 2006-19 gardé séparé ;
intra-famille BCT+loi) ; 31/31 tests ; standalone OK.

**Argument jury** : *« Sous une même rec FATF, je distingue l'obligation contraignante BCT de sa
référence internationale ; quand les deux concordent, c'est une corroboration inter-sources
(preuve indépendante). »*

---

## ✅ POINT 1 — État « en cours » + justificatifs + notification + graine feedback

**Problème** : le Compliance Agent produisait une **décision** mais pas un **workflow**.
Manquaient : état du cycle de vie de la transaction, demande de justificatifs au client,
notification, file superviseur, amélioration continue.

**Fait** (nouveau module **autonome** `decision_workflow.py`, consomme `ComplianceDecision` en
duck-typing, **ne modifie pas** le compliance agent, 0 dépendance Docker/LLM) :
- **Machine à états `TxState`** : `PASS→APPROVED` (auto) · `BLOCK→BLOCKED` (auto) ·
  `REVIEW/EDD→PENDING_DOCUMENTS` (si justif. manquants) sinon `PENDING_REVIEW` ·
  `STR→PENDING_REVIEW`+`str_required`. Les `PENDING_*` = **« en cours »**.
- **`RequiredDocumentEngine`** (déterministe, **chaque doc sourcé**) : catalogue `_DOC_CATALOG` —
  ECONOMIC_JUSTIFICATION/R10, KYC_IDENTITY/R10, SOURCE_OF_FUNDS/R10, PEP_DECLARATION/R12,
  BENEFICIAL_OWNER/R24, BUSINESS_RELATIONSHIP/R16, INTERMEDIARY_BANK_DD/R13. Volet **FISCAL
  séparé** (FATCA_W9, CRS_SELF_CERT) marqué `category=FISCAL`, **jamais** AML. Dérive des lacunes
  tx + recs FATF violées + contexte client.
- **`Notification` + `NotificationSink`** (Console / Jsonl / Multi) → branchable email/Kafka/UI
  sans changer le code.
- **`SupervisorQueue`** (file « en cours » in-memory) + `record_decision(APPROVE/REJECT/ANNOTATE)`
  → **`FeedbackEvent`** persisté `output/supervisor_feedback.jsonl` = **graine du RLHF** (Point 6).
- Orchestrateur `DecisionWorkflow.process(tx, decision) → WorkflowOutcome`.

**Petit ajout `compliance_agent.py`** : `client_context["is_pep"]` (dérivé des `components`
pep_foreign/domestic) pour que le workflow demande la déclaration R12.

**Câblé dans `test_integration.py`** : `run_scenario(..., workflow=)` étape 7 après compliance +
résumé file superviseur dans le récap.

**Validé** : nouveau `test_decision_workflow.py` **10/10** + 31/31 compliance (0 régression).
Démo : EDD+PEP → PENDING_DOCUMENTS avec [R10 KYC, R12 PEP, R10 origine fonds] + 1 FISCAL séparé →
file superviseur ; STR → PENDING_REVIEW ; BLOCK → alerte officier.

**Reste (→ Point 6)** : persistance SQLite (remplace JSONL), API FastAPI, dashboard React+Keycloak,
**3 vraies boucles RLHF** (aujourd'hui le feedback est seulement *stocké*, il ne réinjecte rien).

---

## ✅ POINT 5 — Redis 24h + Kafka + 30j (historique client) — partie Redis/historique faite

**Problème** : le smurfing et l'analyse comportementale ont besoin de **mémoire**. Avant,
`customer_history` était passé **manuellement** par les scénarios. En prod, chaque transaction
arrive **séparément** → il faut un magasin qui agrège.

**Fait** (nouveau module `client_history_store.py`, **repli gracieux** comme Neo4j) :
- **2 backends** : `_RedisBackend` (sorted set `aml:txwin:{client}`, scoré par timestamp,
  **TTL 90j**) + `_InMemoryBackend` (repli si Redis absent). Fabrique `get_history_store()`.
- **API** : `record(tx)`/`ingest(tx)` (ingestion, `ingest` = seam **consumer Kafka**) ;
  `recent_deposits(cid, 72h)` → format `[{amount,currency,timestamp}]` **consommé tel quel** par
  Level 1 `_detect_smurfing` ; `behavioral_profile(cid)` → `BehavioralProfile` (velocity_24h,
  count_30j, volume_30j en TND via FX Level 1, virements intl, **pays récurrents 90j**, STR passés,
  first_seen_days) ; `is_new_receiver()` ; `enrich(tx)`.
- **2 fenêtres** : 24-72h (structuring intraday) + 30-90j (profil).
- **docker-compose** : service `redis:7-alpine` (port 6379, appendonly, healthcheck) + volume
  `redis_data`. `redis==8.0.1` ajouté à `requirements.txt` et installé dans le venv.

**Validé** : `demo_history_smurfing.py` → 3 dépôts 4800 TND séparés : #1 RECORD, #2 RECORD,
**#3 STR (smurfing)** — visible UNIQUEMENT grâce à la mémoire. `test_client_history_store.py`
**9/9**. Suite globale **50 passed** (31+10+9), 7 skip Docker.

**Choix honnêtes (jury)** :
1. le **30j est replié sur Redis** (rétention 90j) ; déport vers TimescaleDB = amélioration prod
   documentée.
2. **Kafka** : seam `ingest()` prêt, brancher un vrai broker = détail de transport.
3. **Pas câblé dans `test_integration.py`** volontairement (store vide casserait S3 qui dépend de
   son `customer_history` inline) → wiring live au **Point 3 (LangGraph)** ou **Point 6 (API)**.

---

## 📂 Fichiers créés / modifiés (récap)

| Fichier | Type | Point |
|---|---|---|
| `compliance_agent.py` | modifié | 2 (réconciliation) + 1 (is_pep) |
| `decision_workflow.py` | **créé** | 1 |
| `test_decision_workflow.py` | **créé** | 1 |
| `client_history_store.py` | **créé** | 5 |
| `test_client_history_store.py` | **créé** | 5 |
| `demo_history_smurfing.py` | **créé** | 5 |
| `test_integration.py` | modifié | 1 (étape workflow) |
| `docker-compose.yml` | modifié | 5 (service redis) |
| `requirements.txt` | modifié | 5 (redis==8.0.1) |

**Tests : 50 passed, 7 skipped (Docker).**

---

## ▶️ Commandes de démonstration

```powershell
# Point 1 — workflow post-décision (standalone, sans Docker)
venv/Scripts/python.exe decision_workflow.py

# Point 5 — smurfing via mémoire glissante (standalone, sans Docker)
venv/Scripts/python.exe demo_history_smurfing.py

# Point 2 — réconciliation source-aware visible dans le reporting
venv/Scripts/python.exe compliance_agent.py     # ligne « Confirmées par ≥2 familles »

# Tests
venv/Scripts/python.exe -m pytest test_compliance_agent.py test_decision_workflow.py test_client_history_store.py -q
```

---

## ⏭️ Prochaines étapes (ordre recommandé)
- **Point 4** — ML fraud detection + SAR generator (rapport détaillé pour l'admin).
- **Point 6** — SQLite + API FastAPI + dashboard + 3 boucles RLHF (consomme les `FeedbackEvent` déjà émis).
- **Point 3** — Orchestration LangGraph (branche aussi le store historique en flux réel).
