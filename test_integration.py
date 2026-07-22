"""
test_integration.py
====================
Test end-to-end : Level 1 → Planning Agent → Retrieval Agent → Compliance Agent

Flux complet :
  Transaction dict                          Client KYC (profil durable, optionnel)
      ↓                                          ↓
  Level1Orchestrator.check_transaction()    ClientScoringAgent.score_client()
      ↓ Level1Result (decision, risk_signals)   ↓ ClientRiskScore (level, FATCA/CRS)
      │                                          ↓ client_score_to_signals()
      └────────── fusion des signaux ───────────┘
      ↓ risk_signals (événementiels) + signaux client (structurels)
  PlanningAgent.run(transaction, risk_signals, customer_history)
      ↓ PlanningResult (3-7 questions calibrées — PEP → R12 ciblée)
  RetrievalPipeline.retrieve(question) × N
      ↓ 5 chunks × N questions
  Dédoublonnage par (source_doc, article_ref)
      ↓ ~10-20 chunks uniques, triés par score
  ComplianceAgent.analyze(transaction, chunks, risk_signals, fatf_checklist,
                          client_profile)   ← plancher de diligence + contribution
      ↓ ComplianceDecision (decision, risk_score, violated_rules, audit_trail)

PRÉREQUIS :
  - docker compose up -d          (Weaviate + Neo4j)
  - python reindex_weaviate.py    (si pas fait récemment)
  - pip install sentence-transformers weaviate-client neo4j

USAGE :
  python test_integration.py                    # 4 scénarios par défaut
  python test_integration.py --scenario 2       # scénario 2 seulement
  python test_integration.py --no-rerank        # sans reranker (plus rapide)
  python test_integration.py --verbose          # détails de chaque étape
"""

import json
import sys
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

# Ajouter le dossier courant au path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "level1_rules"))

from level1_rules.level1_orchestrator import Level1Orchestrator
from planning_agent import PlanningAgent
from retrieval_agent import RetrievalPipeline
from compliance_agent import ComplianceAgent
from client_scoring_agent import (ClientScoringAgent, ClientProfile,
                                   client_score_to_signals)
from decision_workflow import DecisionWorkflow, JsonlNotificationSink

try:
    from neo4j import GraphDatabase as _GraphDatabase
    _NEO4J_AVAILABLE = True
except ImportError:
    _NEO4J_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════
#  MAPPING DES CHAMPS (Level 1 ↔ Planning Agent)
# ═══════════════════════════════════════════════════════════════════════════
#
# Level 1 utilise :  tx_type, beneficiary_name, destination_country
# Planning utilise :  transaction_type, receiver_name, receiver_country
#
# Ce mapper harmonise les deux interfaces.

def l1_to_planning(tx: dict) -> dict:
    """Convertit un dict Level 1 vers le format Planning Agent."""
    return {
        "transaction_type":   tx.get("tx_type", "SWIFT"),
        "amount":             tx.get("amount", 0),
        "currency":           tx.get("currency", ""),
        "timestamp":          tx.get("timestamp"),
        "motive":             tx.get("motive", ""),
        "sender_name":        tx.get("sender_name", ""),
        "sender_country":     tx.get("sender_country", ""),
        "sender_id_type":     tx.get("sender_id_type", "CIN"),
        "sender_account_id":  tx.get("sender_account_id", ""),
        "receiver_name":      tx.get("beneficiary_name", ""),
        "receiver_country":   tx.get("destination_country", ""),
        "receiver_account_id":tx.get("receiver_account_id", ""),
        "has_justification":  tx.get("has_justification"),
        "justification_document": tx.get("justification_document"),
        "intermediary_bank":  tx.get("intermediary_bank"),
        "conversion_rate":    tx.get("conversion_rate"),
        "converted_amount":   tx.get("converted_amount"),
        "converted_currency": tx.get("converted_currency"),
        "operator":           tx.get("operator", ""),
        "withdrawal_mode":    tx.get("withdrawal_mode", ""),
        "notes":              tx.get("notes", ""),
        "denomination":       tx.get("denomination", ""),
        "branch_id":          tx.get("branch_id", ""),
    }


def infer_jurisdiction(tx: dict) -> str:
    """Déduit la juridiction pour le Retrieval Agent."""
    sc = (tx.get("sender_country") or "").lower()
    dc = (tx.get("destination_country") or "").lower()
    if sc == "tunisia" or dc == "tunisia":
        return "Tunisia"
    return None  # pas de filtre → tous les docs


