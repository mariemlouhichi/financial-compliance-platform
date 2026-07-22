"""
level1_rules/threshold_rules.py
================================
Seuils réglementaires durs — NIVEAU 1 (Python pur, 0% LLM, 0% hallucination).

SOURCES RÉGLEMENTAIRES (seuils hard-codés depuis textes officiels) :
  ┌─────────────────────────────────────────────────────────────────────┐
  │ CIRCULAIRE BCT 2017-08 (AML/CFT)                                    │
  │   Art. 43 : Virement local > 10 000 TND      → STR obligatoire     │
  │   Art. 43 : SWIFT > 10 000 USD/EUR           → STR sans justif.    │
  │                                                                     │
  │ CIRCULAIRE BCT 2006-19 (Blanchiment capitaux)                       │
  │   Art. 7  : Dépôt espèces > 3 000 TND        → enregistrement      │
  │   Art. 8  : Dépôt espèces > 5 000 TND        → STR obligatoire     │
  │                                                                     │
  │ DÉCRET-LOI 2021-25 (AML/CFT loi cadre)                             │
  │   Art. 37 : Mandat > 1 000 TND               → identification KYC  │
  │   Art. 37 : Mandat > 3 000 TND + suspicion   → STR obligatoire     │
  │                                                                     │
  │ FATF RECOMMENDATION 16 (Travel Rule)                                │
  │   Transfer > 1 000 EUR/USD    → informations obligatoires           │
  │   Transfer > 15 000 EUR       → KYC renforcé (EDD)                 │
  └─────────────────────────────────────────────────────────────────────┘

IMPORTANT — ARCHITECTURE DU PROJET :
  Ce module appartient au NIVEAU 1 (règles Python fixes, déterministes).
  Il n'utilise PAS les 49 règles ground truth. Ces règles appartiennent
  au NIVEAU 2 (cache Neo4j pour le Retrieval Agent, Step 0).

  NIVEAU 1 ← Constantes ici, issues des circulaires BCT officielles
  NIVEAU 2 ← 49 règles ground truth dans Neo4j (cache Agentic RAG)

USAGE :
  from level1_rules.threshold_rules import ThresholdChecker

  checker = ThresholdChecker()
  result  = checker.check(
      tx_type           = "SWIFT",
      amount            = 15000,
      currency          = "USD",
      has_justification = False,
  )
  if result.action in ("STR", "EDD"):
      print(result.reason)     # Description lisible
      print(result.rule_ref)   # "BCT Circulaire 2017-08, Art. 43"
"""

import sys
from dataclasses import dataclass
from typing import Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


# =============================================================================
#  SEUILS RÉGLEMENTAIRES — HARD-CODÉS DEPUIS TEXTES OFFICIELS BCT/FATF
#  NE PAS MODIFIER sans nouvelle circulaire BCT ou plénière FATF
# =============================================================================

