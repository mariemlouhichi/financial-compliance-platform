"""
llm_client.py — Client LLM résilient (cache + rotation clés/modèles)
====================================================================
PFE AML/KYC 2025-2026

PROBLÈME RÉSOLU :
  Le tier gratuit Groq impose une limite de tokens PAR JOUR, PAR MODÈLE,
  PAR COMPTE (org). Une évaluation complète (24 scénarios × planning +
  reflection + compliance) épuise les 100 000 tokens/jour d'un seul modèle.

DEUX MÉCANISMES COMPLÉMENTAIRES :

  1. CACHE FICHIER (déterministe, temp=0.0)
     La 1ʳᵉ fois qu'un prompt est envoyé, la réponse est stockée dans
     output/llm_cache.json (clé = SHA256 du prompt + température + max_tokens).
     Les fois suivantes → lecture du cache, ZÉRO token consommé.
     → Re-lancer une éval (ex. --stability 3) devient quasi gratuit.

  2. ROTATION CLÉS × MODÈLES
     Sur 429 (rate limit), bascule automatiquement :
       - d'abord vers un AUTRE MODÈLE de la même clé
         (chaque modèle a son propre budget journalier),
       - puis vers la CLÉ SUIVANTE (chaque compte = budget séparé).
     N clés × M modèles ≈ N×M budgets journaliers cumulés.

COMPATIBILITÉ :
  ResilientLLM expose la MÊME interface que le client Groq officiel :
      llm.chat.completions.create(model=, messages=, temperature=, max_tokens=)
  → c'est un remplacement direct (drop-in) dans compliance_agent / planning_agent.

SOURCES DES CLÉS (par ordre de priorité) :
  1. Variable d'env GROQ_API_KEYS (séparées par des virgules)
  2. Fichier groq_keys.json (clé "keys": [...], "models": [...])
  3. Variable d'env GROQ_API_KEY (clé unique — repli)

USAGE :
  from llm_client import get_llm
  llm = get_llm(verbose=True)
  resp = llm.chat.completions.create(
      model="llama-3.3-70b-versatile",
      messages=[{"role": "user", "content": "..."}],
      temperature=0.0, max_tokens=500)
  print(resp.choices[0].message.content)
  llm.print_stats()
"""

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from types import SimpleNamespace

logger = logging.getLogger("llm_client")

SCRIPT_DIR = Path(__file__).resolve().parent
KEYS_FILE  = SCRIPT_DIR / "groq_keys.json"
CACHE_FILE = SCRIPT_DIR / "output" / "llm_cache.json"

# Modèles Groq par défaut (du plus capable au plus économique).
# Les modèles indisponibles/décommissionnés sont détectés et ignorés au runtime.
DEFAULT_MODELS = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "moonshotai/kimi-k2-instruct",
    "qwen/qwen3-32b",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]


# ═══════════════════════════════════════════════════════════════════════
#  CHARGEMENT DE LA CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

def _load_config() -> tuple:
    """Retourne (keys: list[str], models: list[str]) selon l'ordre de priorité."""
    keys, models = [], list(DEFAULT_MODELS)

    # 1. Variable d'env multi-clés
    env_keys = os.environ.get("GROQ_API_KEYS", "")
    if env_keys.strip():
        keys = [k.strip() for k in env_keys.split(",") if k.strip()]

    # 2. Fichier JSON
    if not keys and KEYS_FILE.exists():
        try:
            data = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
            keys = [k for k in data.get("keys", []) if k and k.startswith("gsk_")]
            if data.get("models"):
                models = data["models"]
        except Exception as e:
            logger.warning(f"Lecture {KEYS_FILE.name} échouée : {e}")

    # 3. Clé unique (repli)
    if not keys:
        single = os.environ.get("GROQ_API_KEY", "")
        if single.strip():
            keys = [single.strip()]

    return keys, models


# ═══════════════════════════════════════════════════════════════════════
#  RÉPONSE COMPATIBLE GROQ
# ═══════════════════════════════════════════════════════════════════════

