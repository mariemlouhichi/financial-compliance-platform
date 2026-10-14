"""
compliance_agent.py — v5.1 (CRISP-DM Phase 4 — Modeling)
==========================================================
Agent de conformité AML/KYC — Architecture Neuro-Symbolique Dual-Path.

AMÉLIORATIONS v5.1 (issues de la revue architecturale) :
─────────────────────────────────────────────────────────
  R1 — COUVERTURE TOTALE
       SymbolicReasoner interroge TOUTES les règles actives de Neo4j
       (status IN [GROUND_TRUTH, VALIDATED, LEARNED]), pas seulement GT.
       Le chemin sémantique ne traite QUE les gaps restants.
       → Couverture symbolique croissante transaction après transaction.

  R2 — TAXONOMIE DES RÈGLES
       4 statuts distincts avec niveaux de confiance et usages explicites :
         GROUND_TRUTH  → validation humaine, conf=1.0, référence absolue
         LEARNED       → validée auto + réutilisée ≥3×, conf≥0.80
         VALIDATED     → validée auto (anti-hallu), conf≥0.65
         CANDIDATE     → fraîchement extraite, PAS utilisée pour scoring
       La promotion est strictement contrôlée (GT = humain seulement).

  R3 — DISTINCTION HARD / SOFT SIGNALS
       Signaux HARD (sanctions, blacklist, STR légal) → violations directes
       Signaux SOFT (montant rond, heure, motif vague) → contribution risque
       Un signal SOFT ne crée JAMAIS de violation auto (pratique industrielle).

  R4 — SCORING RESIDUEL (FATF/Basel standard)
       Residual Risk = Inherent Risk × (1 − Control_Effectiveness)
       Inherent = pondération multi-facteurs (violations + signaux SOFT bornés)
       Control_Effectiveness = f(justificatif, KYC, historique, vérification)
       Sévérité définie sur 4 critères : exposition sanctions + type obligation
       + impact réglementaire + probabilité ML (recherche AMLYZE/ACAMS/Basel).

  R5 — PERSISTANCE AUTOMATIQUE NEO4J
       Toute règle sémantique validée (conf ≥ 0.65, snippet verbatim OK)
       est automatiquement persistée dans Neo4j via upsert_with_governance().
       Elle devient disponible pour le raisonnement symbolique futur.
       → Le graphe s'enrichit à chaque transaction.

PIPELINE COMPLET :
─────────────────
  Transaction + risk_signals + chunks + fatf_checklist
      │
      ▼ PHASE 1 — SYMBOLIC PATH ÉTENDU (Neo4j, GT+VALIDATED+LEARNED)
      │   Évalue TOUTES les règles actives par matching déterministe
      │   HARD signals → violations directes
      │
      ▼ PHASE 2 — GAP ANALYSIS
      │   required(checklist) − covered(symbolique) = gaps à explorer
      │
      ▼ PHASE 3 — SEMANTIC PATH (LLM, gaps uniquement)
      │   Compliance Gate → Triage TOP-12 → 2-pass LLM → SPO validation
      │   Règles validées → PERSISTÉES automatiquement dans Neo4j (R5)
      │
      ▼ PHASE 4 — RÉCONCILIATION
      │   AGREEMENT → cross-validated; CONTRADICTION réelle → flag humain
      │
      ▼ PHASE 5 — RISK SCORING (Residual = Inherent × (1−Control)) [R4]
      │
      ▼ PHASE 6 — DÉCISION EXPLICABLE + AUDIT TRAIL
      │
  ComplianceDecision { decision, risk_score, inherent, residual,
                       control_effectiveness, applicable_rules,
                       violated_rules, soft_risk, audit_trail,
                       human_review_flags, fatf_coverage, stats }

Références :
  - FATF Guidance Risk-Based Approach (inherent/residual/control)
  - Basel Committee Sound Management ML/FT
  - ACAMS AML Model Risk Management (validation humaine GT)
  - AMLYZE — non-domination des facteurs, overrides obligatoires
  - Flagright — scoring dynamique vs statique (cas 3.1 Mds$)
  - azakaw — Residual = Inherent × (1−Control_Effectiveness)
  - EBA RTS automated scoring (automated inherent/residual classification)
"""

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger("compliance_agent")


# ── VERSION & EMPREINTE DES CORRECTIONS ─────────────────────────────────
# Si cette ligne n'apparaît pas au démarrage, un ANCIEN fichier tourne.
__version__ = "6.0"
FIXES_APPLIED = "A1.sev-saturation A2.sha256 A3.gap-target B1.trigram B2.spo-src B3.pass2+downgrade B4.snippet-gate C1.fx C2.mitigate C3.jaccard C4.pessimist D5.warn E1.fatf-symbolic"
GROQ_MODEL = "llama-3.3-70b-versatile"

try:
    from groq import Groq
    _GROQ = True
except ImportError:
    _GROQ = False


# ═══════════════════════════════════════════════════════════════════════
#  STATUTS DES RÈGLES (R2)
# ═══════════════════════════════════════════════════════════════════════

class RuleStatus:
    """
    Taxonomie des règles dans Neo4j.

    GROUND_TRUTH  : Validée manuellement par un expert. conf=1.0. Référence
                    absolue pour l'évaluation precision/recall. Ne peut être
                    promue QUE par un humain. (les 49 règles extraites à la main)

    LEARNED       : Validée automatiquement + réutilisée avec succès ≥ 3 fois
                    sur des transactions distinctes. conf ≥ 0.80. Fiabilité
                    élevée, utilisable en symbolique prioritaire.

    VALIDATED     : Passé la validation anti-hallucination automatique
                    (snippet verbatim + seuil littéral + source réelle).
                    conf ≥ 0.65. Utilisable pour scoring mais moins fiable.

    CANDIDATE     : Fraîchement extraite par le LLM. PAS utilisée pour scoring.
                    En quarantaine jusqu'à validation.

    REJECTED      : Échec validation ou contradiction avérée. Archivée.
    DEPRECATED    : Remplacée par une version plus récente (SUPERSEDES).
    """
    GROUND_TRUTH = "GROUND_TRUTH"
    LEARNED      = "LEARNED"
    VALIDATED    = "VALIDATED"
    CANDIDATE    = "CANDIDATE"
    REJECTED     = "REJECTED"
    DEPRECATED   = "DEPRECATED"

    # Statuts utilisables pour le raisonnement symbolique
    ACTIVE = {GROUND_TRUTH, LEARNED, VALIDATED}

    # Seuils de confiance pour promotion
    CONF_VALIDATED = 0.65
    CONF_LEARNED   = 0.80
    USE_COUNT_LEARNED = 3   # réutilisations minimum


# ═══════════════════════════════════════════════════════════════════════
#  CLASSIFICATION DES SIGNAUX HARD / SOFT (R3)
# ═══════════════════════════════════════════════════════════════════════

# HARD : signal = fait avéré → violation directe, certaine (conf=1.0)
#        Un hit sanctions EST une violation. Un pays blacklist EST une violation.
HARD_SIGNALS = {
    "SANCTION_UN_INDIVIDUAL": ("R6",  "CRITIQUE",
        "Personne sanctionnée ONU — gel des fonds obligatoire (UNSCR 1267/1373)"),
    "SANCTION_UN_ENTITY":     ("R6",  "CRITIQUE",
        "Entité sanctionnée ONU — gel des fonds obligatoire"),
    "SANCTION_PEP":           ("R12", "HAUTE",
        "Personne politiquement exposée — mesures renforcées obligatoires"),
    "THRESHOLD_STR":          ("R20", "HAUTE",
        "Seuil réglementaire STR dépassé — déclaration de soupçon obligatoire"),
    "SMURFING_PATTERN":       ("R20", "HAUTE",
        "Fractionnement détecté — déclaration de soupçon obligatoire"),
    "COUNTRY_BLACK":          ("R19", "CRITIQUE",
        "Pays liste noire FATF — contre-mesures obligatoires"),
}

# ═══════════════════════════════════════════════════════════════════════
#  ZONES DUAL-PATH (production) — quelles recs forcer en parallèle
# ═══════════════════════════════════════════════════════════════════════
# Faits durs : tranchés symboliquement à 100% de fiabilité (sanctions,
# listes noires, financement prolifération). Le LLM n'y est PAS forcé —
# il n'apporterait aucune preuve utile, seulement un risque d'hallucination.
HARD_RECS = {"R6", "R7", "R19"}

# Tout le reste de la checklist = obligations contextuelles → zone duale
# (symbolique pour la couverture générique + sémantique pour la preuve).

# SOFT : signal = indicateur de risque → CONTRIBUTION au score, pas violation auto
#        Un montant rond seul n'est PAS une infraction. Il contribue au risque.
#        risk_weight : poids dans le calcul du risque inhérent (0.0-1.0)
SOFT_SIGNALS = {
    "COUNTRY_GREY":               ("R19", "HAUTE",   0.40),
    "AMOUNT_JUST_BELOW_THRESHOLD":("R20", "MOYENNE", 0.25),
    "HIGH_RISK_OPERATOR":         ("R14", "MOYENNE", 0.20),
    "MOTIVE_VAGUE":               (None,  "BASSE",   0.10),
    "AMOUNT_ROUND":               (None,  "BASSE",   0.10),
    "UNUSUAL_HOUR":               (None,  "BASSE",   0.08),
    "NEW_RECEIVER":               (None,  "BASSE",   0.05),
    "CASH_WITHDRAWAL":            ("R32", "BASSE",   0.08),
    # Signaux de seuil Niveau 1 DÉJÀ pris en charge par le plancher L1
    # (l1_floor : EDD=45, KYC=25, RECORD=15). Poids 0 → pas de double comptage,
    # mais reconnus (plus de warning "signal non mappé").
    "THRESHOLD_EDD":              ("R20", "MOYENNE", 0.0),
    "THRESHOLD_KYC":              ("R10", "BASSE",   0.0),
    "THRESHOLD_RECORD":           ("R20", "BASSE",   0.0),
    # Signaux CLIENT — le risque structurel est compté via `client_profile`
    # (contribution + plancher). Poids 0 ici → reconnus, pas de double comptage.
    # FATCA/CRS = FISCAL → informatifs seulement, jamais dans le score AML.
    "CLIENT_HIGH_RISK":           (None,  "BASSE",   0.0),
    "CLIENT_MEDIUM_RISK":         (None,  "BASSE",   0.0),
    "CLIENT_PRIOR_STR":           (None,  "BASSE",   0.0),
    "CLIENT_KYC_INCOMPLETE":      ("R10", "BASSE",   0.0),
    "CLIENT_FATCA_PENDING":       (None,  "BASSE",   0.0),
    "CLIENT_CRS_REPORTABLE":      (None,  "BASSE",   0.0),
}

def classify_signal(sig: str) -> tuple:
    """
    Retourne (type, fatf_rec, severity, weight/text) pour un signal.
    type = 'HARD' | 'SOFT' | 'UNKNOWN'
    """
    for prefix, (fatf, sev, text) in HARD_SIGNALS.items():
        if sig.startswith(prefix):
            return "HARD", fatf, sev, text
    for prefix, (fatf, sev, weight) in SOFT_SIGNALS.items():
        if sig.startswith(prefix):
            return "SOFT", fatf, sev, weight
    # [FIX D5] Signal non mappé → warning (avant : ignoré silencieusement,
    # un nouveau signal Level 1 disparaissait sans trace)
    logger.warning(f"Signal Level 1 non mappé (ignoré du scoring) : {sig}")
    return "UNKNOWN", None, "BASSE", 0.0