REGULATORY_THRESHOLDS = {

    # ── Virement local (Tunisie) ──────────────────────────────────────────
    "VIREMENT_LOCAL": [
        {
            "limit":        10_000,
            "currency":     "TND",
            "action":       "STR",
            "condition":    "always",
            "rule_ref":     "BCT Circulaire 2017-08, Art. 43",
            "description":  "Virement local > 10 000 TND : déclaration de soupçon obligatoire",
        },
    ],

    # ── SWIFT / virement international ────────────────────────────────────
    "SWIFT": [
        {
            # Sans justificatif → STR
            "limit":        10_000,
            "currency":     "USD",
            "action":       "STR",
            "condition":    "no_justification",
            "rule_ref":     "BCT Circulaire 2017-08, Art. 43 + FATF Rec. 16",
            "description":  "SWIFT > 10 000 USD sans justificatif économique : STR obligatoire",
        },
        {
            # Avec justificatif → surveillance renforcée (EDD)
            "limit":        10_000,
            "currency":     "USD",
            "action":       "EDD",
            "condition":    "with_justification",
            "rule_ref":     "BCT Circulaire 2017-08, Art. 43",
            "description":  "SWIFT > 10 000 USD avec justificatif : vigilance renforcée (EDD)",
        },
        {
            # Travel Rule FATF — KYC renforcé au-delà de 15 000 EUR
            "limit":        15_000,
            "currency":     "EUR",
            "action":       "EDD",
            "condition":    "always",
            "rule_ref":     "FATF Recommendation 16 (Travel Rule)",
            "description":  "Transfer > 15 000 EUR : due diligence renforcée (FATF R16)",
        },
    ],

    # ── Dépôt espèces ─────────────────────────────────────────────────────
    "DEPOT_ESPECES": [
        {
            # Enregistrement interne (pas de STR immédiat)
            "limit":        3_000,
            "currency":     "TND",
            "action":       "RECORD",
            "condition":    "always",
            "rule_ref":     "BCT Circulaire 2006-19, Art. 7",
            "description":  "Dépôt espèces > 3 000 TND : enregistrement guichetier obligatoire",
        },
        {
            "limit":        5_000,
            "currency":     "TND",
            "action":       "STR",
            "condition":    "always",
            "rule_ref":     "BCT Circulaire 2006-19, Art. 8",
            "description":  "Dépôt espèces > 5 000 TND : déclaration de soupçon obligatoire (CTAF)",
        },
    ],

    # ── Mandat / money order ──────────────────────────────────────────────
    "MANDAT": [
        {
            # Identification obligatoire au-delà de 1 000 TND
            "limit":        1_000,
            "currency":     "TND",
            "action":       "KYC",
            "condition":    "always",
            "rule_ref":     "Decret-Loi 2021-25, Art. 37",
            "description":  "Mandat > 1 000 TND : identification expediteur/beneficiaire obligatoire",
        },
        {
            # STR si montant > 3 000 TND ET motif suspect
            "limit":        3_000,
            "currency":     "TND",
            "action":       "STR",
            "condition":    "suspicious_motive",
            "rule_ref":     "Decret-Loi 2021-25, Art. 37 + BCT Circulaire 2017-08",
            "description":  "Mandat > 3 000 TND avec motif suspect : STR obligatoire",
        },
    ],
}

# Taux de change fixes BCT (mis a jour trimestriellement)
EXCHANGE_RATES = {
    ("EUR", "TND"): 3.30,
    ("USD", "TND"): 3.10,
    ("GBP", "TND"): 3.85,
    ("CHF", "TND"): 3.45,
    ("USD", "EUR"): 0.92,
    ("EUR", "USD"): 1.09,
    ("TND", "EUR"): 1 / 3.30,
    ("TND", "USD"): 1 / 3.10,
}

# Priorite des actions (ordre croissant de severite)
ACTION_PRIORITY = {
    "PASS":   1,
    "RECORD": 2,
    "KYC":    3,
    "EDD":    4,
    "STR":    5,
    "BLOCK":  6,
}


# =============================================================================
#  DATACLASS RÉSULTAT
# =============================================================================

@dataclass
class ThresholdResult:
    """Resultat d'une verification de seuil reglementaire."""
    exceeded:            bool
    action:              str    # "STR", "EDD", "KYC", "RECORD", "PASS"
    threshold_value:     float  # Seuil applicable
    threshold_currency:  str    # Devise du seuil
    normalized_amount:   float  # Montant converti dans la devise du seuil
    rule_ref:            str    # Reference reglementaire exacte (ex: "BCT 2017-08, Art.43")
    reason:              str    # Description lisible pour rapport
    smurfing_detected:   bool = False


# =============================================================================
#  CHECKER PRINCIPAL
# =============================================================================

