"""
compute_dataset_stats.py — Statistiques OFFLINE du dataset IBM AML HI-Small
============================================================================
Pré-calcule les agrégats du dataset (5M+ lignes) UNE SEULE FOIS et les écrit
dans output/dataset_stats.json — le backend les sert ensuite instantanément
via GET /api/dataset-stats (page « Dataset » de l'espace admin).

Pourquoi offline ? Recalculer 5M de lignes à chaque requête serait absurde ;
les stats d'un dataset figé ne changent jamais.

USAGE (une seule fois, ~1-2 min) :
  python compute_dataset_stats.py
  python compute_dataset_stats.py --csv "chemin/vers/HI-Small_Trans.csv"

Colonnes attendues (format IBM AML) :
  Timestamp, From Bank, Account, To Bank, Account.1, Amount Received,
  Receiving Currency, Amount Paid, Payment Currency, Payment Format,
  Is Laundering
"""

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

DEFAULT_CSV = r"C:\Users\louhi\Downloads\projet_pfe\PFE_AML_KYC\IBM\HI-Small_Trans.csv"
OUT_PATH = Path(__file__).resolve().parents[1] / "output" / "dataset_stats.json"

# Tranches de montants (USD) — échelle log, libellés lisibles pour le front
AMOUNT_BUCKETS = [
    (0, 100, "< 100"),
    (100, 1_000, "100 – 1k"),
    (1_000, 10_000, "1k – 10k"),
    (10_000, 100_000, "10k – 100k"),
    (100_000, 1_000_000, "100k – 1M"),
    (1_000_000, float("inf"), "> 1M"),
]


def bucket_label(amount: float) -> str:
    for lo, hi, label in AMOUNT_BUCKETS:
        if lo <= amount < hi:
            return label
    return AMOUNT_BUCKETS[-1][2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=DEFAULT_CSV, help="Chemin du CSV HI-Small")
    ap.add_argument("--chunksize", type=int, default=500_000)
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"❌ CSV introuvable : {csv_path}")

    print(f"📊 Lecture par blocs de {args.chunksize:,} lignes : {csv_path.name}")

    total = 0
    fraud = 0
    total_amount = 0.0
    by_format: Counter = Counter()
    fraud_by_format: Counter = Counter()
    by_currency: Counter = Counter()
    by_bucket: Counter = Counter()
    fraud_by_hour: Counter = Counter()

    usecols = ["Timestamp", "Amount Paid", "Payment Currency",
               "Payment Format", "Is Laundering"]

    for i, chunk in enumerate(pd.read_csv(csv_path, usecols=usecols,
                                          chunksize=args.chunksize), 1):
        total += len(chunk)
        is_fraud = chunk["Is Laundering"] == 1
        fraud += int(is_fraud.sum())
        total_amount += float(chunk["Amount Paid"].sum())

        by_format.update(chunk["Payment Format"].value_counts().to_dict())
        fraud_by_format.update(
            chunk.loc[is_fraud, "Payment Format"].value_counts().to_dict())
        by_currency.update(chunk["Payment Currency"].value_counts().to_dict())
        by_bucket.update(
            chunk["Amount Paid"].map(bucket_label).value_counts().to_dict())

        # heure de la journée des fraudes (Timestamp "2022/09/01 00:20")
        hours = pd.to_datetime(chunk.loc[is_fraud, "Timestamp"],
                               errors="coerce").dt.hour.dropna().astype(int)
        fraud_by_hour.update({f"{h:02d}h": int(c)
                              for h, c in hours.value_counts().items()})

        print(f"  bloc {i} — cumul {total:,} lignes, {fraud:,} fraudes")

    # Buckets dans l'ordre logique (pas par fréquence)
    ordered_buckets = {label: by_bucket.get(label, 0)
                       for _, _, label in AMOUNT_BUCKETS}
    # Heures triées 00h → 23h
    ordered_hours = {f"{h:02d}h": fraud_by_hour.get(f"{h:02d}h", 0)
                     for h in range(24)}

    stats = {
        "dataset": csv_path.name,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "total_transactions": total,
        "fraud_count": fraud,
        "fraud_rate_pct": round(100.0 * fraud / total, 4) if total else 0.0,
        "total_amount_usd_billion": round(total_amount / 1e9, 2),
        "by_payment_format": dict(by_format.most_common()),
        "fraud_by_payment_format": dict(fraud_by_format.most_common()),
        "by_currency": dict(by_currency.most_common(10)),
        "by_amount_bucket": ordered_buckets,
        "fraud_by_hour": ordered_hours,
    }

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps(stats, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print(f"\n✅ {total:,} transactions · {fraud:,} fraudes "
          f"({stats['fraud_rate_pct']}%)")
    print(f"✅ Écrit : {OUT_PATH}")
    print("→ GET /api/dataset-stats servira désormais ce fichier.")


if __name__ == "__main__":
    main()