# Recommandations FATF pertinentes par type de transaction
_FATF_BY_TX = {
    "SWIFT":          ["R6", "R10", "R11", "R12", "R13", "R16", "R19", "R20", "R24"],
    "VIREMENT_LOCAL": ["R10", "R11", "R13", "R20"],
    "DEPOT_ESPECES":  ["R10", "R11", "R20"],
    "MANDAT":         ["R10", "R14", "R16", "R20"],
}

class _Rec:
    """Wrapper minimal pour un identifiant FATF."""
    def __init__(self, rid: str): self.rec_id = rid

def build_fatf_checklist(tx_type: str) -> list:
    """Retourne la checklist FATF pour un type de transaction."""
    return [_Rec(r) for r in _FATF_BY_TX.get(tx_type, _FATF_BY_TX["SWIFT"])]


# ═══════════════════════════════════════════════════════════════════════════
#  SCÉNARIOS DE TEST
# ═══════════════════════════════════════════════════════════════════════════

SCENARIOS = [

    # ── S1 : Transaction normale — doit passer vite, peu de chunks ────────
    {
        "name": "S1 — VIREMENT_LOCAL normal (attendu: PASS)",
        "transaction": {
            "tx_type": "VIREMENT_LOCAL",
            "amount": 5_000, "currency": "TND",
            "sender_name": "Alice Dupont",
            "beneficiary_name": "Bob Martin",
            "sender_country": "Tunisia",
            "destination_country": "Tunisia",
            "motive": "Paiement facture fournisseur",
        },
        "customer_history": None,
        "expected_decision": "PASS",
        "expected_min_questions": 3,
        "expected_min_chunks": 5,
    },

    # ── S2 : SWIFT haute criticité — sanctions + Iran + gros montant ──────
    {
        "name": "S2 — SWIFT Iran sanctions ONU (attendu: BLOCK)",
        "transaction": {
            "tx_type": "SWIFT",
            "amount": 21_800, "currency": "USD",
            "sender_name": "Eric Badege",
            "beneficiary_name": "Iran National Bank",
            "sender_country": "Congo",
            "destination_country": "Iran",
            "has_justification": False,
            "intermediary_bank": "CITIUS33",
            "timestamp": "2026-05-19T03:15:00",
        },
        "customer_history": {
            "previous_strs": 1,
            "velocity_24h": 3,
            "transaction_count_30d": 5,
            "total_volume_30d": 45_000,
            "avg_amount_30d": 9_000,
            "account_age_days": 120,
            "countries_last_90d": ["Congo", "France"],
            "new_receiver": True,
            "previous_rejected": False,
        },
        "expected_decision": "BLOCK",
        "expected_min_questions": 5,
        "expected_min_chunks": 15,
    },

    # ── S3 : DEPOT_ESPECES smurfing — billets neufs + historique ──────────
    {
        "name": "S3 — DEPOT smurfing + billets neufs (attendu: STR)",
        "transaction": {
            "tx_type": "DEPOT_ESPECES",
            "amount": 4_700, "currency": "TND",
            "sender_name": "Mohamed Trabelsi",
            "beneficiary_name": "Banque BCT",
            "sender_country": "Tunisia",
            "destination_country": "Tunisia",
            "notes": "Billets neufs, client nerveux",
            "branch_id": "AGENCE_TUNIS_CENTRE",
            "timestamp": "2026-05-18T16:45:00",
            "customer_history": [
                {"amount": 4_500, "currency": "TND",
                 "timestamp": (datetime.now() - timedelta(hours=10)).isoformat()},
                {"amount": 4_800, "currency": "TND",
                 "timestamp": (datetime.now() - timedelta(hours=5)).isoformat()},
            ],
        },
        "customer_history": {
            "previous_strs": 0,
            "velocity_24h": 3,
            "transaction_count_30d": 12,
            "total_volume_30d": 35_000,
            "avg_amount_30d": 2_900,
            "account_age_days": 60,
            "new_receiver": False,
            "previous_rejected": True,
        },
        "expected_decision": "STR",
        "expected_min_questions": 5,
        "expected_min_chunks": 10,
    },

    # ── S4 : MANDAT Western Union suspect ─────────────────────────────────
    {
        "name": "S4 — MANDAT WU 4000 TND suspect (attendu: STR)",
        "transaction": {
            "tx_type": "MANDAT",
            "amount": 4_000, "currency": "TND",
            "sender_name": "Alice Dupont",
            "beneficiary_name": "Bob Martin",
            "sender_country": "Tunisia",
            "destination_country": "Tunisia",
            "motive": "Aide familiale",
            "operator": "WESTERN_UNION",
            "withdrawal_mode": "CASH",
        },
        "customer_history": {
            "previous_strs": 0,
            "velocity_24h": 0,
            "transaction_count_30d": 2,
            "total_volume_30d": 6_000,
            "avg_amount_30d": 3_000,
            "account_age_days": 365,
            "new_receiver": True,
            "previous_rejected": False,
        },
        "expected_decision": "STR",
        "expected_min_questions": 4,
        "expected_min_chunks": 10,
    },

    # ── S5 : Transaction BÉNIGNE mais CLIENT à haut risque (PEP étranger) ──
    #         Démontre l'approche par les risques FATF : le PROFIL CLIENT
    #         escalade une transaction que Level 1 laisse passer.
    #         • Level 1 (transaction seule)      → PASS
    #         • Planning (signaux client fusionnés) → question R12 ciblée PEP
    #         • Compliance (client_profile=ÉLEVÉ)   → plancher de diligence EDD
    {
        "name": "S5 — SWIFT normal + CLIENT PEP étranger (L1=PASS → CA=EDD)",
        "transaction": {
            "tx_type": "SWIFT",
            "amount": 8_000, "currency": "USD",
            "sender_name": "Politician X",
            "beneficiary_name": "Supplier France SARL",
            "sender_country": "Tunisia",
            "destination_country": "France",
            "motive": "Paiement facture",
            "has_justification": True,
        },
        "customer_history": None,
        # ── Profil CLIENT durable (≠ transaction) — scoré par ClientScoringAgent
        "client_profile": ClientProfile(
            client_id="PEP-001", full_name="Politician X",
            nationality="Nigeria", country_residence="Tunisia",
            is_pep=True, pep_type="FOREIGN", profession="Import-Export",
            kyc_complete=False, account_age_days=30, previous_strs=2,
            account_type="BUSINESS", beneficial_owner_identified=False),
        "expected_decision": "PASS",          # ← Level 1 (transaction seule)
        "expected_ca_decision": "EDD",        # ← Compliance (avec profil client)
        "expected_min_questions": 4,
        "expected_min_chunks": 5,
    },
]


