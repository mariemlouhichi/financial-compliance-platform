"""
level1_rules/level1_orchestrator.py — v2 (Multi-Factor Suspicion)
===================================================================
Orchestrateur Niveau 1 — Règles Python fixes (déterministes, 0% LLM).

AMÉLIORATIONS v2 :
  1. Multi-Factor Suspicion : remplace le test "motif keyword" par une
     combinaison de facteurs (opérateur, cash, montant rond, motif vague,
     nouveau bénéficiaire). ≥2 facteurs → is_suspicious = True.
  2. Signaux contextuels : AMOUNT_JUST_BELOW_THRESHOLD, AMOUNT_ROUND,
     MOTIVE_VAGUE, CASH_WITHDRAWAL, HIGH_RISK_OPERATOR, etc.
  3. Smurfing étendu : détection pour MANDAT (pas uniquement DEPOT_ESPECES).

DÉCISION FINALE — Priorité décroissante :
  BLOCK(6) > STR(5) > EDD(4) > KYC(3) > RECORD(2) > PASS(1)

SORTIE Level1Result :
  decision      : str       décision finale
  violations    : list[str] raisons lisibles (pour le SAR Niveau 3)
  risk_signals  : list[str] codes structurés (pour le Planning Agent Niveau 2)
  details       : dict      résultats bruts des sous-modules (audit)
"""

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

try:
    from .sanctions_screening  import SanctionsScreener
    from .threshold_rules      import ThresholdChecker,  ACTION_PRIORITY
    from .risk_country_checker import RiskCountryChecker
except ImportError:
    from sanctions_screening  import SanctionsScreener
    from threshold_rules      import ThresholdChecker,  ACTION_PRIORITY
    from risk_country_checker import RiskCountryChecker

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logger = logging.getLogger("level1")


# ═══════════════════════════════════════════════════════════════════════
#  CONSTANTES
# ═══════════════════════════════════════════════════════════════════════

# Motifs vagues (auto-déclarés, pas fiables seuls mais contribuent au score)
VAGUE_MOTIVES = [
    "don", "aide", "gift", "cadeau", "pret", "prêt", "loan",
    "personnel", "personal", "autre", "other", "divers",
    "familial", "famille", "family",
]

# Opérateurs MVTS à risque élevé (FATF Guidance on MVTS, 2016)
HIGH_RISK_OPERATORS = ["WESTERN_UNION", "MONEYGRAM", "WAFACASH", "RIA"]

# Seuils pour détection juste-sous-seuil
JUST_BELOW_THRESHOLDS = {
    ("SWIFT",          "USD"):  10_000,
    ("SWIFT",          "EUR"):  15_000,
    ("VIREMENT_LOCAL", "TND"):  10_000,
    ("DEPOT_ESPECES",  "TND"):   5_000,
    ("MANDAT",         "TND"):   3_000,
}


@dataclass
class Level1Result:
    decision:     str
    violations:   list = field(default_factory=list)
    risk_signals: list = field(default_factory=list)
    details:      dict = field(default_factory=dict)


