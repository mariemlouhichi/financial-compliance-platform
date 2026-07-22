# ============================================================================
# Couche de persistance des dossiers (jobs) — PostgreSQL avec REPLI fichiers.
#
# Tâche 4 : remplacer le stockage en fichiers JSON par une VRAIE base
# transactionnelle (PostgreSQL) permettant des requêtes complexes (montant,
# période, décision, client) et survivant aux redémarrages / au déploiement
# Azure (le système de fichiers des conteneurs est éphémère).
#
# Sélection AUTOMATIQUE du backend au démarrage :
#   • connexion PostgreSQL disponible  → PostgresJobStore
#   • sinon (dev local sans Postgres)  → FileJobStore  (repli, jamais bloquant)
#
# Config PostgreSQL (ordre de priorité) :
#   1. variable d'env  DATABASE_URL = postgresql://user:pass@host:5432/dbname
#      (format standard — c'est aussi celui fourni par Azure)
#   2. fichier db_config.json à la racine (gitignoré) :
#        { "host": "...", "port": 5432, "user": "...", "password": "...",
#          "dbname": "..." }
#   3. aucune des deux → repli fichiers
#
# Le blob complet du dossier est stocké en JSONB (reconstruction fidèle) ;
# quelques colonnes sont extraites en clair pour les requêtes SQL rapides.
# ============================================================================
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


