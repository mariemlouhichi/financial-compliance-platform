"""
build_clients_db.py — Construit clients_db.json depuis HI-Small
================================================================
Extrait les 5000 comptes les plus actifs depuis HI-Small_Trans.csv,
les enrichit avec les métadonnées de HI-Small_accounts.csv (Bank Name,
Bank ID, Entity ID, Entity Name), puis ajoute synthétiquement les
attributs AML/KYC manquants (PEP, KYC, STR antérieurs, FATCA).

Distribution synthétique réaliste (sources : FATF, Wolfsberg 2024) :
  - PEP             : 2%
  - KYC incomplet   : 5%
  - STR antérieurs  : 3%
  - FATCA US        : 4%

Override manuel : 2 profils spécifiques pour la démo soutenance.

Lancer :
  python build_clients_db.py
"""
import pandas as pd
import json
import random
from pathlib import Path
from datetime import datetime, timedelta

random.seed(42)  # reproductibilité

# ── Configuration ─────────────────────────────────────────────────────
TRANS_PATH = r"C:\Users\louhi\Downloads\projet_pfe\PFE_AML_KYC\IBM\HI-Small_Trans.csv"
ACCT_PATH  = r"C:\Users\louhi\Downloads\projet_pfe\PFE_AML_KYC\IBM\HI-Small_accounts.csv"
OUTPUT     = "clients_db.json"

N_CLIENTS = 5000
ROWS_TRANS = 2_000_000

PEP_RATE             = 0.02
KYC_INCOMPLETE_RATE  = 0.05
PRIOR_STR_RATE       = 0.03
FATCA_US_RATE        = 0.04

# Pays réalistes pour clientèle tunisienne (Tunisia majoritaire)
COUNTRY_POOL = (["Tunisia"] * 12 
                + ["France", "Germany", "Italy", "Spain", "Morocco", "Algeria"])
BUSINESS_POOL = ["retail", "import_export", "consulting", "salary_account",
                 "real_estate", "manufacturing", "wholesale"]