# ═══════════════════════════════════════════════════════════════════════════
#  DÉDOUBLONNAGE DES CHUNKS
# ═══════════════════════════════════════════════════════════════════════════

def deduplicate_chunks(all_chunks):
    """
    Dédoublonne les chunks par (source_doc, article_ref).
    Garde le score le plus élevé pour chaque paire.
    """
    seen = OrderedDict()   # (source_doc, article_ref) → RetrievalResult
    for chunk in all_chunks:
        key = (chunk.source_doc, chunk.article_ref)
        if key not in seen or chunk.score > seen[key].score:
            seen[key] = chunk
    # Trier par score décroissant
    deduped = sorted(seen.values(), key=lambda c: -c.score)
    return deduped


# ═══════════════════════════════════════════════════════════════════════════
#  PIPELINE D'INTÉGRATION
# ═══════════════════════════════════════════════════════════════════════════

def run_scenario(scenario: dict,
                 orchestrator: Level1Orchestrator,
                 planner: PlanningAgent,
                 retriever: RetrievalPipeline,
                 compliance_agent: ComplianceAgent,
                 client_agent: ClientScoringAgent = None,
                 workflow: DecisionWorkflow = None,
                 verbose: bool = False,
                 skip_cache: bool = True):
    """
    Exécute un scénario complet : Level1 → Planning → Retrieval → Compliance.

    Returns:
        dict avec toutes les métriques du scénario
    """
    
    tx        = scenario["transaction"]
    cust_hist = scenario.get("customer_history")
    name      = scenario["name"]
    t0        = time.time()

    print(f"\n{'━'*70}")
    print(f"  {name}")
    print(f"{'━'*70}")

    # ── ÉTAPE 1 : Level 1 ─────────────────────────────────────────────────
    t1 = time.time()
    l1_result = orchestrator.check_transaction(tx)
    t1_ms = (time.time() - t1) * 1000

    print(f"\n  📋 LEVEL 1")
    print(f"     Décision     : {l1_result.decision}")
    print(f"     Violations   : {len(l1_result.violations)}")
    for v in l1_result.violations[:3]:
        print(f"       • {v[:85]}")
    print(f"     Risk signals : {l1_result.risk_signals}")
    print(f"     Durée        : {t1_ms:.0f}ms")

    # Vérification
    expected = scenario.get("expected_decision")
    if expected:
        status = "✅" if l1_result.decision == expected else "❌"
        print(f"     Vérification : {status} (attendu={expected}, obtenu={l1_result.decision})")

    # ── ÉTAPE 1bis : Client Scoring Agent (risque STRUCTUREL du client) ────
    # FATF Risk-Based Approach : le profil durable du client module la diligence.
    # Les signaux client sont FUSIONNÉS avec ceux de Level 1 → le Planning
    # générera des questions ciblées (ex: PEP → R12) et le Compliance Agent
    # recevra le profil (plancher de diligence + contribution bornée).
    client_result   = None
    client_signals  = []
    client_profile  = scenario.get("client_profile")
    if client_profile is not None and client_agent is not None:
        client_result  = client_agent.score_client(client_profile)
        client_signals = client_score_to_signals(client_result)
        fatca_obl = (client_result.fatca.obligation
                     if client_result.fatca else "NONE")
        print(f"\n  👤 CLIENT SCORING")
        print(f"     Risque structurel : {client_result.level} "
              f"(score {client_result.score:.0f}/100)")
        print(f"     Facteurs          : "
              f"{', '.join(client_result.components.keys()) or 'aucun'}")
        print(f"     Signaux dérivés   : {client_signals or 'aucun'}")
        if fatca_obl not in ("NONE", None):
            print(f"     ⚑ FATCA (FISCAL ≠ AML) : {fatca_obl}")

    # Fusion des signaux L1 + client (dédoublonnés, ordre préservé) pour le Planning
    planning_signals = list(dict.fromkeys(
        list(l1_result.risk_signals) + client_signals))

    # ── ÉTAPE 2 : Planning Agent ──────────────────────────────────────────
    t2 = time.time()
    planning_tx = l1_to_planning(tx)
    plan_result = planner.plan(planning_tx, planning_signals, cust_hist)
    t2_ms = (time.time() - t2) * 1000

    print(f"\n  🧠 PLANNING AGENT")
    print(f"     Questions    : {len(plan_result.questions)}")
    print(f"       Templates  : {plan_result.template_count}")
    print(f"       Contexte   : {plan_result.context_count}")
    print(f"       Signaux    : {plan_result.signal_count}")
    print(f"       Historique : {plan_result.history_count}")
    if plan_result.context_factors:
        print(f"     Facteurs     : {', '.join(plan_result.context_factors[:5])}")
    print(f"     Durée        : {t2_ms:.0f}ms")

    if verbose:
        for i, q in enumerate(plan_result.questions, 1):
            prio = plan_result.priorities[i-1] if i-1 < len(plan_result.priorities) else "?"
            prio_label = {5:"CRIT",4:"HAUT",3:"MOY",2:"BASE"}.get(prio, "?")
            print(f"       Q{i} [{prio_label}] {q[:80]}")

    # ── ÉTAPE 3 : Retrieval Agent Round 1 (questions initiales) ───────────
    t3 = time.time()
    jurisdiction = infer_jurisdiction(tx)
    tx_type      = tx.get("tx_type", "SWIFT")
    all_chunks   = []
    round1_count = 0

    for i, question in enumerate(plan_result.questions, 1):
        tq = time.time()
        try:
            chunks = retriever.retrieve(
                query=question,
                tx_type=tx_type,
                jurisdiction=jurisdiction,
                skip_cache=skip_cache,
                top_k=5,
            )
            all_chunks.extend(chunks)
            round1_count += len(chunks)
            if verbose:
                tq_ms = (time.time() - tq) * 1000
                print(f"       Q{i}: {len(chunks)} chunks ({tq_ms:.0f}ms)")
        except Exception as e:
            print(f"       Q{i}: ❌ Erreur — {e}")

    t3_ms = (time.time() - t3) * 1000

    # ── ÉTAPE 4 : REFLECTION (gap analysis) ───────────────────────────────
    reflection = None
    round2_count = 0
    
    try:
        t4 = time.time()
        reflection = planner.reflect(
            transaction=planning_tx,
            questions_asked=plan_result.questions,
            chunks_retrieved=all_chunks,
        )
        t4_ms = (time.time() - t4) * 1000
        
        # Si gaps détectés → Round 2 de recherche
        if not reflection.is_sufficient and reflection.new_questions:
            if verbose:
                print(f"\n  🔄 REFLECTION — Round 2")
                print(f"     Gaps détectés : {len(reflection.gaps_found)}")
                for gap in reflection.gaps_found[:5]:
                    print(f"       ❌ {gap}")
            
            for i, question in enumerate(reflection.new_questions, 1):
                tq = time.time()
                try:
                    chunks = retriever.retrieve(
                        query=question,
                        tx_type=tx_type,
                        jurisdiction=jurisdiction,
                        skip_cache=skip_cache,
                        top_k=5,
                    )
                    all_chunks.extend(chunks)
                    round2_count += len(chunks)
                    if verbose:
                        tq_ms = (time.time() - tq) * 1000
                        print(f"       R2-Q{i}: {len(chunks)} chunks ({tq_ms:.0f}ms) — {question[:60]}...")
                except Exception as e:
                    print(f"       R2-Q{i}: ❌ Erreur — {e}")
            
            t3_ms += (time.time() - t4) * 1000  # Include round 2 in retrieval time
    
    except Exception as e:
        if verbose:
            print(f"\n  ⚠️  Reflection skipped: {e}")

    # ── ÉTAPE 5 : Dédoublonnage ───────────────────────────────────────────
    deduped = deduplicate_chunks(all_chunks)

    # ── ÉTAPE 6 : Compliance Agent ────────────────────────────────────────
    t5 = time.time()
    fatf_checklist = build_fatf_checklist(tx_type)
    result_compliance = compliance_agent.analyze(
        transaction    = {**planning_tx, "tx_type": tx_type},
        chunks         = deduped,
        risk_signals   = l1_result.risk_signals,   # signaux ÉVÉNEMENTIELS (transaction)
        fatf_checklist = fatf_checklist,
        level1_decision= l1_result.decision,
        client_profile = client_result,            # risque STRUCTUREL (plancher + contribution)
    )
    t5_ms = (time.time() - t5) * 1000
    total_ms = (time.time() - t0) * 1000

    print(f"\n  🔍 RETRIEVAL AGENT")
    print(f"     Questions R1 : {len(plan_result.questions)}")
    print(f"     Chunks R1    : {round1_count}")
    if reflection and not reflection.is_sufficient:
        print(f"     Couverture   : {reflection.coverage_ratio:.0%} → gaps détectés")
        print(f"     Questions R2 : {len(reflection.new_questions)}")
        print(f"     Chunks R2    : {round2_count}")
    print(f"     Total brut   : {len(all_chunks)}")
    print(f"     Dédoublonnés : {len(deduped)}")
    print(f"     Durée        : {t3_ms/1000:.1f}s")

    # ── RÉSUMÉ DES CHUNKS ─────────────────────────────────────────────────
    print(f"\n  📄 TOP 10 CHUNKS UNIQUES")
    for i, c in enumerate(deduped[:10], 1):
        thresh = " 📊" if c.has_threshold else ""
        ctx    = f" 📎+{len(c.expanded_context)}" if c.expanded_context else ""
        print(f"     {i:2d}. [{c.chunk_role:<12}] {c.source_doc}")
        print(f"         {c.article_ref} | {c.jurisdiction} | "
              f"score={c.score:.4f} | via={c.retrieval_path}{thresh}{ctx}")
        if verbose:
            print(f"         {c.text[:100]}...")

    # ── Sources uniques ───────────────────────────────────────────────────
    unique_docs = sorted(set(c.source_doc for c in deduped))
    unique_roles = sorted(set(c.chunk_role for c in deduped if c.chunk_role))
    print(f"\n  📚 Sources      : {len(unique_docs)} documents")
    for d in unique_docs[:8]:
        print(f"       • {d}")
    print(f"  🏷️  Rôles        : {', '.join(unique_roles)}")

    # ── COMPLIANCE AGENT ─────────────────────────────────────────────────
    print(f"\n  ⚖️  COMPLIANCE AGENT")
    print(f"     Décision L1        : {l1_result.decision}")
    print(f"     Décision CA v5     : {result_compliance.decision}")
    print(f"     Score de risque    : {result_compliance.risk_score}/100")
    # Influence du profil client (FATF RBA) — visible dans le scoring
    if client_result is not None:
        rc = result_compliance.risk_components
        print(f"     ↳ Inhérent client  : +{getattr(rc, 'inherent_client', 0.0):.2f} "
              f"(client {client_result.level})")
        ca_exp = scenario.get("expected_ca_decision")
        if ca_exp:
            ca_status = "✅" if result_compliance.decision == ca_exp else "❌"
            print(f"     ↳ Vérif. RBA       : {ca_status} "
                  f"(attendu CA={ca_exp}, obtenu={result_compliance.decision}) "
                  f"— escalade pilotée par le profil client")
    print(f"     Règles applicables : {len(result_compliance.applicable_rules)}")
    print(f"     Violations         : {len(result_compliance.violated_rules)}")
    print(f"     Cross-validées     : {result_compliance.stats.get('cross_validated', 0)}")
    print(f"     Revue humaine      : {len(result_compliance.human_review_flags)}")
    print(f"     Durée              : {t5_ms:.0f}ms")

    if result_compliance.violated_rules:
        print(f"\n  🔴 VIOLATIONS DÉTECTÉES")
        for r in result_compliance.violated_rules[:5]:
            tag = "✓✓" if r.cross_validated else ("S" if r.provenance == "SYMBOLIC" else "L")
            print(f"     [{tag}|{r.severity[:4]}|{r.fatf_rec or '?':>4}] {r.rule_text[:65]}")

    if result_compliance.human_review_flags and verbose:
        print(f"\n  ⚠️  REVUE HUMAINE")
        for flag in result_compliance.human_review_flags[:3]:
            print(f"     • {flag[:90]}")

    # ── ÉTAPE 7 : Decision Workflow (post-décision, human-in-the-loop) ─────
    outcome = None
    if workflow is not None:
        outcome = workflow.process(
            transaction={**tx, "tx_type": tx_type},
            decision=result_compliance,
            tx_id=name.split(" ")[0])   # ex: "S5"
        print(f"\n  🔄 DECISION WORKFLOW")
        print(f"     État transaction   : {outcome.state}")
        print(f"     {outcome.summary}")
        if outcome.required_documents:
            for d in outcome.required_documents:
                tag = "FISCAL" if d["category"] == "FISCAL" else d["fatf_rec"]
                print(f"       📄 [{tag:>6}] {d['label'][:60]}")
        if outcome.needs_supervisor:
            print(f"     → file superviseur"
                  + (" (STR à déclarer)" if outcome.str_required else ""))

    # ── TIMING TOTAL ──────────────────────────────────────────────────────
    print(f"\n  ⏱️  TIMING TOTAL  : {total_ms/1000:.1f}s")
    print(f"       Level 1     : {t1_ms:.0f}ms")
    print(f"       Planning    : {t2_ms:.0f}ms")
    print(f"       Retrieval   : {t3_ms/1000:.1f}s")
    print(f"       Compliance  : {t5_ms:.0f}ms")

    return {
        "name":              name,
        "l1_decision":       l1_result.decision,
        "l1_signals":        l1_result.risk_signals,
        "questions_r1":      len(plan_result.questions),
        "questions_r2":      len(reflection.new_questions) if reflection else 0,
        "chunks_raw":        len(all_chunks),
        "chunks_r1":         round1_count,
        "chunks_r2":         round2_count,
        "chunks_deduped":    len(deduped),
        "unique_docs":       len(unique_docs),
        "coverage_ratio":    reflection.coverage_ratio if reflection else 1.0,
        "gaps_found":        len(reflection.gaps_found) if reflection else 0,
        "ca_decision":       result_compliance.decision,
        "ca_score":          result_compliance.risk_score,
        "ca_violated":       len(result_compliance.violated_rules),
        "ca_applicable":     len(result_compliance.applicable_rules),
        "ca_cross_validated":result_compliance.stats.get("cross_validated", 0),
        "ca_review_flags":   len(result_compliance.human_review_flags),
        "compliance":        result_compliance,   # objet complet pour l'évaluation GT
        "total_ms":          total_ms,
        "expected_decision": expected,
        "decision_ok":       l1_result.decision == expected if expected else None,
        # ── Volet client (FATF RBA) ──
        "client_level":      client_result.level if client_result else None,
        "client_score":      client_result.score if client_result else None,
        "client_signals":    client_signals,
        "expected_ca_decision": scenario.get("expected_ca_decision"),
        "ca_decision_ok":    (result_compliance.decision == scenario.get("expected_ca_decision"))
                             if scenario.get("expected_ca_decision") else None,
        # ── Volet workflow (Point 1 — human-in-the-loop) ──
        "wf_state":          outcome.state if outcome else None,
        "wf_required_docs":  len(outcome.required_documents) if outcome else 0,
        "wf_needs_supervisor": outcome.needs_supervisor if outcome else False,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  ÉVALUATION GROUND TRUTH
# ═══════════════════════════════════════════════════════════════════════════

def load_ground_truth(path: str) -> list:
    """Charge les règles validées depuis aml_rules_complete_validated.json."""
    import json as _json
    try:
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        rules = data.get("rules", data) if isinstance(data, dict) else data
        return [r for r in rules if r.get("validation_status") == "VALIDATED"]
    except FileNotFoundError:
        print(f"  ⚠️  Fichier GT introuvable : {path}")
        return []


def _match_rule_to_gt(rule, gt: dict) -> bool:
    """
    Vérifie si une ComplianceRule extraite correspond à une règle ground truth.
    Matching sur source_doc + numéro d'article (flexible).
    """
    import re as _re
    # 1) source_doc doit correspondre (même PDF)
    rule_doc = (rule.source_doc or "").lower()
    gt_doc   = gt.get("source_doc", "").lower()
    if not rule_doc or rule_doc not in gt_doc:
        return False

    # 2) article_ref : on extrait les numéros et on cherche une intersection
    gt_art   = gt.get("source_article", "").lower()
    rule_ref = (rule.rule_ref or "").lower()
    if not gt_art or not rule_ref:
        return True   # source_doc seul suffit si pas de ref article
    gt_nums   = set(_re.findall(r"\d+", gt_art))
    rule_nums = set(_re.findall(r"\d+", rule_ref))
    return bool(gt_nums & rule_nums)


def evaluate_ground_truth(results: list, gt_path: str):
    """
    Évalue les règles extraites par le Compliance Agent contre les règles GT.
    Calcule Precision / Recall / F1.
    """
    gt_rules = load_ground_truth(gt_path)
    if not gt_rules:
        return

    # Agréger toutes les règles applicables de tous les scénarios
    all_extracted = []
    for scen in results:
        comp = scen.get("compliance")
        if comp:
            all_extracted.extend(comp.applicable_rules)

    if not all_extracted:
        print("\n  ⚠️  Aucune règle extraite — évaluation impossible")
        return

    # Matching par (source_doc + numéro article)
    true_positives = 0
    matched_gt_ids = set()
    for rule in all_extracted:
        for gt in gt_rules:
            if gt["id"] not in matched_gt_ids and _match_rule_to_gt(rule, gt):
                true_positives += 1
                matched_gt_ids.add(gt["id"])
                break

    precision = true_positives / max(len(all_extracted), 1)
    recall    = true_positives / max(len(gt_rules), 1)
    f1        = 2 * precision * recall / max(precision + recall, 1e-9)

    print(f"\n\n{'═'*70}")
    print(f"  ÉVALUATION GROUND TRUTH ({len(gt_rules)} règles validées)")
    print(f"{'═'*70}")
    print(f"  Règles GT chargées    : {len(gt_rules)}")
    print(f"  Règles extraites      : {len(all_extracted)} (tous scénarios)")
    print(f"  True positives        : {true_positives}")
    print(f"  Precision             : {precision:.1%}")
    print(f"  Recall                : {recall:.1%}")
    print(f"  F1                    : {f1:.1%}")

    # Règles GT non retrouvées
    missed = [gt for gt in gt_rules if gt["id"] not in matched_gt_ids]
    if missed:
        print(f"\n  Règles GT manquées ({len(missed)}) :")
        for gt in missed[:8]:
            print(f"    • [{gt['id']}] {gt['source_doc']} {gt['source_article']}")
    print(f"{'═'*70}")


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    p = argparse.ArgumentParser(description="Test intégration L1 → Planning → Retrieval")
    p.add_argument("--scenario", type=int, default=0,
                   help="Numéro du scénario (1-4), 0=tous")
    p.add_argument("--no-rerank", action="store_true",
                   help="Désactiver le reranker (plus rapide)")
    p.add_argument("--use-cache", action="store_true",
                   help="Utiliser le cache Neo4j Step 0")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--metrics-out", type=str, default="",
                   help="Exporter les métriques en JSON")
    p.add_argument("--eval-gt", action="store_true",
                   help="Évaluer contre les 41 règles ground truth")
    args = p.parse_args()

    if args.no_rerank:
        import retrieval_agent
        retrieval_agent.RERANKING_ENABLED = False

    print(f"\n{'═'*70}")
    print(f"  TEST INTÉGRATION — Level 1 → Planning → Retrieval → Compliance")
    print(f"{'═'*70}")
    print(f"  Date        : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Reranking   : {'OFF' if args.no_rerank else 'ON'}")
    print(f"  Cache Neo4j : {'ON' if args.use_cache else 'OFF (skip)'}")
    print(f"  Scénarios   : {'tous' if args.scenario == 0 else f'S{args.scenario}'}")
    print(f"{'═'*70}")

    # ── Init des composants (une seule fois) ──────────────────────────────
    print("\n  Initialisation des composants...\n")
    orchestrator = Level1Orchestrator()
    retriever    = RetrievalPipeline(verbose=False)
    planner      = PlanningAgent(verbose=args.verbose, retriever=retriever)

    # Client Scoring Agent — réutilise le RiskCountryChecker du Niveau 1
    # (pas de duplication de la liste FATF des pays à risque)
    _country_checker = None
    try:
        from level1_rules.risk_country_checker import RiskCountryChecker
        _country_checker = RiskCountryChecker()
    except Exception as _e:
        print(f"  ⚠️  RiskCountryChecker indisponible ({_e}) — risque géo client désactivé")
    client_agent = ClientScoringAgent(country_checker=_country_checker)

    # Decision Workflow (Point 1) — couche post-décision human-in-the-loop.
    # Notifications persistées en JSONL (l'affichage console est géré par run_scenario).
    workflow = DecisionWorkflow(notifier=JsonlNotificationSink(), verbose=False)

    neo4j_driver = None
    if _NEO4J_AVAILABLE:
        try:
            neo4j_driver = _GraphDatabase.driver(
                "bolt://localhost:7687", auth=("neo4j", "amlkyc2025")
            )
            neo4j_driver.verify_connectivity()
            print("  ✅ Neo4j connecté (chemin symbolique activé)")
        except Exception as _e:
            neo4j_driver = None
            print(f"  ⚠️  Neo4j indisponible ({_e}) — chemin symbolique via signaux L1 uniquement")
    else:
        print("  ⚠️  neo4j non installé — pip install neo4j")

    compliance_agent = ComplianceAgent(neo4j_driver=neo4j_driver, verbose=args.verbose)

    # ── Sélection des scénarios ───────────────────────────────────────────
    if args.scenario > 0:
        scenarios = [SCENARIOS[args.scenario - 1]]
    else:
        scenarios = SCENARIOS

    # ── Exécution ─────────────────────────────────────────────────────────
    results = []
    for scenario in scenarios:
        r = run_scenario(
            scenario, orchestrator, planner, retriever, compliance_agent,
            client_agent=client_agent,
            workflow=workflow,
            verbose=args.verbose,
            skip_cache=not args.use_cache,
        )
        results.append(r)

    # ── Fermeture ─────────────────────────────────────────────────────────
    retriever.close()
    if neo4j_driver:
        neo4j_driver.close()

    # ── TABLEAU RÉCAPITULATIF ─────────────────────────────────────────────
    print(f"\n\n{'═'*70}")
    print(f"  RÉCAPITULATIF — {len(results)} scénarios")
    print(f"{'═'*70}\n")

    print(f"  {'Scénario':<40} {'L1':<7} {'CA':<7} {'Scr':>4} {'Viol':>4} "
          f"{'Clt':<6} {'Q1':<3} {'Uniq':<5} {'Cov':<5} {'Time':<7} {'OK'}")
    print(f"  {'─'*40} {'─'*7} {'─'*7} {'─'*4} {'─'*4} "
          f"{'─'*6} {'─'*3} {'─'*5} {'─'*5} {'─'*7} {'─'*3}")

    all_ok = True
    for r in results:
        # OK = décision L1 attendue ET (si applicable) escalade CA pilotée client
        ok_flags = [v for v in (r["decision_ok"], r.get("ca_decision_ok"))
                    if v is not None]
        ok_str = "✅" if all(ok_flags) else "❌" if ok_flags else "—"
        if False in ok_flags:
            all_ok = False
        cov = f"{r['coverage_ratio']:.0%}" if r.get("coverage_ratio") else "—"
        ca_dec  = r.get("ca_decision", "—")
        ca_scr  = f"{r.get('ca_score', 0):.0f}"
        ca_viol = r.get("ca_violated", 0)
        clt = {"ÉLEVÉ": "ÉLEVÉ", "MOYEN": "MOYEN", "FAIBLE": "FAIB"}.get(
            r.get("client_level"), "—")
        print(f"  {r['name']:<40} {r['l1_decision']:<7} {ca_dec:<7} {ca_scr:>4} {ca_viol:>4} "
              f"{clt:<6} {r['questions_r1']:<3} {r['chunks_deduped']:<5} {cov:<5} "
              f"{r['total_ms']/1000:>5.1f}s {ok_str}")

    total_time = sum(r["total_ms"] for r in results)
    print(f"\n  Total : {total_time/1000:.1f}s")
    print(f"  Statut: {'✅ TOUS OK' if all_ok else '❌ ÉCHECS DÉTECTÉS'}")

    # ── FILE SUPERVISEUR (Point 1 — transactions « en cours ») ─────────────
    pending = workflow.queue.pending()
    if pending:
        print(f"\n  👨‍⚖️  FILE SUPERVISEUR — {len(pending)} transaction(s) EN COURS")
        for o in pending:
            docs = f", {len(o.required_documents)} justif." if o.required_documents else ""
            print(f"     • {o.tx_id:<6} [{o.state:<17}] décision={o.agent_decision}{docs}"
                  + ("  ⚑STR" if o.str_required else ""))
        print(f"     (notifications → output/notifications.jsonl ; "
              f"décisions superviseur → output/supervisor_feedback.jsonl)")

    # ── Évaluation Ground Truth ───────────────────────────────────────────
    if args.eval_gt:
        gt_path = Path(__file__).parent / "output" / "aml_rules_complete_validated.json"
        evaluate_ground_truth(results, str(gt_path))

    # ── Export métriques ──────────────────────────────────────────────────
    if args.metrics_out:
        with open(args.metrics_out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        print(f"\n  ✅ Métriques → {args.metrics_out}")

    print()


if __name__ == "__main__":
    main()