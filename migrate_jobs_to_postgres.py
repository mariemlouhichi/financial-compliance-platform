# -*- coding: utf-8 -*-
"""Migration UNIQUE : copie les dossiers output/jobs/*.json vers PostgreSQL.

À lancer UNE fois, après :
  1. docker compose up -d postgres      (démarre la base)
  2. pip install psycopg2-binary         (driver Python)

Non destructif : les fichiers JSON restent en place (repli conservé).
Idempotent : relançable sans risque (UPSERT par tx_id).

    python migrate_jobs_to_postgres.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from api.store import FileJobStore, PostgresJobStore, _resolve_dsn  # noqa: E402


def main() -> None:
    dsn = _resolve_dsn()
    if not dsn:
        print("Aucun DATABASE_URL ni db_config.json trouvé → rien à migrer.")
        print("Renseigne db_config.json (déjà pré-rempli) et réessaie.")
        return

    files = FileJobStore(Path(__file__).resolve().parent / "output" / "jobs")
    jobs = files.load_all()
    if not jobs:
        print("Aucun dossier fichier (output/jobs/*.json) à migrer.")
        return

    try:
        pg = PostgresJobStore(dsn)
    except Exception as e:
        print(f"Connexion PostgreSQL impossible : {e}")
        print("Vérifie que le conteneur postgres tourne et que psycopg2 est installé.")
        return

    n = 0
    for job in jobs.values():
        pg.save(job)
        n += 1
    print(f"✅ {n} dossier(s) migré(s) depuis output/jobs vers PostgreSQL.")


if __name__ == "__main__":
    main()