def main():
    print("=" * 70)
    print("  BUILD CLIENTS_DB.JSON — extraction HI-Small + enrichissement")
    print("=" * 70)
    
    # ── 1. Charge transactions et compte les comptes actifs ───────────
    print(f"\n  [1/5] Chargement de {ROWS_TRANS:,} transactions...")
    if not Path(TRANS_PATH).exists():
        print(f"  ❌ ERREUR : {TRANS_PATH} introuvable")
        return
    
    trans = pd.read_csv(TRANS_PATH, nrows=ROWS_TRANS)
    trans.columns = [c.strip().replace(" ", "_").replace(".", "_") 
                     for c in trans.columns]
    
    # Renomme Account (1ère et 2ème occurrence)
    cols = list(trans.columns)
    rename, seen = {}, False
    for c in cols:
        if c == "Account" and not seen:
            rename[c] = "from_account"; seen = True
        elif c == "Account_1":
            rename[c] = "to_account"
    trans = trans.rename(columns=rename)
    
    print(f"        ✓ {len(trans):,} transactions chargées")
    
    # ── 2. Comptes les plus actifs ─────────────────────────────────────
    print(f"\n  [2/5] Identification des {N_CLIENTS:,} comptes les plus actifs...")
    counts = pd.concat([
        trans["from_account"].rename("account"),
        trans["to_account"].rename("account"),
    ]).value_counts().head(N_CLIENTS)
    active_accounts = counts.index.astype(str).tolist()
    print(f"        ✓ Top compte : {active_accounts[0]} "
          f"({counts.iloc[0]:,} transactions)")
    
    # ── 3. Charge accounts.csv pour métadonnées ────────────────────────
    print(f"\n  [3/5] Jointure avec accounts.csv...")
    acct_meta = {}
    if Path(ACCT_PATH).exists():
        acct = pd.read_csv(ACCT_PATH)
        acct.columns = [c.strip().replace(" ", "_") for c in acct.columns]
        print(f"        ✓ Colonnes accounts.csv : {list(acct.columns)}")
        
        # Construire mapping account → (bank_name, bank_id, entity_id, entity_name)
        for _, row in acct.iterrows():
            acc_num = str(row.get("Account_Number", ""))
            if acc_num:
                acct_meta[acc_num] = {
                    "bank_name": str(row.get("Bank_Name", "Unknown")),
                    "bank_id": str(row.get("Bank_ID", "0")),
                    "entity_id": str(row.get("Entity_ID", "")),
                    "entity_name": str(row.get("Entity_Name", "Unknown")),
                }
        print(f"        ✓ {len(acct_meta):,} mappings account → metadata")
    else:
        print(f"        ⚠️ {ACCT_PATH} introuvable — métadonnées vides")
    
    # ── 4. Construction des profils enrichis ───────────────────────────
    print(f"\n  [4/5] Enrichissement synthétique...")
    clients = {}
    pep_count, str_count, kyc_incomplete_count, fatca_count = 0, 0, 0, 0
    
    for account in active_accounts:
        meta = acct_meta.get(account, {
            "bank_name": "Unknown Bank",
            "bank_id": "0",
            "entity_id": "",
            "entity_name": f"Entity-{account[-6:]}",
        })
        
        # Tirages synthétiques
        is_pep = random.random() < PEP_RATE
        kyc_complete = random.random() >= KYC_INCOMPLETE_RATE
        prior_strs = 1 if random.random() < PRIOR_STR_RATE else 0
        is_fatca_us = random.random() < FATCA_US_RATE
        
        if is_pep: pep_count += 1
        if not kyc_complete: kyc_incomplete_count += 1
        if prior_strs: str_count += 1
        if is_fatca_us: fatca_count += 1
        
        # Date d'ouverture aléatoire (6 mois à 5 ans)
        days_old = random.randint(180, 365 * 5)
        open_date = (datetime.now() - timedelta(days=days_old)).date()
        
        clients[account] = {
            "client_id": account,
            "bank_name": meta["bank_name"],
            "bank_id": meta["bank_id"],
            "entity_id": meta["entity_id"],
            "entity_name": meta["entity_name"],
            "country_of_residence": random.choice(COUNTRY_POOL),
            "nationality": random.choice(COUNTRY_POOL),
            "is_pep": is_pep,
            "pep_type": random.choice(["DOMESTIC", "FOREIGN"]) if is_pep else None,
            "pep_function": "Public official" if is_pep else None,
            "kyc_complete": kyc_complete,
            "kyc_date": open_date.isoformat(),
            "kyc_missing": [] if kyc_complete else ["proof_of_funds"],
            "prior_strs": prior_strs,
            "last_str_date": (datetime.now() - timedelta(
                days=random.randint(30, 365))).date().isoformat() 
                if prior_strs else None,
            "fatca_status": "US_PERSON" if is_fatca_us else "NOT_US",
            "account_open_date": open_date.isoformat(),
            "expected_monthly_volume": random.randint(2000, 100000),
            "expected_currency": random.choice(["TND", "USD", "EUR"]),
            "business_type": random.choice(BUSINESS_POOL),
            "source": "HI-Small (synthetic enrichment)",
        }
    
    # ── 5. Override manuel — profils démo soutenance ───────────────────
    print(f"\n  [5/5] Ajout des profils démo (override manuel)...")
    
    # Profil 1 — Compte exportateur Congo (scénario fraude)
    clients["100428660"] = {
        "client_id": "100428660",
        "bank_name": "Congo Bank #310",
        "bank_id": "0310",
        "entity_id": "ENT_CONGO_001",
        "entity_name": "Eric Badege Export Co.",
        "country_of_residence": "Congo",
        "nationality": "Congolese",
        "is_pep": False, "pep_type": None, "pep_function": None,
        "kyc_complete": True, 
        "kyc_date": "2024-01-15",
        "kyc_missing": [],
        "prior_strs": 0, "last_str_date": None,
        "fatca_status": "NOT_US",
        "account_open_date": "2022-06-01",
        "expected_monthly_volume": 50000,
        "expected_currency": "USD",
        "business_type": "import_export",
        "source": "DEMO_OVERRIDE",
        "notes": "Scénario démo — exporteur vers zones à risque"
    }
    
    # Profil 2 — PEP avec STR antérieur
    clients["100428780"] = {
        "client_id": "100428780",
        "bank_name": "Tunis Bank #001",
        "bank_id": "001",
        "entity_id": "ENT_TUN_PEP_001",
        "entity_name": "Élu Municipal PEP",
        "country_of_residence": "Tunisia",
        "nationality": "Tunisian",
        "is_pep": True, 
        "pep_type": "DOMESTIC",
        "pep_function": "Élu municipal",
        "kyc_complete": False,
        "kyc_date": "2023-11-05",
        "kyc_missing": ["proof_of_funds"],
        "prior_strs": 1,
        "last_str_date": "2025-09-12",
        "fatca_status": "NOT_US",
        "account_open_date": "2020-08-20",
        "expected_monthly_volume": 25000,
        "expected_currency": "TND",
        "business_type": "consulting",
        "source": "DEMO_OVERRIDE",
        "notes": "PEP domestique avec STR antérieur"
    }
    
    # Profil DEFAULT (fallback pour comptes inconnus)
    clients["DEFAULT"] = {
        "client_id": "DEFAULT",
        "bank_name": "Unknown",
        "bank_id": "0",
        "entity_id": "UNKNOWN",
        "entity_name": "Client inconnu (nouveau)",
        "country_of_residence": "Unknown",
        "nationality": "Unknown",
        "is_pep": False, "pep_type": None,
        "kyc_complete": False,
        "kyc_date": None,
        "kyc_missing": ["all_required"],
        "prior_strs": 0,
        "fatca_status": "UNKNOWN",
        "account_open_date": None,
        "expected_monthly_volume": 0,
        "expected_currency": "TND",
        "business_type": "unknown",
        "source": "DEFAULT",
        "notes": "Profil par défaut pour client jamais vu"
    }
    
    # ── Sauvegarde ─────────────────────────────────────────────────────
    output_data = {
        "_metadata": {
            "description": "Base clients hybride : HI-Small + enrichissement synthétique",
            "version": "2.0",
            "created": datetime.now().isoformat(),
            "total_clients": len(clients),
            "source_dataset": "HI-Small_Trans.csv + HI-Small_accounts.csv",
            "enrichment_rates": {
                "pep": PEP_RATE,
                "kyc_incomplete": KYC_INCOMPLETE_RATE,
                "prior_strs": PRIOR_STR_RATE,
                "fatca_us": FATCA_US_RATE,
            },
            "stats": {
                "total": len(clients),
                "from_dataset": N_CLIENTS,
                "demo_overrides": 2,
                "default": 1,
                "pep_count": pep_count,
                "kyc_incomplete_count": kyc_incomplete_count,
                "prior_strs_count": str_count,
                "fatca_us_count": fatca_count,
            }
        },
        "clients": clients,
    }
    
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n" + "=" * 70)
    print(f"  ✅ Fichier créé : {OUTPUT}")
    print(f"=" * 70)
    print(f"     Total clients : {len(clients)}")
    print(f"     PEP           : {pep_count} ({pep_count/N_CLIENTS*100:.1f}%)")
    print(f"     KYC incomplet : {kyc_incomplete_count} ({kyc_incomplete_count/N_CLIENTS*100:.1f}%)")
    print(f"     STR antérieurs: {str_count} ({str_count/N_CLIENTS*100:.1f}%)")
    print(f"     FATCA US      : {fatca_count} ({fatca_count/N_CLIENTS*100:.1f}%)")
    print(f"     Override démo : 2 profils + DEFAULT")


if __name__ == "__main__":
    main()