# ═══════════════════════════════════════════════════════════════════════
#  SCORING — SÉVÉRITÉS ET POIDS (R4)
# ═══════════════════════════════════════════════════════════════════════

# Poids de sévérité — définis sur 4 critères cumulatifs :
#   1. Exposition sanctions (blocage légal immédiat requis)
#   2. Type obligation (légal absolu vs procédural)
#   3. Impact réglementaire (sanction institutionnelle possible)
#   4. Probabilité ML (corrélation statistique avec blanchiment)
#
# Sources : AMLYZE, ACAMS, Basel Committee Sound Management, FATF RBA
SEVERITY_WEIGHTS = {
    "CRITIQUE": 1.00,  # Sanctions ONU/OFAC, pays blacklist, financement terrorisme
    "HAUTE":    0.70,  # Obligation STR légale, PEP, pays grey list
    "MOYENNE":  0.40,  # KYC/CDD, MVTS, EDD
    "BASSE":    0.15,  # Record keeping, obligations procédurales
}

# [FIX C1] Conversion de devises pour le matching de seuils.
# AVANT : devise différente → règle silencieusement ignorée. Un SWIFT de
# 50 000 TND (~16 000 USD) n'activait JAMAIS la règle "seuil 10 000 USD"
# → angle mort exploitable (arbitrage de devise = technique de blanchiment).
# Taux fixes mis à jour mensuellement (simplification PFE documentée,
# même approche que les listes de sanctions mensuelles).
FX_RATES_TO_USD = {
    "USD": 1.00, "EUR": 1.08, "TND": 0.32,
    "GBP": 1.27, "CHF": 1.13, "SAR": 0.27, "AED": 0.27,
}

def to_usd(amount: float, currency: str) -> float:
    """Convertit un montant en équivalent USD (taux mensuels fixes)."""
    return float(amount) * FX_RATES_TO_USD.get((currency or "").upper(), 1.0)


# Autorité des sources documentaires
SOURCE_AUTHORITY = {
    "loi_2015_26_aml_cft.pdf": 1.00, "loi_2016_48_banques.pdf": 1.00,
    "bct_circulaire_2017_08_aml.pdf": 0.95, "bct_circulaire_2018_09_aml_update.pdf": 0.95,
    "bct_circulaire_2024_02_aml.pdf": 0.95, "bct_circulaire_2025_17_aml_refonte.pdf": 0.95,
    "bct_circulaire_2026_02_change.pdf": 0.90,
    "2024_Reglementation_Bancaire.pdf": 0.90, "2024_Reglementation_des_Changes.pdf": 0.90,
    "ctaf_rapport_activite_2023.pdf": 0.85, "ctaf_rapport_activite_2024.pdf": 0.85,
    "ctaf_note_pays_haut_risque.pdf": 0.85,
    "fatf_recommendations_2025.pdf": 0.80, "fatf_correspondent_banking.pdf": 0.80,
    "fatf_mvts_guidance_2016.pdf": 0.80, "fatf_peps_guidance.pdf": 0.80,
    "fatf_proliferation_financing_2021.pdf": 0.80,
    "eu_transfer_funds_regulation_2023_1113.pdf": 0.75,
    "eba_gl_2021_02_risk_factors.pdf": 0.70, "basel_aml_sound_management_d353.pdf": 0.70,
    "wolfsberg_cdd_guidance_2019.pdf": 0.70,
}

ROLE_SCORE = {
    "OBLIGATION": 1.0, "SANCTIONS": 1.0, "REPORTING": 0.9, "KYC": 0.85,
    "THRESHOLD": 0.85, "PROCEDURE": 0.7, "RISK_ASSESSMENT": 0.65,
    "RED_FLAG": 0.6, "DEFINITION": 0.1, "UNKNOWN": 0.3,
}

AML_KEYWORDS = {
    "must", "shall", "required", "obligation", "report", "suspicious",
    "threshold", "declaration", "ctaf", "str", "freeze", "sanctions",
    "identify", "verify", "prohibited", "customer", "beneficial",
    "correspondent", "wire", "transfer",
}
PREDICATE_WORDS = {
    "must", "shall", "should", "required", "obliged", "prohibited",
    "report", "declare", "freeze", "identify", "verify", "monitor", "retain",
}
KNOWN_FATF_RECS = {
    f"R{n}" for n in [1,6,7,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,27,29,32,35]
}

# [FIX A3] Mots-clés par dimension FATF — utilisés pour ORIENTER le triage
# des chunks vers les gaps détectés (le gap analysis ne servait qu'à décider
# SI on appelait le LLM, pas À QUOI le faire travailler).
GAP_KEYWORDS = {
    "R6":  {"sanctions", "freeze", "designated", "asset"},
    "R10": {"customer due diligence", "identification", "identify", "verify"},
    "R11": {"record", "retention", "keep", "retain", "five years"},
    "R12": {"politically exposed", "pep"},
    "R13": {"correspondent"},
    "R14": {"money or value transfer", "mvts", "remittance"},
    "R16": {"originator", "wire transfer", "beneficiary information", "travel rule"},
    "R19": {"higher-risk", "high risk countr", "enhanced due diligence", "countermeasure"},
    "R20": {"suspicious", "report", "str", "declaration"},
    "R24": {"beneficial owner", "ownership", "control structure", "legal person"},
    "R32": {"cash courier", "cross-border transport", "physical"},
}


# ═══════════════════════════════════════════════════════════════════════
#  DATACLASSES
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class SPOTriplet:
    """Subject-Predicate-Object [RAGulating arXiv:2508.09893]."""
    subject: str
    predicate: str
    obj: str

    def verify_in_text(self, text: str) -> float:
        t = text.lower()
        hits = sum([
            any(w in t for w in self.subject.lower().split()),
            any(w in t for w in self.predicate.lower().split()
                if w in PREDICATE_WORDS),
            len(self.obj) > 3 and self.obj.lower()[:30] in t,
        ])
        return hits / 3.0


@dataclass
class ComplianceRule:
    """Règle de conformité unifiée (symbolique ET sémantique)."""
    rule_id:         str
    rule_text:       str
    applies:         bool   = True
    violated:        bool   = False
    severity:        str    = "MOYENNE"
    category:        str    = "KYC"
    fatf_rec:        str    = ""
    # Provenance
    provenance:      str    = "SEMANTIC"   # SYMBOLIC | SEMANTIC | RECONCILED
    rule_status:     str    = RuleStatus.CANDIDATE
    source_doc:      str    = ""
    rule_ref:        str    = ""
    source_snippet:  str    = ""
    threshold:       Optional[float] = None
    threshold_unit:  str    = ""
    # Scoring
    confidence:      float  = 0.5
    reasoning:       str    = ""
    # Validation
    spo:             Optional[SPOTriplet] = None
    snippet_ok:      bool   = False
    snippet_verbatim:bool   = False   # [B1] snippet trouvé VERBATIM (pas trigrammes)
    threshold_ok:    bool   = False
    cross_validated: bool   = False
    # [Option complète] réconciliation consciente de la source
    cross_source_validated: bool = False  # corroboré par une AUTRE famille (BCT↔FATF) = corroboration indépendante forte
    corroborating_families: list = field(default_factory=list)  # familles de sources qui confirment l'obligation
    pass2_completed: bool   = False   # True = Pass2 a tourné et conclus
    mitigated:       bool   = False   # [C2] violation atténuée par justificatif
    # Enrichissement Neo4j
    persisted:       bool   = False   # True = déjà persistée dans Neo4j


@dataclass
class RiskComponents:
    """Composantes détaillées du scoring de risque (R4)."""
    inherent_violations: float = 0.0  # contribution des violations détectées
    inherent_soft:       float = 0.0  # contribution des signaux SOFT (bornée ≤30%)
    inherent_client:     float = 0.0  # contribution du risque CLIENT structurel (FATF RBA)
    inherent_total:      float = 0.0  # = violations + soft + client
    control_eff:         float = 0.0  # efficacité des contrôles (0.0-0.80)
    residual:            float = 0.0  # = inherent × (1 - control_eff)
    risk_score:          float = 0.0  # = residual × 100


