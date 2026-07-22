"""
client_history_store.py — v1.0
==============================================================================
HISTORIQUE CLIENT (Point 5 du backlog) — mémoire glissante pour la détection
intraday + profil comportemental.

PROBLÈME RÉSOLU
───────────────
Le smurfing (fractionnement) et l'analyse comportementale ont besoin de se
SOUVENIR des transactions passées d'un client. Aujourd'hui, Level 1
(`_detect_smurfing`) et le Planning Agent reçoivent `customer_history` PASSÉ EN
PARAMÈTRE (fourni manuellement par les scénarios). En production, chaque
transaction arrive SÉPARÉMENT : il faut un magasin qui agrège l'historique.

  Transaction t1 ─┐
  Transaction t2 ─┤→  ClientHistoryStore  ─→  customer_history (72h)  → Level 1 smurfing
  Transaction t3 ─┘    (Redis 24h/30j)         behavioral_profile (30j) → Planning/Compliance

DEUX FENÊTRES (conforme à l'architecture cible)
───────────────────────────────────────────────
  • 24-72h  : détection du structuring intraday (3+ dépôts < 5000 TND / 72h).
              Fenêtre glissante, expiration automatique des clés.
  • 30-90j  : agrégat comportemental par client — volume total, fréquence des
              virements internationaux, pays de destination récurrents.
              Enrichit le profil transmis aux agents.

BACKENDS (dégradation gracieuse — comme Neo4j ailleurs dans le projet)
─────────────────────────────────────────────────────────────────────
  • RedisBackend     : production. Sorted set par client, scoré par timestamp,
                       TTL automatique. (docker compose up -d redis)
  • InMemoryBackend  : repli si Redis indisponible → le système reste démontrable
                       hors-ligne (soutenance), avec un avertissement.

INGESTION (seam Kafka)
──────────────────────
  `ingest(transaction)` représente le point d'entrée qu'un CONSUMER KAFKA
  appellerait par message. Brancher un vrai broker Kafka est un détail de
  transport (non requis pour la logique) ; la méthode est prête.

NOTE : les agrégats de volume sont normalisés en TND via les taux de Level 1
(EXCHANGE_RATES) pour être comparables entre devises.
"""

import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger("client_history")

__version__ = "1.0"

# ── Fenêtres temporelles ───────────────────────────────────────────────
SMURFING_HOURS   = 72          # fenêtre de détection du fractionnement
VELOCITY_HOURS   = 24          # vélocité intraday
MONTH_DAYS       = 30          # agrégat mensuel
QUARTER_DAYS     = 90          # pays récurrents / rétention
RETENTION_SECONDS = QUARTER_DAYS * 86400   # TTL des clés Redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
KEY_PREFIX = "aml:txwin:"      # aml:txwin:{client_id} → sorted set


# ═══════════════════════════════════════════════════════════════════════
#  CONVERSION DEVISE (cohérente avec Level 1)
# ═══════════════════════════════════════════════════════════════════════

def _default_fx(amount: float, from_cur: str, to_cur: str = "TND") -> float:
    """Convertit en TND. Réutilise les taux de Level 1 si dispo, sinon table locale."""
    try:
        from level1_rules.threshold_rules import ThresholdChecker
        return ThresholdChecker._convert(float(amount or 0), from_cur or "TND", to_cur)
    except Exception:
        rates = {"EUR": 3.30, "USD": 3.10, "GBP": 3.85, "CHF": 3.45, "TND": 1.0}
        if (from_cur or "TND") == to_cur:
            return float(amount or 0)
        return float(amount or 0) * rates.get(from_cur, 1.0)


# ═══════════════════════════════════════════════════════════════════════
#  BACKENDS
# ═══════════════════════════════════════════════════════════════════════

class _InMemoryBackend:
    """Repli sans Redis : dict client_id → list[(ts_unix, payload_dict)]."""
    name = "in-memory"

    def __init__(self):
        self._data: dict = {}

    def add(self, client_id: str, ts_unix: float, payload: dict) -> None:
        self._data.setdefault(client_id, []).append((ts_unix, payload))

    def range_since(self, client_id: str, since_unix: float) -> list:
        items = self._data.get(client_id, [])
        # purge paresseuse au-delà de la rétention
        cutoff = time.time() - RETENTION_SECONDS
        if items and items[0][0] < cutoff:
            items = [(t, p) for t, p in items if t >= cutoff]
            self._data[client_id] = items
        return [p for t, p in items if t >= since_unix]

    def close(self):
        pass


