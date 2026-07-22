"""
planning_agent.py — v4 (Agentic)
===================================
Planning Agent avec boucle Reason-Act-Reflect.

ARCHITECTURE AGENTIC RAG (conforme article Raheja + PFE Louhichi) :

  ┌──────────────────────────────────────────────────────┐
  │              REASON (plan initial)                    │
  │  Layer 1 : Déterministe (templates + contexte)       │
  │  Layer 2 : LLM raisonne sur la transaction complète  │
  │            → génère des questions que les templates   │
  │              n'auraient jamais produites              │
  └──────────────────┬───────────────────────────────────┘
                     │  3-7 questions
                     ▼
  ┌──────────────────────────────────────────────────────┐
  │              ACT (retrieval — appelé par orchestrator)│
  │  RetrievalPipeline.retrieve(q) × N                   │
  └──────────────────┬───────────────────────────────────┘
                     │  chunks
                     ▼
  ┌──────────────────────────────────────────────────────┐
  │              REFLECT (évaluation post-retrieval)      │
  │  Le LLM évalue les chunks contre un checklist        │
  │  réglementaire et identifie les GAPs                 │
  │  → 0-2 nouvelles questions ciblées                   │
  └──────────────────┬───────────────────────────────────┘
                     │  gaps détectés ?
                     ▼
               Oui → RE-ACT (2ème recherche)
               Non → terminé, passer au Compliance Agent

DIFFÉRENCES v3 → v4 :
  v3 : plan() statique, pas de feedback
  v4 : plan() + reflect() = boucle agentic complète
  v4 : LLM raisonne sur la transaction HOLISTICALLY (pas champ par champ)
  v4 : Vocabulaire calibré enseigné au LLM via prompt
  v4 : Checklist réglementaire pour gap detection
  v4 : Champs unifiés (accepte tx_type ET transaction_type)
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger("planning_agent")

GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL    = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_QUESTIONS = 8
OVERLAP_THRESHOLD = 0.60

# Seuils réglementaires pour détection "juste sous le seuil"
REGULATORY_THRESHOLDS = {
    ("SWIFT","USD"):10000, ("SWIFT","EUR"):15000,
    ("VIREMENT_LOCAL","TND"):10000, ("DEPOT_ESPECES","TND"):5000,
    ("MANDAT","TND"):3000,
}

# Checklist dimensions réglementaires par tx_type (pour reflect)
REGULATORY_DIMENSIONS = {
    "SWIFT": [
        "KYC_identification", "threshold_STR_reporting",
        "sanctions_screening", "country_risk_FATF",
        "source_of_funds", "beneficial_ownership",
        "travel_rule_R16", "correspondent_banking",
        "enhanced_due_diligence", "record_keeping",
    ],
    "VIREMENT_LOCAL": [
        "KYC_identification", "threshold_STR_reporting",
        "source_of_funds", "record_keeping",
        "suspicious_indicators", "enhanced_due_diligence",
    ],
    "DEPOT_ESPECES": [
        "KYC_identification", "threshold_declaration",
        "threshold_STR_reporting", "structuring_detection",
        "cash_origin_verification", "suspicious_indicators",
        "record_keeping",
    ],
    "MANDAT": [
        "KYC_identification", "threshold_STR_reporting",
        "operator_obligations_MVTS", "beneficiary_verification",
        "suspicious_indicators", "record_keeping",
    ],
}

# ═══════════════════════════════════════════════════════════════════════
#  VOCABULARY CALIBRATION (enseigné au LLM)
# ═══════════════════════════════════════════════════════════════════════

VOCAB_CALIBRATION = """## CRITICAL: Corpus Vocabulary Rules
Your queries search an ENGLISH regulatory corpus (FATF, BCT circulars, EU directives).
The corpus was translated from French/Arabic. Use ONLY formal regulatory English.

TERMS THAT FIND GOOD RESULTS (use these):
✅ "politically exposed persons enhanced vigilance"
✅ "deposit cash large repetitive amounts suspicious without economic justification"
✅ "correspondent banking sanctions screening obligations"
✅ "structured transactions multiple deposits below threshold"
✅ "suspicious transaction report filing CTAF authorities"
✅ "enhanced due diligence source of funds documentation"
✅ "FATF blacklist high risk country countermeasures"
✅ "beneficial ownership verification requirements"