@dataclass
class AuditEntry:
    """Trace d'une étape de raisonnement."""
    phase:     str
    action:    str
    detail:    str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ComplianceDecision:
    """Décision finale explicable — CRISP-DM Phase 4 output."""
    decision:            str   = "PASS"
    risk_score:          float = 0.0
    risk_components:     Optional[RiskComponents] = None
    applicable_rules:    list  = field(default_factory=list)
    violated_rules:      list  = field(default_factory=list)
    fatf_coverage:       dict  = field(default_factory=dict)
    binding_coverage:    dict  = field(default_factory=dict)  # rec → couverte par source BCT contraignante
    source_coverage:     dict  = field(default_factory=dict)  # [Option complète] rec → familles de sources qui la couvrent (BCT, FATF, UE…)
    client_context:      dict  = field(default_factory=dict)  # risque client structurel + FATCA/CRS (informatif)
    human_review_flags:  list  = field(default_factory=list)
    audit_trail:         list  = field(default_factory=list)
    stats:               dict  = field(default_factory=dict)

    def explain(self) -> str:
        lines = [f"DÉCISION : {self.decision} | Score risque : {self.risk_score:.0f}/100"]
        if self.risk_components:
            rc = self.risk_components
            lines.append(
                f"Décomposition : inherent={rc.inherent_total:.2f} "
                f"× (1 - control={rc.control_eff:.2f}) = résiduel={rc.residual:.2f}"
            )
        if self.violated_rules:
            lines.append(f"\n{len(self.violated_rules)} violation(s) :")
            for r in self.violated_rules:
                tag = "GT" if r.rule_status == RuleStatus.GROUND_TRUTH else \
                      "SYM" if r.provenance == "SYMBOLIC" else "LLM"
                lines.append(
                    f"  [{tag}|{r.severity[:4]}|{r.fatf_rec or '?':>4}] "
                    f"{r.rule_text[:65]}"
                )
        if self.human_review_flags:
            lines.append(f"\n⚠️ {len(self.human_review_flags)} point(s) revue humaine :")
            for f in self.human_review_flags:
                lines.append(f"  • {f[:100]}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
#  SYMBOLIC REASONER ÉTENDU (R1 + R3)
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
#  MAPPING FATF DÉTERMINISTE (E1) — règle réglementaire → recommandation
# ═══════════════════════════════════════════════════════════════════════

# Mots-clés → recommandation FATF, du plus SPÉCIFIQUE au plus général.
# Source : FATF 40 Recommendations (2012, maj 2023). Mapping fixe et auditable.
_FATF_KEYWORDS = [
    ("R7",  ["proliferation", "prolifération", "weapons of mass", "1718", "dprk"]),
    ("R12", ["politically exposed", "pep", "personne politiquement", "prominent public"]),
    ("R13", ["correspondent bank", "correspondant", "shell bank", "respondent institution"]),
    ("R14", ["money or value transfer", "money value transfer", "mvts",
             "money transfer service", "western union", "moneygram", "remittance", "hawala"]),
    ("R16", ["wire transfer", "travel rule", "originator", "beneficiary information",
             "fund transfer", "cross-border transfer"]),
    ("R24", ["beneficial owner", "bénéficiaire effectif", "ultimate owner", "ownership structure"]),
    ("R11", ["record keeping", "retain", "retention", "conservation", "keep records",
             "maintain records", "archiv"]),
    ("R19", ["high-risk countr", "high risk countr", "haut risque", "country risk",
             "geographical risk", "jurisdiction risk"]),
    ("R6",  ["targeted financial sanction", "asset freez", "freeze", "terrorist financing",
             "gel des fonds", "sanction list", "designated person"]),
    ("R20", ["suspicious transaction", "suspicious activity", "report ", "reporting",
             "declaration", "soupçon", "ctaf", "notify"]),
    ("R10", ["customer due diligence", "due diligence", "identify", "verify", "cdd",
             "kyc", "customer identification", "vigilance", "identification", "verification"]),
    ("R1",  ["risk assessment", "risk-based approach", "assess the risk", "risk factors",
             "évaluation des risques", "overall risk"]),
]

_FATF_BY_ROLE = {
    "KYC": "R10", "SANCTIONS": "R6", "REPORTING": "R20", "THRESHOLD": "R20",
    "RISK_ASSESSMENT": "R1", "RED_FLAG": "R20", "OBLIGATION": "R10",
}


def map_to_fatf_rec(chunk_role: str, text: str, article: str = "") -> str:
    """
    Mappe DÉTERMINISTIQUEMENT une règle réglementaire vers une recommandation FATF.

    [E1] Permet au chemin SYMBOLIQUE de contribuer à la couverture FATF sans LLM.
         Priorité au contenu (mots-clés spécifiques), repli sur le chunk_role.
         Aucune invention : mapping fixe, auditable, reproductible.
    """
    blob = f"{text} {article}".lower()
    for rec, kws in _FATF_KEYWORDS:
        if any(k in blob for k in kws):
            return rec
    return _FATF_BY_ROLE.get((chunk_role or "").upper(), "R10")


# ═══════════════════════════════════════════════════════════════════════
#  SOURCE CONTRAIGNANTE (BCT-first) — droit tunisien vs standards de référence
# ═══════════════════════════════════════════════════════════════════════

def source_family(source_doc: str) -> str:
    """Régulateur d'origine d'un document source."""
    d = (source_doc or "").lower()
    if d.startswith("bct_") or "reglementation" in d:                       return "BCT"
    if d.startswith("ctaf") or d.startswith("loi_") or "decret" in d:       return "Tunisie"
    if d.startswith("fatf") or d.startswith("gafi"):                        return "FATF"
    if d.startswith("amld") or d.startswith("eu_") or d.startswith("eba"):  return "UE"
    if d.startswith("basel") or d.startswith("wolfsberg"):                  return "Basel"
    if d.startswith("level1") or "level1" in d:                             return "BCT"  # seuils L1 = BCT
    return "autre"

# Sources CONTRAIGNANTES pour une banque tunisienne (droit applicable).
# Les standards internationaux (FATF/UE/Basel) sont de la RÉFÉRENCE : ils
# n'ont force de loi en Tunisie que transposés par une circulaire BCT / une loi.
BINDING_FAMILIES = {"BCT", "Tunisie"}

def is_binding(source_doc: str) -> bool:
    """True si la source est du droit tunisien contraignant (BCT/loi/CTAF)."""
    return source_family(source_doc) in BINDING_FAMILIES


class SymbolicReasoner:
    """
    Raisonnement symbolique sur TOUTES les règles actives de Neo4j.

    [R1] Couverture : règles GROUND_TRUTH (origin='human_verified') + LEARNED
         + VALIDATED. Ordre de priorité : GT > LEARNED > VALIDATED.

    [E1] Chaque règle reçoit un fatf_rec déterministe → contribue à la
         couverture FATF même sans LLM (correction du décalage de schéma :
         l'ancienne requête ignorait les 49 règles GT).

    [R3] HARD signals → violations directes (certitude 1.0)
         SOFT signals → contributions au risque seulement (pas de violation)
    """

    def __init__(self, neo4j_driver=None, verbose: bool = False):
        self.driver  = neo4j_driver
        self.verbose = verbose
        self._api    = None
        if neo4j_driver:
            try:
                import sys, pathlib
                sys.path.insert(0, str(pathlib.Path(__file__).parent))
                from neo4j_graph.agent_interface import AgentGraphAPI
                self._api = AgentGraphAPI(neo4j_driver)
            except Exception as e:
                logger.debug(f"AgentGraphAPI unavailable: {e}")

    def reason(self, transaction: dict, risk_signals: list) -> tuple:
        """
        Returns: (symbolic_rules, soft_risk_weight)
        - symbolic_rules : violations déterministes
        - soft_risk_weight : contribution totale des signaux SOFT (0.0-0.30)
        """
        soft_weight = 0.0

        # ── 1. Règles actives Neo4j → COUVERTURE FATF déterministe (E1) ──
        all_active = self._fetch_all_active_rules(
            transaction.get("tx_type", "SWIFT"), risk_signals
        )
        raw = []
        for gt in all_active:
            rule = self._evaluate_rule(gt, transaction)
            if rule:
                raw.append(rule)

        # [E1] Dédoublonnage par recommandation FATF : UNE règle représentative
        #      par rec. Ordre de préférence (BCT-first) :
        #        violée > source CONTRAIGNANTE (BCT/loi/CTAF) > confiance.
        #      → si une rec est couverte par BCT ET FATF, on GARDE la BCT
        #        (droit tunisien applicable cité en priorité).
        def _rank(r):
            return (r.violated, is_binding(r.source_doc), r.confidence)
        best_by_rec = {}
        for r in raw:
            if not r.fatf_rec:
                continue
            cur = best_by_rec.get(r.fatf_rec)
            if cur is None or _rank(r) > _rank(cur):
                best_by_rec[r.fatf_rec] = r
        rules = list(best_by_rec.values())

        # ── 2. HARD signals → violations symboliques directes ────────
        for sig in risk_signals:
            sig_type, fatf, sev, text = classify_signal(sig)
            if sig_type == "HARD":
                # N'ajoute que si aucune VIOLATION existante pour ce fatf
                # (une simple couverture GT ne doit pas masquer la violation L1)
                if not any(r.fatf_rec == fatf and r.violated for r in rules):
                    rules.append(ComplianceRule(
                        rule_id=f"HARD_{fatf}_{sig[:20]}",
                        rule_text=text,
                        applies=True, violated=True,
                        severity=sev, fatf_rec=fatf,
                        provenance="SYMBOLIC",
                        rule_status=RuleStatus.GROUND_TRUTH,
                        confidence=1.0,
                        pass2_completed=True,
                        reasoning=f"Signal Level 1 déterministe : {sig}",
                        source_doc="level1_bct_threshold",  # L1 = seuils BCT → contraignant
                        rule_ref="Niveau 1 — seuil BCT/sanctions",
                    ))

        # ── 3. SOFT signals → poids risque (pas de violation) ────────
        for sig in risk_signals:
            sig_type, fatf, sev, weight = classify_signal(sig)
            if sig_type == "SOFT":
                soft_weight += weight
        soft_weight = min(soft_weight, 0.30)  # non-domination : ≤30%

        return rules, soft_weight

    def _fetch_all_active_rules(self, tx_type: str, risk_signals: list) -> list:
        """
        [E1] Récupère TOUTES les règles actives pour la couverture FATF.

        Corrige le DÉCALAGE DE SCHÉMA : les 49 règles ground truth ont
        origin='human_verified' (PAS de champ status, PAS de relation
        APPLIES_TO). L'ancienne requête exigeait status IN [...] + APPLIES_TO
        → elle les ignorait toutes (couverture symbolique = 0).

        Les champs sont normalisés via coalesce pour gérer les 2 schémas :
          - GT humain  : component_subject/constraint, source_text_snippet,
                         source_article, chunk_role, threshold_value
          - persistées : name, description, threshold, threshold_currency, status
        """
        if not self.driver:
            return []
        try:
            with self.driver.session() as s:
                q = """
                MATCH (r:Rule)
                WHERE r.origin = 'human_verified'
                   OR r.status IN ['GROUND_TRUTH', 'LEARNED', 'VALIDATED']
                RETURN r.id                                              AS id,
                       coalesce(r.name, r.component_subject, '')         AS name,
                       coalesce(r.description, r.component_constraint,
                                r.source_text_snippet, '')               AS description,
                       coalesce(r.severity, 'MOYENNE')                   AS severity,
                       coalesce(r.category, r.chunk_role, 'KYC')         AS category,
                       r.chunk_role                                      AS chunk_role,
                       coalesce(r.threshold, r.threshold_value)          AS threshold,
                       coalesce(r.threshold_currency, '')                AS threshold_currency,
                       coalesce(r.confidence, 0.9)                       AS confidence,
                       coalesce(r.status, 'GROUND_TRUTH')                AS status,
                       coalesce(r.source_article,
                                r.action_description, r.name, '')        AS source_article,
                       coalesce(r.source_doc, '')                        AS source_doc,
                       coalesce(r.source_text_snippet, r.source_snippet,
                                r.component_constraint, '')              AS snippet
                ORDER BY
                    CASE coalesce(r.status, 'GROUND_TRUTH')
                        WHEN 'GROUND_TRUTH' THEN 1
                        WHEN 'LEARNED'      THEN 2
                        WHEN 'VALIDATED'    THEN 3
                        ELSE 4
                    END,
                    coalesce(r.confidence, 0.9) DESC
                LIMIT 80
                """
                return [dict(r) for r in s.run(q)]
        except Exception as e:
            logger.debug(f"Neo4j query failed: {e}")
            return []

    def _evaluate_rule(self, gt: dict, tx: dict) -> Optional[ComplianceRule]:
        """
        Matching déterministe : attributs transaction vs conditions règle.

        [E1] fatf_rec déterministe → contribue à la COUVERTURE FATF.
             Les règles GROUND_TRUTH sont des obligations NON quantifiées
             (« identifier le client », « conserver les données »…) : elles
             attestent que le système CONNAÎT l'obligation, mais ne créent PAS
             de violation automatique (une obligation de CDD ne se « viole »
             pas via un seuil). Les violations restent pilotées par les signaux
             HARD (Niveau 1) et les règles à seuil validées.
        """
        status   = gt.get("status") or "GROUND_TRUTH"
        is_gt    = status == "GROUND_TRUTH"

        # [E1] fatf_rec déterministe — contenu réglementaire → recommandation
        fatf_rec = map_to_fatf_rec(
            gt.get("chunk_role", ""),
            f"{gt.get('description','')} {gt.get('snippet','')} {gt.get('category','')}",
            gt.get("source_article", ""),
        )

        # GT = couverture seule ; seules les règles à seuil VALIDÉES violent
        threshold = None if is_gt else gt.get("threshold")
        currency  = gt.get("threshold_currency", "") or ""
        tx_amount = float(tx.get("amount", 0))
        tx_cur    = (tx.get("currency") or "").upper()
        has_just  = bool(tx.get("has_justification", False))

        def _coverage_only(reason: str) -> ComplianceRule:
            return ComplianceRule(
                rule_id=gt.get("id", "GT_?"),
                rule_text=gt.get("description") or gt.get("name", ""),
                applies=True, violated=False,
                severity=gt.get("severity", "MOYENNE"),
                category=gt.get("category", "KYC"),
                fatf_rec=fatf_rec,
                provenance="SYMBOLIC",
                rule_status=status,
                source_doc=gt.get("source_doc", ""),
                rule_ref=gt.get("source_article") or gt.get("name", ""),
                # [Front] extrait verbatim du graphe (source_text_snippet ancré
                # lors de la construction du graphe) → carte avec citation ❝❞.
                source_snippet=gt.get("snippet", "") or "",
                snippet_verbatim=bool((gt.get("snippet") or "").strip()),
                threshold=threshold, threshold_unit=currency,
                confidence=float(gt.get("confidence", 0.9)),
                pass2_completed=True,
                reasoning=reason,
            )

        # Pas de seuil testable → couverture seule (atteste l'obligation)
        if threshold is None:
            return _coverage_only(f"Obligation applicable — couverture FATF {fatf_rec}")

        # [FIX C1] Comparaison en ÉQUIVALENT USD (une règle "10 000 USD"
        # s'applique à 50 000 TND ~16 000 USD).
        thr_usd = to_usd(float(threshold), currency or tx_cur)
        tx_usd  = to_usd(tx_amount, tx_cur)
        if tx_usd <= thr_usd:
            # Sous le seuil → on GARDE la règle pour la couverture (sans violation)
            return _coverage_only(f"Sous le seuil — couverture FATF {fatf_rec}")
        cross_currency = bool(currency) and tx_cur != currency.upper()

        # [FIX C2] Le justificatif n'ANNULE plus la violation — il l'ATTÉNUE.
        violated  = True
        mitigated = has_just
        sev = gt.get("severity", "MOYENNE")
        fx_note = " [équiv. USD]" if cross_currency else ""
        reasoning = (f"Montant {tx_amount:,.0f} {tx_cur} > seuil "
                     f"{threshold:,.0f} {currency}{fx_note}"
                     + (" — atténué par justificatif" if mitigated else ""))

        return ComplianceRule(
            rule_id=gt.get("id", "GT_?"),
            rule_text=gt.get("description") or gt.get("name", ""),
            applies=True,
            violated=violated,
            mitigated=mitigated,
            severity=sev,
            category=gt.get("category", "KYC"),
            fatf_rec=fatf_rec,
            provenance="SYMBOLIC",
            rule_status=status,
            source_doc=gt.get("source_doc", ""),
            rule_ref=gt.get("source_article") or gt.get("name", ""),
            # [Front] extrait verbatim du graphe → carte avec citation ❝❞.
            source_snippet=gt.get("snippet", "") or "",
            snippet_verbatim=bool((gt.get("snippet") or "").strip()),
            threshold=threshold,
            threshold_unit=currency,
            confidence=float(gt.get("confidence", 0.9)),
            pass2_completed=True,  # symbolic = déterministe = toujours complet
            reasoning=reasoning,
        )


# ═══════════════════════════════════════════════════════════════════════
#  COMPLIANCE GATE [GraphCompliance arXiv:2510.26309]
# ═══════════════════════════════════════════════════════════════════════

class ComplianceGate:
    """Filtre déterministe avant LLM — économise les appels LLM."""

    TYPE_KEYWORDS = {
        "SWIFT": {"swift", "wire transfer", "international", "correspondent", "cross-border"},
        "VIREMENT_LOCAL": {"local transfer", "domestic", "virement local"},
        "DEPOT_ESPECES": {"cash deposit", "cash", "espèces", "dépôt"},
        "MANDAT": {"money order", "mandat", "mvts", "western union", "transfer service"},
    }

    def __init__(self, transaction: dict):
        self.tx = transaction

    def check(self, chunk) -> tuple:
        text = self._g(chunk, "text").lower()
        role = self._g(chunk, "chunk_role", "UNKNOWN").upper()
        if role == "DEFINITION":
            return False, "DEFINITION — no actionable rule"
        tx_type = self.tx.get("tx_type", "SWIFT")
        specific = any(kw in text for kws in self.TYPE_KEYWORDS.values() for kw in kws)
        if specific and not any(kw in text for kw in self.TYPE_KEYWORDS.get(tx_type, set())):
            return False, f"Chunk spécifique autre tx_type"
        # Seuil incohérent
        for amt_str, cur in re.findall(r"\b(\d[\d,\s.]*)\s*(USD|EUR|TND)\b",
                                        self._g(chunk, "text"), re.I):
            try:
                ca = float(amt_str.replace(",","").replace(" ",""))
                ta = float(self.tx.get("amount", 0))
                if cur.upper() == (self.tx.get("currency","")).upper() and ca > ta*3 and ca > 50000:
                    return False, f"Seuil chunk >> tx"
            except ValueError:
                pass
        return True, "OK"

    @staticmethod
    def _g(c, f, d=""): return str(c.get(f, d) or d) if isinstance(c, dict) else str(getattr(c, f, d) or d)


# ═══════════════════════════════════════════════════════════════════════
#  SEMANTIC REASONER + PERSISTANCE NEO4J (R5)
# ═══════════════════════════════════════════════════════════════════════

class SemanticReasoner:
    """
    Extraction LLM des règles depuis les chunks (gaps uniquement).
    [R5] Règles validées (conf ≥ 0.65) → persistées automatiquement dans Neo4j.
    """

    TOP_K = 12
    BATCH = 5
    SUBJECTS = ["financial institution", "obliged entity", "bank",
                "subject entit", "institution", "money transfer", "MVTS"]

    def __init__(self, llm=None, neo4j_driver=None, verbose: bool = False):
        self.llm     = llm
        self.driver  = neo4j_driver
        self.verbose = verbose
        self.rejected_ungrounded = 0   # [B4] règles rejetées (snippet non ancré)

    def reason(self, transaction: dict, chunks: list,
               gap_dims: set, risk_signals: list) -> list:
        if not self.llm or not chunks:
            return []
        gate   = ComplianceGate(transaction)
        passed = [c for c in chunks if gate.check(c)[0]]
        top    = self._triage(passed, gap_dims)

        raw = []
        for i in range(0, len(top), self.BATCH):
            batch = top[i:i+self.BATCH]
            try:
                batch_rules = self._pass1(transaction, batch, risk_signals,
                                           len(raw), gap_dims)
                raw.extend(batch_rules)
                if i + self.BATCH < len(top):
                    time.sleep(0.5)  # éviter rate limit Groq
            except Exception as e:
                if "429" in str(e) or "rate_limit" in str(e).lower():
                    logger.info("Rate limit → pause 8s + retry")
                    time.sleep(8)
                    try:
                        raw.extend(self._pass1(transaction, batch,
                                                risk_signals, len(raw), gap_dims))
                    except Exception as e2:
                        logger.warning(f"Pass1 retry failed: {e2}")
                else:
                    logger.warning(f"Pass1 failed: {e}")

        validated  = self._validate(raw, chunks)
        # [B4] GATE ANTI-HALLUCINATION : une règle sémantique n'est ADMISE que
        # si son snippet est ANCRÉ dans le corpus (verbatim ou trigrammes ≥60%).
        # Une règle au snippet NON vérifiable est REJETÉE — on n'admet aucun
        # « claim » réglementaire non sourçable, même si sa confiance ≥ 0.5
        # (la confiance pouvait être gonflée par l'autorité de la source +
        # les mots-clés AML communs). → fait chuter le taux d'hallucination.
        candidates = [r for r in validated if r.applies and r.confidence >= 0.5]
        applicable = [r for r in candidates if r.snippet_ok]
        self.rejected_ungrounded = len(candidates) - len(applicable)
        if applicable:
            self._pass2(transaction, applicable, risk_signals)

        # [R5] Persistance automatique des règles validées dans Neo4j
        self._persist_validated(applicable, transaction.get("tx_type", "SWIFT"))

        return applicable

    def _triage(self, chunks: list, gap_dims: set = None) -> list:
        # [FIX A3] Le triage BOOSTE les chunks pertinents aux dimensions FATF
        # manquantes — le sémantique cherche désormais à COMBLER les gaps,
        # pas seulement à trier par autorité générique.
        gap_dims = gap_dims or set()
        gap_kws = set()
        for d in gap_dims:
            gap_kws |= GAP_KEYWORDS.get(d, set())
        scored = []
        for c in chunks:
            src  = self._g(c, "source_doc")
            role = self._g(c, "chunk_role", "UNKNOWN").upper()
            text = self._g(c, "text")
            tl   = text.lower()
            auth = SOURCE_AUTHORITY.get(src, 0.5)
            rs   = ROLE_SCORE.get(role, 0.3)
            rrf  = min(float(c.get("score", 0) if isinstance(c,dict)
                             else getattr(c,"score",0)) / 0.035, 1.0)
            dens = len(AML_KEYWORDS & set(tl.split())) / len(AML_KEYWORDS)
            gap_boost = 1.0 if gap_kws and any(kw in tl for kw in gap_kws) else 0.0
            score = auth*0.30 + rs*0.25 + rrf*0.15 + dens*0.10 + gap_boost*0.20
            if role == "DEFINITION":
                score *= 0.2
            scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:self.TOP_K]]

    def _pass1(self, tx, batch, signals, offset, gap_dims=None):
        chunks_str = ""
        for j, c in enumerate(batch, 1):
            chunks_str += (
                f"\n[{j}] {self._g(c,'source_doc')} | "
                f"{self._g(c,'article_ref')} | {self._g(c,'chunk_role')}\n"
                f"{self._g(c,'text')[:420]}\n"
            )
        gap_line = (f"PRIORITY FATF dimensions to cover: {sorted(gap_dims)}\n"
                    if gap_dims else "")
        prompt = (
            "AML compliance: extract regulatory obligations that APPLY to this transaction.\n"
            "Focus ONLY on applicability — do NOT assess violations here.\n"
            + gap_line + "\n"
            f"TRANSACTION:\n"
            f"  Type: {tx.get('tx_type','?')} | Amount: {tx.get('amount','?')} "
            f"{tx.get('currency','?')}\n"
            f"  Sender: {tx.get('sender_name','?')} ({tx.get('sender_country','?')})\n"
            f"  Receiver: {tx.get('beneficiary_name','?')} "
            f"({tx.get('destination_country','?')})\n"
            f"  Justification: {tx.get('has_justification',False)} | Signals: {signals}\n\n"
            f"REGULATORY CHUNKS:\n{chunks_str}\n"
            "For each obligation: rule_text, applies(bool), source_snippet(verbatim ≤80 chars), "
            "threshold(num|null), threshold_unit, rule_ref, source_doc, "
            "fatf_rec(R6/R10/R11/R12/R13/R16/R19/R20/R24/R32|null), "
            "severity(CRITIQUE/HAUTE/MOYENNE/BASSE), category, "
            "llm_confidence(0-1), reasoning.\n\n"
            "ANTI-HALLUCINATION: source_snippet MUST be verbatim. "
            "threshold MUST appear as a number in the chunk. No invented rules.\n\n"
            'JSON array only: [{"rule_text":"...","applies":true,"source_snippet":"exact",'
            '"threshold":10000,"threshold_unit":"USD","rule_ref":"BCT 2017-08 Art.43",'
            '"source_doc":"...","fatf_rec":"R20","severity":"HAUTE","category":"STR",'
            '"llm_confidence":0.9,"reasoning":"..."}]\n'
            "If none: []"
        )
        resp = self.llm.chat.completions.create(
            model=GROQ_MODEL, messages=[{"role":"user","content":prompt}],
            temperature=0.0, max_tokens=2000)
        raw = re.sub(r"```json\s*|```\s*$", "",
                     resp.choices[0].message.content.strip())
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            data = [data]
        return [ComplianceRule(
            rule_id=f"SEM_{offset+k+1:03d}",
            rule_text=d.get("rule_text",""),
            applies=bool(d.get("applies",True)),
            severity=d.get("severity","MOYENNE"), category=d.get("category","KYC"),
            fatf_rec=d.get("fatf_rec","") or "", provenance="SEMANTIC",
            rule_status=RuleStatus.CANDIDATE,
            source_doc=d.get("source_doc","") or "", rule_ref=d.get("rule_ref","") or "",
            source_snippet=d.get("source_snippet") or "", threshold=d.get("threshold"),
            threshold_unit=d.get("threshold_unit","") or "",
            confidence=float(d.get("llm_confidence",0.5)),
            reasoning=d.get("reasoning",""),
        ) for k, d in enumerate(data) if d.get("rule_text")]

    def _validate(self, rules, all_chunks):
        """Validation SPO + score composite [AMI Score + Trustworthiness]."""
        all_text = " ".join(self._g(c,"text") for c in all_chunks).lower()
        src_set  = {self._g(c,"source_doc") for c in all_chunks}
        # [FIX B2] index texte par document source pour validation ciblée
        text_by_src = {}
        for c in all_chunks:
            sd = self._g(c, "source_doc")
            text_by_src[sd] = text_by_src.get(sd, "") + " " + self._g(c, "text").lower()

        for r in rules:
            # [FIX B2] SPO validé contre le chunk SOURCE déclaré, pas le
            # corpus concaténé (évite la cross-contamination : sujet trouvé
            # dans chunk 3 + objet dans chunk 7 = fausse validation)
            ref_text = text_by_src.get(r.source_doc, all_text)
            spo = self._spo(r.rule_text)
            r.spo = spo
            spo_sc = spo.verify_in_text(ref_text) if spo else 0.0
            # [FIX B1] Snippet : verbatim OU trigrammes CONSÉCUTIFS.
            # L'ancien matching (70% des mots individuels) validait des
            # snippets inventés composés de mots AML ultra-communs
            # (institutions/must/report/transactions → tous présents).
            snip = (r.source_snippet or "").lower().strip()
            snip_sc = 0.0
            r.snippet_verbatim = False
            if len(snip) > 10:
                if snip in all_text:
                    snip_sc = 1.0
                    r.snippet_ok = True
                    r.snippet_verbatim = True
                else:
                    words = snip.split()
                    if len(words) >= 4:
                        trigrams = [" ".join(words[i:i+3])
                                    for i in range(len(words)-2)]
                        hits = sum(1 for t in trigrams if t in all_text)
                        if hits >= len(trigrams) * 0.6:
                            snip_sc = 0.6
                            r.snippet_ok = True
            # [FIX B1] Threshold absent = NEUTRE (0.5), pas récompensé (0.8).
            # L'ancien défaut donnait 0.20 de confiance gratuite aux règles
            # qualitatives → une hallucination pouvait atteindre 0.79.
            thr_sc = 0.5
            if r.threshold is not None:
                n = int(r.threshold)
                thr_sc = 1.0 if any(v in all_text for v in [
                    str(n), f"{n:,}", f"{n:,}".replace(","," "), f"{n:,}".replace(",",".")
                ]) else 0.0
                r.threshold_ok = thr_sc == 1.0
            # Source authority
            ref_sc = SOURCE_AUTHORITY.get(r.source_doc, 0.5) if r.source_doc in src_set else 0.0
            # FATF rec
            fatf_sc = 1.0 if r.fatf_rec in KNOWN_FATF_RECS else 0.0
            # Composite [AMI Score] — spo_sc intégré au signal de qualité
            qual_sc = max(spo_sc,
                          0.8 if any(v in r.rule_text.lower()
                                     for v in PREDICATE_WORDS) else 0.3)
            r.confidence = round(
                snip_sc*0.30 + thr_sc*0.25 + ref_sc*0.20
                + qual_sc*0.15 + fatf_sc*0.10, 3)
            # Promotion de statut (si validée)
            if r.confidence >= RuleStatus.CONF_VALIDATED and r.snippet_ok:
                r.rule_status = RuleStatus.VALIDATED
        return rules

    def _spo(self, text):
        tl = text.lower()
        subj = next((s for s in self.SUBJECTS if s in tl), "entity")
        pred = next((v for v in ["must not","shall not","must","shall","should","required"]
                     if v in tl), "")
        if not pred:
            return None
        parts = tl.split(pred, 1)
        obj = re.sub(r"\s+", " ", parts[1][:80]).strip(".,;") if len(parts) > 1 else ""
        return SPOTriplet(subj, pred, obj)

    def _pass2(self, tx, rules, signals):
        """
        Pass 2 : violation detection.
        Déterministe d'abord (seuil), LLM pour les règles sans seuil.
        """
        tx_amount = float(tx.get("amount", 0))
        tx_cur    = (tx.get("currency") or "").upper()
        has_just  = bool(tx.get("has_justification", False))

        for r in rules:
            if r.threshold is not None:
                # [FIX C1] comparaison en équivalent USD (cross-devise)
                thr_usd = to_usd(r.threshold, r.threshold_unit or tx_cur)
                tx_usd  = to_usd(tx_amount, tx_cur)
                if tx_usd > thr_usd:
                    r.violated = True
                    # [FIX C2] justificatif atténue, n'annule plus
                    r.mitigated = has_just
                    r.pass2_completed = True
                    r.reasoning = (r.reasoning or
                        f"Montant {tx_amount:,.0f} {tx_cur} > seuil "
                        f"{r.threshold:,.0f} {r.threshold_unit} [équiv. USD]"
                        + (" — atténué par justificatif" if has_just else ""))

        rules_llm = [r for r in rules if not r.pass2_completed]
        if not self.llm or not rules_llm:
            self._downgrade_low_conf_violations(rules)
            return
        # [FIX B3] Contexte enrichi : snippet source + condition explicite.
        # L'ancien prompt (rule_text[:100] seul) faisait juger le LLM sur
        # une phrase tronquée → sur-déclaration de violations (faux positif S1).
        rules_str = "".join(
            f"\n[{i}] {r.rule_id}: {r.rule_text[:150]}"
            f"\n    Source: {r.source_snippet[:100] or 'n/a'}"
            f"\n    Threshold: {r.threshold} {r.threshold_unit}"
            for i, r in enumerate(rules_llm, 1)
        )
        prompt = (
            "AML: which rules below are VIOLATED by this transaction?\n"
            "CRITICAL DISTINCTION: 'applies to this bank' is NOT 'violated by "
            "this transaction'. violated=true ONLY if the transaction itself "
            "breaches the obligation. A reporting obligation is violated only "
            "if the transaction IS suspicious AND unreported — not merely "
            "because the obligation exists. When in doubt → violated=false.\n"
            f"TRANSACTION: {tx.get('tx_type')} {tx.get('amount')} {tx.get('currency')}, "
            f"{tx.get('sender_country')}→{tx.get('destination_country')}, "
            f"justification={has_just}, signals={signals}\n"
            f"RULES:{rules_str}\n"
            'Return JSON only: [{"rule_id":"SEM_001","violated":true,"reasoning":"..."}]'
        )
        try:
            resp = self.llm.chat.completions.create(
                model=GROQ_MODEL, messages=[{"role":"user","content":prompt}],
                temperature=0.0, max_tokens=800)
            raw = re.sub(r"```json\s*|```\s*$","",resp.choices[0].message.content.strip())
            if not raw:
                self._downgrade_low_conf_violations(rules)
                return
            data = json.loads(raw)
            vmap = {d["rule_id"]: d for d in (data if isinstance(data,list) else [data])
                    if "rule_id" in d}
            for r in rules_llm:
                if r.rule_id in vmap:
                    r.violated = bool(vmap[r.rule_id].get("violated",False))
                    r.reasoning = vmap[r.rule_id].get("reasoning", r.reasoning)
                    r.pass2_completed = True
        except Exception as e:
            logger.debug(f"Pass2 LLM: {e}")
        # [FIX S1] Une violation LLM avec confiance < 0.65 n'est pas assez
        # fiable pour impacter le score → downgrade en applicable non violée.
        self._downgrade_low_conf_violations(rules)

    @staticmethod
    def _downgrade_low_conf_violations(rules):
        """[FIX S1] violated=False pour les règles SEMANTIC conf < 0.65."""
        for r in rules:
            if (r.violated and r.provenance == "SEMANTIC"
                    and r.confidence < RuleStatus.CONF_VALIDATED):
                r.violated = False
                r.reasoning = (r.reasoning or "") + " [downgraded: conf<0.65]"

    def _rule_quality_ok(self, rule) -> bool:
        """
        [Étape 2 — JUGE DE QUALITÉ] La règle extraite est-elle une VRAIE
        obligation réglementaire (un sujet DOIT / NE DOIT PAS faire une action —
        modalité déontique), ou seulement du texte DESCRIPTIF, une définition ou
        du contexte ? Complète l'ancrage verbatim (étape 1) avant d'enrichir le
        graphe → un rulebase plus propre.

        Piloté par $env:RULE_QUALITY_JUDGE (défaut : actif).
        Repli SÛR : si le juge est indisponible, on laisse passer (la règle est
        déjà ancrée verbatim → l'étape 1 fait foi).
        """
        if os.getenv("RULE_QUALITY_JUDGE", "true").lower() != "true":
            return True
        llm = self.llm
        if llm is None:
            return True
        text = (getattr(rule, "rule_text", "") or getattr(rule, "description", "") or "")[:400]
        snippet = (getattr(rule, "source_snippet", "") or "")[:300]
        prompt = (
            "Tu valides la QUALITÉ d'une règle de conformité AML/KYC extraite d'un "
            "texte réglementaire. Est-ce une VRAIE OBLIGATION (un sujet DOIT ou NE "
            "DOIT PAS faire une action — modalité déontique), ou seulement du texte "
            "DESCRIPTIF, une définition ou du contexte ?\n"
            f"RÈGLE : {text}\n"
            f"EXTRAIT SOURCE : {snippet}\n\n"
            'Réponds en JSON strict : {"is_rule": true, "raison": "…"}')
        try:
            resp = llm.chat.completions.create(
                model=None, messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=120)
            raw = re.sub(r"```json\s*|```", "",
                         (resp.choices[0].message.content or "").strip())
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return bool(json.loads(m.group(0)).get("is_rule", True))
        except Exception as e:
            logger.debug(f"Juge de qualité indisponible : {e}")
        return True   # repli : ne pas bloquer une règle déjà ancrée verbatim

    def _persist_validated(self, rules: list, tx_type: str):
        """
        [R5] Persistance automatique dans Neo4j.
        Seulement les règles conf ≥ 0.65 ET snippet validé (anti-hallucination)
        ET jugées « vraie obligation » par le juge de qualité (étape 2).
        """
        if not self.driver:
            return
        for r in rules:
            if r.persisted or r.confidence < RuleStatus.CONF_VALIDATED:
                continue
            # [FIX B1] Persistance UNIQUEMENT si snippet VERBATIM.
            # Le matching trigrammes (0.6) suffit pour utiliser la règle
            # dans CETTE analyse, mais pas pour polluer le graphe durable.
            if not r.snippet_verbatim:
                continue
            # [Étape 2] Juge de qualité : vraie obligation, pas du descriptif ?
            if not self._rule_quality_ok(r):
                self.rejected_low_quality = getattr(self, "rejected_low_quality", 0) + 1
                continue
            try:
                with self.driver.session() as s:
                    # Vérification doublon
                    dup = s.run("""
                        MATCH (existing:Rule)
                        WHERE existing.status IN ['GROUND_TRUTH','LEARNED','VALIDATED']
                        AND toLower(existing.description) CONTAINS toLower($fatf)
                        AND toLower(existing.name) CONTAINS toLower($cat)
                        RETURN count(existing) AS n
                    """, fatf=r.fatf_rec or "", cat=r.category).single()
                    if dup and dup["n"] > 0:
                        continue   # doublon détecté → ne pas réinsérer

                    # MERGE Rule (insert ou update)
                    s.run("""
                        MERGE (r:Rule {rule_text_hash: $hash})
                        ON CREATE SET
                            r.id                      = $id,
                            r.name                    = $name,
                            r.description             = $desc,
                            r.severity                = $severity,
                            r.category                = $category,
                            r.threshold               = $threshold,
                            r.threshold_currency      = $threshold_unit,
                            r.confidence              = $confidence,
                            r.status                  = 'VALIDATED',
                            r.origin                  = 'llm_extracted',
                            r.validated_against_source= true,
                            r.source_snippet          = $snippet,
                            r.created_at              = timestamp(),
                            r.use_count               = 1
                        ON MATCH SET
                            r.use_count               = r.use_count + 1,
                            r.confidence              = CASE
                                WHEN $confidence > r.confidence
                                THEN $confidence ELSE r.confidence END,
                            r.status = CASE
                                WHEN r.use_count >= $use_threshold
                                THEN 'LEARNED' ELSE r.status END
                        WITH r
                        MERGE (tt:TransactionType {name: $tx_type})
                        MERGE (r)-[:APPLIES_TO]->(tt)
                    """,
                    # [FIX A2] hash() Python est randomisé par processus
                    # (PYTHONHASHSEED) → le MERGE ne dédupliquait jamais entre
                    # les runs, et use_count ne s'incrémentait pas → la
                    # promotion LEARNED (≥3 réutilisations) était impossible.
                    hash=hashlib.sha256(
                        r.rule_text[:60].lower().strip().encode()
                    ).hexdigest()[:16],
                    id=r.rule_id,
                    name=r.rule_text[:60],
                    desc=r.rule_text,
                    severity=r.severity,
                    category=r.category,
                    threshold=r.threshold,
                    threshold_unit=r.threshold_unit,
                    confidence=r.confidence,
                    snippet=r.source_snippet[:80],
                    tx_type=tx_type,
                    use_threshold=RuleStatus.USE_COUNT_LEARNED)

                r.persisted = True
                r.rule_status = RuleStatus.VALIDATED
                if self.verbose:
                    print(f"     💾 Persisté Neo4j : {r.rule_id} [{r.fatf_rec}] conf={r.confidence:.2f}")
            except Exception as e:
                logger.debug(f"Persist failed for {r.rule_id}: {e}")

    @staticmethod
    def _g(c, f, d=""): return str(c.get(f, d) or d) if isinstance(c, dict) else str(getattr(c, f, d) or d)