class _RedisBackend:
    """Production : sorted set Redis par client, scoré par timestamp."""
    name = "redis"

    def __init__(self, client):
        self.r = client

    def add(self, client_id: str, ts_unix: float, payload: dict) -> None:
        key = KEY_PREFIX + client_id
        member = json.dumps(payload, ensure_ascii=False)
        self.r.zadd(key, {member: ts_unix})
        # purge des événements au-delà de la rétention + TTL de la clé
        self.r.zremrangebyscore(key, 0, time.time() - RETENTION_SECONDS)
        self.r.expire(key, RETENTION_SECONDS)

    def range_since(self, client_id: str, since_unix: float) -> list:
        key = KEY_PREFIX + client_id
        raw = self.r.zrangebyscore(key, since_unix, "+inf")
        out = []
        for m in raw:
            try:
                out.append(json.loads(m))
            except (ValueError, TypeError):
                continue
        return out

    def close(self):
        try:
            self.r.close()
        except Exception:
            pass


def _connect_redis(url: str, verbose: bool):
    """Tente une connexion Redis. Retourne le client ou None (repli mémoire)."""
    try:
        import redis  # type: ignore
        client = redis.Redis.from_url(url, decode_responses=True,
                                      socket_connect_timeout=2)
        client.ping()
        if verbose:
            print(f"  ✅ Redis connecté ({url})")
        return client
    except Exception as e:
        logger.info(f"Redis indisponible ({e}) — repli mémoire")
        if verbose:
            print(f"  ⚠️  Redis indisponible ({e}) — repli mémoire (in-memory)")
        return None


# ═══════════════════════════════════════════════════════════════════════
#  PROFIL COMPORTEMENTAL (sortie agrégée)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class BehavioralProfile:
    """Agrégat comportemental d'un client (enrichit Planning/Compliance)."""
    client_id:                 str = ""
    velocity_24h:              int = 0
    transaction_count_30d:     int = 0
    total_volume_30d:          float = 0.0   # TND
    avg_amount_30d:            float = 0.0   # TND
    international_wire_count_30d: int = 0
    countries_last_90d:        list = field(default_factory=list)
    previous_strs:             int = 0
    first_seen_days:           int = 0       # proxy d'ancienneté (≠ KYC account_age)

    def to_history_dict(self) -> dict:
        """Format consommé par le Planning Agent (customer_history)."""
        return {
            "previous_strs":          self.previous_strs,
            "velocity_24h":           self.velocity_24h,
            "transaction_count_30d":  self.transaction_count_30d,
            "total_volume_30d":       round(self.total_volume_30d, 2),
            "avg_amount_30d":         round(self.avg_amount_30d, 2),
            "international_wire_count_30d": self.international_wire_count_30d,
            "countries_last_90d":     self.countries_last_90d,
        }


# ═══════════════════════════════════════════════════════════════════════
#  STORE
# ═══════════════════════════════════════════════════════════════════════