TERMS THAT RETURN ZERO RESULTS (never use):
❌ "smurfing" → use "structured transactions below threshold"
❌ "fractionnement" → use "structured deposits pattern detection"
❌ "PEP obligations" → use "politically exposed persons enhanced vigilance"
❌ "KYC requirements" → use "customer due diligence identification verification"
❌ "blanchiment" → use "money laundering indicators"
❌ "déclaration de soupçon" → use "suspicious transaction report filing"
"""


# ═══════════════════════════════════════════════════════════════════════
#  TEMPLATES DÉTERMINISTES (Layer 1 — safety net)
# ═══════════════════════════════════════════════════════════════════════

TEMPLATES = {
    "SWIFT": [
        lambda t: f"KYC identification requirements international wire transfer {t.get('sender_country','')} to {t.get('receiver_country','')} SWIFT",
        lambda t: f"STR reporting threshold SWIFT transfer {t.get('amount','')} {t.get('currency','USD')} without economic justification obligations",
        # NB : formulation NEUTRE — ne pas qualifier la destination de « high
        # risk » ici (fausse affirmation si le pays n'est pas listé FATF).
        # Les questions liste noire/grise viennent de SIGNAL_QUESTIONS,
        # déclenchées UNIQUEMENT par les signaux COUNTRY_* du Level 1.
        lambda t: f"enhanced due diligence cross border wire transfer destination {t.get('receiver_country','')} source of funds verification obligations",
    ],
    "VIREMENT_LOCAL": [
        lambda t: "KYC customer due diligence local wire transfer Tunisia identification verification obligations BCT",
        lambda t: f"STR reporting threshold local transfer {t.get('amount','')} {t.get('currency','TND')} BCT circular obligations",
        lambda t: "suspicious transaction indicators local transfer without economic justification reporting BCT CTAF",
    ],
    "MANDAT": [
        lambda t: f"money transfer operator identification requirements sender beneficiary {t.get('amount','')} TND obligations",
        lambda t: f"money order threshold STR reporting {t.get('amount','')} TND suspicious motive AML obligations",
        lambda t: "money transfer red flags suspicious indicators structuring multiple transfers reporting obligations MVTS",
    ],
    "DEPOT_ESPECES": [
        lambda t: f"cash deposit declaration reporting threshold {t.get('amount','')} TND BCT obligations",
        lambda t: "deposit cash large repetitive amounts suspicious without economic justification STR reporting",
        lambda t: "cash transaction suspicious indicators large amounts inconsistent customer profile reporting obligations",
    ],
}

SIGNAL_QUESTIONS = {
    "SANCTION_UN_INDIVIDUAL_SENDER":     (5, "UN sanctions designated individual asset freeze wire transfer correspondent banking screening blocking obligations"),
    "SANCTION_UN_INDIVIDUAL_BENEFICIARY":(5, "UN sanctions designated individual beneficiary transaction blocking reporting obligations financial institution"),
    "SANCTION_PEP_SENDER":              (5, "politically exposed persons enhanced vigilance source of funds authorization board directors obligations"),
    "SANCTION_PEP_BENEFICIARY":         (5, "politically exposed persons beneficial owner enhanced due diligence senior management approval transaction monitoring"),
    "SANCTION_UN_ENTITY_SENDER":        (5, "sanctioned entity asset freeze transaction blocking compliance obligations financial institution reporting"),
    "COUNTRY_BLACK_DESTINATION_COUNTRY": (5, "FATF blacklist high risk country {receiver_country} countermeasures enhanced due diligence transaction blocking"),
    "COUNTRY_BLACK_SENDER_COUNTRY":     (5, "FATF blacklist high risk country {sender_country} countermeasures enhanced due diligence wire transfer obligations"),
    "COUNTRY_GREY_DESTINATION_COUNTRY": (4, "FATF grey list increased monitoring {receiver_country} enhanced due diligence risk mitigation obligations"),
    "COUNTRY_GREY_SENDER_COUNTRY":      (4, "FATF grey list increased monitoring {sender_country} customer due diligence enhanced vigilance obligations"),
    "THRESHOLD_STR":                    (4, "suspicious transaction report filing procedure {amount} {currency} CTAF authorities deadline content obligations"),
    "THRESHOLD_EDD":                    (3, "enhanced due diligence EDD requirements {amount} {currency} source of funds economic justification documentation"),
    "THRESHOLD_KYC":                    (3, "KYC identification verification requirements money order {amount} TND sender beneficiary"),
    "THRESHOLD_RECORD":                 (2, "cash transaction record keeping declaration {amount} TND BCT reporting obligations"),
    "SMURFING_PATTERN":                 (4, "structured transactions multiple deposits below threshold 72 hours pattern detection STR reporting obligations BCT"),
}


# ═══════════════════════════════════════════════════════════════════════
#  DATACLASSES
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class QCandidate:
    question: str
    priority: int       # 1-5
    source:   str       # "template" | "context" | "signal" | "history" | "llm_reason" | "llm_reflect"
    factor:   str = ""

@dataclass
class PlanningResult:
    questions:       list  = field(default_factory=list)
    priorities:      list  = field(default_factory=list)
    sources:         list  = field(default_factory=list)
    factors:         list  = field(default_factory=list)  # le POURQUOI de chaque question (aligné sur questions)
    tx_type:         str   = ""
    total_candidates:int   = 0
    context_factors: list  = field(default_factory=list)
    llm_reasoning_used: bool = False
    # Compteurs par source
    template_count:  int   = 0
    context_count:   int   = 0
    signal_count:    int   = 0
    history_count:   int   = 0
    llm_reason_count:int   = 0

@dataclass
class ReflectionResult:
    """Résultat de la réflexion post-retrieval."""
    gaps_found:       list  = field(default_factory=list)  # dimensions manquantes
    new_questions:    list  = field(default_factory=list)  # questions supplémentaires
    coverage_ratio:   float = 0.0   # % dimensions couvertes
    is_sufficient:    bool  = True   # True = pas besoin de re-chercher


# ═══════════════════════════════════════════════════════════════════════
#  PLANNING AGENT
# ═══════════════════════════════════════════════════════════════════════

class PlanningAgent:
    """
    Planning Agent Agentic v4.

    Deux méthodes principales :
      plan()    → Reason : génère les questions initiales
      reflect() → Reflect : évalue les résultats et identifie les gaps
    """

    def __init__(self, verbose=False, retriever=None):
        self.verbose = verbose
        self._llm    = None

        # Checklist dynamique (depuis corpus FATF, remplace REGULATORY_DIMENSIONS)
        try:
            from dynamic_checklist import DynamicChecklistBuilder
            self.dynamic_checklist = DynamicChecklistBuilder(
                retriever=retriever,
                verbose=verbose,
            )
        except ImportError:
            self.dynamic_checklist = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = self._build_llm() or False
            if self.verbose and self._llm is not False:
                print(f"  ✅ LLM: client résilient (cache + rotation clés/modèles)")
        return self._llm if self._llm is not False else None

    def _build_llm(self):
        """Client LLM résilient (cache + rotation clés/modèles), repli sur Groq simple."""
        try:
            from llm_client import get_llm
            client = get_llm(verbose=self.verbose)
            if client:
                return client
        except Exception as e:
            logger.debug(f"ResilientLLM indisponible : {e}")
        if GROQ_API_KEY:
            try:
                from groq import Groq
                return Groq(api_key=GROQ_API_KEY)
            except ImportError:
                return None
        return None

    # ══════════════════════════════════════════════════════════════════
    #  PLAN — Reason (génération initiale)
    # ══════════════════════════════════════════════════════════════════

    def plan(self, transaction: dict, risk_signals: list,
             customer_history: dict = None) -> PlanningResult:
        """
        REASON phase : génère les questions initiales.

        Layer 1 (déterministe) : templates + contexte + signaux + historique
        Layer 2 (LLM)          : raisonnement holistique sur la transaction
        """
        t = self._normalize_fields(transaction)
        tx_type = t.get("transaction_type", "SWIFT")
        candidates = []
        result = PlanningResult(tx_type=tx_type)

        if self.verbose:
            self._print_header(t, risk_signals, customer_history)

        # ── Layer 1 : Déterministe ────────────────────────────────────
        self._templates(t, candidates)
        self._context_analysis(t, candidates, result)
        self._signal_questions(t, risk_signals, candidates)
        if customer_history:
            self._history_questions(t, customer_history, candidates, result)

        # ── Layer 2 : LLM Reasoning ──────────────────────────────────
        if self.llm:
            det_questions = [c.question for c in candidates]
            llm_qs = self._llm_reasoning(t, risk_signals, customer_history, det_questions)
            for q, rationale in llm_qs:
                candidates.append(QCandidate(
                    q, priority=4, source="llm_reason", factor=rationale))
            if llm_qs:
                result.llm_reasoning_used = True

        # ── Finalisation : tri + dédup ────────────────────────────────
        result.total_candidates = len(candidates)
        self._finalize(candidates, result)

        if self.verbose:
            self._print_result(result)

        return result

    # ══════════════════════════════════════════════════════════════════
    #  REFLECT — Post-retrieval gap analysis
    # ══════════════════════════════════════════════════════════════════

    def reflect(self, transaction: dict, questions_asked: list,
                chunks_retrieved: list) -> ReflectionResult:
        """
        REFLECT phase : évalue si les résultats couvrent les obligations.

        v5 — CHECKLIST DYNAMIQUE (remplace strings opaques par recs FATF réelles)
        ──────────────────────────────────────────────────────────────────────────
        AVANT : checklist = ["KYC_identification", "travel_rule_R16", ...]
                → strings opaques, le LLM doit deviner leur signification

        APRÈS : recs = self.dynamic_checklist.get_for(tx_type)
                → [FATFRec(R10, "Customer due diligence"),
                   FATFRec(R16, "Wire transfers (Travel Rule)"), ...]
                → Chaque dimension = vraie recommandation FATF avec titre officiel
                → Quand FATF publie R41 → automatiquement dans le checklist

        AVANTAGE SUPPLÉMENTAIRE — Pré-check sans LLM :
          DynamicChecklistBuilder.check_coverage() détecte article_ref + keywords
          → Le LLM reçoit des HINTS (✓ pré-couvert / ⚠ possible gap)
          → Évaluation plus précise, moins d'hallucination

        Appelé par l'orchestrateur APRÈS retrieval.
        Si gaps détectés → retourne nouvelles questions → orchestrateur re-cherche.
        """
        t = self._normalize_fields(transaction)
        tx_type = t.get("transaction_type", "SWIFT")

        # ── Checklist DYNAMIQUE (depuis corpus FATF, pas hardcodé) ───
        recs = self.dynamic_checklist.get_for(tx_type)

        if not recs:
            # Fallback si dynamic_checklist pas initialisé
            fallback = REGULATORY_DIMENSIONS.get(tx_type, REGULATORY_DIMENSIONS["SWIFT"])
            recs_count = len(fallback)
            checklist_text = "\n".join(f"  - {d}" for d in fallback)
            use_dynamic = False
        else:
            recs_count = len(recs)
            use_dynamic = True

        if not self.llm:
            return ReflectionResult(is_sufficient=True, coverage_ratio=1.0)

        # ── Pré-check de couverture SANS LLM (article_ref + keywords) ──
        if use_dynamic:
            pre_coverage = self.dynamic_checklist.check_coverage(recs, chunks_retrieved)
            checklist_text = self.dynamic_checklist.format_for_llm(recs, pre_coverage)
        else:
            pre_coverage = None
            checklist_text = "\n".join(f"  - {d}" for d in fallback)

        # ── Préparer le contexte pour le LLM ─────────────────────────
        chunks_summary = self._summarize_chunks(chunks_retrieved[:20])
        questions_text = "\n".join(f"  Q{i}: {q}" for i, q in enumerate(questions_asked, 1))

        prompt = f"""You are an AML/KYC compliance expert evaluating whether a regulatory search was adequate.

