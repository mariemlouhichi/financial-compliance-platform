"""
evaluation_scenarios.py — Jeu de scénarios étiquetés pour l'évaluation du Compliance Agent
==========================================================================================
PFE AML/KYC 2025-2026 | CRISP-DM Phase 5 — Évaluation

Chaque scénario est un cas de test étiqueté manuellement avec :
  - transaction      : la transaction enrichie
  - risk_signals     : signaux Level 1 simulés (comme produits par l'orchestrateur L1)
  - fatf_checklist   : recommandations FATF applicables à ce type de transaction
  - level1_decision  : décision Level 1 attendue
  - expected         : décision finale attendue du Compliance Agent
  - justification    : référence réglementaire qui justifie la décision (pour le jury)
  - category         : famille du scénario (pour l'analyse par catégorie)

Couverture : 24 scénarios répartis sur les 5 décisions possibles
  PASS (5) · REVIEW (3) · EDD (4) · STR (7) · BLOCK (5)

Les décisions attendues sont fondées sur la réglementation tunisienne (BCT, CTAF),
les recommandations FATF, et le décret-loi 2018-09. Chaque justification cite la
source réglementaire précise.
"""

# ══════════════════════════════════════════════════════════════════════
#  JEU DE SCÉNARIOS ÉTIQUETÉS
# ══════════════════════════════════════════════════════════════════════