class ClientHistoryStore:
    """
    Magasin d'historique client à fenêtre glissante.

    Usage :
        store = get_history_store()
        store.ingest(tx1); store.ingest(tx2); store.ingest(tx3)
        hist = store.recent_deposits("CLIENT_A")        # → Level 1 smurfing
        prof = store.behavioral_profile("CLIENT_A")     # → Planning/Compliance
    """

    def __init__(self, backend, fx_convert=None, verbose: bool = False):
        self.backend = backend
        self.fx = fx_convert or _default_fx
        self.verbose = verbose

    # ── Identité client ────────────────────────────────────────────────
    @staticmethod
    def client_id_of(tx: dict) -> str:
        return str(tx.get("client_id") or tx.get("sender_account_id")
                   or tx.get("sender_name") or "UNKNOWN")

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _to_unix(ts) -> float:
        """Parse un timestamp ISO en unix (UTC). Défaut = maintenant."""
        if not ts:
            return time.time()
        try:
            dt = datetime.fromisoformat(str(ts))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            return time.time()

    # ── ÉCRITURE ───────────────────────────────────────────────────────
    def record(self, transaction: dict, decision: str = None,
               is_str: bool = False, client_id: str = None,
               timestamp: str = None) -> str:
        """Enregistre une transaction dans la fenêtre glissante. Retourne le client_id."""
        cid = client_id or self.client_id_of(transaction)
        ts  = timestamp or transaction.get("timestamp")
        ts_unix = self._to_unix(ts)
        amount   = float(transaction.get("amount") or 0)
        currency = transaction.get("currency") or "TND"
        payload = {
            "uid":       uuid.uuid4().hex[:8],   # unicité (sorted set)
            "ts":        ts_unix,
            "tx_type":   transaction.get("tx_type", ""),
            "amount":    amount,
            "currency":  currency,
            "amount_tnd": round(self.fx(amount, currency, "TND"), 2),
            "destination_country": transaction.get("destination_country", ""),
            "receiver_id": str(transaction.get("receiver_account_id")
                               or transaction.get("beneficiary_name") or ""),
            "decision":  decision or "",
            "is_str":    bool(is_str) or decision == "STR",
        }
        self.backend.add(cid, ts_unix, payload)
        if self.verbose:
            print(f"  📥 record {cid}: {payload['tx_type']} "
                  f"{amount} {currency} @ {datetime.fromtimestamp(ts_unix)}")
        return cid

    def ingest(self, transaction: dict, **kw) -> dict:
        """
        Point d'entrée façon CONSUMER KAFKA : enregistre PUIS retourne
        l'enrichissement (historique récent + profil) pour les agents.
        """
        cid = self.record(transaction, **kw)
        return self.enrich(transaction, client_id=cid)

    # ── LECTURE ────────────────────────────────────────────────────────
    def _events_since(self, client_id: str, seconds: float, now: float = None) -> list:
        now = now or self._now()
        return self.backend.range_since(client_id, now - seconds)

    def recent_deposits(self, client_id: str, hours: int = SMURFING_HOURS,
                        now: float = None) -> list:
        """
        Historique des DÉPÔTS ESPÈCES récents au format attendu par Level 1
        `_detect_smurfing` : [{amount, currency, timestamp}].
        """
        evts = self._events_since(client_id, hours * 3600, now)
        deposits = []
        for e in evts:
            if e.get("tx_type") == "DEPOT_ESPECES":
                deposits.append({
                    "amount":    e.get("amount", 0),
                    "currency":  e.get("currency", "TND"),
                    "timestamp": datetime.fromtimestamp(
                        e.get("ts", 0), tz=timezone.utc).isoformat(),
                })
        return deposits

    def behavioral_profile(self, client_id: str, now: float = None) -> BehavioralProfile:
        """Agrège l'historique en un profil comportemental (30j/90j)."""
        now = now or self._now()
        evts_90 = self._events_since(client_id, QUARTER_DAYS * 86400, now)
        if not evts_90:
            return BehavioralProfile(client_id=client_id)

        evts_30 = [e for e in evts_90 if e.get("ts", 0) >= now - MONTH_DAYS * 86400]
        evts_24 = [e for e in evts_90 if e.get("ts", 0) >= now - VELOCITY_HOURS * 3600]

        vol_30 = sum(e.get("amount_tnd", 0) for e in evts_30)
        countries = sorted({e.get("destination_country", "") for e in evts_90
                            if e.get("destination_country")})
        first_ts = min(e.get("ts", now) for e in evts_90)

        return BehavioralProfile(
            client_id=client_id,
            velocity_24h=len(evts_24),
            transaction_count_30d=len(evts_30),
            total_volume_30d=vol_30,
            avg_amount_30d=(vol_30 / len(evts_30)) if evts_30 else 0.0,
            international_wire_count_30d=sum(1 for e in evts_30
                                            if e.get("tx_type") == "SWIFT"),
            countries_last_90d=countries,
            previous_strs=sum(1 for e in evts_90 if e.get("is_str")),
            first_seen_days=int((now - first_ts) / 86400),
        )

    def is_new_receiver(self, client_id: str, receiver_id: str,
                        days: int = QUARTER_DAYS, now: float = None) -> bool:
        """True si le bénéficiaire n'apparaît PAS dans l'historique récent."""
        if not receiver_id:
            return False
        evts = self._events_since(client_id, days * 86400, now)
        return all(e.get("receiver_id") != str(receiver_id) for e in evts)

    def enrich(self, transaction: dict, client_id: str = None) -> dict:
        """
        Construit le bloc `customer_history` consommable par Level 1 + Planning,
        à partir de l'historique stocké (l'événement courant doit être enregistré
        AVANT si on veut l'inclure ; ici on agrège l'historique connu).
        """
        cid = client_id or self.client_id_of(transaction)
        prof = self.behavioral_profile(cid)
        receiver = (transaction.get("receiver_account_id")
                    or transaction.get("beneficiary_name") or "")
        hist = prof.to_history_dict()
        hist["new_receiver"] = self.is_new_receiver(cid, str(receiver))
        return {
            "client_id":         cid,
            "customer_history":  self.recent_deposits(cid),   # liste dépôts (smurfing)
            "behavioral_profile": hist,                        # dict (Planning)
        }

    def close(self):
        self.backend.close()


