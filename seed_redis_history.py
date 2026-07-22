"""
seed_redis_history.py — Pré-charge Redis avec un historique réaliste
=====================================================================
Crée un historique simulé de 30 jours pour les comptes de la démo.
Sans ce seed, le MLPredictor (Étape 5) aurait toujours history_size=0
et utiliserait des features par défaut → faux positifs sur les normales.

Profils créés (cohérent avec clients_db.json) :
  - 100428660 (Congo, export) : 15 SWIFT vers France/Germany/Italy/Spain
  - 802559360 (Tunisia, retail): 8 VIREMENT_LOCAL réguliers
  - 801176C70 (Tunisia, retail): 2 dépôts (compte récent, peu d'historique)
  - 100428780 (PEP+STR)       : 6 transactions dont 1 STR antérieure

À lancer UNE FOIS avant test_e2e_pipeline.py.
Redis garde l'historique 90 jours (TTL natif), donc pas besoin de re-seed.
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
sys.path.insert(0, "level1_rules")

from client_history_store import get_history_store

print("\n" + "=" * 70)
print("  SEED REDIS — Création d'historiques simulés pour la démo")
print("=" * 70)

store = get_history_store(verbose=True)
now = datetime.now(timezone.utc)

# ── Client 100428660 (Congo, exporteur actif) ──────────────────────────
print("\n  [1/4] Seed 100428660 (Congo, exporteur — 15 SWIFT/30j)...")
countries = ["France", "Germany", "Italy", "Spain", "Belgium"]
for i in range(15):
    days_ago = 28 - i * 1.8
    store.record({
        "client_id": "100428660",
        "sender_account_id": "100428660",
        "tx_type": "SWIFT",
        "amount": 5000 + (i * 1500),
        "currency": "USD",
        "destination_country": countries[i % len(countries)],
        "beneficiary_name": f"PARTNER_{i % 5}",
        "receiver_account_id": f"REC_{i % 5}",
        "timestamp": (now - timedelta(days=days_ago)).isoformat(),
    })

prof = store.behavioral_profile("100428660")
print(f"        ✓ {prof.transaction_count_30d} tx/30j, "
      f"volume {prof.total_volume_30d:,.0f} TND, "
      f"intl_wire {prof.international_wire_count_30d}, "
      f"pays {len(prof.countries_last_90d)}")

# ── Client 802559360 (Tunisia, particulier régulier) ──────────────────
print("\n  [2/4] Seed 802559360 (Tunisia, particulier — 8 VIREMENT_LOCAL)...")
for i in range(8):
    store.record({
        "client_id": "802559360",
        "sender_account_id": "802559360",
        "tx_type": "VIREMENT_LOCAL",
        "amount": 1500 + (i * 200),
        "currency": "TND",
        "destination_country": "Tunisia",
        "beneficiary_name": f"LOCAL_{i % 3}",
        "receiver_account_id": f"LOC_{i % 3}",
        "timestamp": (now - timedelta(days=25 - i * 3)).isoformat(),
    })

prof = store.behavioral_profile("802559360")
print(f"        ✓ {prof.transaction_count_30d} tx/30j, "
      f"volume {prof.total_volume_30d:,.0f} TND, "
      f"profil régulier")

# ── Client 801176C70 (Tunisia, NOUVEAU — peu d'historique) ────────────
print("\n  [3/4] Seed 801176C70 (Tunisia, nouveau — 2 dépôts seulement)...")
for i in range(2):
    store.record({
        "client_id": "801176C70",
        "sender_account_id": "801176C70",
        "tx_type": "DEPOT_ESPECES",
        "amount": 500 + i * 100,
        "currency": "TND",
        "destination_country": "Tunisia",
        "beneficiary_name": "SELF",
        "receiver_account_id": "SELF_801176C70",
        "timestamp": (now - timedelta(days=10 - i * 5)).isoformat(),
    })

prof = store.behavioral_profile("801176C70")
print(f"        ✓ {prof.transaction_count_30d} tx/30j "
      f"(< 3 → LSTM-AE désactivé pour ce client)")

# ── Client 100428780 (PEP avec 1 STR antérieur) ───────────────────────
print("\n  [4/4] Seed 100428780 (PEP + STR antérieur)...")
for i in range(6):
    is_str_tx = (i == 2)  # une transaction STR au milieu
    store.record({
        "client_id": "100428780",
        "sender_account_id": "100428780",
        "tx_type": "VIREMENT_LOCAL",
        "amount": 5000 + i * 3000,
        "currency": "TND",
        "destination_country": "Tunisia",
        "beneficiary_name": f"BEN_{i}",
        "receiver_account_id": f"BEN_{i}",
        "timestamp": (now - timedelta(days=20 - i * 3)).isoformat(),
    },
    decision="STR" if is_str_tx else "PASS",
    is_str=is_str_tx)

prof = store.behavioral_profile("100428780")
print(f"        ✓ {prof.transaction_count_30d} tx/30j, "
      f"STR antérieurs={prof.previous_strs}, "
      f"volume {prof.total_volume_30d:,.0f} TND")

# ── Récapitulatif ─────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  ✅ RÉCAPITULATIF DES HISTORIQUES")
print("=" * 70)
print(f"  {'Client':<15} {'Tx/30j':>8} {'Volume':>15} {'STR':>5} {'LSTM-AE':>10}")
print(f"  {'─'*15} {'─'*8} {'─'*15} {'─'*5} {'─'*10}")
for cid in ["100428660", "802559360", "801176C70", "100428780"]:
    prof = store.behavioral_profile(cid)
    lstm_active = "✓ ACTIF" if prof.transaction_count_30d >= 3 else "✗ inactif"
    print(f"  {cid:<15} {prof.transaction_count_30d:>8} "
          f"{prof.total_volume_30d:>15,.0f} {prof.previous_strs:>5} "
          f"{lstm_active:>10}")

store.close()
print(f"\n  ✅ Seed terminé. Tu peux lancer test_e2e_pipeline.py")
print(f"  ℹ️  Note : 801176C70 n'aura que 2 tx → ML utilisera features par défaut")
print(f"  ℹ️  Les 3 autres clients : ML utilisera leurs vraies features\n")