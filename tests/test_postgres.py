# -*- coding: utf-8 -*-
"""Test rapide de la connexion PostgreSQL (Tâche 4) — indépendant de l'API.

Objectif : vérifier EN ISOLATION que la base est joignable et que la couche
d'accès (api/store.py) fonctionne, AVANT de lancer uvicorn.

    python test_postgres.py

Affiche un diagnostic clair (backend choisi, aller-retour save/load, nombre de
dossiers en base) ou l'erreur exacte s'il y a un problème.
"""

# Ce script vit dans un sous-dossier : on expose la racine du projet aux imports.
import sys as _sys, pathlib as _pathlib
_RACINE = _pathlib.Path(__file__).resolve().parents[1]
if str(_RACINE) not in _sys.path:
    _sys.path.insert(0, str(_RACINE))

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from api.store import _resolve_dsn, PostgresJobStore, get_store  # noqa: E402

print("=" * 64)
print("  Test de connexion PostgreSQL — couche de persistance (Tâche 4)")
print("=" * 64)

# 1. La config est-elle trouvée ?
dsn = _resolve_dsn()
if not dsn:
    print("❌ Aucun DATABASE_URL ni db_config.json trouvé.")
    print("   → db_config.json devrait exister à la racine (déjà pré-rempli).")
    sys.exit(1)
# On masque le mot de passe dans l'affichage
safe_dsn = dsn
if "@" in dsn and "://" in dsn:
    head, tail = dsn.split("://", 1)
    if "@" in tail and ":" in tail.split("@")[0]:
        creds, host = tail.split("@", 1)
        user = creds.split(":", 1)[0]
        safe_dsn = f"{head}://{user}:****@{host}"
print(f"✔ Config trouvée : {safe_dsn}")

# 2. Le driver psycopg2 est-il installé ?
try:
    import psycopg2  # noqa: F401
    print("✔ Driver psycopg2 installé.")
except ImportError:
    print("❌ psycopg2 n'est pas installé.")
    print("   → pip install psycopg2-binary")
    sys.exit(1)

# 3. La base est-elle joignable ? (création du schéma incluse)
try:
    store = PostgresJobStore(dsn)
    print("✔ Connexion PostgreSQL OK + table `jobs` prête.")
except Exception as e:
    print(f"❌ Connexion PostgreSQL impossible : {e}")
    print("   → docker compose up -d postgres  (puis attends ~10 s)")
    sys.exit(1)

# 4. Aller-retour save → load sur un dossier de test
test_job = {
    "tx_id": "TX-TEST-POSTGRES-0001",
    "owner": "test@local", "source": "TEST", "status": "DONE",
    "summary": {"ca_decision": "STR", "risk_level": "CRITIQUE",
                "state": "REJECTED_BY_SUPERVISOR"},
    "transaction": {"tx_type": "VIREMENT_LOCAL", "amount": 12650000,
                    "currency": "TND"},
    "created_at": "2026-07-21T10:00:00",
    "_files": [{"filename": "gros.pdf", "content_b64": "AAAA..."}],
}
store.save(test_job)
loaded = store.load_all()
ok = "TX-TEST-POSTGRES-0001" in loaded
files_stripped = "_files" not in loaded.get("TX-TEST-POSTGRES-0001", {})
res = store.query(decision="STR", min_amount=10000)
print(f"✔ Aller-retour save/load : {'OK' if ok else 'ÉCHEC'} "
      f"(_files exclus : {'oui' if files_stripped else 'NON'})")
print(f"✔ Requête SQL query(decision=STR, min_amount=10000) : "
      f"{len(res)} dossier(s)")

# NETTOYAGE : on retire le dossier de test pour ne pas polluer la base ni les
# écrans (il n'a pas de clé `steps`, ce qui ne représente pas un vrai dossier).
store.delete("TX-TEST-POSTGRES-0001")
remaining = store.load_all()
print(f"✔ Dossier de test supprimé — dossiers réels en base : {len(remaining)}")

print()
print("🎉 PostgreSQL est opérationnel — au lancement d'uvicorn tu devrais voir")
print("   « ✅ persistance PostgreSQL active ».")