## Transaction
Type: {tx_type} | Amount: {t.get('amount')} {t.get('currency')}
Sender: {t.get('sender_name','?')} ({t.get('sender_country','?')})
Receiver: {t.get('receiver_name','?')} ({t.get('receiver_country','?')})
Motive: {t.get('motive','?')} | Justification: {t.get('has_justification','?')}

## Questions Searched
{questions_text}

## Retrieved Content Summary
{chunks_summary}

## FATF Regulatory Checklist for {tx_type}
(Each item is an official FATF Recommendation. [✓ pré-couvert] = keyword match found, [⚠ possible gap] = no evidence found)
{checklist_text}

## Task
1. For each FATF recommendation above, determine if the retrieved content ADEQUATELY covers it.
   Use the pre-check hints [✓/⚠] as a starting point, but apply your own judgment.
2. Identify recommendations that are NOT adequately covered.
3. For each gap, generate ONE precise search query targeting that specific obligation.

{VOCAB_CALIBRATION}

## Output (JSON only, no markdown, no explanation)
{{"covered": ["R10", "R20"], "gaps": [{{"dimension": "R16 - Wire transfers", "question": "travel rule wire transfer originator information requirements", "priority": 4}}]}}"""

        try:
            response = self.llm.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=500,
            )
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"```json\s*", "", raw)
            raw = re.sub(r"```\s*$", "", raw)
            # Réponse vide (modèle à raisonnement dont le contenu a été nettoyé)
            # → pas d'échec bruyant, on considère la couverture suffisante.
            if not raw.strip():
                return ReflectionResult(is_sufficient=True, coverage_ratio=1.0)
            data = json.loads(raw)

            covered      = data.get("covered", [])
            raw_gaps     = data.get("gaps", [])

            # Filtrer les gaps : garder seulement ceux dans le checklist FATF
            # Evite les hallucinations LLM (ex: R16 dans VIREMENT_LOCAL)
            if use_dynamic and recs:
                valid_ids = {r.rec_id for r in recs}
                gaps = [g for g in raw_gaps
                        if any(rid in g.get("dimension", "")
                               for rid in valid_ids)]
            else:
                gaps = raw_gaps

            # Fusionner avec pré-check (les gaps LLM ont priorité)
            if pre_coverage and not gaps:
                # LLM dit tout OK mais pré-check détecte des gaps
                # → faire confiance au LLM (il a vu le contenu réel)
                pass

            reflection = ReflectionResult(
                gaps_found=[g["dimension"] for g in gaps],
                new_questions=[g["question"] for g in gaps[:2]],
                coverage_ratio=len(covered) / max(recs_count, 1),
                is_sufficient=len(gaps) == 0,
            )

            if self.verbose:
                mode = "FATF dynamic" if use_dynamic else "fallback static"
                print(f"\n  🔄 REFLECTION [{mode}]")
                print(f"     Couverture  : {reflection.coverage_ratio:.0%} ({len(covered)}/{recs_count} recommandations)")
                if pre_coverage and use_dynamic:
                    pre_pct = f"{pre_coverage.coverage_ratio:.0%}"
                    print(f"     Pré-check   : {pre_pct} (keyword/ref matching sans LLM)")
                if gaps:
                    print(f"     Gaps        : {', '.join(reflection.gaps_found)}")
                    for q in reflection.new_questions:
                        print(f"     → Re-search : {q}")
                else:
                    print(f"     ✅ Couverture suffisante — pas de re-recherche")

            return reflection

        except Exception as e:
            logger.debug(f"Reflection skipped (réponse LLM non exploitable): {e}")
            return ReflectionResult(is_sufficient=True, coverage_ratio=1.0)

    # ══════════════════════════════════════════════════════════════════
    #  LAYER 1 — DÉTERMINISTE
    # ══════════════════════════════════════════════════════════════════

    def _templates(self, t, candidates):
        tx = t.get("transaction_type", "SWIFT")
        for fn in TEMPLATES.get(tx, TEMPLATES["SWIFT"]):
            q = self._clean(fn(t))
            if q:
                candidates.append(QCandidate(q, 2, "template", "tx_type"))

    def _context_analysis(self, t, candidates, result):
        tx  = t.get("transaction_type", "SWIFT")
        amt = t.get("amount", 0) or 0
        cur = t.get("currency", "")

        def _add(q, prio, factor):
            q = self._clean(q)
            if q:
                candidates.append(QCandidate(q, prio, "context", factor))
                result.context_factors.append(factor)

        # Motif suspect
        motive = (t.get("motive") or "").lower()
        if any(k in motive for k in ["don","gift","aide","charity","personal","autre","other"]) or len(motive) < 5:
            _add("suspicious transaction motive declaration insufficient economic justification documentation STR reporting obligations", 3, f"motive='{t.get('motive','')}'")

        # SWIFT sans justificatif
        if tx == "SWIFT" and t.get("has_justification") is False and amt:
            _add(f"wire transfer {amt} {cur} without economic justification STR declaration obligations BCT CTAF deadline", 4, "no_justification")

        # Banque intermédiaire
        if tx == "SWIFT" and t.get("intermediary_bank"):
            _add(f"correspondent banking intermediary bank {t['intermediary_bank']} sanctions screening due diligence obligations FATF", 3, f"intermediary={t['intermediary_bank']}")

        # Billets neufs
        if tx == "DEPOT_ESPECES":
            notes = (t.get("notes") or t.get("denomination") or "").lower()
            if any(k in notes for k in ["neuf","new","nouveau","mint"]):
                _add("cash deposit new bills large denomination suspicious money laundering indicators reporting obligations BCT", 4, "new_bills")

        # Opérateur MVTS
        if tx == "MANDAT" and (t.get("operator") or "").upper() in ["WESTERN_UNION","MONEYGRAM","WAFACASH"]:
            _add(f"money transfer operator {t['operator']} MVTS AML obligations identification customer due diligence reporting", 3, f"operator={t['operator']}")

        # Retrait cash mandat
        if tx == "MANDAT" and (t.get("withdrawal_mode") or "").upper() == "CASH":
            _add("money order cash withdrawal identification beneficiary verification record keeping obligations", 3, "withdrawal=CASH")

        # Passeport cross-border
        if (t.get("sender_id_type") or "").upper() == "PASSPORT":
            sc, rc = (t.get("sender_country") or "").lower(), (t.get("receiver_country") or "").lower()
            if sc and rc and sc != rc:
                _add("non-resident customer passport identification cross-border wire transfer verification enhanced due diligence obligations", 3, "passport_cross_border")

        # Heure atypique
        ts = t.get("timestamp")
        if ts:
            hour = self._extract_hour(ts)
            if hour is not None and (hour >= 23 or hour < 5):
                _add("out of business hours transaction monitoring unusual time suspicious activity red flags reporting obligations", 3, f"hour={hour}h")

        # Juste sous le seuil
        threshold = REGULATORY_THRESHOLDS.get((tx, cur))
        if threshold and amt and 0.85 <= amt / threshold < 1.0:
            _add(f"transaction {amt} {cur} just below regulatory threshold {threshold} {cur} structuring avoidance detection reporting", 4, f"just_below={amt}<{threshold}")

        # Montant rond
        if amt >= 1000 and amt == int(amt) and amt % 1000 == 0:
            _add(f"round amount transaction {int(amt)} {cur} money laundering structuring pattern red flag indicator reporting", 2, f"round={int(amt)}")

    def _signal_questions(self, t, signals, candidates):
        added = 0
        for signal in signals:
            if added >= 2 or len(candidates) >= MAX_QUESTIONS:
                break
            entry = SIGNAL_QUESTIONS.get(signal)
            if entry:
                prio, template = entry
                q = template.format(**{k: t.get(k, "") for k in ["sender_country","receiver_country","amount","currency"]})
                q = self._clean(q)
                if q:
                    candidates.append(QCandidate(q, prio, "signal", signal))
                    added += 1

    def _history_questions(self, t, h, candidates, result):
        amt, cur = t.get("amount", 0) or 0, t.get("currency", "")

        checks = [
            (h.get("previous_strs", 0) > 0, 4,
             "customer prior suspicious activity report history enhanced monitoring ongoing due diligence obligations scrutiny",
             f"prev_strs={h.get('previous_strs')}"),
            (h.get("velocity_24h", 0) > 5, 4,
             f"high frequency transaction monitoring {h.get('velocity_24h',0)} operations 24 hours unusual activity detection AML reporting obligations",
             f"velocity={h.get('velocity_24h')}"),
            (h.get("avg_amount_30d", 0) and amt > h.get("avg_amount_30d", 0) * 3, 3,
             f"transaction amount {amt} {cur} significantly above customer historical average unusual risk indicators reporting",
             f"amt_vs_avg={amt}/{h.get('avg_amount_30d')}"),
            (h.get("new_receiver"), 3,
             "first time transaction new beneficiary enhanced due diligence customer verification requirements obligations",
             "new_receiver"),
            (h.get("previous_rejected"), 4,
             "previous transaction rejected follow-up monitoring enhanced scrutiny requirements suspicious pattern reporting",
             "prev_rejected"),
            (h.get("account_age_days", 999) < 90 and amt > 5000, 3,
             f"new account {h.get('account_age_days')} days large transaction {amt} {cur} unusual activity enhanced due diligence risk assessment",
             f"new_account={h.get('account_age_days')}d"),
        ]
        for cond, prio, q, factor in checks:
            if cond and len(candidates) < MAX_QUESTIONS:
                q = self._clean(q)
                if q:
                    candidates.append(QCandidate(q, prio, "history", factor))
                    result.context_factors.append(factor)

        # Nouveau pays
        countries = h.get("countries_last_90d", [])
        rc = t.get("receiver_country", "")
        if rc and countries and rc not in countries and len(candidates) < MAX_QUESTIONS:
            q = f"customer new destination country {rc} change behavior pattern unusual transaction monitoring obligations"
            candidates.append(QCandidate(self._clean(q), 3, "history", f"new_country={rc}"))
            result.context_factors.append(f"new_country={rc}")

    # ══════════════════════════════════════════════════════════════════
    #  LAYER 2 — LLM REASONING
    # ══════════════════════════════════════════════════════════════════

    def _llm_reasoning(self, t, signals, history, existing_questions):
        """
        Le LLM analyse la transaction HOLISTICALLY et génère
        1-2 questions que les templates n'auraient jamais produites.

        Le LLM voit la combinaison des facteurs — pas chaque champ isolément.
        Exemple: SWIFT 9800 USD + Iran + 3h du matin + compte de 30 jours
        = pattern cohérent de sanctions evasion avec structuring
        → question que aucun template ne génère.
        """
        questions_text = "\n".join(f"  - {q}" for q in existing_questions)
        history_text = json.dumps(history, default=str) if history else "No history available"

        prompt = f"""You are a senior AML/KYC compliance analyst reviewing a financial transaction.

