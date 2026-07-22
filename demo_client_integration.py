"""
demo_client_integration.py
==========================
Démontre l'approche par les risques : la MÊME transaction donne une décision
DIFFÉRENTE selon le profil du client. Et FATCA reste séparé de l'AML.
"""
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from compliance_agent import ComplianceAgent
from client_scoring_agent import (ClientScoringAgent, ClientProfile,
                                   client_score_to_signals)

# ── 1. Deux clients, scorés par le ClientScoringAgent ──────────────────
client_agent = ClientScoringAgent()

standard = client_agent.score_client(ClientProfile(
    client_id="STD", full_name="Sami Ben Ali", nationality="Tunisia",
    kyc_complete=True, account_age_days=1000, previous_strs=0,
    account_type="INDIVIDUAL"))

risky = client_agent.score_client(ClientProfile(
    client_id="PEP", full_name="Politician X", nationality="Nigeria",
    is_pep=True, pep_type="FOREIGN", profession="Import-Export",
    kyc_complete=False, account_age_days=30, previous_strs=2,
    account_type="BUSINESS", beneficial_owner_identified=False))

us_person = client_agent.score_client(ClientProfile(
    client_id="US", full_name="John Smith", nationality="United States",
    country_residence="Tunisia", tax_residences=["US", "TN"],
    birth_country="United States", kyc_complete=True, account_age_days=800,
    previous_strs=0, account_type="INDIVIDUAL", has_w9=False))

# ── 2. UNE SEULE transaction, identique pour les trois ─────────────────
TX = {
    "tx_type": "SWIFT", "amount": 8000, "currency": "USD",
    "sender_country": "Tunisia", "destination_country": "France",
    "motif": "Paiement facture", "has_justification": True, "kyc_complete": True,
}

agent = ComplianceAgent(neo4j_driver=None, verbose=False)
CHECKLIST = ["R10", "R16", "R20"]

print("\n" + "=" * 70)
print("  MÊME TRANSACTION (SWIFT 8000 USD → France), 3 CLIENTS DIFFÉRENTS")
print("=" * 70)

for label, client in [("Client STANDARD", standard),
                      ("Client HAUT RISQUE (PEP étranger)", risky),
                      ("Client US PERSON (FATCA)", us_person)]:
    signals = client_score_to_signals(client)
    d = agent.analyze(TX, [], risk_signals=signals, fatf_checklist=CHECKLIST,
                      level1_decision="PASS", client_profile=client)
    rc = d.risk_components
    fatca = (d.client_context.get("fatca_obligation") or "—")
    print(f"\n  ── {label} ──")
    print(f"     Risque client structurel : {client.level} (score {client.score:.0f})")
    print(f"     Signaux dérivés          : {signals or 'aucun'}")
    print(f"     Inhérent : viol={rc.inherent_violations:.2f} "
          f"+ soft={rc.inherent_soft:.2f} + CLIENT={rc.inherent_client:.2f} "
          f"= {rc.inherent_total:.2f}")
    print(f"     → DÉCISION TRANSACTION : {d.decision}  (score {d.risk_score:.0f}/100)")
    if fatca != "—" and fatca != "NONE":
        print(f"     ⚑ FATCA (FISCAL, ≠ AML)  : {fatca}")

print("\n" + "=" * 70)
print("  CONCLUSION : la transaction est identique, mais")
print("    • Client standard      → PASS")
print("    • Client haut risque   → EDD (plancher de diligence FATF RBA)")
print("    • Client US person     → décision AML inchangée + obligation FATCA séparée")
print("=" * 70 + "\n")
