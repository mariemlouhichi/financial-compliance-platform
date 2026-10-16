# -*- coding: utf-8 -*-
"""
show_postgres.py — Affiche le CONTENU ACTUEL de la base PostgreSQL (Azure ou local).
Pratique si le jury demande à voir les données réelles.

Usage :
    . .\\cloud_env.ps1                 # charge AZURE_DATABASE_URL (base en ligne)
    python show_postgres.py
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import psycopg2
except ImportError:
    print("Manque : pip install psycopg2-binary")
    sys.exit(1)

# Priorité à la base Azure (celle que voit le jury en ligne), sinon locale.
DSN = os.environ.get("AZURE_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not DSN:
    print("Définis AZURE_DATABASE_URL (fais d'abord :  . .\\cloud_env.ps1)")
    sys.exit(1)

conn = psycopg2.connect(DSN)
cur = conn.cursor()

cur.execute("""SELECT table_name FROM information_schema.tables
               WHERE table_schema = 'public' ORDER BY table_name""")
tables = [r[0] for r in cur.fetchall()]

print(f"\n📊 Base : {DSN.split('@')[-1].split('?')[0]}")
print(f"   Tables ({len(tables)}) : {', '.join(tables) or '(aucune)'}\n")

for t in tables:
    cur.execute(f'SELECT count(*) FROM "{t}"')
    n = cur.fetchone()[0]
    print(f"── {t} : {n} ligne(s) " + "─" * 40)
    cur.execute(f'SELECT * FROM "{t}" ORDER BY 1 DESC LIMIT 5')
    cols = [d[0] for d in cur.description]
    print("   colonnes : " + ", ".join(cols))
    for row in cur.fetchall():
        vals = [str(v)[:38].replace("\n", " ") for v in row]
        print("   • " + " | ".join(vals))
    print()

cur.close()
conn.close()
print("✅ Terminé.")