def _wrap(content: str):
    """Construit un objet réponse imitant l'API Groq (resp.choices[0].message.content)."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


# ═══════════════════════════════════════════════════════════════════════
#  CLIENT RÉSILIENT
# ═══════════════════════════════════════════════════════════════════════

class _Completions:
    def __init__(self, parent): self._p = parent
    def create(self, model=None, messages=None, temperature=0.0,
               max_tokens=1024, **_):
        return self._p._create(messages or [], temperature, max_tokens,
                               preferred_model=model)


class _Chat:
    def __init__(self, parent): self.completions = _Completions(parent)


class ResilientLLM:
    """
    Client LLM avec cache fichier + rotation clés × modèles.
    Interface drop-in : .chat.completions.create(...).
    """

    def __init__(self, keys: list, models: list = None,
                 cache_path: Path = CACHE_FILE, no_cache: bool = False,
                 verbose: bool = False):
        self.keys     = keys
        self.models   = models or list(DEFAULT_MODELS)
        self.verbose  = verbose
        self.no_cache = no_cache or os.environ.get("LLM_NO_CACHE", "") == "1"
        self.cache_path = Path(cache_path)
        self.chat = _Chat(self)

        # État interne
        self._clients      = {}          # key_idx -> client Groq
        self._dead_models  = set()       # modèles invalides (décommissionnés)
        self._cooldown     = set()       # (key_idx, model) épuisés cette session
        self._cursor_key   = 0           # reprend depuis la dernière clé qui a marché
        self._lock         = threading.Lock()

        # Stats
        self._hits = self._calls = self._fails = 0
        self._model_usage = {}

        # Cache
        self.cache = {}
        if not self.no_cache:
            self._load_cache()

        if self.verbose:
            print(f"  ✅ ResilientLLM : {len(self.keys)} clés × {len(self.models)} modèles "
                  f"| cache={'OFF' if self.no_cache else len(self.cache)} entrées")

    # ── Cache ───────────────────────────────────────────────────────────
    def _load_cache(self):
        try:
            if self.cache_path.exists():
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Cache illisible ({e}) — repart à vide")
            self.cache = {}

    def _save_cache(self):
        if self.no_cache:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self.cache, ensure_ascii=False, indent=0),
                encoding="utf-8")
        except Exception as e:
            logger.debug(f"Écriture cache échouée : {e}")

    @staticmethod
    def _clean_reasoning(text: str) -> str:
        """Retire les blocs de raisonnement des modèles 'thinking' (qwen3, gpt-oss).
        Ces modèles encadrent leur réflexion dans <think>...</think> avant la
        réponse → casserait le parsing JSON des agents. On ne garde que la réponse."""
        if not text:
            return text
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL | re.IGNORECASE)
        return text.strip()

    @staticmethod
    def _cache_key(messages, temperature, max_tokens) -> str:
        # Clé indépendante du modèle (temp=0 → réponse déterministe réutilisable
        # quel que soit le modèle qui a répondu).
        blob = json.dumps({"m": messages, "t": temperature, "k": max_tokens},
                          sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # ── Client Groq par clé (lazy) ──────────────────────────────────────
    def _client(self, key_idx: int):
        if key_idx in self._clients:
            return self._clients[key_idx]
        try:
            from groq import Groq
            c = Groq(api_key=self.keys[key_idx])
            self._clients[key_idx] = c
            return c
        except Exception as e:
            logger.warning(f"Init client Groq clé #{key_idx+1} échouée : {e}")
            self._clients[key_idx] = None
            return None

    # ── Cœur : cache → rotation ─────────────────────────────────────────
    def _create(self, messages, temperature, max_tokens, preferred_model=None):
        ck = self._cache_key(messages, temperature, max_tokens)

        # 1. Cache hit
        if not self.no_cache and ck in self.cache:
            self._hits += 1
            if self.verbose:
                print(f"     💾 cache hit")
            return _wrap(self.cache[ck])

        # 2. Ordre des modèles (préféré en tête s'il est valide)
        models = list(self.models)
        if preferred_model and preferred_model in models:
            models.remove(preferred_model)
            models.insert(0, preferred_model)

        last_exc = None
        n_keys = len(self.keys)

        # 3. Rotation : clé (externe) × modèle (interne), depuis le curseur
        for ko in range(n_keys):
            key_idx = (self._cursor_key + ko) % n_keys
            client = self._client(key_idx)
            if client is None:
                continue
            for model in models:
                if model in self._dead_models:
                    continue
                if (key_idx, model) in self._cooldown:
                    continue
                try:
                    resp = client.chat.completions.create(
                        model=model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens)
                    content = self._clean_reasoning(resp.choices[0].message.content)
                    # Succès → mémorise la clé, cache, stats
                    with self._lock:
                        self._cursor_key = key_idx
                        self._calls += 1
                        self._model_usage[model] = self._model_usage.get(model, 0) + 1
                        if not self.no_cache:
                            self.cache[ck] = content
                            self._save_cache()
                    if self.verbose:
                        print(f"     🤖 clé#{key_idx+1} · {model}")
                    return _wrap(content)
                except Exception as e:
                    last_exc = e
                    msg = str(e).lower()
                    if any(s in msg for s in
                           ["decommission", "not found", "does not exist",
                            "invalid model", "model_not_found", "no such model"]):
                        self._dead_models.add(model)
                        if self.verbose:
                            print(f"     ⛔ modèle indisponible : {model}")
                    elif "429" in str(e) or "rate" in msg or "quota" in msg \
                            or "limit" in msg:
                        self._cooldown.add((key_idx, model))
                        if self.verbose:
                            print(f"     ⏳ quota épuisé : clé#{key_idx+1} · {model}")
                    else:
                        if self.verbose:
                            print(f"     ⚠️  erreur clé#{key_idx+1} · {model} : {str(e)[:70]}")

        self._fails += 1
        raise RuntimeError(
            f"Tous les ({n_keys} clés × {len(models)} modèles) épuisés ou en échec. "
            f"Dernière erreur : {last_exc}")

    # ── Observabilité ───────────────────────────────────────────────────
    def stats(self) -> dict:
        return {
            "cache_hits":    self._hits,
            "api_calls":     self._calls,
            "failures":      self._fails,
            "model_usage":   dict(self._model_usage),
            "dead_models":   sorted(self._dead_models),
            "exhausted_combos": len(self._cooldown),
            "cache_size":    len(self.cache),
        }

    def print_stats(self):
        s = self.stats()
        print(f"\n  ── LLM stats ──")
        print(f"  Cache hits : {s['cache_hits']} | Appels API : {s['api_calls']} "
              f"| Échecs : {s['failures']}")
        if s["model_usage"]:
            print(f"  Modèles utilisés : {s['model_usage']}")
        if s["dead_models"]:
            print(f"  Modèles indisponibles : {s['dead_models']}")
        print(f"  Combos épuisés (429) : {s['exhausted_combos']} | Cache : {s['cache_size']} entrées")


# ═══════════════════════════════════════════════════════════════════════
#  FABRIQUE
# ═══════════════════════════════════════════════════════════════════════

_SINGLETON = None

def get_llm(verbose: bool = False, no_cache: bool = False, shared: bool = True):
    """
    Retourne un ResilientLLM prêt à l'emploi, ou None si aucune clé trouvée.

    shared=True : réutilise une instance unique (partage cache + cooldowns
                  entre tous les agents d'un même run → rotation optimale).
    """
    global _SINGLETON
    if shared and _SINGLETON is not None:
        return _SINGLETON
    keys, models = _load_config()
    if not keys:
        logger.warning("Aucune clé Groq trouvée (GROQ_API_KEYS / groq_keys.json / GROQ_API_KEY)")
        return None
    llm = ResilientLLM(keys, models, no_cache=no_cache, verbose=verbose)
    if shared:
        _SINGLETON = llm
    return llm


# ═══════════════════════════════════════════════════════════════════════
#  TEST STANDALONE
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

    print("=" * 60)
    print("  TEST — ResilientLLM (cache + rotation)")
    print("=" * 60)

    keys, models = _load_config()
    print(f"  Clés détectées   : {len(keys)}")
    print(f"  Modèles          : {len(models)}")
    if not keys:
        print("  ❌ Aucune clé — ajoute groq_keys.json ou GROQ_API_KEYS")
        sys.exit(1)

    llm = get_llm(verbose=True)
    msg = [{"role": "user", "content": "Reply with exactly one word: COMPLIANT"}]

    print("\n  1er appel (devrait toucher l'API) :")
    r1 = llm.chat.completions.create(model="llama-3.3-70b-versatile",
                                     messages=msg, temperature=0.0, max_tokens=10)
    print(f"     → {r1.choices[0].message.content.strip()}")

    print("\n  2e appel identique (devrait être un CACHE HIT, 0 token) :")
    r2 = llm.chat.completions.create(model="llama-3.3-70b-versatile",
                                     messages=msg, temperature=0.0, max_tokens=10)
    print(f"     → {r2.choices[0].message.content.strip()}")

    llm.print_stats()
    print("\n  ✅ Test terminé")