# ═══════════════════════════════════════════════════════════════════════
#  RECONCILIATION ENGINE
# ═══════════════════════════════════════════════════════════════════════

class ReconciliationEngine:
    """
    Fusion symbolique + sémantique.
    Contradiction réelle = les 2 chemins ont évalué LA MÊME règle ET divergent.
    [FIX C3] Matching par (FATF rec + similarité Jaccard) : deux règles R20
    différentes (seuil STR vs fractionnement) ne sont plus confondues —
    élimine les fausses contradictions observées en test (S3).
    """

    SIM_THRESHOLD = 0.30   # Jaccard minimal pour considérer "même règle"

    @staticmethod
    def _jaccard(a: str, b: str) -> float:
        wa = set(re.sub(r"[^\w\s]", " ", a.lower()).split())
        wb = set(re.sub(r"[^\w\s]", " ", b.lower()).split())
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / len(wa | wb)

    def reconcile(self, symbolic: list, semantic: list) -> tuple:
        flags = []
        reconciled = list(symbolic)
        sym_by_fatf = {}
        for r in symbolic:
            if r.fatf_rec:
                sym_by_fatf.setdefault(r.fatf_rec, []).append(r)

        for sem in semantic:
            candidates = sym_by_fatf.get(sem.fatf_rec, [])
            # [FIX C3] même FATF rec ET texte similaire = même règle
            matches = [(self._jaccard(s.rule_text, sem.rule_text), s)
                       for s in candidates]
            matches = [(j, s) for j, s in matches if j >= self.SIM_THRESHOLD]

            if matches:
                matches.sort(key=lambda x: x[0], reverse=True)
                sym = matches[0][1]
                if sym.violated == sem.violated:
                    sym.cross_validated = True
                    sym.confidence = min(1.0, sym.confidence + 0.10)
                    sym.provenance = "RECONCILED"
                    # [Option complète] corroboration CONSCIENTE DE LA SOURCE.
                    # On enregistre les familles qui confirment l'obligation et on
                    # distingue une corroboration INTER-source (BCT↔FATF = preuve
                    # indépendante, forte) d'une corroboration intra-famille.
                    fam_sym = source_family(sym.source_doc)
                    fam_sem = source_family(sem.source_doc)
                    for f in (fam_sym, fam_sem):
                        if f and f not in sym.corroborating_families:
                            sym.corroborating_families.append(f)
                    if fam_sem and fam_sym and fam_sem != fam_sym:
                        sym.cross_source_validated = True
                elif sem.pass2_completed:
                    flags.append(
                        f"CONTRADICTION {sem.fatf_rec} (sim={matches[0][0]:.2f}) : "
                        f"symbolique violated={sym.violated} "
                        f"vs LLM violated={sem.violated}. Symbolique prévaut."
                    )
            else:
                # Même FATF rec mais texte différent → COMPLEMENT (pas
                # contradiction) : règle distincte sous la même recommandation.
                # [Option complète] la clé de regroupement secondaire étant
                # (fatf_rec, source_family), une obligation BCT et une obligation
                # FATF sous la même rec restent DISTINCTES (jamais fusionnées).
                if sem.confidence >= 0.5:
                    reconciled.append(sem)

        # [Option complète] dédup par (fatf_rec, source_family, ref/texte).
        # La famille de source fait partie de la clé → on ne collapse jamais une
        # obligation BCT et une obligation FATF distinctes. En cas de collision
        # (même famille), on garde la plus confiante et on FUSIONNE les familles
        # corroborantes pour ne perdre aucune trace de corroboration.
        seen  = {}
        for r in reconciled:
            if not r.corroborating_families:
                r.corroborating_families = [source_family(r.source_doc)]
            key = (r.fatf_rec, source_family(r.source_doc),
                   r.rule_ref or r.rule_text[:40])
            if key not in seen:
                seen[key] = r
            else:
                kept, drop = ((seen[key], r) if seen[key].confidence >= r.confidence
                              else (r, seen[key]))
                for f in drop.corroborating_families:
                    if f not in kept.corroborating_families:
                        kept.corroborating_families.append(f)
                kept.cross_source_validated = (kept.cross_source_validated
                                               or drop.cross_source_validated)
                seen[key] = kept
        return sorted(seen.values(), key=lambda r: r.confidence, reverse=True), flags


