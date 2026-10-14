# -*- coding: utf-8 -*-
"""
rlhf.py — Réentraînement INCRÉMENTAL du modèle ML à partir du feedback superviseur.
==================================================================================
Ferme vraiment la boucle RLHF, sans le piège de l'« oubli catastrophique » :

  1. feedback → dataset   les transactions étiquetées par le Compliance Officer
                          deviennent des exemples (features réelles + label).
  2. warm-start           on REPART du modèle existant (LightGBM init_model) et on
                          ajoute quelques itérations de boosting sur
                          feedback + replay buffer (échantillon du dataset de base).
                          → rapide (secondes), garde le savoir des 600k lignes IBM.
  3. GARDE-FOU            on évalue le nouveau modèle sur un jeu de validation ;
                          on ne DÉPLOIE que si F1 ET PR-AUC ne régressent pas —
                          sinon ROLLBACK (l'ancien modèle est conservé).
  4. TRACE               chaque cycle écrit un rapport auditable (ancien/nouveau).

Le réentraînement COMPLET (train_demo_ml.py, 600k + LSTM) reste le chemin lourd
offline ; ici on fait l'apprentissage continu léger et sûr.
"""
from __future__ import annotations
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
PREFIX = "aml_ml_demo"
META_FILE   = BASE / f"{PREFIX}_meta.json"
MODEL_FILE  = BASE / f"{PREFIX}_lgb.txt"
SAMPLE_FILE = BASE / f"{PREFIX}_sample.csv"
STATE_DIR   = BASE / "output" / "retraining"
FEEDBACK_DS = STATE_DIR / "feedback_dataset.csv"

MIN_LABELS  = int(os.getenv("RETRAIN_MIN_LABELS", "20"))
WARM_ROUNDS = int(os.getenv("RLHF_WARM_ROUNDS", "60"))
PR_AUC_TOLERANCE = 0.005   # marge de non-régression sur la PR-AUC

LABEL_CORRECT = "CORRECT"
LABEL_FALSE_POSITIVE = "FALSE_POSITIVE"
LABEL_TRUE_FRAUD = "TRUE_FRAUD"


# ═══════════════════════════════════════════════════════════════════════
#  1. FEEDBACK → DATASET ÉTIQUETÉ
# ═══════════════════════════════════════════════════════════════════════

def _label_for(supervisor_label: str, summary: dict):
    """Traduit le verdict humain en étiquette binaire (fraude=1 / normal=0)."""
    if supervisor_label == LABEL_TRUE_FRAUD:
        return 1
    if supervisor_label == LABEL_FALSE_POSITIVE:
        return 0
    if supervisor_label == LABEL_CORRECT:
        # « décision correcte » = l'humain confirme le système → on reprend
        # le sens de la décision système (suspect → 1, sinon → 0).
        flagged = (summary.get("ca_decision") in ("STR", "BLOCK")) or \
                  ("FRAUD" in str(summary.get("ml_decision", "")).upper())
        return 1 if flagged else 0
    return None   # ANNOTATE seul / label absent → non exploitable


def build_feedback_dataset() -> dict:
    """Joint le feedback superviseur aux features ML stockées → CSV étiqueté."""
    import pandas as pd
    meta = json.loads(META_FILE.read_text(encoding="utf-8"))
    feats = meta["features"]

    from retraining import load_feedback
    events = load_feedback()

    try:
        from api.store import get_store
        jobs = get_store(BASE / "output" / "jobs").load_all()
    except Exception as e:
        jobs = {}
        print(f"  [RLHF] dossiers illisibles ({e})")

    rows, missing_features = [], 0
    for evt in events:
        job = jobs.get(evt.get("tx_id")) or {}
        y = _label_for(evt.get("supervisor_label", ""), job.get("summary") or {})
        if y is None:
            continue
        fv = job.get("ml_features") or {}
        if not fv:
            missing_features += 1     # transaction antérieure à la persistance
            continue
        row = {f: float(fv.get(f, 0.0) or 0.0) for f in feats}
        row["is_laundering"] = int(y)
        rows.append(row)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if rows:
        pd.DataFrame(rows).to_csv(FEEDBACK_DS, index=False)

    pos = sum(r["is_laundering"] for r in rows)
    return {
        "events": len(events),
        "usable": len(rows),
        "positives": pos,
        "negatives": len(rows) - pos,
        "missing_features": missing_features,
        "path": str(FEEDBACK_DS),
    }


# ═══════════════════════════════════════════════════════════════════════
#  2+3. RÉENTRAÎNEMENT INCRÉMENTAL (warm-start) + GARDE-FOU
# ═══════════════════════════════════════════════════════════════════════

def _lgb_params(y_train):
    """Params alignés sur train_demo_ml.py (train_lightgbm)."""
    import numpy as np
    pos = int((np.asarray(y_train) == 1).sum())
    neg = int((np.asarray(y_train) == 0).sum())
    spw = (neg / pos) if pos else 1.0
    return {
        "objective": "binary", "metric": "average_precision",
        "scale_pos_weight": spw, "max_depth": 8, "num_leaves": 127,
        "min_child_samples": 20, "subsample": 0.8, "colsample_bytree": 0.8,
        "learning_rate": 0.02, "reg_alpha": 0.05, "reg_lambda": 1.0,
        "seed": 42, "n_jobs": -1, "verbosity": -1,
    }