class ThresholdChecker:
    """
    Verification des seuils reglementaires AML — Niveau 1.
    Logique deterministe : aucun LLM, aucun acces reseau.
    """

    def check(self,
              tx_type:           str,
              amount:            float,
              currency:          str,
              has_justification: bool = False,
              is_suspicious:     bool = False,
              customer_history:  Optional[list] = None,
              ) -> ThresholdResult:
        """
        Verifie les seuils reglementaires pour une transaction.

        Args:
            tx_type:           VIREMENT_LOCAL | SWIFT | MANDAT | DEPOT_ESPECES
            amount:            Montant de la transaction
            currency:          Devise (TND, EUR, USD, GBP, CHF)
            has_justification: Justificatif economique fourni ? (SWIFT uniquement)
            is_suspicious:     Motif declare suspect ? (MANDAT)
            customer_history:  Historique depots pour detection smurfing
                               Format: [{"amount": 4500, "currency": "TND",
                                         "timestamp": "2025-05-15T10:00:00"}, ...]

        Returns:
            ThresholdResult avec l'action la plus severe declenchee
        """
        if tx_type not in REGULATORY_THRESHOLDS:
            return ThresholdResult(
                exceeded=False, action="PASS",
                threshold_value=0, threshold_currency="",
                normalized_amount=amount,
                rule_ref="N/A",
                reason=f"Aucun seuil defini pour le type '{tx_type}'",
            )

        rules         = REGULATORY_THRESHOLDS[tx_type]
        best_action   = "PASS"
        best_priority = ACTION_PRIORITY["PASS"]
        best_rule     = None
        best_norm     = amount

        for rule in rules:
            # ── Evaluer la condition ───────────────────────────────────
            condition = rule["condition"]
            if condition == "no_justification"  and has_justification:  continue
            if condition == "with_justification" and not has_justification: continue
            if condition == "suspicious_motive"  and not is_suspicious:  continue
            # "always" -> toujours evaluer

            # ── Conversion de devise ──────────────────────────────────
            norm = self._convert(amount, currency, rule["currency"])
            if norm <= rule["limit"]:
                continue    # Seuil non atteint

            # ── Seuil depasse : garder la regle la plus severe ────────
            priority = ACTION_PRIORITY.get(rule["action"], 0)
            if priority > best_priority:
                best_priority = priority
                best_action   = rule["action"]
                best_rule     = rule
                best_norm     = norm

        # ── Detection smurfing (DEPOT_ESPECES uniquement) ─────────────
        smurfing = False
        if tx_type == "DEPOT_ESPECES" and customer_history:
            smurfing = self._detect_smurfing(amount, currency, customer_history)
            if smurfing and ACTION_PRIORITY["STR"] > best_priority:
                best_priority = ACTION_PRIORITY["STR"]
                best_action   = "STR"
                best_rule     = {
                    "limit": 5_000, "currency": "TND",
                    "rule_ref":    "BCT Circulaire 2006-19, Art. 8 — fractionnement",
                    "description": "Smurfing : 3+ depots < 5 000 TND en 72h",
                }

        # ── Construire le resultat final ──────────────────────────────
        if best_action == "PASS":
            ref_rule = rules[0]
            return ThresholdResult(
                exceeded=False, action="PASS",
                threshold_value=ref_rule["limit"],
                threshold_currency=ref_rule["currency"],
                normalized_amount=self._convert(amount, currency, ref_rule["currency"]),
                rule_ref=ref_rule["rule_ref"],
                reason="Montant en dessous des seuils reglementaires",
            )

        return ThresholdResult(
            exceeded=True,
            action=best_action,
            threshold_value=best_rule["limit"],
            threshold_currency=best_rule["currency"],
            normalized_amount=best_norm,
            rule_ref=best_rule["rule_ref"],
            reason=(
                f"{best_rule['description']} "
                f"({best_norm:,.0f} {best_rule['currency']} "
                f"> {best_rule['limit']:,} {best_rule['currency']})"
            ),
            smurfing_detected=smurfing,
        )

    # ─────────────────────────────────────────────────────────────────────
    #  CONVERSION DE DEVISE
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _convert(amount: float, from_cur: str, to_cur: str) -> float:
        """Convertit un montant vers la devise cible (taux BCT fixes)."""
        from_cur = from_cur.upper().strip()
        to_cur   = to_cur.upper().strip()
        if from_cur == to_cur:
            return amount

        rate = EXCHANGE_RATES.get((from_cur, to_cur))
        if rate:
            return amount * rate

        # Conversion en 2 etapes via TND
        to_tnd   = EXCHANGE_RATES.get((from_cur, "TND"), 1.0)
        from_tnd = EXCHANGE_RATES.get(("TND", to_cur),   1.0)
        return amount * to_tnd * from_tnd

    # ─────────────────────────────────────────────────────────────────────
    #  SMURFING DETECTION
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_smurfing(current_amount: float,
                          currency: str,
                          history: list) -> bool:
        """
        Detecte le fractionnement (smurfing) de depots especes.

        Critere : 3+ depots, chacun < 5 000 TND, dans les 72 dernieres heures.
        Source   : BCT Circulaire 2006-19 + FATF Typologies Report 2021.
        """
        from datetime import datetime, timezone

        LIMIT_TND      = 5_000
        LOOKBACK_HOURS = 72

        checker    = ThresholdChecker()
        curr_tnd   = checker._convert(current_amount, currency, "TND")
        now        = datetime.now(timezone.utc)
        small_deps = []

        for tx in history:
            try:
                ts = tx.get("timestamp", "")
                if not ts:
                    continue
                tx_time = datetime.fromisoformat(ts)
                if tx_time.tzinfo is None:
                    tx_time = tx_time.replace(tzinfo=timezone.utc)
                if (now - tx_time).total_seconds() / 3600 > LOOKBACK_HOURS:
                    continue
                tx_tnd = checker._convert(
                    tx.get("amount", 0),
                    tx.get("currency", "TND"),
                    "TND",
                )
                if tx_tnd < LIMIT_TND:
                    small_deps.append(tx_tnd)
            except (ValueError, TypeError):
                continue

        # Inclure le depot actuel
        if curr_tnd < LIMIT_TND:
            small_deps.append(curr_tnd)

        return len(small_deps) >= 3