class Level1Orchestrator:

    _PRIORITY = ACTION_PRIORITY

    def __init__(self, data_dir: Path = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent / "data"
        print(f"\n  Init Level1Orchestrator (data={data_dir})")
        self.sanctions = SanctionsScreener(data_dir)
        self.threshold = ThresholdChecker()
        self.country   = RiskCountryChecker(data_dir)
        print("  Level1Orchestrator pret.\n")

    def check_transaction(self, tx: dict) -> Level1Result:
        """
        Vérification complète d'une transaction.

        Champs attendus (dict) :
          tx_type              str   SWIFT | VIREMENT_LOCAL | MANDAT | DEPOT_ESPECES
          amount               float
          currency             str   TND | EUR | USD | GBP | CHF
          sender_name          str
          beneficiary_name     str
          sender_country       str   pays d'émission
          destination_country  str   pays de destination
          has_justification    bool  (opt.) justificatif fourni (SWIFT)
          motive               str   (opt.) motif déclaré
          customer_history     list  (opt.) historique dépôts (smurfing)
          operator             str   (opt.) WESTERN_UNION, MONEYGRAM...
          withdrawal_mode      str   (opt.) CASH, CHEQUE, VIREMENT
          new_receiver         bool  (opt.) première tx vers ce bénéficiaire
          timestamp            str   (opt.) ISO format
        """
        violations   = []
        risk_signals = []
        cur_priority = self._PRIORITY["PASS"]
        cur_decision = "PASS"

        def _upgrade(action: str):
            nonlocal cur_decision, cur_priority
            p = self._PRIORITY.get(action, 0)
            if p > cur_priority:
                cur_priority = p
                cur_decision = action

        # ── 1. SANCTIONS SCREENING ────────────────────────────────────
        for field_key, role in [("sender_name", "SENDER"), ("beneficiary_name", "BENEFICIARY")]:
            name = tx.get(field_key, "") or ""
            res  = self.sanctions.check(name, "individual")
            if res.is_sanctioned:
                violations.append(
                    f"[SANCTIONS-{role}] '{res.matched_name}' sanctionne "
                    f"({res.match_type}, confiance {res.confidence:.0%}): "
                    f"{res.reason[:80]}"
                )
                risk_signals.append(f"SANCTION_{res.match_type}_{role}")
                _upgrade("BLOCK")

        # ── 2. MULTI-FACTOR SUSPICION ─────────────────────────────────
        #
        # v1 : is_suspicious = "suspect" in motive  ← FAIBLE
        # v2 : is_suspicious = ≥2 facteurs combinés ← INTELLIGENT
        #
        # La régulation (Décret-Loi 2021-25 Art.37) dit "suspicion",
        # pas "mot-clé dans le motif". La suspicion naît de la
        # COMBINAISON de facteurs, pas d'un seul champ.

        suspicion_factors = []
        tx_type  = tx.get("tx_type", "")
        amount   = tx.get("amount", 0) or 0
        currency = tx.get("currency", "TND")
        motive   = (tx.get("motive") or "").strip().lower()

        # Facteur 1 : Motif explicitement suspect
        if any(kw in motive for kw in ["suspect", "suspicious", "illicit",
                                        "fraud", "unknown", "inconnu"]):
            suspicion_factors.append("MOTIVE_EXPLICIT_SUSPECT")

        # Facteur 2 : Motif vague ou absent
        if not motive or len(motive) < 3 or any(kw in motive for kw in VAGUE_MOTIVES):
            suspicion_factors.append("MOTIVE_VAGUE")
            risk_signals.append("MOTIVE_VAGUE")

        # Facteur 3 : Retrait en espèces
        if (tx.get("withdrawal_mode") or "").upper() == "CASH":
            suspicion_factors.append("CASH_WITHDRAWAL")
            risk_signals.append("CASH_WITHDRAWAL")

        # Facteur 4 : Opérateur MVTS à risque
        operator = (tx.get("operator") or "").upper()
        if operator in HIGH_RISK_OPERATORS:
            suspicion_factors.append("HIGH_RISK_OPERATOR")
            risk_signals.append(f"HIGH_RISK_OPERATOR_{operator}")

        # Facteur 5 : Montant rond (multiple de 500 ou 1000)
        if amount >= 1000 and amount == int(amount) and amount % 500 == 0:
            suspicion_factors.append("AMOUNT_ROUND")
            risk_signals.append("AMOUNT_ROUND")

        # Facteur 6 : Nouveau bénéficiaire
        if tx.get("new_receiver"):
            suspicion_factors.append("NEW_RECEIVER")
            risk_signals.append("NEW_RECEIVER")

        # Facteur 7 : Heure atypique (23h-5h)
        timestamp = tx.get("timestamp")
        if timestamp:
            hour = self._extract_hour(timestamp)
            if hour is not None and (hour >= 23 or hour < 5):
                suspicion_factors.append("UNUSUAL_HOUR")
                risk_signals.append(f"UNUSUAL_HOUR_{hour}h")

        # ── Décision multi-facteurs ───────────────────────────────────
        # Motif explicitement suspect → toujours is_suspicious
        # ≥2 facteurs contextuels   → is_suspicious
        is_suspicious = (
            "MOTIVE_EXPLICIT_SUSPECT" in suspicion_factors
            or len(suspicion_factors) >= 2
        )

        # ── 3. SEUILS RÉGLEMENTAIRES ────────────────────────────────
        thresh = self.threshold.check(
            tx_type           = tx_type,
            amount            = amount,
            currency          = currency,
            has_justification = bool(tx.get("has_justification", False)),
            is_suspicious     = is_suspicious,
            customer_history  = tx.get("customer_history"),
        )

        if thresh.action == "STR":
            violations.append(
                f"[SEUIL-STR] {thresh.reason} | Ref: {thresh.rule_ref}"
            )
            if thresh.smurfing_detected:
                violations.append("[SMURFING] Fractionnement: 3+ depots < 5 000 TND en 72h")
                risk_signals.append("SMURFING_PATTERN")
            else:
                risk_signals.append("THRESHOLD_STR")
            _upgrade("STR")

        elif thresh.action == "EDD":
            violations.append(
                f"[SEUIL-EDD] {thresh.reason} | Ref: {thresh.rule_ref}"
            )
            risk_signals.append("THRESHOLD_EDD")
            _upgrade("EDD")

        elif thresh.action == "KYC":
            violations.append(
                f"[KYC] {thresh.reason} | Ref: {thresh.rule_ref}"
            )
            risk_signals.append("THRESHOLD_KYC")
            _upgrade("KYC")

        elif thresh.action == "RECORD":
            violations.append(
                f"[ENREGISTREMENT] {thresh.reason} | Ref: {thresh.rule_ref}"
            )
            risk_signals.append("THRESHOLD_RECORD")
            _upgrade("RECORD")

        # ── 4. DÉTECTION JUSTE-SOUS-SEUIL ────────────────────────────
        threshold = JUST_BELOW_THRESHOLDS.get((tx_type, currency))
        if threshold and amount and 0.85 <= amount / threshold < 1.0:
            risk_signals.append("AMOUNT_JUST_BELOW_THRESHOLD")
            violations.append(
                f"[STRUCTURING] Montant {amount:,.0f} {currency} "
                f"juste sous le seuil reglementaire "
                f"{threshold:,} {currency} ({amount/threshold:.0%})"
            )

        # ── 5. PAYS À RISQUE — destination ET expéditeur ─────────────
        for field_key, label in [
            ("destination_country", "Pays destination"),
            ("sender_country",      "Pays expediteur"),
        ]:
            country_val = (tx.get(field_key) or "").strip()
            if not country_val:
                continue

            cr = self.country.check(country_val)

            if cr.action == "BLOCK":
                violations.append(
                    f"[PAYS-BLOQUE] {label} '{cr.country}' [{cr.iso_code}] "
                    f"en liste noire FATF: {cr.reason}"
                )
                risk_signals.append(f"COUNTRY_BLACK_{field_key.upper()}")
                _upgrade("BLOCK")

            elif cr.action == "EDD":
                violations.append(
                    f"[PAYS-GRIS] {label} '{cr.country}' [{cr.iso_code}] "
                    f"sous surveillance FATF: {cr.reason}"
                )
                risk_signals.append(f"COUNTRY_GREY_{field_key.upper()}")
                _upgrade("EDD")

        # ── Log suspicion factors (audit trail) ───────────────────────
        details = {
            "threshold": thresh,
            "suspicion_factors": suspicion_factors,
            "is_suspicious": is_suspicious,
        }

        return Level1Result(
            decision=cur_decision,
            violations=violations,
            risk_signals=risk_signals,
            details=details,
        )

    @staticmethod
    def _extract_hour(ts):
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"]:
            try: return datetime.strptime(str(ts)[:19], fmt).hour
            except (ValueError, TypeError): continue
        return None


# =============================================================================
#  TESTS D'INTÉGRATION — 10 scénarios (v2)
# =============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("  LEVEL 1 ORCHESTRATOR v2 — Tests (10 scenarios)")
    print("=" * 70)

    orch = Level1Orchestrator()

    tests = [
        # ── Tests originaux (T1-T8) ──────────────────────────────────
        ("T1 PASS    — transaction normale",
         "PASS", {
             "tx_type": "VIREMENT_LOCAL", "amount": 5_000, "currency": "TND",
             "sender_name": "Alice Dupont", "beneficiary_name": "Bob Martin",
             "sender_country": "Tunisia",   "destination_country": "Tunisia",
             "motive": "Paiement facture fournisseur",
         }),
        ("T2 BLOCK   — sanctions ONU + Iran black list",
         "BLOCK", {
             "tx_type": "SWIFT", "amount": 20_000, "currency": "EUR",
             "sender_name": "Eric Badege",
             "beneficiary_name": "Iran National Bank",
             "sender_country": "Congo",
             "destination_country": "Iran",
         }),
        ("T3 STR     — SWIFT 15k EUR sans justificatif",
         "STR", {
             "tx_type": "SWIFT", "amount": 15_000, "currency": "EUR",
             "sender_name": "Alice Dupont", "beneficiary_name": "Bob Martin",
             "sender_country": "Tunisia",   "destination_country": "France",
             "has_justification": False,
         }),
        ("T4 EDD     — pays grey list Yemen",
         "EDD", {
             "tx_type": "SWIFT", "amount": 3_000, "currency": "EUR",
             "sender_name": "Alice Dupont", "beneficiary_name": "Bob Martin",
             "sender_country": "Tunisia",   "destination_country": "Yemen",
         }),
        ("T5 STR     — smurfing (3 depots < 5000 TND / 72h)",
         "STR", {
             "tx_type": "DEPOT_ESPECES", "amount": 4_700, "currency": "TND",
             "sender_name": "Alice Dupont", "beneficiary_name": "Banque BCT",
             "sender_country": "Tunisia",   "destination_country": "Tunisia",
             "customer_history": [
                 {"amount": 4_500, "currency": "TND",
                  "timestamp": (datetime.now() - timedelta(hours=10)).isoformat()},
                 {"amount": 4_800, "currency": "TND",
                  "timestamp": (datetime.now() - timedelta(hours=5)).isoformat()},
             ],
         }),
        ("T6 EDD     — SWIFT 12k USD avec justificatif",
         "EDD", {
             "tx_type": "SWIFT", "amount": 12_000, "currency": "USD",
             "sender_name": "Alice Dupont", "beneficiary_name": "Bob Martin",
             "sender_country": "Tunisia",   "destination_country": "France",
             "has_justification": True,
         }),
        ("T7 KYC     — Mandat 1500 TND normal",
         "KYC", {
             "tx_type": "MANDAT", "amount": 1_500, "currency": "TND",
             "sender_name": "Alice Dupont", "beneficiary_name": "Bob Martin",
             "sender_country": "Tunisia",   "destination_country": "Tunisia",
             "motive": "Paiement loyer",
         }),
        ("T8 STR     — Mandat 4000 TND + motif explicitement suspect",
         "STR", {
             "tx_type": "MANDAT", "amount": 4_000, "currency": "TND",
             "sender_name": "Alice Dupont", "beneficiary_name": "Bob Martin",
             "sender_country": "Tunisia",   "destination_country": "Tunisia",
             "motive": "Unknown suspicious origin",
         }),

        # ── NOUVEAUX tests v2 ────────────────────────────────────────

        ("T9  STR    — Mandat 4000 TND + WU + CASH + aide (multi-factor)",
         "STR", {
             "tx_type": "MANDAT", "amount": 4_000, "currency": "TND",
             "sender_name": "Alice Dupont", "beneficiary_name": "Bob Martin",
             "sender_country": "Tunisia",   "destination_country": "Tunisia",
             "motive": "Aide familiale",
             "operator": "WESTERN_UNION",
             "withdrawal_mode": "CASH",
         }),
        # ↑ v1 retournait KYC (motif pas "suspect")
        # ↑ v2 retourne STR (3 facteurs : MOTIVE_VAGUE + CASH + HIGH_RISK_OPERATOR)

        ("T10 PASS+SIG — SWIFT 9800 USD juste sous seuil",
         "PASS", {
             "tx_type": "SWIFT", "amount": 9_800, "currency": "USD",
             "sender_name": "Alice Dupont", "beneficiary_name": "Bob Martin",
             "sender_country": "Tunisia",   "destination_country": "France",
             "has_justification": True,
         }),
        # ↑ Décision PASS (sous seuil avec justificatif)
        # ↑ Mais risk_signal AMOUNT_JUST_BELOW_THRESHOLD doit être généré
    ]

    passed = 0
    for name, expected, tx in tests:
        result = orch.check_transaction(tx)
        ok     = result.decision == expected
        mark   = "V" if ok else "X"

        print(f"  [{mark}] {name}")
        print(f"       Decision: {result.decision}  |  Expected: {expected}")
        for v in result.violations[:3]:
            print(f"       {v[:90]}")
        if result.risk_signals:
            print(f"       Signals: {result.risk_signals}")

        # Afficher les facteurs de suspicion (v2)
        factors = result.details.get("suspicion_factors", [])
        if factors:
            print(f"       Suspicion: {factors} → is_suspicious={result.details.get('is_suspicious')}")

        if not ok:
            print(f"       !! FAIL: attendu={expected}, obtenu={result.decision}")
        print()
        if ok:
            passed += 1

    # Test spécial T10 : vérifier que le signal JUST_BELOW est présent
    t10_result = orch.check_transaction(tests[-1][2])
    has_jbt = "AMOUNT_JUST_BELOW_THRESHOLD" in t10_result.risk_signals
    print(f"  [{'V' if has_jbt else 'X'}] T10 signal AMOUNT_JUST_BELOW_THRESHOLD: {'present' if has_jbt else 'ABSENT'}")
    if has_jbt:
        passed += 1
    print()

    total = len(tests) + 1  # +1 for T10 signal check
    print("=" * 70)
    print(f"  Resultat : {passed}/{total} tests passes")
    print("=" * 70 + "\n")