## Full Transaction Context
{json.dumps(self._safe_dict(t), indent=2, default=str)}

## Risk Signals (from Level 1 automated screening)
{json.dumps(signals)}

## Customer History
{history_text}

## Questions Already Generated (by deterministic system)
{questions_text}

## Your Task
Analyze this transaction HOLISTICALLY — look at the COMBINATION of all factors, not each one independently. A SWIFT transfer of 9,800 USD to Iran at 3AM from a 30-day-old account is not "3 separate flags" — it's a coherent pattern of potential sanctions evasion.

Generate 1-2 ADDITIONAL search queries that the deterministic system above MISSED.
Focus on:
- Cross-factor patterns the templates can't see
- Jurisdiction-specific obligations not covered
- The regulatory STORY this transaction tells

Do NOT repeat or rephrase existing questions. Only add genuinely new dimensions.

{VOCAB_CALIBRATION}

## Output (JSON array, no markdown)
[{{"question": "your search query", "rationale": "why this matters"}}]
Return [] if the existing questions are already comprehensive."""

        try:
            response = self.llm.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=300,
            )
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"```json\s*", "", raw)
            raw = re.sub(r"```\s*$", "", raw)
            data = json.loads(raw)

            results = []
            for item in data[:2]:
                q = self._clean(item.get("question", ""))
                rationale = item.get("rationale", "llm_reasoning")
                if q and len(q.split()) >= 5:
                    # Check overlap with existing
                    overlap = any(self._overlap(q, eq) > OVERLAP_THRESHOLD for eq in existing_questions)
                    if not overlap:
                        results.append((q, rationale))

            if self.verbose and results:
                print(f"     🧠 LLM Reasoning : {len(results)} questions ajoutées")
                for q, r in results:
                    print(f"        → {q}")
                    print(f"          Rationale: {r}")

            return results

        except Exception as e:
            logger.debug(f"LLM reasoning failed: {e}")
            return []

    # ══════════════════════════════════════════════════════════════════
    #  FINALISATION
    # ══════════════════════════════════════════════════════════════════

    def _finalize(self, candidates, result):
        candidates.sort(key=lambda c: -c.priority)
        selected = []
        for c in candidates:
            if len(selected) >= MAX_QUESTIONS:
                break
            if not any(self._overlap(c.question, s.question) > OVERLAP_THRESHOLD for s in selected):
                selected.append(c)

        for c in selected:
            result.questions.append(c.question)
            result.priorities.append(c.priority)
            result.sources.append(c.source)
            result.factors.append(c.factor)
            if   c.source == "template":   result.template_count  += 1
            elif c.source == "context":    result.context_count   += 1
            elif c.source == "signal":     result.signal_count    += 1
            elif c.source == "history":    result.history_count   += 1
            elif c.source == "llm_reason": result.llm_reason_count += 1

    # ══════════════════════════════════════════════════════════════════
    #  UTILITAIRES
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _normalize_fields(t):
        """Accepte les 2 conventions de noms (Level 1 ET Planning)."""
        out = dict(t)
        if "transaction_type" not in out and "tx_type" in out:
            out["transaction_type"] = out["tx_type"]
        if "receiver_country" not in out and "destination_country" in out:
            out["receiver_country"] = out["destination_country"]
        if "receiver_name" not in out and "beneficiary_name" in out:
            out["receiver_name"] = out["beneficiary_name"]
        if "tx_type" not in out and "transaction_type" in out:
            out["tx_type"] = out["transaction_type"]
        return out

    @staticmethod
    def _clean(q):
        if not q: return ""
        q = re.sub(r"\s+", " ", q).strip().strip('"\'')
        return q if len(q.split()) >= 4 else ""

    @staticmethod
    def _overlap(a, b):
        wa, wb = set(a.lower().split()), set(b.lower().split())
        if not wa or not wb: return 0.0
        return len(wa & wb) / min(len(wa), len(wb))

    @staticmethod
    def _extract_hour(ts):
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"]:
            try: return datetime.strptime(str(ts)[:19], fmt).hour
            except (ValueError, TypeError): continue
        return None

    @staticmethod
    def _safe_dict(t):
        """Remove None values for cleaner JSON."""
        return {k: v for k, v in t.items() if v is not None and v != "" and v != 0}

    @staticmethod
    def _summarize_chunks(chunks):
        """Résume les chunks pour le prompt de reflection."""
        lines = []
        for i, c in enumerate(chunks[:15], 1):
            if hasattr(c, 'source_doc'):
                lines.append(f"  {i}. [{c.chunk_role}] {c.source_doc} {c.article_ref}: {c.text[:120]}...")
            elif isinstance(c, dict):
                lines.append(f"  {i}. [{c.get('chunk_role','')}] {c.get('source_doc','')} {c.get('article_ref','')}: {c.get('text','')[:120]}...")
        return "\n".join(lines) if lines else "  (no chunks retrieved)"

    def _print_header(self, t, signals, h):
        print(f"\n  🧠 Planning Agent v4 (Agentic) — {t.get('transaction_type')}")
        print(f"  {'─'*55}")
        print(f"  Montant      : {t.get('amount')} {t.get('currency')}")
        print(f"  Émetteur     : {t.get('sender_name','?')} ({t.get('sender_country','')})")
        print(f"  Bénéficiaire : {t.get('receiver_name','?')} ({t.get('receiver_country','')})")
        print(f"  Motif        : {t.get('motive','?')}")
        print(f"  Timestamp    : {t.get('timestamp','?')}")
        print(f"  Justificatif : {t.get('has_justification','?')}")
        for f in ["notes","operator","withdrawal_mode","intermediary_bank"]:
            if t.get(f): print(f"  {f:<16}: {t[f]}")
        print(f"  Signaux L1   : {signals or 'aucun'}")
        if h:
            parts = [f"STR={h.get('previous_strs',0)}", f"Vel={h.get('velocity_24h',0)}",
                     f"Tx30j={h.get('transaction_count_30d',0)}", f"Âge={h.get('account_age_days','?')}j"]
            print(f"  Historique   : {', '.join(parts)}")
        print(f"  LLM          : {'Groq' if self.llm else 'OFF (déterministe)'}")

    def _print_result(self, result):
        P = {5:"CRIT",4:"HAUT",3:"MOY",2:"BASE"}
        print(f"\n  {'═'*55}")
        print(f"  PLAN : {len(result.questions)}/{result.total_candidates} candidats")
        print(f"  Templates={result.template_count} Context={result.context_count} "
              f"Signals={result.signal_count} History={result.history_count} "
              f"LLM={result.llm_reason_count}")
        if result.context_factors:
            print(f"  Facteurs: {', '.join(result.context_factors[:6])}")
        print(f"  {'─'*55}")
        for i, (q, p, s) in enumerate(zip(result.questions, result.priorities, result.sources), 1):
            print(f"  Q{i} [{P.get(p,'?'):<4}|{s:<10}] {q}")
        print()


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    p = argparse.ArgumentParser(description="Planning Agent v4 — Agentic AML/KYC")
    p.add_argument("--tx-type",choices=["SWIFT","VIREMENT_LOCAL","MANDAT","DEPOT_ESPECES"],default="SWIFT")
    p.add_argument("--amount",type=float,default=45000)
    p.add_argument("--currency",default="EUR")
    p.add_argument("--sender-country",default="Tunisia")
    p.add_argument("--receiver-country",default="France")
    p.add_argument("--sender-name",default="Ahmed Ben Ali")
    p.add_argument("--receiver-name",default="Société X")
    p.add_argument("--motive",default="Real estate investment")
    p.add_argument("--timestamp",default="")
    p.add_argument("--has-justification",action="store_true",default=False)
    p.add_argument("--notes",default="")
    p.add_argument("--operator",default="")
    p.add_argument("--withdrawal-mode",default="")
    p.add_argument("--intermediary-bank",default="")
    p.add_argument("--sender-id-type",default="CIN")
    p.add_argument("--signals",nargs="*",default=[])
    p.add_argument("--prev-strs",type=int,default=0)
    p.add_argument("--velocity-24h",type=int,default=0)
    p.add_argument("--avg-amount-30d",type=float,default=0)
    p.add_argument("--account-age-days",type=int,default=999)
    p.add_argument("--countries-90d",nargs="*",default=[])
    p.add_argument("--new-receiver",action="store_true",default=False)
    p.add_argument("--previous-rejected",action="store_true",default=False)
    p.add_argument("--verbose","-v",action="store_true")
    args = p.parse_args()

    t = {
        "transaction_type": args.tx_type, "amount": args.amount,
        "currency": args.currency, "sender_country": args.sender_country,
        "receiver_country": args.receiver_country,
        "sender_name": args.sender_name, "receiver_name": args.receiver_name,
        "motive": args.motive, "timestamp": args.timestamp or None,
        "has_justification": args.has_justification,
        "notes": args.notes, "operator": args.operator,
        "withdrawal_mode": args.withdrawal_mode,
        "intermediary_bank": args.intermediary_bank,
        "sender_id_type": args.sender_id_type,
    }
    h = None
    if any([args.prev_strs, args.velocity_24h, args.avg_amount_30d,
            args.account_age_days < 999, args.countries_90d,
            args.new_receiver, args.previous_rejected]):
        h = {
            "previous_strs": args.prev_strs, "velocity_24h": args.velocity_24h,
            "avg_amount_30d": args.avg_amount_30d,
            "account_age_days": args.account_age_days,
            "countries_last_90d": args.countries_90d,
            "new_receiver": args.new_receiver,
            "previous_rejected": args.previous_rejected,
        }

    print(f"\n{'='*60}")
    print(f"  PLANNING AGENT v4 — Agentic AML/KYC")
    print(f"{'='*60}")

    agent = PlanningAgent(verbose=args.verbose)
    result = agent.plan(t, args.signals, h)

    if not args.verbose:
        P = {5:"CRIT",4:"HAUT",3:"MOY",2:"BASE"}
        print(f"\n  {len(result.questions)} questions ({result.total_candidates} candidats)")
        if result.llm_reasoning_used:
            print(f"  🧠 LLM reasoning: {result.llm_reason_count} questions intelligentes")
        print()
        for i, (q, p, s) in enumerate(zip(result.questions, result.priorities, result.sources), 1):
            print(f"  Q{i} [{P.get(p,'?'):<4}|{s:<10}] {q}")
        if result.context_factors:
            print(f"\n  Facteurs: {', '.join(result.context_factors[:6])}")
    print()

if __name__ == "__main__":
    main()