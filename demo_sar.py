"""
demo_sar.py — Démonstration du SAR Generator
============================================
Démo sans Docker, sans LLM, sans pipeline complet — juste pour valider
que le SAR Generator produit un rapport complet et lisible.

Simule les sorties des 3 composants (Compliance Agent, ML, Client Scoring)
pour le scénario S2 (Iran BLOCK) et S3 (smurfing STR).
"""
from dataclasses import dataclass, field
from sar_generator import SARGenerator


# ═══════════════════════════════════════════════════════════════════════
#  MOCKS — Simule les sorties des 3 composants
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class MockRule:
    rule_text: str
    source_doc: str
    rule_ref: str
    fatf_rec: str
    severity: str = "HIGH"
    cross_validated: bool = False
    provenance: str = "SYMBOLIC"


@dataclass
class MockComplianceDecision:
    decision: str
    risk_score: float
    violated_rules: list = field(default_factory=list)
    applicable_rules: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    required_documents: list = field(default_factory=list)


@dataclass
class MockFatca:
    obligation: str = "NONE"


@dataclass
class MockClient:
    client_id: str
    level: str
    score: float
    components: dict = field(default_factory=dict)
    fatca: MockFatca = None


# ═══════════════════════════════════════════════════════════════════════
#  SCÉNARIO S2 — SWIFT vers Iran (BLOCK)
# ═══════════════════════════════════════════════════════════════════════

def demo_s2_iran_block():
    print("\n" + "=" * 70)
    print("  DÉMO S2 — SWIFT 21 800 USD vers Iran (BLOCK)")
    print("=" * 70)
    
    transaction = {
        "tx_type": "SWIFT",
        "amount": 21800,
        "currency": "USD",
        "sender_name": "Eric Badege",
        "sender_country": "Congo",
        "beneficiary_name": "Iran National Bank",
        "destination_country": "Iran",
        "has_justification": False,
        "timestamp": "2026-05-19T03:15:00",
        "motive": "",
    }
    
    compliance = MockComplianceDecision(
        decision="BLOCK",
        risk_score=100,
        violated_rules=[
            MockRule(
                rule_text="Transactions avec pays sous sanctions ONU "
                          "interdites — gel automatique des fonds requis",
                source_doc="bct_circulaire_2017_08_aml.pdf",
                rule_ref="Article 43",
                fatf_rec="R6",
                severity="CRITICAL",
                cross_validated=True),
            MockRule(
                rule_text="Iran figure sur la liste FATF des juridictions "
                          "à haut risque (countermeasures)",
                source_doc="fatf_recommendations_2024.pdf",
                rule_ref="Recommendation 19",
                fatf_rec="R19",
                severity="CRITICAL"),
            MockRule(
                rule_text="Obligation de déclaration de soupçon à la CTAF "
                          "dans les 48h pour toute transaction suspecte",
                source_doc="bct_circulaire_2006_19.pdf",
                rule_ref="Article 12",
                fatf_rec="R20",
                severity="HIGH"),
        ],
        applicable_rules=[
            MockRule("Sanctions ONU vérifiées", "bct_circulaire_2017_08",
                     "Art.43", "R6", "CRITICAL"),
            MockRule("Vérification PEP", "bct_circulaire_2006_19",
                     "Art.5", "R12", "HIGH"),
            MockRule("Identification donneur d'ordre", "loi_2003_75",
                     "Art.7", "R10", "HIGH"),
            MockRule("Conservation 5 ans", "bct_circulaire_2017_08",
                     "Art.45", "R11", "MEDIUM"),
            MockRule("Travel Rule SWIFT", "fatf_recommendations_2024",
                     "R.16", "R16", "HIGH"),
        ],
        stats={"fatf_coverage": 0.947, "bct_coverage": 0.841,
               "cross_validated": 3},
        required_documents=[
            "Justification économique impérative (refus si absent)",
            "Documentation du donneur d'ordre et du bénéficiaire effectif",
            "Formulaire goAML pour transmission CTAF dans les 48h",
        ])
    
    ml_result = {
        "ml_score": 0.97,
        "ml_decision": "FRAUDE_SUSPECTÉE",
        "shap_top": [
            {"feature": "reconstruction_error", "value": 6.2,
             "shap": 13.4, "pushes": "FRAUDE"},
            {"feature": "currency_mismatch", "value": 1,
             "shap": 8.1, "pushes": "FRAUDE"},
            {"feature": "is_night", "value": 1,
             "shap": 3.2, "pushes": "FRAUDE"},
            {"feature": "sender_fanout_ratio", "value": 0.85,
             "shap": 2.1, "pushes": "FRAUDE"},
            {"feature": "amount_vs_sender_mean", "value": 4.5,
             "shap": 1.7, "pushes": "FRAUDE"},
        ],
    }
    
    client = MockClient(
        client_id="CLT-S2-001",
        level="FAIBLE",
        score=10,
        components={},
        fatca=MockFatca(obligation="NONE"))
    
    # Génération
    gen = SARGenerator()
    sar = gen.generate(compliance, ml_result, client, transaction)
    
    # Export
    md_path = "sar_S2_iran_block.md"
    json_path = "sar_S2_iran_block.json"
    sar.to_markdown(md_path)
    sar.to_json(json_path)
    
    # Affichage
    print(f"\n✅ SAR ID : {sar.sar_id}")
    print(f"   Décision système     : {sar.decision_trigger}")
    print(f"   Score combiné        : {sar.risk_score_combined}/100")
    print(f"   Niveau de risque     : {sar.risk_level}")
    print(f"   Lois violées         : {len(sar.violated_rules)}")
    print(f"   Lois respectées      : {len(sar.respected_rules)}")
    print(f"   Documents requis     : {len(sar.required_documents)}")
    print(f"   Recommandation       : {sar.recommendation}")
    print(f"\n📄 Fichiers générés :")
    print(f"   • {md_path}    (lisible par admin)")
    print(f"   • {json_path}  (exploitable par frontend)")