# =============================================================================
#  TEST RAPIDE
# =============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  THRESHOLD CHECKER — Test (seuils BCT/FATF officiels)")
    print("=" * 70)
    print()

    checker = ThresholdChecker()

    test_cases = [
        # (tx_type, amount, currency, has_justif, is_susp, description, action_attendue)
        ("VIREMENT_LOCAL", 12_000, "TND",  False, False, "Virement local 12k TND",             "STR"),
        ("VIREMENT_LOCAL",  8_000, "TND",  False, False, "Virement local 8k TND (sous seuil)", "PASS"),
        ("SWIFT",          11_000, "USD",  False, False, "SWIFT 11k USD sans justif",           "STR"),
        ("SWIFT",          11_000, "USD",  True,  False, "SWIFT 11k USD avec justif -> EDD",   "EDD"),
        ("SWIFT",           5_000, "EUR",  False, False, "SWIFT 5k EUR (sous seuil)",           "PASS"),
        ("SWIFT",          16_000, "EUR",  True,  False, "SWIFT 16k EUR -> FATF R16 EDD",       "EDD"),
        ("DEPOT_ESPECES",   6_000, "TND",  False, False, "Depot 6k TND > 5k -> STR",           "STR"),
        ("DEPOT_ESPECES",   4_000, "TND",  False, False, "Depot 4k TND > 3k -> RECORD",        "RECORD"),
        ("DEPOT_ESPECES",   1_500, "TND",  False, False, "Depot 1.5k TND (sous seuil)",        "PASS"),
        ("MANDAT",          1_500, "TND",  False, False, "Mandat 1.5k -> KYC obligatoire",      "KYC"),
        ("MANDAT",          4_000, "TND",  False, True,  "Mandat 4k + motif suspect -> STR",    "STR"),
        ("MANDAT",          4_000, "TND",  False, False, "Mandat 4k sans suspicion -> KYC",     "KYC"),
    ]

    ICONS = {"STR":"RED", "EDD":"EDD", "KYC":"KYC", "RECORD":"REC", "PASS":"OK ", "BLOCK":"BLK"}
    passed = 0
    failed = []

    for tx_type, amount, currency, has_just, is_susp, desc, expected in test_cases:
        result = checker.check(tx_type, amount, currency, has_just, is_susp)
        ok     = result.action == expected
        tag    = ICONS.get(result.action, "???")
        mark   = "V" if ok else "X FAIL"

        print(f"  [{mark}] [{tag}]  {desc}")
        if result.exceeded:
            print(f"         {result.normalized_amount:>10,.0f} {result.threshold_currency}"
                  f" > {result.threshold_value:,} {result.threshold_currency}")
            print(f"         {result.rule_ref}")
        if not ok:
            print(f"         !! ATTENDU: {expected}, OBTENU: {result.action}")
            failed.append(desc)
        print()

        if ok:
            passed += 1

    # Test smurfing
    print("  [SMURFING TEST] 3 depots < 5000 TND en 72h :")
    history = [
        {"amount": 4_500, "currency": "TND", "timestamp": "2025-05-15T10:00:00"},
        {"amount": 4_800, "currency": "TND", "timestamp": "2025-05-15T14:00:00"},
    ]
    result = checker.check("DEPOT_ESPECES", 4_700, "TND", customer_history=history)
    ok = result.action == "STR" and result.smurfing_detected
    print(f"  [{'V' if ok else 'X'}] Smurfing -> {result.action}  (smurfing={result.smurfing_detected})")
    if ok:
        passed += 1
    print()

    total = len(test_cases) + 1
    print("=" * 70)
    print(f"  Resultat : {passed}/{total} tests passes")
    if failed:
        print(f"  Echecs   : {failed}")
    print("=" * 70 + "\n")
