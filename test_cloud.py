# -*- coding: utf-8 -*-
"""
test_cloud.py — Vérifie les 3 services cloud avant migration/déploiement.

    . .\\cloud_env.ps1        (charge les identifiants dans la session)
    python test_cloud.py

Teste Neo4j Aura, Weaviate Cloud et Redis (Upstash), et affiche le contenu
déjà présent dans chacun. N'affiche JAMAIS de mot de passe ni de clé.
"""
import os
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def masque(url: str) -> str:
    """Masque le mot de passe dans une URL de connexion."""
    import re
    return re.sub(r"://([^:/@]+):[^@]+@", r"://\1:****@", url or "")


ok = {"neo4j": False, "weaviate": False, "redis": False}
print("=" * 66)
print("  Vérification des services cloud")
print("=" * 66)

# ── 1. Neo4j Aura ──────────────────────────────────────────────────────
uri = os.getenv("NEO4J_URI", "")
print(f"\n[1] Neo4j Aura — {uri or '(NEO4J_URI non défini)'}")
if not uri or "REMPLACER" in uri:
    print("    ✗ NEO4J_URI non renseigné (as-tu lancé  . .\\cloud_env.ps1  ?)")
elif not uri.startswith("neo4j+s://"):
    print("    ✗ l'URI Aura doit commencer par neo4j+s:// (connexion chiffrée)")
else:
    try:
        from neo4j import GraphDatabase
        t0 = time.time()
        drv = GraphDatabase.driver(
            uri, auth=(os.getenv("NEO4J_USER", "neo4j"),
                       os.getenv("NEO4J_PASSWORD", "")))
        drv.verify_connectivity()
        with drv.session() as s:
            n = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            r = s.run("MATCH ()-[x]->() RETURN count(x) AS c").single()["c"]
            labels = s.run(
                "CALL db.labels() YIELD label RETURN collect(label) AS l"
            ).single()["l"]
        drv.close()
        print(f"    ✓ connecté en {time.time()-t0:.1f}s")
        print(f"      nœuds : {n}   relations : {r}")
        print(f"      labels : {labels or '(base vide — migration à faire)'}")
        ok["neo4j"] = True
    except Exception as e:
        print(f"    ✗ {type(e).__name__} : {str(e)[:160]}")

# ── 2. Weaviate Cloud ──────────────────────────────────────────────────
wurl = os.getenv("WEAVIATE_URL", "")
print(f"\n[2] Weaviate Cloud — {wurl or '(WEAVIATE_URL non défini)'}")
if not wurl or "REMPLACER" in wurl:
    print("    ✗ WEAVIATE_URL non renseigné")
elif not wurl.startswith("https://"):
    print("    ✗ l'URL doit commencer par https:// (la console l'affiche sans)")
else:
    try:
        import weaviate
        from weaviate.classes.init import Auth
        t0 = time.time()
        c = weaviate.connect_to_weaviate_cloud(
            cluster_url=wurl,
            auth_credentials=Auth.api_key(os.getenv("WEAVIATE_API_KEY", "")))
        pret = c.is_ready()
        cols = list(c.collections.list_all())
        print(f"    ✓ connecté en {time.time()-t0:.1f}s (ready={pret})")
        if cols:
            for nom in cols:
                tot = c.collections.get(nom).aggregate.over_all(
                    total_count=True).total_count
                print(f"      collection {nom} : {tot} objets")
        else:
            print("      aucune collection (indexation à faire)")
        c.close()
        ok["weaviate"] = True
    except Exception as e:
        print(f"    ✗ {type(e).__name__} : {str(e)[:160]}")

# ── 3. Redis (Upstash) ─────────────────────────────────────────────────
rurl = os.getenv("REDIS_URL", "")
print(f"\n[3] Redis — {masque(rurl) or '(REDIS_URL non défini)'}")
if not rurl or "REMPLACER" in rurl:
    print("    ✗ REDIS_URL non renseigné")
elif not rurl.startswith("rediss://"):
    print("    ✗ l'URL doit commencer par rediss:// (deux « s » = TLS)")
else:
    try:
        import redis
        t0 = time.time()
        cli = redis.Redis.from_url(
            rurl, decode_responses=True,
            socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT", "10")))
        cli.ping()
        cli.set("aml:test", "ok", ex=60)
        lu = cli.get("aml:test")
        print(f"    ✓ connecté en {time.time()-t0:.1f}s — écriture/lecture : {lu}")
        print(f"      clés présentes : {cli.dbsize()}")
        ok["redis"] = True
    except Exception as e:
        print(f"    ✗ {type(e).__name__} : {str(e)[:160]}")

# ── Bilan ──────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
reussis = sum(ok.values())
for nom, etat in ok.items():
    print(f"  {'✓' if etat else '✗'} {nom}")
print(f"\n  {reussis}/3 service(s) joignable(s)")
if reussis == 3:
    print("  → Prête pour la migration des données.")
else:
    print("  → Corrige les points ✗ ci-dessus avant de continuer.")
print("=" * 66)
sys.exit(0 if reussis == 3 else 1)