# ═══════════════════════════════════════════════════════════════════════
#  SCÉNARIO S3 — DEPOT smurfing (STR)
# ═══════════════════════════════════════════════════════════════════════

def demo_s3_smurfing_str():
    print("\n" + "=" * 70)
    print("  DÉMO S3 — DEPOT_ESPECES smurfing 4 700 TND (STR)")
    print("=" * 70)
    
    transaction = {
        "tx_type": "DEPOT_ESPECES",
        "amount": 4700,
        "currency": "TND",
        "sender_name": "Mohamed Trabelsi",
        "sender_country": "Tunisia",
        "beneficiary_name": "Banque BCT",
        "destination_country": "Tunisia",
        "motive": "Dépôt en espèces",
        "has_justification": False,
        "timestamp": "2026-05-18T16:45:00",
    }
    
    compliance = MockComplianceDecision(
        decision="STR",
        risk_score=75,
        violated_rules=[
            MockRule(
                rule_text="Détection de fractionnement : 3 dépôts sous "
                          "5 000 TND en 24h pour le même client",
                source_doc="bct_circulaire_2006_19.pdf",
                rule_ref="Article 8",
                fatf_rec="R20",
                severity="HIGH",
                cross_validated=True),
            MockRule(
                rule_text="Déclaration de soupçon à la CTAF obligatoire "
                          "pour toute opération inhabituelle",
                source_doc="loi_2015_26.pdf",
                rule_ref="Article 107",
                fatf_rec="R20",
                severity="HIGH"),
        ],
        applicable_rules=[
            MockRule("Seuil déclaration espèces 10k", "bct_circulaire_2017_08",
                     "Art.43", "R20", "HIGH"),
            MockRule("Identification client", "loi_2003_75",
                     "Art.7", "R10", "HIGH"),
            MockRule("Justification origine fonds",
                     "bct_circulaire_2006_19", "Art.10", "R10", "MEDIUM"),
            MockRule("Conservation pièces", "bct_circulaire_2017_08",
                     "Art.45", "R11", "MEDIUM"),
        ],
        stats={"fatf_coverage": 0.92, "bct_coverage": 0.85})
    
    ml_result = {
        "ml_score": 0.84,
        "ml_decision": "FRAUDE_SUSPECTÉE",
        "shap_top": [
            {"feature": "sender_fanout_ratio", "value": 0.92,
             "shap": 11.2, "pushes": "FRAUDE"},
            {"feature": "is_round_100", "value": 0,
             "shap": 4.3, "pushes": "FRAUDE"},
            {"feature": "is_cash", "value": 1,
             "shap": 3.8, "pushes": "FRAUDE"},
            {"feature": "reconstruction_error", "value": 2.1,
             "shap": 2.9, "pushes": "FRAUDE"},
            {"feature": "amount_vs_sender_mean", "value": 1.6,
             "shap": 1.5, "pushes": "FRAUDE"},
        ],
    }
    
    client = MockClient(
        client_id="CLT-S3-002",
        level="MOYEN",
        score=45,
        components={"prior_strs": True, "incomplete_kyc": True})
    
    gen = SARGenerator()
    sar = gen.generate(compliance, ml_result, client, transaction)
    
    md_path = "sar_S3_smurfing_str.md"
    json_path = "sar_S3_smurfing_str.json"
    sar.to_markdown(md_path)
    sar.to_json(json_path)
    
    print(f"\n✅ SAR ID : {sar.sar_id}")
    print(f"   Décision système     : {sar.decision_trigger}")
    print(f"   Score combiné        : {sar.risk_score_combined}/100")
    print(f"   Niveau de risque     : {sar.risk_level}")
    print(f"   Lois violées         : {len(sar.violated_rules)}")
    print(f"   Lois respectées      : {len(sar.respected_rules)}")
    print(f"   Documents requis     : {len(sar.required_documents)}")
    print(f"   Recommandation       : {sar.recommendation}")
    print(f"\n📄 Fichiers générés :")
    print(f"   • {md_path}    (lisible par admin)")
    print(f"   • {json_path}  (exploitable par frontend)")


# ═══════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    demo_s2_iran_block()
    demo_s3_smurfing_str()
    print("\n" + "=" * 70)
    print("  ✅ Démo terminée — ouvrir les fichiers .md pour voir les rapports")
    print("=" * 70)