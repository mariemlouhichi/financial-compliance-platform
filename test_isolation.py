# -*- coding: utf-8 -*-
"""
test_isolation.py — Test RAPIDE de la branche Isolation Forest (isolation_score).
Ne nécessite AUCUN service (ni Neo4j, ni Weaviate) — seulement les fichiers
modèle aml_ml_demo_*. Montre le signal d'anomalie pour un NOUVEAU client.

    python test_isolation.py
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from test_e2e_pipeline import MLPredictor

m = MLPredictor()
print(f"\n  Isolation Forest chargé        : {m._iforest is not None}")
print(f"  'isolation_score' dans features : {'isolation_score' in m.features}\n")

cas = [
    ("Virement local NORMAL — 2 500 TND",
     {"tx_type": "VIREMENT_LOCAL", "amount": 2500, "currency": "TND",
      "sender_country": "Tunisia", "destination_country": "Tunisia",
      "beneficiary_name": "Ste Services", "timestamp": "2026-08-14T10:00:00"}),
    ("Virement local ÉLEVÉ — 120 000 TND",
     {"tx_type": "VIREMENT_LOCAL", "amount": 120000, "currency": "TND",
      "sender_country": "Tunisia", "destination_country": "Tunisia",
      "beneficiary_name": "Beneficiaire X", "timestamp": "2026-08-14T10:00:00"}),
    ("SWIFT international — 50 000 USD → UAE",
     {"tx_type": "SWIFT", "amount": 50000, "currency": "USD",
      "sender_country": "Tunisia", "destination_country": "United Arab Emirates",
      "beneficiary_name": "Gulf Co", "timestamp": "2026-08-14T10:00:00"}),
]

print("  " + "-" * 66)
for nom, tx in cas:
    r = m.predict(tx, client_id="nouveau_client_test")
    print(f"  {nom}")
    print(f"      ml_score        : {r['ml_score']:.4f}   ({r['ml_decision']})")
    print(f"      isolation_score : {r['isolation_score']:.4f}   "
          f"(0 = normal · 1 = atypique)")
    print(f"      profil          : {r['profile_status']}")
    print("  " + "-" * 66)

print("\n  ✅ Test terminé. Un client sans historique reçoit désormais un")
print("     signal d'anomalie (isolation_score) — c'était le gap comblé.\n")
