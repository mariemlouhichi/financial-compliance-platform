"""
test_client_history_store.py — tests de l'historique client (Point 5)
=====================================================================
Tests rapides, hors-ligne (backend MÉMOIRE forcé → aucun broker Redis requis).
Couvrent : fenêtre glissante, format smurfing, profil comportemental, nouveau
bénéficiaire, expiration, et l'intégration avec la détection Level 1.

Lancer :  venv/Scripts/python.exe -m pytest test_client_history_store.py -q
"""

from datetime import datetime, timedelta, timezone

import pytest

from client_history_store import ClientHistoryStore, _InMemoryBackend


@pytest.fixture
def store():
    return ClientHistoryStore(_InMemoryBackend())


def _ts(hours_ago=0, days_ago=0):
    return (datetime.now(timezone.utc)
            - timedelta(hours=hours_ago, days=days_ago)).isoformat()


# ══════════════════════════════════════════════════════════════════════
#  FENÊTRE GLISSANTE & FORMAT SMURFING
# ══════════════════════════════════════════════════════════════════════

def test_recent_deposits_within_window(store):
    cid = "C1"
    for h in (10, 5, 1):
        store.record({"client_id": cid, "tx_type": "DEPOT_ESPECES",
                      "amount": 4800, "currency": "TND", "timestamp": _ts(hours_ago=h)})
    deps = store.recent_deposits(cid, hours=72)
    assert len(deps) == 3
    # format attendu par Level 1 _detect_smurfing
    assert set(deps[0].keys()) == {"amount", "currency", "timestamp"}


def test_recent_deposits_excludes_old(store):
    cid = "C2"
    store.record({"client_id": cid, "tx_type": "DEPOT_ESPECES", "amount": 4800,
                  "currency": "TND", "timestamp": _ts(hours_ago=100)})   # > 72h
    store.record({"client_id": cid, "tx_type": "DEPOT_ESPECES", "amount": 4800,
                  "currency": "TND", "timestamp": _ts(hours_ago=2)})
    assert len(store.recent_deposits(cid, hours=72)) == 1


def test_recent_deposits_only_cash(store):
    cid = "C3"
    store.record({"client_id": cid, "tx_type": "SWIFT", "amount": 9000,
                  "currency": "USD", "timestamp": _ts(hours_ago=1)})
    store.record({"client_id": cid, "tx_type": "DEPOT_ESPECES", "amount": 4800,
                  "currency": "TND", "timestamp": _ts(hours_ago=1)})
    deps = store.recent_deposits(cid, hours=72)
    assert len(deps) == 1   # le SWIFT n'est pas un dépôt espèces


# ══════════════════════════════════════════════════════════════════════
#  PROFIL COMPORTEMENTAL
# ══════════════════════════════════════════════════════════════════════

def test_behavioral_profile_aggregates(store):
    cid = "C4"
    store.record({"client_id": cid, "tx_type": "SWIFT", "amount": 8000, "currency": "USD",
                  "destination_country": "France", "timestamp": _ts(days_ago=10)})
    store.record({"client_id": cid, "tx_type": "SWIFT", "amount": 12000, "currency": "USD",
                  "destination_country": "Germany", "timestamp": _ts(days_ago=5)})
    store.record({"client_id": cid, "tx_type": "SWIFT", "amount": 9000, "currency": "USD",
                  "destination_country": "France", "timestamp": _ts(hours_ago=2)})
    p = store.behavioral_profile(cid)
    assert p.transaction_count_30d == 3
    assert p.international_wire_count_30d == 3
    assert p.velocity_24h == 1                       # un seul dans les 24h
    assert sorted(p.countries_last_90d) == ["France", "Germany"]
    assert p.total_volume_30d > 0                    # normalisé TND


def test_previous_strs_counted(store):
    cid = "C5"
    store.record({"client_id": cid, "tx_type": "DEPOT_ESPECES", "amount": 4800,
                  "currency": "TND", "timestamp": _ts(days_ago=3)}, decision="STR")
    store.record({"client_id": cid, "tx_type": "VIREMENT_LOCAL", "amount": 2000,
                  "currency": "TND", "timestamp": _ts(days_ago=1)})
    assert store.behavioral_profile(cid).previous_strs == 1


def test_30d_aggregate_excludes_beyond_30d(store):
    cid = "C6"
    store.record({"client_id": cid, "tx_type": "SWIFT", "amount": 9000, "currency": "USD",
                  "destination_country": "France", "timestamp": _ts(days_ago=45)})
    store.record({"client_id": cid, "tx_type": "SWIFT", "amount": 9000, "currency": "USD",
                  "destination_country": "Spain", "timestamp": _ts(days_ago=2)})
    p = store.behavioral_profile(cid)
    assert p.transaction_count_30d == 1              # le tx de 45j est hors 30j
    assert sorted(p.countries_last_90d) == ["France", "Spain"]   # mais dans 90j


# ══════════════════════════════════════════════════════════════════════
#  NOUVEAU BÉNÉFICIAIRE
# ══════════════════════════════════════════════════════════════════════

def test_is_new_receiver(store):
    cid = "C7"
    store.record({"client_id": cid, "tx_type": "SWIFT", "amount": 5000, "currency": "USD",
                  "beneficiary_name": "ACME", "timestamp": _ts(days_ago=3)})
    assert store.is_new_receiver(cid, "ACME") is False
    assert store.is_new_receiver(cid, "NEWCO") is True


# ══════════════════════════════════════════════════════════════════════
#  INTÉGRATION AVEC LEVEL 1 (la valeur métier du module)
# ══════════════════════════════════════════════════════════════════════

def test_feeds_level1_smurfing_detection(store):
    """3 dépôts arrivés SÉPARÉMENT → l'historique reconstitué déclenche STR."""
    pytest.importorskip("level1_rules.threshold_rules", reason="Level 1 requis")
    from level1_rules.threshold_rules import ThresholdChecker

    cid = "C8"
    for h in (10, 5):  # 2 dépôts antérieurs
        store.record({"client_id": cid, "tx_type": "DEPOT_ESPECES", "amount": 4800,
                      "currency": "TND", "timestamp": _ts(hours_ago=h)})
    history = store.recent_deposits(cid)
    # 3e dépôt courant + 2 en historique = fractionnement
    res = ThresholdChecker().check("DEPOT_ESPECES", 4800, "TND",
                                   customer_history=history)
    assert res.smurfing_detected is True
    assert res.action == "STR"


def test_enrich_structure(store):
    cid = "C9"
    store.record({"client_id": cid, "tx_type": "SWIFT", "amount": 8000, "currency": "USD",
                  "destination_country": "France", "beneficiary_name": "OLD",
                  "timestamp": _ts(days_ago=2)})
    enr = store.enrich({"client_id": cid, "tx_type": "SWIFT", "beneficiary_name": "NEW"})
    assert enr["client_id"] == cid
    assert isinstance(enr["customer_history"], list)
    assert "behavioral_profile" in enr
    assert enr["behavioral_profile"]["new_receiver"] is True


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