# ── Extraction des colonnes requêtables depuis le blob job ──────────────────
def _cols(job: dict) -> dict:
    """Colonnes structurées extraites du dossier (pour les requêtes SQL)."""
    summary = job.get("summary") or {}
    tx = job.get("transaction") or {}
    amount = tx.get("amount")
    try:
        amount = float(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    created_dt = _parse_dt(job.get("created_at"))
    return {
        "tx_id": job.get("tx_id"),
        "owner": job.get("owner"),
        "source": job.get("source"),
        "status": job.get("status"),
        "decision": summary.get("ca_decision"),
        "risk_level": summary.get("risk_level"),
        "state": summary.get("state"),
        "tx_type": tx.get("tx_type"),
        "amount": amount,
        "currency": tx.get("currency"),
        "created_at": created_dt,
    }


def _parse_dt(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _strip_files(job: dict) -> dict:
    """Retire les fichiers base64 volumineux avant persistance."""
    return {k: v for k, v in job.items() if k != "_files"}


def _filter_in_memory(jobs, owner=None, decision=None, min_amount=None,
                      date_from=None, date_to=None, limit=200):
    """Filtrage Python (repli fichiers/mémoire, même sémantique que le SQL)."""
    df, dt = _parse_dt(date_from), _parse_dt(date_to)
    out = []
    for job in jobs:
        c = _cols(job)
        if owner and owner.lower() not in (c["owner"] or "").lower():
            continue
        if decision and c["decision"] != decision:
            continue
        if min_amount is not None and (c["amount"] or 0) < float(min_amount):
            continue
        if df and (not c["created_at"] or c["created_at"] < df):
            continue
        if dt and (not c["created_at"] or c["created_at"] > dt):
            continue
        out.append(job)
    out.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return out[:limit]


# ── Backend FICHIERS (repli dev local — comportement historique) ────────────
class FileJobStore:
    kind = "fichiers JSON"

    def __init__(self, jobs_dir: Path):
        self.dir = Path(jobs_dir)

    def save(self, job: dict) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / f"{job['tx_id']}.json").write_text(
                json.dumps(_strip_files(job), ensure_ascii=False, default=str),
                encoding="utf-8")
        except Exception as e:
            print(f"  [STORE] ⚠️ persistance {job.get('tx_id')} échouée : {e}")

    def load_all(self) -> dict:
        out: dict = {}
        if not self.dir.exists():
            return out
        for p in sorted(self.dir.glob("TX-*.json")):
            try:
                job = json.loads(p.read_text(encoding="utf-8"))
                out[job["tx_id"]] = job
            except Exception:
                continue
        return out

    def query(self, **filters) -> list:
        return _filter_in_memory(self.load_all().values(), **filters)

    def delete(self, tx_id: str) -> None:
        try:
            (self.dir / f"{tx_id}.json").unlink(missing_ok=True)
        except Exception as e:
            print(f"  [STORE] ⚠️ suppression {tx_id} échouée : {e}")


# ── Backend PostgreSQL ──────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    tx_id       TEXT PRIMARY KEY,
    owner       TEXT,
    source      TEXT,
    status      TEXT,
    decision    TEXT,
    risk_level  TEXT,
    state       TEXT,
    tx_type     TEXT,
    amount      NUMERIC,
    currency    TEXT,
    created_at  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ DEFAULT now(),
    data        JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_owner    ON jobs (owner);
CREATE INDEX IF NOT EXISTS idx_jobs_created  ON jobs (created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_decision ON jobs (decision);
"""

_UPSERT = """
INSERT INTO jobs (tx_id, owner, source, status, decision, risk_level, state,
                  tx_type, amount, currency, created_at, updated_at, data)
VALUES (%(tx_id)s, %(owner)s, %(source)s, %(status)s, %(decision)s,
        %(risk_level)s, %(state)s, %(tx_type)s, %(amount)s, %(currency)s,
        %(created_at)s, now(), %(data)s)
ON CONFLICT (tx_id) DO UPDATE SET
    owner=EXCLUDED.owner, source=EXCLUDED.source, status=EXCLUDED.status,
    decision=EXCLUDED.decision, risk_level=EXCLUDED.risk_level,
    state=EXCLUDED.state, tx_type=EXCLUDED.tx_type, amount=EXCLUDED.amount,
    currency=EXCLUDED.currency, created_at=EXCLUDED.created_at,
    updated_at=now(), data=EXCLUDED.data;
"""


class PostgresJobStore:
    kind = "PostgreSQL"

    def __init__(self, dsn: str):
        import psycopg2
        from psycopg2 import pool
        self._psycopg2 = psycopg2
        # connect_timeout court : si Postgres est injoignable, on bascule vite
        # sur le repli fichiers au lieu de bloquer le démarrage.
        self._pool = pool.ThreadedConnectionPool(1, 8, dsn, connect_timeout=3)
        self._init_schema()

    def _init_schema(self) -> None:
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
            conn.commit()
        finally:
            self._pool.putconn(conn)

    def save(self, job: dict) -> None:
        from psycopg2.extras import Json
        row = _cols(job)
        row["data"] = Json(_strip_files(job),
                           dumps=lambda o: json.dumps(o, ensure_ascii=False,
                                                      default=str))
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(_UPSERT, row)
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"  [STORE] ⚠️ upsert {job.get('tx_id')} échoué : {e}")
        finally:
            self._pool.putconn(conn)

    def load_all(self) -> dict:
        from psycopg2.extras import RealDictCursor
        conn = self._pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT data FROM jobs ORDER BY created_at")
                rows = cur.fetchall()
            return {r["data"]["tx_id"]: r["data"]
                    for r in rows if r["data"] and r["data"].get("tx_id")}
        finally:
            self._pool.putconn(conn)

    def delete(self, tx_id: str) -> None:
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM jobs WHERE tx_id = %s", (tx_id,))
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"  [STORE] ⚠️ suppression {tx_id} échouée : {e}")
        finally:
            self._pool.putconn(conn)

    def query(self, owner=None, decision=None, min_amount=None,
              date_from=None, date_to=None, limit=200) -> list:
        from psycopg2.extras import RealDictCursor
        sql = ["SELECT data FROM jobs WHERE TRUE"]
        params: dict = {}
        if owner:
            sql.append("AND owner ILIKE %(owner)s"); params["owner"] = f"%{owner}%"
        if decision:
            sql.append("AND decision = %(decision)s"); params["decision"] = decision
        if min_amount is not None:
            sql.append("AND amount >= %(min_amount)s")
            params["min_amount"] = float(min_amount)
        if date_from:
            sql.append("AND created_at >= %(date_from)s")
            params["date_from"] = _parse_dt(date_from)
        if date_to:
            sql.append("AND created_at <= %(date_to)s")
            params["date_to"] = _parse_dt(date_to)
        sql.append("ORDER BY created_at DESC LIMIT %(limit)s")
        params["limit"] = int(limit)
        conn = self._pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(" ".join(sql), params)
                return [r["data"] for r in cur.fetchall()]
        finally:
            self._pool.putconn(conn)


# ── Résolution config + sélection du backend ────────────────────────────────
def _resolve_dsn() -> Optional[str]:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    cfg = ROOT / "db_config.json"
    if cfg.exists():
        try:
            c = json.loads(cfg.read_text(encoding="utf-8"))
            if c.get("host") and c.get("dbname"):
                pwd = c.get("password", "")
                return (f"postgresql://{c.get('user', 'postgres')}:{pwd}"
                        f"@{c['host']}:{c.get('port', 5432)}/{c['dbname']}")
        except Exception:
            pass
    return None


def get_store(jobs_dir: Path):
    """Sélectionne le backend : PostgreSQL si joignable, sinon fichiers JSON.

    Ne lève jamais : toute erreur Postgres (driver absent, base injoignable)
    bascule proprement sur le repli fichiers pour ne pas bloquer l'API.
    """
    dsn = _resolve_dsn()
    if dsn:
        try:
            store = PostgresJobStore(dsn)
            print("  [STORE] ✅ persistance PostgreSQL active")
            return store
        except Exception as e:
            print(f"  [STORE] ⚠️ PostgreSQL indisponible ({e}) "
                  f"→ repli fichiers JSON")
    else:
        print("  [STORE] ℹ️ pas de DATABASE_URL/db_config.json "
              "→ persistance fichiers JSON")
    return FileJobStore(jobs_dir)
