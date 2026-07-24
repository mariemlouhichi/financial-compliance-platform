# -*- coding: utf-8 -*-
"""
sync_docker_env.py — Génère le fichier .env (format Docker) à partir de
                     cloud_env.ps1, pour injecter les identifiants cloud dans
                     le conteneur `api` sans les retaper.

    python sync_docker_env.py
    docker compose up -d --build

docker compose charge automatiquement .env et interpole ${VAR} dans
docker-compose.yml. Le fichier .env est gitignoré.
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE / "cloud_env.ps1"
OUT = BASE / ".env"

# Variables cloud à transférer vers le conteneur (PAS DATABASE_URL : en test
# local, l'API garde le PostgreSQL conteneurisé pour l'historique des dossiers).
CLES = ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
        "WEAVIATE_URL", "WEAVIATE_API_KEY",
        "REDIS_URL", "REDIS_CONNECT_TIMEOUT"]


def main():
    if not SRC.exists():
        print(f"❌ {SRC.name} introuvable. Crée-le d'abord (voir cloud_env.example.ps1).")
        return 1
    txt = SRC.read_text(encoding="utf-8")

    lignes = []
    manquants = []
    for cle in CLES:
        m = re.search(rf'\$env:{cle}\s*=\s*"([^"]*)"', txt)
        val = m.group(1) if m else ""
        if not val or "COLLER_ICI" in val or "REMPLACER" in val:
            manquants.append(cle)
            continue
        lignes.append(f"{cle}={val}")

    if manquants:
        print("⚠️  Valeurs absentes/non renseignées dans cloud_env.ps1 :",
              ", ".join(manquants))
        print("   Complète cloud_env.ps1 puis relance.")
        # On écrit quand même ce qu'on a, mais on signale.

    entete = ("# Généré par sync_docker_env.py depuis cloud_env.ps1 — NE PAS COMMITTER.\n"
              "# Injecté dans le conteneur api via l'interpolation de docker-compose.\n")
    OUT.write_text(entete + "\n".join(lignes) + "\n", encoding="utf-8")

    print(f"✅ {OUT.name} écrit ({len(lignes)} variable(s)) :")
    for l in lignes:
        cle = l.split("=", 1)[0]
        # aperçu masqué pour les secrets
        if any(s in cle for s in ("PASSWORD", "API_KEY", "URL")):
            v = l.split("=", 1)[1]
            apercu = (v[:18] + "…") if len(v) > 20 else v
            print(f"   {cle} = {apercu}")
        else:
            print(f"   {l}")
    print("\nÉtape suivante :  docker compose up -d --build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
