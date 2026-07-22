"""
client_scoring_agent.py — v1.0
==============================================================================
Agent de SCORING CLIENT (KYC) — distinct du scoring TRANSACTION.

POSITIONNEMENT DANS L'ARCHITECTURE
──────────────────────────────────
Le ComplianceAgent existant score une TRANSACTION (risque événementiel :
« cette opération est-elle suspecte ? »). Cet agent score le CLIENT (risque
STRUCTUREL : « ce client est-il intrinsèquement à risque ? »). Les deux sont
orthogonaux et complémentaires — c'est l'approche par les risques de la FATF :
la diligence s'adapte au profil du client.

  ┌─────────────────┐     client_profile      ┌──────────────────────┐
  │ ClientScoring   │ ──────────────────────► │  ComplianceAgent     │
  │ Agent (KYC)     │   ClientRiskScore        │  (transaction)       │
  └─────────────────┘                          └──────────────────────┘
        │                                              │
        │ score structurel (0-100)                     │ score événementiel
        │ + drapeau FATCA/CRS                           │
        └──────────────► décision finale combinée ◄─────┘

TROIS SOUS-MODULES
──────────────────
  1. RiskFactorScorer  — profil de risque pondéré (profession, PEP, pays,
                         ancienneté, historique STR, type de compte…)
  2. FATCAChecker      — détection « US person » (loi US 2010) → obligation
                         déclarative IRS (formulaires W-9 / W-8)
  3. CRSChecker        — détection résidence fiscale étrangère déclarable
                         (norme OCDE, plus large que FATCA — Tunisie 2025)

RÉFÉRENCES
──────────
  - FATF Recommendation 1   (risk-based approach, profil client)
  - FATF Recommendation 10  (customer due diligence)
  - FATF Recommendation 12  (PEP)
  - FATCA (Foreign Account Tax Compliance Act, HIRE Act 2010, IRS Form 8938)
  - OCDE CRS (Common Reporting Standard) — Tunisie participante depuis 2025
  - EBA GL 2021/02 (risk factors guidelines)

NOTE MÉTHODOLOGIQUE (défense)
─────────────────────────────
  FATCA/CRS NE sont PAS un risque de blanchiment : ce sont des obligations
  FISCALES déclaratives. On les garde donc SÉPARÉS du score AML — un client
  « US person » parfaitement honnête déclenche une obligation FATCA sans être
  à risque de blanchiment. Mélanger les deux serait une erreur de conception.
  Le score AML et le drapeau fiscal sont retournés dans des champs distincts.
"""

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger("client_scoring")

__version__ = "1.0"


# ═══════════════════════════════════════════════════════════════════════
#  DATACLASSES — ENTRÉE
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ClientProfile:
    """
    Profil client durable (≠ transaction). Tous les champs sont optionnels :
    le scoring s'adapte à l'information disponible (principe prudentiel —
    l'absence d'info n'octroie jamais de crédit favorable).
    """
    client_id:        str  = ""
    full_name:        str  = ""
    # Identité / géographie
    nationality:      str  = ""        # ex. "Tunisia", "United States"
    country_residence:str  = ""        # pays de résidence physique
    tax_residences:   list = field(default_factory=list)  # ["US", "FR"] résidences FISCALES
    birth_country:    str  = ""
    # Profil de risque
    profession:       str  = ""
    is_pep:           bool = False
    pep_type:         str  = ""        # "DOMESTIC" | "FOREIGN" | "INTERNATIONAL_ORG" | ""
    # Relation bancaire
    account_type:     str  = ""        # "INDIVIDUAL" | "BUSINESS" | "CORRESPONDENT" | "TRUST"
    account_age_days: Optional[int] = None
    previous_strs:    Optional[int] = None
    kyc_complete:     bool = False
    beneficial_owner_identified: bool = False
    # Indices FATCA / CRS (collectés au KYC — self-certification W-8/W-9)
    us_indicia:       list = field(default_factory=list)  # voir FATCAChecker.US_INDICIA
    has_w9:           bool = False     # formulaire W-9 fourni (US person)
    has_w8:           bool = False     # formulaire W-8 fourni (non-US)