def _evaluate(booster, X_val, y_val) -> dict:
    from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score
    import ml_fraud_detector_v4 as ml
    p = booster.predict(X_val)
    thr = float(ml.optimize_threshold_fbeta(y_val.values, p, beta=2.0))
    yhat = (p >= thr).astype(int)
    return {
        "pr_auc": round(float(average_precision_score(y_val, p)), 4),
        "f1": round(float(f1_score(y_val, yhat, zero_division=0)), 4),
        "precision": round(float(precision_score(y_val, yhat, zero_division=0)), 4),
        "recall": round(float(recall_score(y_val, yhat, zero_division=0)), 4),
        "threshold": round(thr, 4),
    }


def incremental_retrain(force: bool = False) -> dict:
    """Warm-start LightGBM sur feedback + replay, avec garde-fou de non-régression."""
    import numpy as np
    import pandas as pd
    import lightgbm as lgb
    from sklearn.model_selection import train_test_split

    started = datetime.now()
    meta = json.loads(META_FILE.read_text(encoding="utf-8"))
    feats = meta["features"]

    ds = build_feedback_dataset()
    n_fb = ds["usable"]

    # ── GARDE-FOU 1 : volume minimum ────────────────────────────────────
    if n_fb < MIN_LABELS and not force:
        return {
            "deployed": False, "status": "VOLUME_INSUFFISANT",
            "reason": (f"{n_fb} feedback(s) exploitable(s) < {MIN_LABELS} requis "
                       f"— on accumule (apprendre sur si peu = bruit)."),
            "feedback": ds,
        }

    # ── Jeu = replay buffer (base) + feedback ───────────────────────────
    base = pd.read_csv(SAMPLE_FILE)
    fb = pd.read_csv(FEEDBACK_DS) if FEEDBACK_DS.exists() and n_fb else \
        pd.DataFrame(columns=feats + ["is_laundering"])
    for c in feats:
        if c not in base.columns: base[c] = 0.0
        if c not in fb.columns:   fb[c] = 0.0
    combined = pd.concat([base[feats + ["is_laundering"]],
                          fb[feats + ["is_laundering"]]], ignore_index=True)
    X = combined[feats].astype(float)
    y = combined["is_laundering"].astype(int)

    strat = y if y.nunique() > 1 else None
    X_tr, X_val, y_tr, y_val = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=strat)

    # ── Modèle actuel + warm-start (init_model) ─────────────────────────
    current = lgb.Booster(model_file=str(MODEL_FILE))
    dtrain = lgb.Dataset(X_tr, label=y_tr, free_raw_data=False)
    candidate = lgb.train(_lgb_params(y_tr), dtrain,
                          num_boost_round=WARM_ROUNDS, init_model=current)

    m_cur = _evaluate(current, X_val, y_val)
    m_new = _evaluate(candidate, X_val, y_val)

    # ── GARDE-FOU 2 : ne déployer que si pas de régression ──────────────
    improved = (m_new["f1"] >= m_cur["f1"]) and \
               (m_new["pr_auc"] >= m_cur["pr_auc"] - PR_AUC_TOLERANCE)

    deployed = False
    if improved:
        # backup + swap modèle + mise à jour meta (seuil recalibré)
        shutil.copy2(MODEL_FILE, MODEL_FILE.with_suffix(".txt.bak"))
        candidate.save_model(str(MODEL_FILE))
        meta["threshold"] = m_new["threshold"]
        meta.setdefault("metrics", {}).update({
            "pr_auc": m_new["pr_auc"], "f1": m_new["f1"],
            "precision": m_new["precision"], "recall": m_new["recall"],
            "threshold": m_new["threshold"],
        })
        meta["last_rlhf"] = {
            "at": started.isoformat(), "warm_rounds": WARM_ROUNDS,
            "feedback_used": n_fb,
        }
        META_FILE.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        deployed = True

    report = {
        "deployed": deployed,
        "status": "DÉPLOYÉ" if deployed else "ROLLBACK",
        "reason": ("le modèle incrémental améliore (ou égale) les métriques "
                   "→ déployé, seuil recalibré."
                   if deployed else
                   "le modèle incrémental régresse → conservé l'actuel (garde-fou)."),
        "metrics_before": m_cur, "metrics_after": m_new,
        "feedback": ds,
        "warm_rounds": WARM_ROUNDS,
        "replay_rows": int(len(base)),
        "val_rows": int(len(y_val)),
        "duration_s": round((datetime.now() - started).total_seconds(), 2),
        "note": ("Redémarrer l'API (ou recharger le modèle) pour servir la "
                 "nouvelle version." if deployed else ""),
    }
    return report


if __name__ == "__main__":
    import sys
    rep = incremental_retrain(force="--force" in sys.argv)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