# ═══════════════════════════════════════════════════════════════════════
#  RISK SCORING ENGINE — Residual = Inherent × (1 − Control_Eff) (R4)
# ═══════════════════════════════════════════════════════════════════════

class RiskScoringEngine:
    """
    Scoring de risque conforme au standard FATF/Basel (R4).

    Inherent Risk  = contributions pondérées (violations + signaux SOFT bornés)
    Control Eff.   = f(justificatif, KYC, historique, vérification parties)
    Residual Risk  = Inherent × (1 − Control_Effectiveness)
    Decision       = f(Residual) avec plancher Level 1

    Sources : azakaw (formule FATF), AMLYZE (non-domination, overrides),
              ACAMS (gouvernance), Flagright (dynamique), EBA RTS, Basel Sound Mgmt.
    """

    MAX_CONTROL_EFF = 0.80   # aucune banque n'a 100% de contrôle (Basel)

    # Contribution du risque CLIENT structurel au risque inhérent (bornée).
    # Source : FATF RBA — la diligence s'adapte au profil du client.
    CLIENT_RISK_CONTRIB = {"ÉLEVÉ": 0.15, "MOYEN": 0.08, "FAIBLE": 0.0}
    # Plancher de diligence imposé par le profil client (FATF R12/RBA :
    # client haut risque → EDD minimum sur ses transactions).
    CLIENT_FLOOR = {"ÉLEVÉ": 45, "MOYEN": 25, "FAIBLE": 0}

    def compute(self, rules: list, soft_risk: float,
                transaction: dict, level1_decision: str = "PASS",
                client_level: str = None) -> tuple:
        """
        Returns: (risk_score 0-100, decision, RiskComponents)

        client_level : risque AML STRUCTUREL du client (FAIBLE/MOYEN/ÉLEVÉ).
        Module le scrutin (contribution bornée + plancher de diligence), sans
        jamais créer de violation (un client à risque n'est pas une infraction).
        """
        violated = [r for r in rules if r.violated]

        # ── Override 1 : Level 1 BLOCK non négociable ────────────────
        if level1_decision == "BLOCK":
            rc = RiskComponents(inherent_total=1.0, residual=1.0, risk_score=100.0)
            return 100.0, "BLOCK", rc

        # ── Override 2 : violation CRITIQUE → BLOCK auto ─────────────
        # [DUAL] SYMBOLIQUE PRÉVAUT : seule une violation CRITIQUE issue d'un
        # fait dur (symbolique/réconcilié) peut bloquer. Une règle purement
        # SÉMANTIQUE (extraction LLM) ne déclenche JAMAIS un BLOCK seule —
        # elle contribue au risque mais ne tranche pas un fait dur.
        if any(r.severity == "CRITIQUE" and r.provenance != "SEMANTIC"
               for r in violated):
            rc = RiskComponents(inherent_total=1.0, residual=1.0, risk_score=100.0)
            return 100.0, "BLOCK", rc

        # ── Étape 1 : Risque inhérent (violations pondérées) ─────────
        # [FIX A1] Normalisation par RÉFÉRENCE ABSOLUE (pas par max des
        # violations présentes). L'ancienne formule weighted/max_possible
        # se réduisait algébriquement à mean(confidence) — la sévérité
        # n'avait AUCUN effet sur le score (BASSE == HAUTE à conf égale).
        # Nouvelle : saturation à 3 violations CRITIQUES. La somme pondérée
        # encode naturellement le nombre ET la sévérité.
        if violated:
            SATURATION = 3.0 * SEVERITY_WEIGHTS["CRITIQUE"]   # = 3.0
            weighted_sum = sum(SEVERITY_WEIGHTS.get(r.severity, 0.4) * r.confidence
                               for r in violated)
            # Atténuation : violation mitigée par justificatif → demi-poids
            weighted_sum -= sum(
                0.5 * SEVERITY_WEIGHTS.get(r.severity, 0.4) * r.confidence
                for r in violated if getattr(r, "mitigated", False))
            inh_violations = min(1.0, max(0.0, weighted_sum) / SATURATION)
        else:
            inh_violations = 0.0

        # ── Étape 2 : Signaux SOFT (contribution bornée ≤ 30%) ───────
        inh_soft = soft_risk  # déjà borné à 0.30 par SymbolicReasoner

        # ── Étape 2bis : Risque CLIENT structurel (bornée, FATF RBA) ──
        inh_client = self.CLIENT_RISK_CONTRIB.get(client_level, 0.0) if client_level else 0.0

        # ── Étape 3 : Inherent total ──────────────────────────────────
        inherent = min(1.0, inh_violations + inh_soft + inh_client)

        # ── Étape 4 : Efficacité des contrôles ───────────────────────
        control_eff = self._control_effectiveness(transaction)

        # ── Étape 5 : Résiduel [formule FATF/Basel] ───────────────────
        residual = inherent * (1.0 - control_eff)
        risk_score = round(residual * 100, 1)

        # ── Étape 6 : Plancher (Niveau 1 OU profil client) ───────────
        # On ne descend jamais sous l'obligation légale (L1) NI sous le
        # scrutin imposé par un client à risque (FATF RBA : ÉLEVÉ → EDD min).
        l1_floor     = {"STR": 65, "EDD": 45, "KYC": 25, "RECORD": 15}.get(level1_decision, 0)
        client_floor = self.CLIENT_FLOOR.get(client_level, 0) if client_level else 0
        floor = max(l1_floor, client_floor)
        if risk_score < floor:
            risk_score = float(floor)
            residual = risk_score / 100.0

        decision = self._to_decision(risk_score, level1_decision)

        rc = RiskComponents(
            inherent_violations=round(inh_violations, 3),
            inherent_soft=round(inh_soft, 3),
            inherent_client=round(inh_client, 3),
            inherent_total=round(inherent, 3),
            control_eff=round(control_eff, 3),
            residual=round(residual, 3),
            risk_score=risk_score,
        )
        return risk_score, decision, rc

    def _control_effectiveness(self, tx: dict) -> float:
        """
        Efficacité des contrôles de l'institution.
        Facteurs conformes à AMLYZE/ACAMS : justificatif, KYC, historique...
        Maximum 80% (Basel : aucune banque n'a contrôle parfait).
        """
        eff = 0.0
        if tx.get("has_justification"):           eff += 0.30
        if tx.get("kyc_complete"):                eff += 0.20
        # [FIX C4] Crédit "historique propre" SEULEMENT si l'information
        # existe ET est favorable. AVANT : champ absent → +0.15 (défaut
        # optimiste = inverse du principe prudentiel AML).
        if tx.get("previous_strs") is not None and not tx.get("previous_strs"):
            eff += 0.15
        if tx.get("sender_verified"):             eff += 0.15
        if tx.get("beneficiary_verified"):        eff += 0.10
        if tx.get("justification_document"):      eff += 0.10
        return min(eff, self.MAX_CONTROL_EFF)

    @staticmethod
    def _to_decision(score: float, l1: str) -> str:
        if l1 in ("BLOCK",): return "BLOCK"
        if l1 == "STR" or score >= 65: return "STR"
        if score >= 45:  return "EDD"
        if score >= 25:  return "REVIEW"
        return "PASS"