# ═══════════════════════════════════════════════════════════════════════
#  DATACLASSES — SORTIE
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FATCAResult:
    """Résultat de la détection FATCA (obligation fiscale US)."""
    is_us_person:    bool = False
    status:          str  = "NON_US"   # US_PERSON | NON_US | RECALCITRANT | PENDING_DOC
    indicia_found:   list = field(default_factory=list)
    obligation:      str  = "NONE"     # COLLECT_W9 | REPORT_IRS | NONE
    reason:          str  = ""


@dataclass
class CRSResult:
    """Résultat de la détection CRS (obligations OCDE — plus large que FATCA)."""
    is_reportable:   bool = False
    reportable_jurisdictions: list = field(default_factory=list)
    obligation:      str  = "NONE"     # COLLECT_SELF_CERT | REPORT_LOCAL_AUTHORITY | NONE
    reason:          str  = ""


@dataclass
class ClientRiskScore:
    """Sortie complète de l'agent — à passer au ComplianceAgent."""
    client_id:       str   = ""
    score:           float = 0.0       # 0-100 (risque AML structurel)
    level:           str   = "FAIBLE"  # FAIBLE | MOYEN | ÉLEVÉ
    components:      dict  = field(default_factory=dict)  # détail par facteur (jury)
    # Volet fiscal (SÉPARÉ du risque AML)
    fatca:           Optional[FATCAResult] = None
    crs:             Optional[CRSResult]   = None
    # Recommandations opérationnelles
    required_actions:list  = field(default_factory=list)
    audit_trail:     list  = field(default_factory=list)

    def explain(self) -> str:
        lines = [
            f"CLIENT {self.client_id or '?'} — Risque AML structurel : "
            f"{self.score:.0f}/100 [{self.level}]"
        ]
        if self.components:
            lines.append("  Facteurs :")
            for factor, contrib in sorted(self.components.items(),
                                          key=lambda x: -x[1]):
                if contrib > 0:
                    lines.append(f"    + {factor:<28} {contrib:>5.1f}")
        if self.fatca and self.fatca.is_us_person:
            lines.append(f"  ⚑ FATCA : {self.fatca.status} → {self.fatca.obligation}")
        if self.crs and self.crs.is_reportable:
            lines.append(f"  ⚑ CRS   : déclarable {self.crs.reportable_jurisdictions} "
                         f"→ {self.crs.obligation}")
        if self.required_actions:
            lines.append("  Actions requises :")
            for a in self.required_actions:
                lines.append(f"    • {a}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
#  SOUS-MODULE 1 — SCORING DES FACTEURS DE RISQUE (pondéré, déterministe)
# ═══════════════════════════════════════════════════════════════════════

class RiskFactorScorer:
    """
    Score de risque client par somme pondérée de facteurs.
    Chaque facteur a un poids documenté (défendable au jury). Le score brut
    est borné à 100. Sources : FATF R1/R10/R12, EBA GL 2021/02.
    """

    # Professions à risque élevé (cash-intensive, opacité, exposition)
    HIGH_RISK_PROFESSIONS = {
        "casino", "jeux", "gambling", "crypto", "cryptomonnaie",
        "marchand d'art", "art dealer", "antiquaire", "metaux precieux",
        "precious metals", "immobilier", "real estate", "import export",
        "import-export", "change", "money exchange", "bureau de change",
        "armement", "arms", "defense", "petrole", "oil", "mining", "mines",
    }

    # Poids des facteurs (0-100 cumulés, bornés à 100).
    # Calibrés pour que : 1 facteur fort seul → MOYEN, 2+ → ÉLEVÉ.
    WEIGHTS = {
        "pep_foreign":         35.0,   # PEP étranger = risque max (R12)
        "pep_domestic":        20.0,   # PEP domestique = risque élevé
        "high_risk_profession":18.0,
        "high_risk_country":   25.0,   # résidence/nationalité pays FATF noir
        "grey_country":        12.0,
        "prior_strs":          20.0,   # historique de déclarations de soupçon
        "new_relationship":    10.0,   # relation < 90 jours
        "incomplete_kyc":      15.0,
        "no_beneficial_owner": 12.0,   # BO non identifié (sociétés/trusts)
        "correspondent_account":15.0,  # compte de correspondance (R13)
        "trust_account":       10.0,   # structure opaque potentielle
    }

    LEVEL_THRESHOLDS = {"ÉLEVÉ": 50.0, "MOYEN": 25.0}  # sinon FAIBLE

    def __init__(self, country_checker=None):
        # Réutilise le RiskCountryChecker du Niveau 1 si fourni (pas de
        # duplication de la liste FATF). Sinon, dégradé gracieux.
        self.country = country_checker

    def score(self, p: ClientProfile) -> tuple:
        """Returns (score 0-100, level, components dict, audit list)."""
        comp = {}
        audit = []

        def add(factor, reason):
            w = self.WEIGHTS.get(factor, 0.0)
            comp[factor] = w
            audit.append(f"{factor} (+{w}) — {reason}")

        # ── PEP (R12) ────────────────────────────────────────────────
        if p.is_pep:
            if p.pep_type == "FOREIGN":
                add("pep_foreign", "PEP étranger — vigilance renforcée R12")
            else:
                add("pep_domestic", f"PEP {p.pep_type or 'domestique'} — R12")

        # ── Profession ───────────────────────────────────────────────
        prof = (p.profession or "").lower()
        if any(kw in prof for kw in self.HIGH_RISK_PROFESSIONS):
            add("high_risk_profession", f"profession à risque : {p.profession}")

        # ── Géographie (résidence + nationalité) ─────────────────────
        risk_geo = self._geo_risk(p)
        if risk_geo == "BLACK":
            add("high_risk_country", "pays de résidence/nationalité liste noire FATF")
        elif risk_geo == "GREY":
            add("grey_country", "pays de résidence/nationalité liste grise FATF")

        # ── Historique STR ───────────────────────────────────────────
        if p.previous_strs is not None and p.previous_strs > 0:
            add("prior_strs", f"{p.previous_strs} STR antérieure(s)")

        # ── Ancienneté de la relation ────────────────────────────────
        if p.account_age_days is not None and p.account_age_days < 90:
            add("new_relationship", f"relation récente ({p.account_age_days} j)")

        # ── KYC ──────────────────────────────────────────────────────
        if not p.kyc_complete:
            add("incomplete_kyc", "dossier KYC incomplet")

        # ── Bénéficiaire effectif (sociétés / trusts) ────────────────
        if p.account_type in ("BUSINESS", "TRUST", "CORRESPONDENT") \
                and not p.beneficial_owner_identified:
            add("no_beneficial_owner", "bénéficiaire effectif non identifié (R24)")

        # ── Type de compte ───────────────────────────────────────────
        if p.account_type == "CORRESPONDENT":
            add("correspondent_account", "compte de correspondance bancaire (R13)")
        elif p.account_type == "TRUST":
            add("trust_account", "structure fiduciaire (opacité potentielle)")

        # ── Agrégation ───────────────────────────────────────────────
        # Somme pondérée, puis COMPRESSION DOUCE au-delà de 50 : les premiers
        # facteurs comptent plein pot, les suivants à rendement décroissant.
        # Évite la saturation brutale à 100 (qui écraserait la granularité
        # entre « 2 facteurs forts » et « 6 facteurs forts ») tout en gardant
        # un plafond. Au-delà de 50, chaque point brut ne vaut plus que 50 %.
        raw = sum(comp.values())
        if raw <= 50.0:
            score = raw
        else:
            score = 50.0 + (raw - 50.0) * 0.5
        score = min(100.0, score)
        level = ("ÉLEVÉ" if score >= self.LEVEL_THRESHOLDS["ÉLEVÉ"]
                 else "MOYEN" if score >= self.LEVEL_THRESHOLDS["MOYEN"]
                 else "FAIBLE")
        return round(score, 1), level, comp, audit

    def _geo_risk(self, p: ClientProfile) -> str:
        """Risque géographique le plus élevé entre résidence et nationalité."""
        if not self.country:
            return "NONE"
        worst = "NONE"
        for val in (p.country_residence, p.nationality):
            if not val:
                continue
            try:
                cr = self.country.check(val)
                if cr.action == "BLOCK":
                    return "BLACK"   # le pire — court-circuite
                if cr.action == "EDD":
                    worst = "GREY"
            except Exception as e:
                logger.debug(f"geo check failed for {val}: {e}")
        return worst


# ═══════════════════════════════════════════════════════════════════════
#  SOUS-MODULE 2 — FATCA (loi US 2010)
# ═══════════════════════════════════════════════════════════════════════

class FATCAChecker:
    """
    Détection « US person » au sens FATCA → obligation déclarative IRS.

    Les 7 indices US (« US indicia ») de la due diligence FATCA :
      1. Citoyenneté ou résidence US
      2. Lieu de naissance aux US
      3. Adresse postale ou de résidence US (y compris boîte postale)
      4. Numéro de téléphone US
      5. Ordre de virement permanent vers un compte US
      6. Procuration/signataire avec adresse US
      7. Adresse « c/o » ou « hold mail » comme seule adresse US

    Conséquence :
      - US person identifié + W-9 fourni  → REPORT_IRS (déclaration annuelle)
      - US person identifié sans W-9       → COLLECT_W9 (PENDING_DOC)
      - refus de fournir la doc            → RECALCITRANT (retenue 30 % possible)
      - aucun indice                       → NON_US
    """

    US_INDICIA = {
        "us_citizen", "us_residence", "us_birthplace", "us_address",
        "us_phone", "us_standing_order", "us_poa", "us_care_of_address",
    }

    def check(self, p: ClientProfile) -> FATCAResult:
        indicia = []

        # Indices structurels déduits du profil
        if (p.nationality or "").strip().lower() in ("united states", "usa", "us", "états-unis"):
            indicia.append("us_citizen")
        if (p.country_residence or "").strip().lower() in ("united states", "usa", "us"):
            indicia.append("us_residence")
        if (p.birth_country or "").strip().lower() in ("united states", "usa", "us"):
            indicia.append("us_birthplace")
        if "US" in [t.upper() for t in p.tax_residences]:
            indicia.append("us_tax_residence")

        # Indices déclarés explicitement au KYC
        for ind in p.us_indicia:
            if ind in self.US_INDICIA and ind not in indicia:
                indicia.append(ind)

        if not indicia:
            return FATCAResult(
                is_us_person=False, status="NON_US", obligation="NONE",
                reason="Aucun indice US — client hors champ FATCA.")

        # US person détecté → statut selon documentation
        if p.has_w9:
            return FATCAResult(
                is_us_person=True, status="US_PERSON", indicia_found=indicia,
                obligation="REPORT_IRS",
                reason=("US person confirmé (W-9 fourni) → déclaration annuelle "
                        "à l'IRS via l'autorité locale (IGA)."))
        # US person sans documentation → à régulariser
        return FATCAResult(
            is_us_person=True, status="PENDING_DOC", indicia_found=indicia,
            obligation="COLLECT_W9",
            reason=("Indices US présents sans W-9 → collecte du formulaire W-9 "
                    "requise. À défaut : statut RECALCITRANT, retenue 30 % "
                    "sur paiements US."))


# ═══════════════════════════════════════════════════════════════════════
#  SOUS-MODULE 3 — CRS (norme OCDE — plus large que FATCA)
# ═══════════════════════════════════════════════════════════════════════

class CRSChecker:
    """
    Common Reporting Standard (OCDE). Plus large que FATCA : vise les résidents
    fiscaux de TOUTE juridiction participante (pas seulement les US). La Tunisie
    est participante depuis 2025.

    Logique : si le client a une (ou plusieurs) résidence(s) fiscale(s) hors
    de la juridiction de la banque (Tunisie), et que cette juridiction est
    participante au CRS → compte déclarable à l'autorité locale, qui échange
    avec les juridictions concernées.
    """

    HOME_JURISDICTION = "TN"   # juridiction de la banque (Tunisie)

    # Sous-ensemble illustratif de juridictions CRS participantes (à charger
    # depuis data/crs_jurisdictions.json en production — voir charge_lists()).
    # NOTE : les États-Unis NE participent PAS au CRS — ils ont créé FATCA
    # comme régime distinct. Une résidence fiscale US relève donc de FATCA,
    # jamais du CRS. C'est pourquoi "US" est volontairement ABSENT de ce set.
    DEFAULT_PARTICIPATING = {
        "FR", "DE", "GB", "IT", "ES", "BE", "NL", "CH", "LU",
        "CA", "AE", "SA", "QA", "MA", "DZ", "EG", "TR", "TN",
    }

    def __init__(self, participating: set = None):
        self.participating = participating or set(self.DEFAULT_PARTICIPATING)

    def check(self, p: ClientProfile) -> CRSResult:
        # Normalise les résidences fiscales en codes 2 lettres majuscules
        residences = {t.upper()[:2] for t in p.tax_residences if t}
        # Déclarable = résidences fiscales ÉTRANGÈRES participantes au CRS
        foreign = {r for r in residences
                   if r != self.HOME_JURISDICTION and r in self.participating}

        if not foreign:
            return CRSResult(
                is_reportable=False, obligation="NONE",
                reason="Pas de résidence fiscale étrangère déclarable (CRS).")

        # Self-certification présente (W-8 sert d'analogue documentaire) ?
        if p.has_w8 or p.kyc_complete:
            obligation = "REPORT_LOCAL_AUTHORITY"
            reason = (f"Résidence(s) fiscale(s) étrangère(s) {sorted(foreign)} → "
                      f"déclaration annuelle à l'autorité tunisienne (échange CRS).")
        else:
            obligation = "COLLECT_SELF_CERT"
            reason = (f"Résidence(s) fiscale(s) étrangère(s) {sorted(foreign)} sans "
                      f"auto-certification → collecte requise avant déclaration.")

        return CRSResult(
            is_reportable=True, reportable_jurisdictions=sorted(foreign),
            obligation=obligation, reason=reason)


# ═══════════════════════════════════════════════════════════════════════
#  AGENT ORCHESTRATEUR
# ═══════════════════════════════════════════════════════════════════════

class ClientScoringAgent:
    """
    Orchestre les 3 sous-modules et produit un ClientRiskScore complet.

    Usage :
        from level1_rules.risk_country_checker import RiskCountryChecker
        agent = ClientScoringAgent(country_checker=RiskCountryChecker())
        result = agent.score_client(profile)
        print(result.explain())

        # puis, côté transaction :
        decision = compliance_agent.analyze(
            tx, chunks, signals, checklist, l1_decision,
            client_profile=result)          # ← input du score transaction
    """

    def __init__(self, country_checker=None, crs_participating: set = None,
                 verbose: bool = False):
        self.verbose = verbose
        self.scorer  = RiskFactorScorer(country_checker)
        self.fatca   = FATCAChecker()
        self.crs     = CRSChecker(crs_participating)

    def score_client(self, profile: ClientProfile) -> ClientRiskScore:
        # ── 1. Risque AML structurel ─────────────────────────────────
        score, level, components, audit = self.scorer.score(profile)

        # ── 2. Volet fiscal FATCA ────────────────────────────────────
        fatca = self.fatca.check(profile)

        # ── 3. Volet fiscal CRS ──────────────────────────────────────
        crs = self.crs.check(profile)

        # ── 4. Actions opérationnelles ───────────────────────────────
        actions = []
        if level == "ÉLEVÉ":
            actions.append("Diligence renforcée (EDD) + approbation hiérarchique")
        if profile.is_pep:
            actions.append("Approbation senior management + source des fonds (R12)")
        if not profile.kyc_complete:
            actions.append("Compléter le dossier KYC avant toute opération")
        if fatca.obligation == "COLLECT_W9":
            actions.append("Collecter le formulaire W-9 (FATCA)")
        elif fatca.obligation == "REPORT_IRS":
            actions.append("Inscrire au reporting FATCA annuel (IRS via IGA)")
        if crs.obligation == "COLLECT_SELF_CERT":
            actions.append("Collecter l'auto-certification de résidence fiscale (CRS)")
        elif crs.obligation == "REPORT_LOCAL_AUTHORITY":
            actions.append("Inscrire au reporting CRS annuel (autorité tunisienne)")

        result = ClientRiskScore(
            client_id=profile.client_id,
            score=score, level=level, components=components,
            fatca=fatca, crs=crs,
            required_actions=actions,
            audit_trail=audit,
        )

        if self.verbose:
            print(result.explain())

        return result


# ═══════════════════════════════════════════════════════════════════════
#  PONT VERS LE NIVEAU 2 — traduit le score client en signaux
# ═══════════════════════════════════════════════════════════════════════

def client_score_to_signals(client_result: "ClientRiskScore") -> list:
    """
    Traduit le ClientRiskScore en signaux pour le Planning Agent (qui orientera
    ses questions) et le Compliance Agent.

    NOTE : le risque AML structurel du client est aussi passé DIRECTEMENT au
    Compliance Agent via `analyze(client_profile=...)` (contribution bornée +
    plancher de diligence). Ces signaux servent surtout au PLANNING (cibler les
    recherches R10/R12…) et à la traçabilité. FATCA/CRS = informatifs (fiscal).
    """
    signals = []
    comp = client_result.components or {}

    # Niveau de risque AML structurel
    if client_result.level == "ÉLEVÉ":
        signals.append("CLIENT_HIGH_RISK")
    elif client_result.level == "MOYEN":
        signals.append("CLIENT_MEDIUM_RISK")

    # Facteurs spécifiques → orientent les recherches réglementaires
    if "pep_foreign" in comp:
        signals.append("SANCTION_PEP_SENDER")        # déjà mappé HARD (R12)
    if "prior_strs" in comp:
        signals.append("CLIENT_PRIOR_STR")
    if "incomplete_kyc" in comp:
        signals.append("CLIENT_KYC_INCOMPLETE")
    if "high_risk_country" in comp:
        signals.append("COUNTRY_BLACK_SENDER_COUNTRY")
    elif "grey_country" in comp:
        signals.append("COUNTRY_GREY_SENDER_COUNTRY")

    # Obligations FISCALES → signaux informatifs (PAS de l'AML)
    if client_result.fatca and client_result.fatca.obligation in ("COLLECT_W9", "REPORT_IRS"):
        signals.append("CLIENT_FATCA_PENDING")
    if client_result.crs and client_result.crs.is_reportable:
        signals.append("CLIENT_CRS_REPORTABLE")

    return signals


# ═══════════════════════════════════════════════════════════════════════
#  TEST STANDALONE
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"\n  CLIENT SCORING AGENT v{__version__} — démonstration\n")

    # Réutilise le checker pays du Niveau 1 si disponible
    cc = None
    try:
        from level1_rules.risk_country_checker import RiskCountryChecker
        cc = RiskCountryChecker()
    except Exception as e:
        print(f"  ⚠️  RiskCountryChecker indisponible ({e}) — risque géo désactivé\n")

    agent = ClientScoringAgent(country_checker=cc, verbose=True)

    cases = [
        ("C1 — Client local standard", ClientProfile(
            client_id="C1", full_name="Sami Ben Ali",
            nationality="Tunisia", country_residence="Tunisia",
            tax_residences=["TN"], profession="Enseignant",
            account_type="INDIVIDUAL", account_age_days=1200,
            previous_strs=0, kyc_complete=True)),

        ("C2 — US person (FATCA) résidant en Tunisie", ClientProfile(
            client_id="C2", full_name="John Smith",
            nationality="United States", country_residence="Tunisia",
            tax_residences=["US", "TN"], birth_country="United States",
            profession="Consultant", account_type="INDIVIDUAL",
            account_age_days=400, kyc_complete=True, has_w9=False)),

        ("C3 — PEP étranger + profession à risque", ClientProfile(
            client_id="C3", full_name="Politician X",
            nationality="Nigeria", country_residence="Tunisia",
            tax_residences=["NG"], profession="Import-Export",
            is_pep=True, pep_type="FOREIGN",
            account_type="BUSINESS", account_age_days=45,
            kyc_complete=False, beneficial_owner_identified=False)),

        ("C4 — Résident fiscal France (CRS, pas FATCA)", ClientProfile(
            client_id="C4", full_name="Marie Dubois",
            nationality="France", country_residence="Tunisia",
            tax_residences=["FR"], profession="Médecin",
            account_type="INDIVIDUAL", account_age_days=900,
            kyc_complete=True, has_w8=True)),
    ]

    for title, profile in cases:
        print(f"\n  {'─'*64}")
        print(f"  {title}")
        print(f"  {'─'*64}")
        agent.score_client(profile)
