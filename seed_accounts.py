# -*- coding: utf-8 -*-
"""
seed_accounts.py — Génère `platform_seed.db` : des comptes de démonstration
DÉJÀ VÉRIFIÉS (verified=1), embarqués dans l'image de l'API.

Pourquoi : sur Azure Container Apps, le stockage est ÉPHÉMÈRE et `platform.db`
n'est pas monté → une base créée à chaud disparaît au moindre redémarrage. En
pré-chargeant ces comptes dans l'image, ils sont TOUJOURS présents, quel que
soit le nombre de redémarrages. Aucun email de vérification n'est requis pour eux.

    python seed_accounts.py       # (après avoir renseigné AGENT_EMAIL ci-dessous)

Le Dockerfile copie ensuite `platform_seed.db` → `/app/platform.db`.
`platform_seed.db` est gitignoré (il contient des hash de mots de passe).
En local, docker-compose monte le vrai `platform.db` qui prend le dessus.
"""
import hashlib
import json
import secrets
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "platform_seed.db"

# ── ⚠️ METS TON EMAIL ICI ───────────────────────────────────────────────────
#  C'est le compte AGENT (client) : il reçoit les emails de DÉCISION pendant la
#  démo. Mets ton adresse réelle pour montrer l'email arriver dans ta boîte.
AGENT_EMAIL = "agent@demo.local"
# ────────────────────────────────────────────────────────────────────────────


# Même hachage que api/auth.py (PBKDF2-SHA256, 200 000 itérations).
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return f"{salt}${dk.hex()}"


# Profil KYC complet pour l'agent → Client Scoring « complet », risque FAIBLE.
PROFIL_AGENT = {
    "client_id": AGENT_EMAIL,
    "full_name": "Agent Bancaire",
    "nationality": "TN",
    "country_of_residence": "TN",
    "account_type": "INDIVIDUAL",
    "rib": "07098000012345678901",
    "address": "Avenue Habib Bourguiba, Tunis",
    "phone": "+216 71 000 000",
    "is_pep": False,
    "pep_type": "",
    "prior_strs": 0,
    "beneficial_owner_identified": True,
    "kyc_complete": True,
    "fatca_status": "NONE",
    "account_open_date": "2020-01-15",
}

# (rôle, email, mot de passe, nom affiché, profil KYC ou None)
#  Mots de passe uniformes PAR RÔLE pour la démo — faciles à retenir.
COMPTES = [
    # ── Admins ──
    ("ADMIN",              "admin@demo.local", "Admin@2026",   "Administrateur Plateforme", None),
    ("ADMIN",              "admin2@demo.local", "Admin@2026",  "Administrateur",            None),
    # ── Superviseur (Compliance Officer) ──
    ("COMPLIANCE_OFFICER", "compliance@demo.local",    "Officer@2026", "Responsable Conformité",    None),
    # ── Auditeur ──
    ("AUDITOR",            "user@demo.local", "Auditor@2026", "Auditeur Interne",          None),
    # ── Agents (clients) — le 1er a un profil KYC complet ──
    ("USER",               AGENT_EMAIL,                "Agent@2026",   "Agent Bancaire",            PROFIL_AGENT),
    ("USER",               "agent2@demo.local", "Agent@2026", "Agent Bancaire 2",          None),
    ("USER",               "agent3@demo.local", "Agent@2026",  "Agent Bancaire 3",          None),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username    TEXT PRIMARY KEY,
    email       TEXT UNIQUE NOT NULL,
    pw_hash     TEXT NOT NULL,
    role        TEXT NOT NULL,
    verified    INTEGER DEFAULT 0,
    verify_code TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    reset_code  TEXT,
    full_name   TEXT,
    profile     TEXT
)
"""


def main() -> int:
    if AGENT_EMAIL == "TON_EMAIL@gmail.com":
        print("⚠️  Renseigne d'abord AGENT_EMAIL (ton email) en haut du script,")
        print("    sinon le compte agent ne recevra pas les emails de décision.")
    if OUT.exists():
        OUT.unlink()
    conn = sqlite3.connect(OUT)
    conn.execute(SCHEMA)
    for role, email, pwd, name, profile in COMPTES:
        email = email.strip().lower()
        conn.execute(
            "INSERT INTO users (username, email, pw_hash, role, verified, "
            "verify_code, full_name, profile) VALUES (?,?,?,?,1,NULL,?,?)",
            (email, email, hash_password(pwd), role, name,
             json.dumps(profile, ensure_ascii=False) if profile else None))
    conn.commit()
    conn.close()

    print(f"\n✅ {OUT.name} créé avec {len(COMPTES)} comptes vérifiés :\n")
    print(f"   {'RÔLE':20} {'EMAIL':28} MOT DE PASSE")
    print(f"   {'-'*20} {'-'*28} {'-'*12}")
    for role, email, pwd, *_ in COMPTES:
        print(f"   {role:20} {email:28} {pwd}")
    print("\nÉtape suivante :  .\\redeploy_api_azure.ps1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