# ═══════════════════════════════════════════════════════════════════════
#  COMPLIANCE AGENT — Orchestrateur
# ═══════════════════════════════════════════════════════════════════════

class ComplianceAgent:
    """
    Agent de conformité neuro-symbolique v6.0.
    CRISP-DM Phase 4 — Modeling.

    Usage :
        agent = ComplianceAgent(neo4j_driver=driver, verbose=True)
        decision = agent.analyze(transaction, chunks, risk_signals,
                                 fatf_checklist, level1_decision="STR")
        print(decision.explain())
    """

    def __init__(self, neo4j_driver=None, verbose: bool = False):
        self.verbose    = verbose
        self._llm       = None
        self.symbolic   = SymbolicReasoner(neo4j_driver, verbose)
        self.semantic   = None   # lazy
        self.reconciler = ReconciliationEngine()
        self.scorer     = RiskScoringEngine()
        self._neo4j     = neo4j_driver

    @property
    def llm(self):
        if self._llm is None:
            self._llm = self._build_llm() or False
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
        if _GROQ:
            key = os.environ.get("GROQ_API_KEY", "")
            if key:
                return Groq(api_key=key)
        return None

    def analyze(self, transaction: dict, chunks: list,
                risk_signals: list = None,
                fatf_checklist: list = None,
                level1_decision: str = "PASS",
                force_gap_recs: set = None,
                dual_mode: bool = False,
                client_profile=None) -> ComplianceDecision:
        """
        force_gap_recs : ÉVALUATION UNIQUEMENT (mode --stress-semantic).
        Force ces recommandations FATF à être traitées par le chemin SÉMANTIQUE
        (LLM) même si le symbolique les couvre déjà → permet de MESURER le taux
        d'hallucination et de distinguer la couverture symbolique générique de
        la couverture adossée à une preuve contextuelle. En production : None.

        dual_mode : MODE PRODUCTION 3 ZONES. Force le sémantique sur les
        obligations CONTEXTUELLES uniquement (tout sauf les faits durs R6/R7/R19
        déjà tranchés symboliquement). Les faits durs restent symbolique-seul
        (sanctions/listes noires = lookup déterministe, le LLM n'apporte rien).
        """
        t0 = time.time()
        risk_signals   = risk_signals or []
        fatf_checklist = fatf_checklist or []
        audit = []

        # ── Profil CLIENT (risque structurel) — orthogonal à la transaction ──
        # Le risque AML du client module le scrutin (FATF RBA) ; FATCA/CRS sont
        # FISCAUX → informatifs seulement, JAMAIS dans le score AML.
        client_level = None
        client_ctx = {}
        if client_profile is not None:
            def _cg(obj, attr):
                return getattr(obj, attr, None) if not isinstance(obj, dict) else obj.get(attr)
            client_level = _cg(client_profile, "level")
            fatca = _cg(client_profile, "fatca")
            crs   = _cg(client_profile, "crs")
            comps = _cg(client_profile, "components") or {}
            client_ctx = {
                "level": client_level,
                "score": _cg(client_profile, "score"),
                # PEP dérivé des facteurs de scoring client (utile workflow/SAR)
                "is_pep": ("pep_foreign" in comps) or ("pep_domestic" in comps),
                # FATCA/CRS = fiscal, séparé de l'AML
                "fatca_obligation": _cg(fatca, "obligation") if fatca else None,
                "crs_reportable":   bool(_cg(crs, "is_reportable")) if crs else False,
            }

        def log(phase, action, detail):
            audit.append(AuditEntry(phase, action, detail))
            if self.verbose:
                print(f"     [{phase}] {action}: {detail}")

        tx_type = transaction.get("tx_type", transaction.get("transaction_type","SWIFT"))

        if self.verbose:
            print(f"\n  {'═'*64}")
            print(f"  COMPLIANCE AGENT v{__version__} — {tx_type} "
                  f"{transaction.get('amount','?')} {transaction.get('currency','?')}")
            print(f"  Fixes: {FIXES_APPLIED}")
            print(f"  CRISP-DM Phase 4 — Modeling")
            print(f"  {'═'*64}")

        # ── PHASE 1 — SYMBOLIC PATH ÉTENDU ───────────────────────────
        if self.verbose:
            print(f"\n  🔷 PHASE 1 — Symbolique (Neo4j : GT+LEARNED+VALIDATED)")
        symbolic_rules, soft_risk = self.symbolic.reason(transaction, risk_signals)
        log("SYMBOLIC", "rules", f"{len(symbolic_rules)} règles | soft_risk={soft_risk:.2f}")
        if self.verbose:
            hard_count = sum(1 for r in symbolic_rules if r.violated)
            print(f"     Règles trouvées : {len(symbolic_rules)} | Violations : {hard_count} | Soft : {soft_risk:.0%}")
            for r in symbolic_rules:
                v = "🔴" if r.violated else "🟢"
                st = r.rule_status[:2].upper()
                print(f"     {v} [{st}|{r.severity[:4]}] {r.fatf_rec or '?':>4} {r.rule_text[:50]}")

        # ── PHASE 2 — GAP ANALYSIS ────────────────────────────────────
        covered  = {r.fatf_rec for r in symbolic_rules if r.fatf_rec}
        required = {rec.rec_id if hasattr(rec,"rec_id") else str(rec)
                    for rec in fatf_checklist}
        gap_dims = required - covered
        # [STRESS] Forçage d'évaluation : force le sémantique sur des recs
        # déjà couvertes symboliquement → mesure hallucination + preuve.
        if force_gap_recs:
            gap_dims = gap_dims | (set(force_gap_recs) & required)

        # [DUAL-3-ZONES] Mode production : force le sémantique sur les
        # obligations CONTEXTUELLES (tout sauf les faits durs déjà tranchés
        # symboliquement). Les faits durs (R6/R7/R19) restent symbolique-seul.
        if dual_mode:
            # Faits durs RÉELLEMENT tranchés par une violation symbolique
            hard_settled = {r.fatf_rec for r in symbolic_rules
                            if r.violated and r.fatf_rec in HARD_RECS}
            # Zone contextuelle = checklist − faits durs tranchés − faits durs
            contextual = (required - hard_settled) - HARD_RECS
            gap_dims = gap_dims | (contextual & required)
        if self.verbose:
            print(f"\n  🔍 PHASE 2 — Gap analysis")
            print(f"     Couverts (symbolique) : {sorted(covered) or 'aucun'}")
            print(f"     Gaps → sémantique     : {sorted(gap_dims) or 'aucun'}")
        log("GAPS", "dims", f"{len(gap_dims)} gaps : {sorted(gap_dims)}")

        # ── PHASE 3 — SEMANTIC PATH (gaps only) + PERSISTANCE ────────
        semantic_rules = []
        if gap_dims and self.llm:
            if self.semantic is None:
                self.semantic = SemanticReasoner(self.llm, self._neo4j, self.verbose)
            if self.verbose:
                print(f"\n  🔶 PHASE 3 — Sémantique (LLM, {len(gap_dims)} gaps) + persistance Neo4j")
            semantic_rules = self.semantic.reason(
                transaction, chunks, gap_dims, risk_signals
            )
            persisted = sum(1 for r in semantic_rules if r.persisted)
            log("SEMANTIC", "rules", f"{len(semantic_rules)} extraites | {persisted} persistées Neo4j")
            if self.verbose:
                for r in semantic_rules:
                    v = "🔴" if r.violated else "🟢"
                    p = "💾" if r.persisted else ""
                    print(f"     {v} [{r.confidence:.2f}] {r.fatf_rec or '?':>4} {r.rule_text[:48]} {p}")
        elif self.verbose:
            reason = "pas de gaps" if not gap_dims else "LLM indisponible"
            print(f"\n  🔶 PHASE 3 — Sémantique SKIP ({reason})")

        # ── PHASE 4 — RÉCONCILIATION ──────────────────────────────────
        if self.verbose:
            print(f"\n  ⚖️  PHASE 4 — Réconciliation")
        reconciled, flags = self.reconciler.reconcile(symbolic_rules, semantic_rules)
        cross = sum(1 for r in reconciled if r.cross_validated)
        log("RECONCILE", "merged", f"{len(reconciled)} | cross={cross} | contrad={len(flags)}")
        if self.verbose:
            print(f"     Fusionnées : {len(reconciled)} | Cross-validées : {cross} | Contradictions : {len(flags)}")

        # ── PHASE 5 — RISK SCORING ────────────────────────────────────
        risk_score, decision, rc = self.scorer.compute(
            reconciled, soft_risk, transaction, level1_decision,
            client_level=client_level
        )
        log("SCORING",
            "risk",
            f"inherent={rc.inherent_total:.2f} control={rc.control_eff:.2f} "
            f"residual={rc.residual:.2f} → {decision}")
        if self.verbose:
            print(f"\n  📊 PHASE 5 — Scoring résiduel [FATF/Basel]")
            print(f"     Inhérent (violations) : {rc.inherent_violations:.2f}")
            print(f"     Inhérent (soft signaux): {rc.inherent_soft:.2f}  "
                  f"[non-domination ≤30% ✓]")
            print(f"     Inhérent total         : {rc.inherent_total:.2f}")
            print(f"     Efficacité contrôles   : {rc.control_eff:.0%}")
            print(f"     Risque résiduel        : {rc.residual:.2f}  "
                  f"= {rc.inherent_total:.2f} × (1−{rc.control_eff:.2f})")
            print(f"     Score final            : {risk_score:.0f}/100 → {decision}")

        # ── PHASE 6 — DÉCISION ───────────────────────────────────────
        applicable = [r for r in reconciled if r.applies]
        violated   = [r for r in applicable if r.violated]

        fatf_coverage = {}
        binding_coverage = {}
        source_coverage = {}
        for rec in fatf_checklist:
            rid = rec.rec_id if hasattr(rec,"rec_id") else str(rec)
            rec_rules = [r for r in applicable if r.fatf_rec == rid]
            fatf_coverage[rid] = bool(rec_rules)
            # Couverture CONTRAIGNANTE : une source BCT/loi/CTAF couvre-t-elle la rec ?
            binding_coverage[rid] = any(is_binding(r.source_doc) for r in rec_rules)
            # [Option complète] familles de sources qui couvrent la rec (BCT, FATF, UE…)
            fams = sorted({source_family(r.source_doc) for r in rec_rules if r.source_doc})
            if fams:
                source_coverage[rid] = fams

        persisted_total = sum(1 for r in semantic_rules if r.persisted)

        decision_obj = ComplianceDecision(
            decision=decision,
            risk_score=risk_score,
            risk_components=rc,
            applicable_rules=applicable,
            violated_rules=violated,
            fatf_coverage=fatf_coverage,
            binding_coverage=binding_coverage,
            source_coverage=source_coverage,
            client_context=client_ctx,
            human_review_flags=flags,
            audit_trail=audit,
            stats={
                "symbolic_rules":    len(symbolic_rules),
                "semantic_rules":    len(semantic_rules),
                "semantic_rejected": getattr(self.semantic, "rejected_ungrounded", 0) if self.semantic else 0,
                "rules_persisted":   persisted_total,
                "reconciled":        len(reconciled),
                "cross_validated":   cross,
                "cross_source_validated": sum(1 for r in reconciled
                                              if getattr(r, "cross_source_validated", False)),
                "applicable":        len(applicable),
                "violated":          len(violated),
                "contradictions":    len(flags),
                "gap_dims":          len(gap_dims),
                "soft_risk":         round(soft_risk, 3),
                "elapsed_ms":        int((time.time()-t0)*1000),
            }
        )

        if self.verbose:
            self._print(decision_obj)

        return decision_obj

    def _print(self, d: ComplianceDecision):
        s = d.stats
        print(f"\n  {'═'*64}")
        print(f"  DÉCISION : {d.decision}  |  Risque : {d.risk_score:.0f}/100")
        print(f"  Pipeline : {s['symbolic_rules']}↑sym + {s['semantic_rules']}sem"
              f" → {s['reconciled']} règles | {s['rules_persisted']} persistées Neo4j")
        print(f"  Cross-validées : {s['cross_validated']} "
              f"(dont inter-sources {s.get('cross_source_validated', 0)}) "
              f"| Contradictions : {s['contradictions']}")
        if d.risk_components:
            rc = d.risk_components
            print(f"  Scoring : {rc.inherent_total:.2f}×(1−{rc.control_eff:.2f})={rc.residual:.2f}")

        if d.violated_rules:
            print(f"\n  🔴 VIOLATIONS ({len(d.violated_rules)})")
            for r in d.violated_rules:
                tag = "GT" if r.rule_status == RuleStatus.GROUND_TRUTH else \
                      "L" if r.rule_status == RuleStatus.LEARNED else \
                      "V" if r.rule_status == RuleStatus.VALIDATED else "L"
                xv = "✓✓" if r.cross_validated else tag
                # BCT-first : source contraignante citée en premier, FATF en référence
                fam = source_family(r.source_doc)
                src = f"{r.source_doc}" + (f" {r.rule_ref}" if r.rule_ref else "")
                bind = "⚖️BCT" if is_binding(r.source_doc) else f"({fam})"
                print(f"  → [{xv}|{r.severity[:4]}] {bind} {src[:42]} · réf.FATF {r.fatf_rec or '?'}")
                print(f"       {r.rule_text[:62]}")
                # [Option complète] corroboration inter-sources (preuve indépendante)
                if getattr(r, "cross_source_validated", False):
                    print(f"       🔗 confirmé inter-sources : "
                          f"{' + '.join(r.corroborating_families)}")

        if d.human_review_flags:
            print(f"\n  ⚠️  REVUE HUMAINE ({len(d.human_review_flags)})")
            for f in d.human_review_flags:
                print(f"  • {f[:90]}")

        if d.fatf_coverage:
            ok = sum(1 for v in d.fatf_coverage.values() if v)
            nb = sum(1 for v in d.binding_coverage.values() if v) if d.binding_coverage else 0
            tot = len(d.fatf_coverage)
            print(f"\n  Couverture BCT contraignante : {nb}/{tot}  "
                  f"« droit tunisien applicable »")
            print(f"  Couverture FATF (référence)  : {ok}/{tot}  "
                  f"« standard international »")
            # [Option complète] recommandations confirmées par ≥2 familles de sources
            if d.source_coverage:
                multi = {k: v for k, v in d.source_coverage.items() if len(v) > 1}
                if multi:
                    print(f"  Confirmées par ≥2 familles   : "
                          + ", ".join(f"{k}({'+'.join(v)})"
                                      for k, v in sorted(multi.items())))

        # ── Contexte CLIENT (risque structurel AML + fiscal séparé) ──
        cc = d.client_context
        if cc:
            if cc.get("level"):
                print(f"\n  👤 Risque CLIENT structurel : {cc['level']}  "
                      f"(score {cc.get('score','?')}) → module le scrutin de la tx")
            if cc.get("fatca_obligation") and cc["fatca_obligation"] != "NONE":
                print(f"  ⚑ FATCA (FISCAL, ≠ AML)     : {cc['fatca_obligation']}")
            if cc.get("crs_reportable"):
                print(f"  ⚑ CRS (FISCAL, ≠ AML)       : déclarable")
        print(f"  Durée : {d.stats.get('elapsed_ms',0)}ms")
        print(f"  {'═'*64}\n")


