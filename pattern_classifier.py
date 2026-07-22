"""
pattern_classifier.py — Classifie une transaction selon un pattern AML métier
=============================================================================
Convertit les features ML en un PATTERN MÉTIER que l'admin comprend.

Patterns AML détectés :
  FAN_OUT          : émetteur diversifie vers de nombreux récepteurs (smurfing)
  FAN_IN           : récepteur collecte de nombreux émetteurs
  LAYERING         : multi-devises pour brouiller la piste
  STRUCTURING      : montant juste sous le seuil
  HIGH_RISK_ROUTE  : route géographique à risque (Iran, Congo...)
  NIGHT_BURST      : rafale de transactions nocturnes
  NORMAL           : pas de pattern détecté

Utilise les features existantes du pipeline (pas de ML supplémentaire).
"""


def classify_pattern(transaction: dict, ml_features: dict,
                     l1_signals: list) -> dict:
    """Classifie une transaction selon un pattern AML.

    Args:
        transaction : la transaction (dict avec amount, tx_type, etc.)
        ml_features : features ML (sender_fanout_ratio, etc.)
        l1_signals  : signaux du Level 1 (HIGH_RISK_COUNTRY, etc.)

    Returns:
        dict {primary_pattern, all_patterns, summary}
    """
    patterns_detected = []

    # ── 1. FAN_OUT (smurfing) ─────────────────────────────────────────
    fanout = ml_features.get("sender_fanout_ratio", 0)
    if fanout >= 0.8:
        patterns_detected.append({
            "pattern": "FAN_OUT",
            "confidence": min(fanout, 1.0),
            "explanation": (
                f"L'émetteur a diversifié vers {fanout*100:.0f}% de "
                f"récepteurs uniques. Pattern classique de smurfing : "
                f"fractionnement vers de nombreux comptes pour brouiller "
                f"la piste."
            )
        })

    # ── 2. FAN_IN (collecte) ───────────────────────────────────────────
    fanin = ml_features.get("receiver_fanin_ratio", 0)
    if fanin >= 0.8:
        patterns_detected.append({
            "pattern": "FAN_IN",
            "confidence": min(fanin, 1.0),
            "explanation": (
                f"Le bénéficiaire collecte de {fanin*100:.0f}% de "
                f"émetteurs uniques. Pattern de collecte centralisée — "
                f"possible mule account ou compte de regroupement."
            )
        })

    # ── 3. STRUCTURING (juste sous seuil) ──────────────────────────────
    if ml_features.get("just_below_10k", 0) == 1:
        patterns_detected.append({
            "pattern": "STRUCTURING",
            "confidence": 0.9,
            "explanation": (
                f"Montant ({transaction.get('amount', 0):,}) juste sous le "
                f"seuil de 10 000. Évitement intentionnel possible du "
                f"seuil de déclaration BCT."
            )
        })
    if ml_features.get("just_below_5k", 0) == 1:
        patterns_detected.append({
            "pattern": "STRUCTURING",
            "confidence": 0.85,
            "explanation": (
                f"Montant ({transaction.get('amount', 0):,}) juste sous le "
                f"seuil de 5 000 TND (interdiction d'utilisation des "
                f"espèces BCT)."
            )
        })

    # ── 4. LAYERING (multi-devises) ───────────────────────────────────
    if ml_features.get("currency_mismatch", 0) == 1:
        patterns_detected.append({
            "pattern": "LAYERING",
            "confidence": 0.7,
            "explanation": (
                "Conversion de devise dans la transaction. Pattern de "
                "layering possible — brouillage de la piste par changement "
                "de monnaie."
            )
        })

    # ── 5. HIGH_RISK_ROUTE ─────────────────────────────────────────────
    high_risk_signals = [s for s in l1_signals
                          if "COUNTRY_BLACK" in s or "COUNTRY_GREY" in s
                          or "SANCTION_HIT" in s]
    if high_risk_signals:
        patterns_detected.append({
            "pattern": "HIGH_RISK_ROUTE",
            "confidence": 1.0,
            "explanation": (
                f"Route géographique impliquant un pays à haut risque "
                f"(signaux : {', '.join(high_risk_signals)}). Contre-mesures "
                f"FATF obligatoires."
            )
        })

    # ── 6. NIGHT_BURST ────────────────────────────────────────────────
    is_night = ml_features.get("is_night", 0)
    if is_night == 1 and transaction.get("amount", 0) > 10000:
        patterns_detected.append({
            "pattern": "NIGHT_BURST",
            "confidence": 0.6,
            "explanation": (
                "Transaction nocturne de montant élevé. Pattern à risque "
                "modéré — souvent utilisé pour limiter la détection."
            )
        })

    # ── 7. Aucun pattern ──────────────────────────────────────────────
    if not patterns_detected:
        return {
            "primary_pattern": "NORMAL",
            "all_patterns": [],
            "summary": "Aucun pattern AML connu détecté.",
        }

    # Tri par confiance
    patterns_detected.sort(key=lambda p: -p["confidence"])
    primary = patterns_detected[0]

    return {
        "primary_pattern": primary["pattern"],
        "all_patterns": patterns_detected,
        "summary": (f"Pattern dominant : **{primary['pattern']}** "
                   f"(confiance {primary['confidence']*100:.0f}%). "
                   f"{primary['explanation']}"),
    }
