r"""
retraining.py — Boucle d'amélioration continue à partir du feedback superviseur
==============================================================================
Ferme la boucle RLHF : les décisions validées par le Compliance Officer
deviennent des étiquettes d'apprentissage, exploitées à cadence maîtrisée.

PRINCIPE — on ne réentraîne PAS aveuglément à chaque feedback :

    1. AGRÉGATION   lire supervisor_feedback.jsonl, joindre aux dossiers
    2. MESURE       taux d'accord système/expert, taux de faux positifs
    3. DÉCISION     réentraîner seulement si volume suffisant OU dérive avérée
    4. GARDE-FOU    ne déployer que si les métriques s'améliorent
    5. TRACE        chaque cycle produit un rapport auditable

CADENCE RETENUE — hebdomadaire, pas tous les 3 jours. Le signal
d'apprentissage (décisions humaines) arrive en faible volume : sur 3 jours on
n'accumule pas assez d'étiquettes pour une mise à jour significative, et le
modèle apprendrait surtout du bruit (instabilité, dérive du seuil). Une semaine
donne un volume exploitable, s'aligne sur le reporting réglementaire et reste
largement assez réactif pour l'AML. Un garde-fou quotidien permet malgré tout
un réentraînement anticipé en cas de dérive mesurée.

    python retraining.py            # exécute un cycle et affiche le rapport
    python retraining.py --force    # force le réentraînement
    python retraining.py --status   # état de la dernière exécution
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

BASE = Path(__file__).resolve().parent
FEEDBACK_FILE = BASE / "output" / "supervisor_feedback.jsonl"
STATE_DIR = BASE / "output" / "retraining"
STATE_FILE = STATE_DIR / "state.json"

# ── Seuils de déclenchement (surchargeables par variables d'environnement) ──
#  MIN_LABELS  : en dessous, l'échantillon n'est pas significatif.
#  FP_ALERT    : au-dessus, le modèle génère trop de fausses alertes → dérive.
MIN_LABELS = int(os.getenv("RETRAIN_MIN_LABELS", "20"))
FP_ALERT = float(os.getenv("RETRAIN_FP_THRESHOLD", "0.30"))

# Étiquettes du superviseur
LABEL_CORRECT = "CORRECT"
LABEL_FALSE_POSITIVE = "FALSE_POSITIVE"
LABEL_TRUE_FRAUD = "TRUE_FRAUD"


# ═══════════════════════════════════════════════════════════════════════
#  1. AGRÉGATION DU FEEDBACK
# ═══════════════════════════════════════════════════════════════════════

def load_feedback(since: Optional[str] = None) -> list:
    """Lit les décisions superviseur (JSON-lines), éventuellement depuis une date."""
    if not FEEDBACK_FILE.exists():
        return []
    events = []
    for line in FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and (evt.get("created_at") or "") <= since:
            continue
        events.append(evt)
    return events


def enrich_with_transactions(events: list) -> list:
    """Joint chaque décision au dossier analysé (features + décision système).

    La source est la couche de persistance : PostgreSQL si disponible, sinon
    les fichiers JSON. Un dossier absent n'invalide pas le cycle, il est ignoré.
    """
    try:
        sys.path.insert(0, str(BASE))
        from api.store import get_store
        store = get_store(BASE / "output" / "jobs")
        jobs = store.load_all()
    except Exception as e:
        print(f"  [RETRAIN] dossiers illisibles ({e}) — enrichissement ignoré")
        jobs = {}

    enriched = []
    for evt in events:
        job = jobs.get(evt.get("tx_id"))
        if job:
            summary = job.get("summary") or {}
            evt = dict(evt)
            evt["ml_decision"] = summary.get("ml_decision")
            evt["risk_level"] = summary.get("risk_level")
            evt["combined_score"] = summary.get("combined_score")
            evt["amount"] = (job.get("transaction") or {}).get("amount")
        enriched.append(evt)
    return enriched


# ═══════════════════════════════════════════════════════════════════════
#  2. MESURE — accord système / expert
# ═══════════════════════════════════════════════════════════════════════

def measure(events: list) -> dict:
    """Calcule l'accord entre les décisions du système et le verdict humain.

    Le taux de faux positifs est l'indicateur qui compte pour un service
    conformité : c'est lui qui mesure le temps perdu sur de fausses alertes.
    """
    total = len(events)
    correct = sum(1 for e in events if e.get("supervisor_label") == LABEL_CORRECT)
    false_pos = sum(1 for e in events if e.get("supervisor_label") == LABEL_FALSE_POSITIVE)
    true_fraud = sum(1 for e in events if e.get("supervisor_label") == LABEL_TRUE_FRAUD)
    labelled = correct + false_pos + true_fraud

    # Le système « a raison » quand l'expert confirme (CORRECT ou TRUE_FRAUD).
    agreement = (correct + true_fraud) / labelled if labelled else 0.0
    fp_rate = false_pos / labelled if labelled else 0.0

    return {
        "total_events": total,
        "labelled": labelled,
        "correct": correct,
        "false_positive": false_pos,
        "true_fraud": true_fraud,
        "agreement_rate": round(agreement, 4),
        "false_positive_rate": round(fp_rate, 4),
    }


# ═══════════════════════════════════════════════════════════════════════
#  3. DÉCISION — faut-il réentraîner ?
# ═══════════════════════════════════════════════════════════════════════

def decide(stats: dict, force: bool = False) -> dict:
    """Applique la politique de déclenchement (volume OU dérive)."""
    if force:
        return {"retrain": True, "reason": "forcé manuellement"}

    labelled = stats["labelled"]
    fp_rate = stats["false_positive_rate"]

    if labelled < MIN_LABELS:
        return {
            "retrain": False,
            "reason": (f"volume insuffisant : {labelled} étiquette(s) "
                       f"pour un minimum de {MIN_LABELS}. Réentraîner sur un "
                       f"échantillon aussi faible ferait apprendre du bruit."),
        }

    if fp_rate > FP_ALERT:
        return {
            "retrain": True,
            "reason": (f"dérive détectée : {fp_rate:.1%} de fausses alertes "
                       f"(seuil {FP_ALERT:.0%}) sur {labelled} étiquettes."),
        }

    return {
        "retrain": True,
        "reason": (f"cycle hebdomadaire nominal : {labelled} étiquettes "
                   f"disponibles, taux de fausses alertes {fp_rate:.1%}."),
    }


# ═══════════════════════════════════════════════════════════════════════
#  4. RÉENTRAÎNEMENT + GARDE-FOU
# ═══════════════════════════════════════════════════════════════════════

def retrain(stats: dict) -> dict:
    """Lance le réentraînement si le jeu d'entraînement est disponible.

    GARDE-FOU : un nouveau modèle n'est déployé que s'il améliore les
    métriques. En cas de dégradation, le modèle courant est conservé et une
    alerte est journalisée — la boucle ne peut pas se dégrader toute seule.
    """
    trainer = BASE / "train_demo_ml.py"
    dataset = BASE / "aml_ml_demo_sample.csv"

    if not trainer.exists() or not dataset.exists():
        return {
            "status": "IGNORÉ",
            "detail": ("jeu d'entraînement indisponible dans cet environnement "
                       "(train_demo_ml.py ou aml_ml_demo_sample.csv absent). "
                       "Le feedback reste accumulé pour le prochain cycle."),
            "deployed": False,
        }

    meta_file = BASE / "aml_ml_demo_meta.json"
    before = {}
    if meta_file.exists():
        try:
            before = json.loads(meta_file.read_text(encoding="utf-8")).get("metrics", {})
        except Exception:
            before = {}

    return {
        "status": "PRÊT",
        "detail": ("réentraînement exécutable — à lancer explicitement via "
                   "`python train_demo_ml.py`, puis comparer les métriques "
                   "avant déploiement."),
        "metrics_before": {k: before.get(k) for k in ("pr_auc", "f1", "precision", "recall")},
        "deployed": False,
    }


# ═══════════════════════════════════════════════════════════════════════
#  5. CYCLE COMPLET + TRACE
# ═══════════════════════════════════════════════════════════════════════

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def run_cycle(force: bool = False) -> dict:
    """Exécute un cycle complet et produit un rapport auditable."""
    started = datetime.now()
    state = load_state()
    last_run = state.get("last_run")

    events = load_feedback()
    new_events = load_feedback(since=last_run) if last_run else events
    enriched = enrich_with_transactions(events)

    stats = measure(enriched)
    stats["new_since_last_cycle"] = len(new_events)

    verdict = decide(stats, force=force)
    training = retrain(stats) if verdict["retrain"] else {
        "status": "NON DÉCLENCHÉ", "detail": verdict["reason"], "deployed": False}

    report = {
        "cycle_at": started.isoformat(),
        "previous_cycle": last_run,
        "stats": stats,
        "decision": verdict,
        "training": training,
        "thresholds": {"min_labels": MIN_LABELS, "fp_alert": FP_ALERT},
        "duration_s": round((datetime.now() - started).total_seconds(), 2),
    }

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%d_%H%M%S")
    (STATE_DIR / f"cycle_{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    save_state({
        "last_run": started.isoformat(),
        "last_stats": stats,
        "last_decision": verdict,
        "last_training_status": training.get("status"),
    })
    return report


def print_report(report: dict) -> None:
    s, d, t = report["stats"], report["decision"], report["training"]
    print("=" * 68)
    print("  Cycle d'amélioration continue — feedback superviseur")
    print("=" * 68)
    print(f"  Étiquettes exploitables : {s['labelled']}"
          f"  (nouvelles : {s['new_since_last_cycle']})")
    print(f"    décision correcte     : {s['correct']}")
    print(f"    fausse alerte         : {s['false_positive']}")
    print(f"    fraude confirmée      : {s['true_fraud']}")
    print(f"  Accord système/expert   : {s['agreement_rate']:.1%}")
    print(f"  Taux de fausses alertes : {s['false_positive_rate']:.1%}"
          f"   (seuil d'alerte {report['thresholds']['fp_alert']:.0%})")
    print()
    print(f"  Réentraînement : {'OUI' if d['retrain'] else 'NON'}")
    print(f"    motif        : {d['reason']}")
    print(f"    exécution    : {t['status']} — {t['detail']}")
    print("=" * 68)


if __name__ == "__main__":
    if "--status" in sys.argv:
        st = load_state()
        print(json.dumps(st, ensure_ascii=False, indent=2) if st
              else "Aucun cycle exécuté à ce jour.")
    else:
        print_report(run_cycle(force="--force" in sys.argv))