# ═══════════════════════════════════════════════════════════════════════
#  FABRIQUE
# ═══════════════════════════════════════════════════════════════════════

def get_history_store(prefer_redis: bool = True, verbose: bool = False,
                      fx_convert=None) -> ClientHistoryStore:
    """Crée un store avec backend Redis si possible, sinon repli mémoire."""
    backend = None
    if prefer_redis:
        client = _connect_redis(REDIS_URL, verbose)
        if client is not None:
            backend = _RedisBackend(client)
    if backend is None:
        backend = _InMemoryBackend()
        if verbose:
            print(f"  ℹ️  Backend historique : in-memory (non persistant)")
    return ClientHistoryStore(backend, fx_convert=fx_convert, verbose=verbose)


# ═══════════════════════════════════════════════════════════════════════
#  DÉMONSTRATION STANDALONE (Redis si dispo, sinon mémoire)
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"\n  CLIENT HISTORY STORE v{__version__} — démonstration\n" + "=" * 64)
    store = get_history_store(verbose=True)
    print(f"  Backend : {store.backend.name}\n")

    from datetime import timedelta
    now = datetime.now(timezone.utc)

    # ── Scénario : 3 dépôts espèces < 5000 TND répartis sur la journée ──
    cid = "CLIENT_SMURF"
    for i, h in enumerate([10, 5, 1]):   # il y a 10h, 5h, 1h
        store.record({
            "client_id": cid, "tx_type": "DEPOT_ESPECES",
            "amount": 4800, "currency": "TND",
            "timestamp": (now - timedelta(hours=h)).isoformat(),
        })
    print(f"  ── {cid} : 3 dépôts espèces 4800 TND dans les 10 dernières heures ──")
    deps = store.recent_deposits(cid)
    print(f"     recent_deposits (72h) : {len(deps)} dépôts → "
          f"alimente Level 1 _detect_smurfing")

    # Vérifie que Level 1 détecte le smurfing avec cet historique
    try:
        from level1_rules.threshold_rules import ThresholdChecker
        res = ThresholdChecker().check("DEPOT_ESPECES", 4800, "TND",
                                       customer_history=deps)
        ok = res.smurfing_detected and res.action == "STR"
        print(f"     Level 1 → action={res.action}, smurfing={res.smurfing_detected} "
              f"{'✅' if ok else '❌'}")
    except Exception as e:
        print(f"     (Level 1 non testé : {e})")

    # ── Scénario : profil comportemental (virements internationaux) ──
    cid2 = "CLIENT_INTL"
    for i, (h, country, amt) in enumerate([
        (240, "France", 8000), (120, "Germany", 12000),
        (48, "France", 5000), (2, "Turkey", 9000)]):
        store.record({
            "client_id": cid2, "tx_type": "SWIFT", "amount": amt, "currency": "USD",
            "destination_country": country, "beneficiary_name": f"BEN_{i}",
            "timestamp": (now - timedelta(hours=h)).isoformat(),
        })
    print(f"\n  ── {cid2} : 4 SWIFT internationaux sur 10 jours ──")
    prof = store.behavioral_profile(cid2)
    print(f"     velocity_24h            : {prof.velocity_24h}")
    print(f"     transaction_count_30d   : {prof.transaction_count_30d}")
    print(f"     total_volume_30d (TND)  : {prof.total_volume_30d:,.0f}")
    print(f"     intl_wire_count_30d     : {prof.international_wire_count_30d}")
    print(f"     countries_last_90d      : {prof.countries_last_90d}")
    print(f"     new_receiver 'BEN_NEW'  : {store.is_new_receiver(cid2, 'BEN_NEW')}")
    print(f"     new_receiver 'BEN_0'    : {store.is_new_receiver(cid2, 'BEN_0')}")

    print(f"\n  enrich() (ce qui serait passé aux agents) :")
    enr = store.enrich({"client_id": cid2, "tx_type": "SWIFT",
                        "beneficiary_name": "BEN_NEW"})
    print(f"     {json.dumps(enr['behavioral_profile'], ensure_ascii=False)}")

    store.close()
    print(f"\n{'═'*64}\n  ✅ Démonstration terminée (backend: {store.backend.name}).\n")