# ═══════════════════════════════════════════════════════════════════════
#  TEST STANDALONE
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    TX = {
        "tx_type": "SWIFT", "amount": 21800, "currency": "USD",
        "sender_name": "Eric Badege", "sender_country": "Congo",
        "beneficiary_name": "Iran National Bank", "destination_country": "Iran",
        "motive": "", "has_justification": False,
        "kyc_complete": False, "sender_verified": True,
        "beneficiary_verified": False, "previous_strs": 1,
    }
    SIGNALS = [
        "SANCTION_UN_INDIVIDUAL_SENDER",  # HARD → violation R6
        "THRESHOLD_STR",                  # HARD → violation R20
        "COUNTRY_BLACK_DESTINATION",      # HARD → violation R19
        "COUNTRY_GREY_SENDER",            # SOFT → +risque
        "MOTIVE_VAGUE",                   # SOFT → +risque
        "UNUSUAL_HOUR_3h",                # SOFT → +risque
    ]

    class Rec:
        def __init__(self, rid): self.rec_id = rid
    CHECKLIST = [Rec(r) for r in ["R6","R10","R11","R12","R13","R16","R19","R20","R24"]]

    CHUNKS = [
        {"text":"Article 43: Any transaction exceeding 10,000 USD without economic justification "
                "must be reported to CTAF within 5 working days.",
         "source_doc":"bct_circulaire_2017_08_aml.pdf","article_ref":"Article 43",
         "chunk_role":"REPORTING","score":0.032},
        {"text":"Customer due diligence: institutions must identify and verify the identity "
                "of customers and beneficial owners before establishing a relationship.",
         "source_doc":"fatf_recommendations_2025.pdf","article_ref":"Numbered 10",
         "chunk_role":"KYC","score":0.030},
        {"text":"Record keeping: institutions shall retain all transaction documentation "
                "for a minimum period of 5 years.",
         "source_doc":"fatf_recommendations_2025.pdf","article_ref":"Numbered 11",
         "chunk_role":"OBLIGATION","score":0.029},
        {"text":"Correspondent banking requires assessment of the respondent institution "
                "AML/CFT controls before establishing the relationship.",
         "source_doc":"fatf_correspondent_banking.pdf","article_ref":"Para 5",
         "chunk_role":"KYC","score":0.031},
    ]

    print("\n  TEST — ComplianceAgent v6.0 (CRISP-DM Phase 4)")
    print("  Signaux HARD : R6/R19/R20 → violations directes")
    print("  Signaux SOFT : country_grey + motive_vague + hour → risque")

    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j","amlkyc2025"))
        driver.verify_connectivity()
        print("  ✅ Neo4j connecté")
    except Exception:
        driver = None
        print("  ⚠️  Neo4j non disponible — symbolique via signaux L1 uniquement")

    agent = ComplianceAgent(neo4j_driver=driver, verbose=True)
    result = agent.analyze(TX, CHUNKS, SIGNALS, CHECKLIST, level1_decision="BLOCK")

    print("\n  EXPLICATION LISIBLE :")
    print("  " + "─"*60)
    for line in result.explain().split("\n"):
        print(f"  {line}")

    if driver:
        driver.close()