r"""
api/scheduler.py — Planificateur de tâches périodiques
==============================================================================
Automatise les deux traitements récurrents de la plateforme :

    • le CYCLE DE RÉENTRAÎNEMENT       (hebdomadaire)
    • le RAPPORT HEBDOMADAIRE par email (hebdomadaire)
    • un CONTRÔLE DE DÉRIVE            (quotidien, déclencheur anticipé)

CHOIX TECHNIQUE — pourquoi pas APScheduler
    Un thread et une boucle de réveil suffisent pour une cadence hebdomadaire.
    Ajouter une dépendance imposerait de reconstruire l'image Docker (~30 min)
    et alourdirait le déploiement, pour une fonctionnalité qui tient en une
    centaine de lignes. Le projet suit la même ligne que ses graphiques ou son
    routeur : pas de dépendance pour ce qui est simple à écrire et à auditer.

ROBUSTESSE
    • l'état est PERSISTÉ : un redémarrage ne rejoue pas une tâche déjà faite
      dans la période, et ne la saute pas non plus ;
    • toute exception d'une tâche est capturée et journalisée — le
      planificateur ne peut jamais faire tomber l'API ;
    • thread démon : il n'empêche pas l'arrêt du serveur.
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

BASE = Path(__file__).resolve().parent.parent
STATE_FILE = BASE / "output" / "scheduler_state.json"

# Intervalle de réveil : une minute suffit pour une précision à la minute.
TICK_SECONDS = int(os.getenv("SCHEDULER_TICK", "60"))
ENABLED = os.getenv("SCHEDULER_ENABLED", "1") not in ("0", "false", "False")

# Jours de la semaine : lundi = 0 … dimanche = 6
MONDAY, FRIDAY, SUNDAY = 0, 4, 6


@dataclass
class Job:
    """Tâche périodique. `weekday=None` signifie quotidienne."""
    name: str
    func: Callable[[], dict]
    hour: int
    minute: int = 0
    weekday: Optional[int] = None
    label: str = ""

    def period_key(self, now: datetime) -> str:
        """Identifiant de la période courante — sert à ne pas rejouer."""
        if self.weekday is None:
            return now.strftime("%Y-%m-%d")
        year, week, _ = now.isocalendar()
        return f"{year}-W{week:02d}"

    def is_due(self, now: datetime, last_period: Optional[str]) -> bool:
        if self.period_key(now) == last_period:
            return False                      # déjà exécutée cette période
        if self.weekday is not None and now.weekday() != self.weekday:
            return False
        return (now.hour, now.minute) >= (self.hour, self.minute)


class Scheduler:
    def __init__(self):
        self._jobs: list[Job] = []
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._history: list[dict] = []

    # ── Enregistrement ────────────────────────────────────────────────
    def register(self, job: Job) -> None:
        self._jobs.append(job)

    # ── État persistant ───────────────────────────────────────────────
    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_state(self, state: dict) -> None:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except Exception as e:
            print(f"  [SCHED] état non persisté : {e}")

    # ── Exécution ─────────────────────────────────────────────────────
    def run_job(self, name: str, manual: bool = False) -> dict:
        """Exécute une tâche par son nom. Ne lève jamais."""
        job = next((j for j in self._jobs if j.name == name), None)
        if job is None:
            return {"job": name, "status": "INCONNUE"}

        started = datetime.now()
        entry = {"job": name, "started_at": started.isoformat(),
                 "manual": manual}
        try:
            result = job.func() or {}
            entry.update({"status": "OK", "result": result})
            print(f"  [SCHED] ✅ {name} exécutée"
                  f"{' (manuellement)' if manual else ''}")
        except Exception as e:
            entry.update({"status": "ERREUR", "error": str(e)})
            print(f"  [SCHED] ⚠️ {name} en échec : {e}")
            traceback.print_exc()

        entry["duration_s"] = round(
            (datetime.now() - started).total_seconds(), 2)

        with self._lock:
            self._history.insert(0, entry)
            del self._history[20:]           # ne garder que les 20 dernières

        if not manual:
            state = self._load_state()
            state[job.name] = job.period_key(started)
            state.setdefault("_runs", {})[job.name] = entry
            self._save_state(state)
        return entry

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                now = datetime.now()
                state = self._load_state()
                for job in self._jobs:
                    if job.is_due(now, state.get(job.name)):
                        self.run_job(job.name)
            except Exception as e:
                print(f"  [SCHED] boucle : {e}")
            self._stop.wait(TICK_SECONDS)

    def start(self) -> None:
        if not ENABLED:
            print("  [SCHED] désactivé (SCHEDULER_ENABLED=0)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="scheduler")
        self._thread.start()
        planning = " · ".join(
            f"{j.name}({'quotidien' if j.weekday is None else 'hebdo'} "
            f"{j.hour:02d}h{j.minute:02d})" for j in self._jobs)
        print(f"  [SCHED] ✅ planificateur démarré — {planning}")

    def stop(self) -> None:
        self._stop.set()

    # ── Introspection ─────────────────────────────────────────────────
    def status(self) -> dict:
        state = self._load_state()
        now = datetime.now()
        return {
            "enabled": ENABLED,
            "running": bool(self._thread and self._thread.is_alive()),
            "tick_seconds": TICK_SECONDS,
            "server_time": now.isoformat(),
            "jobs": [
                {
                    "name": j.name,
                    "label": j.label,
                    "cadence": "quotidienne" if j.weekday is None else "hebdomadaire",
                    "scheduled": f"{j.hour:02d}:{j.minute:02d}",
                    "weekday": j.weekday,
                    "last_period": state.get(j.name),
                    "already_run_this_period": state.get(j.name) == j.period_key(now),
                }
                for j in self._jobs
            ],
            "history": self._history[:10],
        }


SCHEDULER = Scheduler()
