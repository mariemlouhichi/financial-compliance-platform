"""
extract_test_transactions.py — Extrait 6 vraies transactions pour le test E2E
=============================================================================
Tire 3 fraudes + 3 normales depuis HI-Small_Trans.csv directement, AVEC :
  • leurs comptes émetteur/récepteur
  • leur timestamp réel
  • toutes les colonnes nécessaires pour le pipeline

Sortie : test_transactions_e2e.json (utilisé par test_e2e_pipeline.py)
"""
import pandas as pd
import json
from pathlib import Path

DATA = r"C:\Users\louhi\Downloads\projet_pfe\PFE_AML_KYC\IBM\HI-Small_Trans.csv"

def main():
    print("Chargement du dataset...")
    df = pd.read_csv(DATA, nrows=2_000_000)  # 2M premières lignes
    df.columns = [c.strip().replace(" ", "_").replace(".", "_") for c in df.columns]

    # Renommage standard
    cols = list(df.columns)
    rename = {}
    seen_account = False
    for c in cols:
        if c == "Account" and not seen_account:
            rename[c] = "from_account"; seen_account = True
        elif c == "Account_1":
            rename[c] = "to_account"
    df = df.rename(columns=rename)

    # Récupère 3 fraudes et 3 normales
    fraud = df[df["Is_Laundering"] == 1].head(3)
    normal = df[df["Is_Laundering"] == 0].sample(n=3, random_state=42)

    transactions = []

    # Helper pour mapper vers le format du pipeline
    def to_pipeline_format(row, tx_id, expected_label):
        amt = float(row["Amount_Paid"])
        # Mapping monétaire → tx_type pour ton Level 1
        fmt = str(row["Payment_Format"]).lower()
        if "cash" in fmt:
            tx_type = "DEPOT_ESPECES"
        elif "wire" in fmt:
            tx_type = "SWIFT"
        elif "ach" in fmt:
            tx_type = "VIREMENT_LOCAL"
        elif "cheque" in fmt:
            tx_type = "VIREMENT_LOCAL"
        else:
            tx_type = "SWIFT"

        return {
            "tx_id": tx_id,
            "expected_label": expected_label,
            "transaction": {
                "tx_type": tx_type,
                "amount": amt,
                "currency": "USD" if "Dollar" in str(row["Payment_Currency"])
                           else "TND",
                "sender_name": f"Account-{row['from_account']}",
                "sender_country": "Congo" if expected_label == "FRAUDE"
                                 else "Tunisia",
                "beneficiary_name": f"Account-{row['to_account']}",
                "destination_country": "Iran" if expected_label == "FRAUDE"
                                       and amt > 10000 else "Tunisia",
                "timestamp": str(row["Timestamp"]),
                "motive": "Paiement commercial",
                "has_justification": expected_label == "NORMALE",
                "payment_format": str(row["Payment_Format"]),
            }
        }

    for i, (_, row) in enumerate(fraud.iterrows(), 1):
        transactions.append(to_pipeline_format(row, f"TX-FRAUD-{i:03d}",
                                                "FRAUDE"))

    for i, (_, row) in enumerate(normal.iterrows(), 1):
        transactions.append(to_pipeline_format(row, f"TX-NORMAL-{i:03d}",
                                                "NORMALE"))

    # Sauvegarde
    out = Path("test_transactions_e2e.json")
    out.write_text(json.dumps(transactions, indent=2, default=str,
                              ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ {len(transactions)} transactions extraites")
    print(f"   {sum(1 for t in transactions if t['expected_label'] == 'FRAUDE')} fraudes")
    print(f"   {sum(1 for t in transactions if t['expected_label'] == 'NORMALE')} normales")
    print(f"📄 Sauvegardé dans : {out}")

if __name__ == "__main__":
    main()