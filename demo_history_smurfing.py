"""
demo_history_smurfing.py
========================
Démontre la VALEUR du Point 5 (historique Redis) : trois dépôts espèces
arrivent SÉPARÉMENT dans la journée. Sans mémoire, Level 1 voit trois
opérations isolées sous le seuil → PASS à chaque fois. AVEC l'historique
glissant, le 3ᵉ dépôt révèle le fractionnement (smurfing) → STR.

  Pattern de production (par transaction) :
    1. récupérer l'historique passé du client (store.recent_deposits)
    2. l'injecter dans la transaction → Level 1 détecte le pattern
    3. enregistrer la transaction dans le store (mémoire glissante)

Lancer :  venv/Scripts/python.exe demo_history_smurfing.py
"""
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
sys.path.insert(0, "level1_rules")

from client_history_store import get_history_store
from level1_rules.level1_orchestrator import Level1Orchestrator

store = get_history_store(verbose=True)
orch  = Level1Orchestrator()
now   = datetime.now(timezone.utc)

CID = "CLIENT_DUPONT"

# Trois dépôts espèces de 4 800 TND, étalés sur la journée (chacun < seuil 5000)
deposits = [
    {"label": "Dépôt #1 (matin)",      "hours_ago": 8},
    {"label": "Dépôt #2 (midi)",       "hours_ago": 4},
    {"label": "Dépôt #3 (après-midi)", "hours_ago": 0},
]

print("\n" + "=" * 70)
print("  SCÉNARIO : 3 dépôts espèces de 4 800 TND dans la journée (même client)")
print("  Chaque dépôt < seuil 5 000 TND — isolément, rien d'anormal.")
print("=" * 70)

for d in deposits:
    tx = {
        "client_id": CID, "tx_type": "DEPOT_ESPECES",
        "amount": 4800, "currency": "TND",
        "sender_name": "Ali Dupont", "sender_country": "Tunisia",
        "beneficiary_name": "Compte propre", "destination_country": "Tunisia",
        "timestamp": (now - timedelta(hours=d["hours_ago"])).isoformat(),
        "has_justification": False,
    }

    # 1. Mémoire glissante : historique PASSÉ du client (avant ce dépôt)
    tx["customer_history"] = store.recent_deposits(CID)

    # 2. Level 1 évalue le dépôt courant À LA LUMIÈRE de l'historique
    result = orch.check_transaction(tx)

    # 3. Enregistrer ce dépôt dans le store (pour les suivants)
    store.record(tx, decision=result.decision)

    n_hist = len(tx["customer_history"])
    flag = "🔴 SMURFING" if result.decision == "STR" else "🟢"
    print(f"\n  {d['label']}")
    print(f"     Historique connu (72h) : {n_hist} dépôt(s) antérieur(s)")
    print(f"     Décision Level 1       : {result.decision}  {flag}")
    if result.risk_signals:
        print(f"     Signaux                : {result.risk_signals}")

print("\n" + "=" * 70)
print("  CONCLUSION")
print("    • Dépôts #1 et #2 : PASS (pas assez d'historique pour conclure)")
print("    • Dépôt #3        : STR — le fractionnement n'est visible QUE grâce")
print("                        à la mémoire glissante (Redis/Point 5).")
print("    Sans historique partagé, les 3 dépôts auraient tous été PASS.")
print("=" * 70)

# ── Bonus : profil comportemental agrégé du client ────────────────────
prof = store.behavioral_profile(CID)
print(f"\n  👤 Profil comportemental {CID} (transmis aux agents Niveau 2) :")
print(f"     velocity_24h={prof.velocity_24h} | tx_30j={prof.transaction_count_30d} "
      f"| volume_30j={prof.total_volume_30d:,.0f} TND | STR_passés={prof.previous_strs}")

store.close()
print(f"\n  Backend historique : {store.backend.name}\n")