SCENARIOS = [

    # ─────────────────────────────────────────────────────────────────
    #  CATÉGORIE PASS — Transactions normales (5)
    # ─────────────────────────────────────────────────────────────────
    {
        "id": "PASS_01",
        "category": "PASS",
        "description": "Virement local petit montant, motif clair",
        "transaction": {
            "tx_type": "VIREMENT_LOCAL", "amount": 1500, "currency": "TND",
            "sender_name": "Sami Ben Ali", "sender_country": "Tunisia",
            "beneficiary_name": "Leila Khelifi", "destination_country": "Tunisia",
            "motif": "Remboursement prêt personnel", "has_justification": True,
            "kyc_complete": True, "previous_strs": 0,
        },
        "risk_signals": [],
        "fatf_checklist": ["R10", "R20"],
        "level1_decision": "PASS",
        "expected": "PASS",
        "justification": "Montant faible (1500 TND < seuil STR 5000), KYC complet, "
                         "justificatif présent. Aucune obligation déclarative (BCT 2018-09).",
    },
    {
        "id": "PASS_02",
        "category": "PASS",
        "description": "Dépôt salaire mensuel récurrent",
        "transaction": {
            "tx_type": "VIREMENT_LOCAL", "amount": 2800, "currency": "TND",
            "sender_name": "STEG SA", "sender_country": "Tunisia",
            "beneficiary_name": "Ahmed Gharbi", "destination_country": "Tunisia",
            "motif": "Salaire mensuel", "has_justification": True,
            "kyc_complete": True, "previous_strs": 0, "sender_verified": True,
        },
        "risk_signals": [],
        "fatf_checklist": ["R10", "R20"],
        "level1_decision": "PASS",
        "expected": "PASS",
        "justification": "Virement salarial d'une entreprise vérifiée, motif documenté, "
                         "pas de red flag. Transaction de routine.",
    },
    {
        "id": "PASS_03",
        "category": "PASS",
        "description": "SWIFT documenté pays à faible risque",
        "transaction": {
            "tx_type": "SWIFT", "amount": 8000, "currency": "EUR",
            "sender_name": "Tech Solutions SARL", "sender_country": "Tunisia",
            "beneficiary_name": "GmbH Software", "destination_country": "Germany",
            "motif": "Paiement facture logiciel n°2024-451", "has_justification": True,
            "kyc_complete": True, "justification_document": True,
            "sender_verified": True, "beneficiary_verified": True, "previous_strs": 0,
        },
        "risk_signals": [],
        "fatf_checklist": ["R10", "R16", "R20"],
        "level1_decision": "PASS",
        "expected": "PASS",
        "justification": "SWIFT vers pays non listé FATF (Allemagne), facture identifiée, "
                         "toutes parties vérifiées. Conforme R16 (travel rule).",
    },
    {
        "id": "PASS_04",
        "category": "PASS",
        "description": "Mandat aide familiale petit montant légitime",
        "transaction": {
            "tx_type": "MANDAT", "amount": 800, "currency": "TND",
            "sender_name": "Fatma Sassi", "sender_country": "Tunisia",
            "beneficiary_name": "Mongi Sassi", "destination_country": "Tunisia",
            "motif": "Aide familiale mensuelle", "has_justification": True,
            "kyc_complete": True, "previous_strs": 0,
        },
        "risk_signals": [],
        "fatf_checklist": ["R10", "R14", "R20"],
        "level1_decision": "PASS",
        "expected": "PASS",
        "justification": "Mandat < seuil 3000 TND, relation familiale identifiée, "
                         "montant cohérent. Pas de déclenchement STR (BCT mandat).",
    },
    {
        "id": "PASS_05",
        "category": "PASS",
        "description": "Virement local avec historique propre",
        "transaction": {
            "tx_type": "VIREMENT_LOCAL", "amount": 3200, "currency": "TND",
            "sender_name": "Nizar Bouzid", "sender_country": "Tunisia",
            "beneficiary_name": "Imprimerie Centrale", "destination_country": "Tunisia",
            "motif": "Paiement facture impression", "has_justification": True,
            "kyc_complete": True, "previous_strs": 0, "sender_verified": True,
        },
        "risk_signals": [],
        "fatf_checklist": ["R10", "R20"],
        "level1_decision": "PASS",
        "expected": "PASS",
        "justification": "Paiement commercial B2B documenté, client connu, "
                         "historique sans déclaration. Transaction normale.",
    },

    # ─────────────────────────────────────────────────────────────────
    #  CATÉGORIE REVIEW — Risque modéré (3)
    # ─────────────────────────────────────────────────────────────────
    {
        "id": "REVIEW_01",
        "category": "REVIEW",
        "description": "Montant rond élevé sans justificatif",
        "transaction": {
            "tx_type": "VIREMENT_LOCAL", "amount": 5000, "currency": "TND",
            "sender_name": "Karim Mejri", "sender_country": "Tunisia",
            "beneficiary_name": "Walid Tlili", "destination_country": "Tunisia",
            "motif": "Transfert", "has_justification": False,
            "kyc_complete": True, "previous_strs": 0,
        },
        "risk_signals": ["AMOUNT_ROUND"],
        "fatf_checklist": ["R10", "R20"],
        "level1_decision": "PASS",
        "expected": "PASS",   # signal SOFT seul → reste PASS (conservateur acceptable: REVIEW)
        "acceptable": ["PASS", "REVIEW"],
        "justification": "Montant rond au seuil mais motif présent et KYC complet. "
                         "Signal SOFT isolé (AMOUNT_ROUND) insuffisant pour EDD.",
    },
    {
        "id": "REVIEW_02",
        "category": "REVIEW",
        "description": "Nouveau client montant modéré",
        "transaction": {
            "tx_type": "VIREMENT_LOCAL", "amount": 4500, "currency": "TND",
            "sender_name": "Client Nouveau", "sender_country": "Tunisia",
            "beneficiary_name": "Bénéficiaire X", "destination_country": "Tunisia",
            "motif": "Achat équipement", "has_justification": False,
            "kyc_complete": False,
        },
        "risk_signals": [],  # KYC porté par level1_decision
        "fatf_checklist": ["R10", "R20"],
        "level1_decision": "KYC",
        "expected": "REVIEW",
        "acceptable": ["REVIEW", "KYC", "EDD"],
        "justification": "Nouveau client sans KYC complet → vérification d'identité "
                         "requise avant exécution (R10 CDD).",
    },
    {
        "id": "REVIEW_03",
        "category": "REVIEW",
        "description": "SWIFT montant modéré pays faible risque sans doc",
        "transaction": {
            "tx_type": "SWIFT", "amount": 6000, "currency": "USD",
            "sender_name": "Société ABC", "sender_country": "Tunisia",
            "beneficiary_name": "Partner Inc", "destination_country": "Canada",
            "motif": "Service de consultation", "has_justification": False,
            "kyc_complete": True,
        },
        "risk_signals": ["MOTIVE_VAGUE"],
        "fatf_checklist": ["R10", "R16", "R20"],
        "level1_decision": "EDD",
        "expected": "EDD",
        "acceptable": ["REVIEW", "EDD"],
        "justification": "SWIFT sans justificatif documenté → diligence renforcée "
                         "pour vérifier l'objet économique (R10/R16).",
    },

    # ─────────────────────────────────────────────────────────────────
    #  CATÉGORIE EDD — Diligence renforcée (4)
    # ─────────────────────────────────────────────────────────────────
    {
        "id": "EDD_01",
        "category": "EDD",
        "description": "Destination pays grey-list FATF",
        "transaction": {
            "tx_type": "SWIFT", "amount": 9000, "currency": "USD",
            "sender_name": "Import Export SA", "sender_country": "Tunisia",
            "beneficiary_name": "Trading Co", "destination_country": "Morocco",
            "motif": "Importation marchandise", "has_justification": True,
            "kyc_complete": True,
        },
        "risk_signals": ["COUNTRY_GREY_DESTINATION_COUNTRY"],
        "fatf_checklist": ["R10", "R19", "R20"],
        "level1_decision": "EDD",
        "expected": "EDD",
        "acceptable": ["EDD", "STR"],
        "justification": "Pays destination en liste grise FATF → diligence renforcée "
                         "obligatoire (R19 higher-risk countries).",
    },
    {
        "id": "EDD_02",
        "category": "EDD",
        "description": "Personne politiquement exposée (PEP)",
        "transaction": {
            "tx_type": "VIREMENT_LOCAL", "amount": 4000, "currency": "TND",
            "sender_name": "Ministre X", "sender_country": "Tunisia",
            "beneficiary_name": "Société Y", "destination_country": "Tunisia",
            "motif": "Investissement", "has_justification": True, "kyc_complete": True,
        },
        "risk_signals": ["SANCTION_PEP_SENDER"],
        "fatf_checklist": ["R12", "R20"],
        "level1_decision": "EDD",
        "expected": "EDD",
        "acceptable": ["EDD", "STR"],
        "justification": "Émetteur PEP → diligence renforcée et approbation hiérarchique "
                         "obligatoires (R12 PEP).",
    },
    {
        "id": "EDD_03",
        "category": "EDD",
        "description": "Montant élevé sous seuil STR sans justificatif",
        "transaction": {
            "tx_type": "SWIFT", "amount": 9500, "currency": "USD",
            "sender_name": "Entreprise Z", "sender_country": "Tunisia",
            "beneficiary_name": "Offshore Ltd", "destination_country": "Cyprus",
            "motif": "Frais de gestion", "has_justification": False, "kyc_complete": True,
        },
        "risk_signals": ["AMOUNT_JUST_BELOW_THRESHOLD", "MOTIVE_VAGUE"],
        "fatf_checklist": ["R10", "R16", "R20"],
        "level1_decision": "EDD",
        "expected": "EDD",
        "acceptable": ["EDD", "STR"],
        "justification": "Montant proche seuil + destination offshore + pas de justificatif "
                         "→ vigilance renforcée (R10).",
    },
    {
        "id": "EDD_04",
        "category": "EDD",
        "description": "Banque correspondante montant modéré",
        "transaction": {
            "tx_type": "SWIFT", "amount": 8500, "currency": "EUR",
            "sender_name": "Banque A", "sender_country": "Tunisia",
            "beneficiary_name": "Bank B", "destination_country": "Lebanon",
            "motif": "Correspondent banking settlement", "has_justification": True,
            "kyc_complete": True, "intermediary_bank": "INTLXX22",
        },
        "risk_signals": ["COUNTRY_GREY_DESTINATION_COUNTRY", "AMOUNT_JUST_BELOW_THRESHOLD"],
        "fatf_checklist": ["R10", "R13", "R19", "R20"],
        "level1_decision": "EDD",
        "expected": "EDD",
        "acceptable": ["EDD", "STR"],
        "justification": "Relation de correspondance bancaire + pays à risque → "
                         "diligence renforcée correspondant (R13).",
    },

    # ─────────────────────────────────────────────────────────────────
    #  CATÉGORIE STR — Déclaration de soupçon (7)
    # ─────────────────────────────────────────────────────────────────
    {
        "id": "STR_01",
        "category": "STR",
        "description": "Smurfing — dépôts fractionnés (scénario S3)",
        "transaction": {
            "tx_type": "DEPOT_ESPECES", "amount": 4700, "currency": "TND",
            "sender_name": "Mohamed Trabelsi", "sender_country": "Tunisia",
            "beneficiary_name": "Compte propre", "destination_country": "Tunisia",
            "notes": "Billets neufs, client nerveux", "has_justification": False,
        },
        "risk_signals": ["SMURFING_PATTERN", "AMOUNT_JUST_BELOW_THRESHOLD", "MOTIVE_VAGUE"],
        "fatf_checklist": ["R10", "R11", "R20"],
        "level1_decision": "STR",
        "expected": "STR",
        "justification": "Fractionnement détecté (3+ dépôts < 5000 TND en 72h) → "
                         "déclaration de soupçon obligatoire (R20, BCT 2018-09 art. structuring).",
    },
    {
        "id": "STR_02",
        "category": "STR",
        "description": "Mandat Western Union suspect (scénario S4)",
        "transaction": {
            "tx_type": "MANDAT", "amount": 4000, "currency": "TND",
            "sender_name": "Alice Dupont", "sender_country": "Tunisia",
            "beneficiary_name": "Bob Martin", "destination_country": "Tunisia",
            "motif": "Aide familiale", "has_justification": False,
            "operator": "WESTERN_UNION", "withdrawal_mode": "CASH",
        },
        "risk_signals": ["MOTIVE_VAGUE", "CASH_WITHDRAWAL",
                         "HIGH_RISK_OPERATOR_WESTERN_UNION", "THRESHOLD_STR"],
        "fatf_checklist": ["R10", "R14", "R16", "R20"],
        "level1_decision": "STR",
        "expected": "STR",
        "justification": "Mandat > 3000 TND, opérateur à risque, motif vague, retrait espèces "
                         "→ STR obligatoire (R20, R14 MVTS).",
    },
    {
        "id": "STR_03",
        "category": "STR",
        "description": "SWIFT au-dessus du seuil sans justificatif",
        "transaction": {
            "tx_type": "SWIFT", "amount": 18000, "currency": "USD",
            "sender_name": "Société Opaque", "sender_country": "Tunisia",
            "beneficiary_name": "Unknown Trading", "destination_country": "UAE",
            "motif": "", "has_justification": False,
        },
        "risk_signals": ["THRESHOLD_STR", "MOTIVE_VAGUE"],
        "fatf_checklist": ["R10", "R16", "R20"],
        "level1_decision": "STR",
        "expected": "STR",
        "justification": "SWIFT > 10000 USD sans justificatif économique → STR "
                         "obligatoire (BCT seuil + R20).",
    },
    {
        "id": "STR_04",
        "category": "STR",
        "description": "Dépôt espèces juste sous seuil répété",
        "transaction": {
            "tx_type": "DEPOT_ESPECES", "amount": 4900, "currency": "TND",
            "sender_name": "Client Récurrent", "sender_country": "Tunisia",
            "beneficiary_name": "Compte propre", "destination_country": "Tunisia",
            "has_justification": False,
        },
        "risk_signals": ["AMOUNT_JUST_BELOW_THRESHOLD", "SMURFING_PATTERN"],
        "fatf_checklist": ["R10", "R11", "R20"],
        "level1_decision": "STR",
        "expected": "STR",
        "justification": "Montant 4900 (98% du seuil 5000) + vélocité élevée = structuration "
                         "manifeste → STR (R20).",
    },
    {
        "id": "STR_05",
        "category": "STR",
        "description": "Mandat motif vague gros montant",
        "transaction": {
            "tx_type": "MANDAT", "amount": 5500, "currency": "TND",
            "sender_name": "Expéditeur", "sender_country": "Tunisia",
            "beneficiary_name": "Destinataire", "destination_country": "Tunisia",
            "motif": "Divers", "has_justification": False, "operator": "WESTERN_UNION",
        },
        "risk_signals": ["THRESHOLD_STR", "MOTIVE_VAGUE", "HIGH_RISK_OPERATOR_WESTERN_UNION"],
        "fatf_checklist": ["R10", "R14", "R20"],
        "level1_decision": "STR",
        "expected": "STR",
        "justification": "Mandat élevé via opérateur à risque, motif non spécifié "
                         "→ STR (R20, R14).",
    },
    {
        "id": "STR_06",
        "category": "STR",
        "description": "Transfert montant élevé heure inhabituelle",
        "transaction": {
            "tx_type": "SWIFT", "amount": 15000, "currency": "USD",
            "sender_name": "Compte Pro", "sender_country": "Tunisia",
            "beneficiary_name": "Beneficiary", "destination_country": "Turkey",
            "motif": "Payment", "has_justification": False,
            "timestamp": "2026-05-19T03:15:00",
        },
        "risk_signals": ["THRESHOLD_STR", "UNUSUAL_HOUR_3h", "MOTIVE_VAGUE"],
        "fatf_checklist": ["R10", "R16", "R20"],
        "level1_decision": "STR",
        "expected": "STR",
        "justification": "Montant élevé + heure anormale (3h) + motif vague = faisceau "
                         "d'indices → STR (R20).",
    },
    {
        "id": "STR_07",
        "category": "STR",
        "description": "Dépôt espèces important client nerveux",
        "transaction": {
            "tx_type": "DEPOT_ESPECES", "amount": 4800, "currency": "TND",
            "sender_name": "Déposant", "sender_country": "Tunisia",
            "beneficiary_name": "Compte propre", "destination_country": "Tunisia",
            "notes": "Comportement nerveux, refus de justifier l'origine",
            "has_justification": False,
        },
        "risk_signals": ["AMOUNT_JUST_BELOW_THRESHOLD", "MOTIVE_VAGUE",
                         "SMURFING_PATTERN"],
        "fatf_checklist": ["R10", "R11", "R20"],
        "level1_decision": "STR",
        "expected": "STR",
        "justification": "Dépôt sous seuil + refus de justifier origine des fonds "
                         "→ soupçon de blanchiment, STR (R20).",
    },

    # ─────────────────────────────────────────────────────────────────
    #  CATÉGORIE BLOCK — Blocage immédiat (5)
    # ─────────────────────────────────────────────────────────────────
    {
        "id": "BLOCK_01",
        "category": "BLOCK",
        "description": "Individu sanctionné ONU (scénario S2)",
        "transaction": {
            "tx_type": "SWIFT", "amount": 21800, "currency": "USD",
            "sender_name": "Eric Badege", "sender_country": "Congo",
            "beneficiary_name": "Iran National Bank", "destination_country": "Iran",
            "motif": "", "has_justification": False,
        },
        "risk_signals": ["SANCTION_UN_INDIVIDUAL_SENDER", "THRESHOLD_STR",
                         "COUNTRY_BLACK_DESTINATION_COUNTRY",
                         "COUNTRY_GREY_SENDER_COUNTRY", "MOTIVE_VAGUE"],
        "fatf_checklist": ["R6", "R10", "R19", "R20"],
        "level1_decision": "BLOCK",
        "expected": "BLOCK",
        "justification": "Émetteur sur liste sanctions ONU (UNSCR 1267) → gel des fonds "
                         "obligatoire, blocage immédiat (R6). Override absolu.",
    },
    {
        "id": "BLOCK_02",
        "category": "BLOCK",
        "description": "Pays liste noire FATF (Corée du Nord)",
        "transaction": {
            "tx_type": "SWIFT", "amount": 12000, "currency": "USD",
            "sender_name": "Société", "sender_country": "Tunisia",
            "beneficiary_name": "DPRK Entity", "destination_country": "North Korea",
            "motif": "Trade", "has_justification": False,
        },
        "risk_signals": ["COUNTRY_BLACK_DESTINATION_COUNTRY", "THRESHOLD_STR"],
        "fatf_checklist": ["R19", "R20"],
        "level1_decision": "BLOCK",
        "expected": "BLOCK",
        "justification": "Destination en liste noire FATF (Corée du Nord) → contre-mesures "
                         "obligatoires, blocage (R19). Override absolu.",
    },
    {
        "id": "BLOCK_03",
        "category": "BLOCK",
        "description": "Entité sanctionnée bénéficiaire",
        "transaction": {
            "tx_type": "SWIFT", "amount": 7000, "currency": "EUR",
            "sender_name": "Client", "sender_country": "Tunisia",
            "beneficiary_name": "Sanctioned Entity Ltd", "destination_country": "Syria",
            "motif": "Payment", "has_justification": True,
        },
        "risk_signals": ["SANCTION_UN_ENTITY_BENEFICIARY",
                         "COUNTRY_BLACK_DESTINATION_COUNTRY"],
        "fatf_checklist": ["R6", "R19", "R20"],
        "level1_decision": "BLOCK",
        "expected": "BLOCK",
        "justification": "Bénéficiaire entité sanctionnée + pays sous embargo → gel "
                         "obligatoire (R6). Le justificatif ne lève PAS le blocage.",
    },
    {
        "id": "BLOCK_04",
        "category": "BLOCK",
        "description": "Sanctions + seuil combinés",
        "transaction": {
            "tx_type": "SWIFT", "amount": 30000, "currency": "USD",
            "sender_name": "Designated Person", "sender_country": "Iran",
            "beneficiary_name": "Receiver", "destination_country": "Iran",
            "motif": "Business", "has_justification": False,
        },
        "risk_signals": ["SANCTION_UN_INDIVIDUAL_SENDER", "THRESHOLD_STR",
                         "COUNTRY_BLACK_SENDER_COUNTRY",
                         "COUNTRY_BLACK_DESTINATION_COUNTRY"],
        "fatf_checklist": ["R6", "R19", "R20"],
        "level1_decision": "BLOCK",
        "expected": "BLOCK",
        "justification": "Cumul sanctions individuelles + pays noir des deux côtés "
                         "→ blocage absolu (R6 + R19).",
    },
    {
        "id": "BLOCK_05",
        "category": "BLOCK",
        "description": "PEP sanctionné montant élevé",
        "transaction": {
            "tx_type": "SWIFT", "amount": 50000, "currency": "USD",
            "sender_name": "Sanctioned PEP", "sender_country": "Tunisia",
            "beneficiary_name": "Shell Company", "destination_country": "Iran",
            "motif": "Investment", "has_justification": False,
        },
        "risk_signals": ["SANCTION_UN_INDIVIDUAL_SENDER", "SANCTION_PEP_SENDER",
                         "THRESHOLD_STR", "COUNTRY_BLACK_DESTINATION_COUNTRY"],
        "fatf_checklist": ["R6", "R12", "R19", "R20"],
        "level1_decision": "BLOCK",
        "expected": "BLOCK",
        "justification": "PEP sanctionné + société-écran + destination liste noire "
                         "→ blocage immédiat (R6 + R12 + R19).",
    },
]


# ══════════════════════════════════════════════════════════════════════
#  STATISTIQUES DU JEU DE TEST
# ══════════════════════════════════════════════════════════════════════

def scenario_stats():
    """Retourne la répartition des scénarios par catégorie."""
    from collections import Counter
    cats = Counter(s["category"] for s in SCENARIOS)
    return dict(cats)


if __name__ == "__main__":
    stats = scenario_stats()
    print(f"Jeu de scénarios étiquetés : {len(SCENARIOS)} cas")
    print(f"Répartition par décision attendue :")
    for cat, n in sorted(stats.items()):
        print(f"  {cat:<8} : {n}")