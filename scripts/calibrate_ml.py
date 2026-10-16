"""
calibrate_ml.py — Calibration Isotonic du modèle LightGBM
============================================================
Corrige la sur-confiance du modèle (probas saturées à 0.99+).

STRATÉGIE :
  1. Charge le modèle LightGBM entraîné (aml_ml_demo_lgb.txt)
  2. Charge un ÉCHANTILLON ÉQUILIBRÉ (aml_ml_demo_sample.csv)
  3. Split calibration/validation (80/20)
  4. Fit un IsotonicRegression sur les probas → probas calibrées
  5. Sauvegarde le calibrateur (aml_ml_demo_calibrator.pkl)

APRÈS CALIBRATION :
  - Proba 0.9994 (transaction normale) → ~0.30 (interprétable)
  - Proba 0.9999 (vraie fraude)        → ~0.85 (fiable)
  - Seuil devient interprétable (0.5)

RÉFÉRENCES :
  - Platt (1999) "Probabilistic Outputs for SVMs and Comparisons to Regularized
    Likelihood Methods"
  - Zadrozny & Elkan (2002) "Transforming classifier scores into accurate
    multiclass probability estimates"
  - Niculescu-Mizil & Caruana (2005) "Predicting Good Probabilities With
    Supervised Learning"
"""
import sys
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (brier_score_loss, log_loss, average_precision_score,
                              precision_score, recall_score, f1_score)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PREFIX = "aml_ml_demo"
BASE = Path(".")


def main():
    print("=" * 70)
    print("  CALIBRATION ISOTONIC DU MODÈLE LIGHTGBM")
    print("=" * 70)

    # ── 1. Charge modèle + meta + sample ──────────────────────────────
    print(f"\n  [1/5] Chargement des artefacts...")
    meta = json.loads((BASE / f"{PREFIX}_meta.json").read_text(encoding="utf-8"))
    features = meta["features"]
    old_threshold = meta["threshold"]
    model = lgb.Booster(model_file=str(BASE / f"{PREFIX}_lgb.txt"))
    sample = pd.read_csv(BASE / f"{PREFIX}_sample.csv")

    if "is_laundering" not in sample.columns:
        print(f"  ❌ ERREUR : is_laundering absente dans {PREFIX}_sample.csv")
        return

    n_fraud = int(sample["is_laundering"].sum())
    n_normal = len(sample) - n_fraud
    print(f"        ✓ Échantillon chargé : {len(sample)} tx "
          f"({n_fraud} fraudes + {n_normal} normales)")

    # ── 2. Prédictions sur l'échantillon ──────────────────────────────
    print(f"\n  [2/5] Prédictions sur l'échantillon...")
    X = sample[features].values.astype(np.float32)
    y = sample["is_laundering"].values.astype(int)
    probas_raw = model.predict(X)
    print(f"        ✓ Probas brutes calculées")
    print(f"        min/max : {probas_raw.min():.6f} / {probas_raw.max():.6f}")
    print(f"        moyenne normales : {probas_raw[y==0].mean():.6f}")
    print(f"        moyenne fraudes  : {probas_raw[y==1].mean():.6f}")

    # ── 3. Split calibration/validation ───────────────────────────────
    print(f"\n  [3/5] Split calibration (80%) / validation (20%)...")
    from sklearn.model_selection import train_test_split
    p_calib, p_valid, y_calib, y_valid = train_test_split(
        probas_raw, y, test_size=0.2, stratify=y, random_state=42)
    print(f"        Calibration : {len(p_calib)} tx")
    print(f"        Validation  : {len(p_valid)} tx")

    # ── 4. Fit isotonic + Platt (comparaison) ─────────────────────────
    print(f"\n  [4/5] Ajustement des calibrateurs...")

    # A. Isotonic regression
    iso_calib = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso_calib.fit(p_calib, y_calib)
    p_iso_valid = iso_calib.predict(p_valid)
    p_iso_valid = np.clip(p_iso_valid, 1e-6, 1 - 1e-6)

    # B. Platt scaling (comparaison)
    platt_calib = LogisticRegression()
    platt_calib.fit(p_calib.reshape(-1, 1), y_calib)
    p_platt_valid = platt_calib.predict_proba(p_valid.reshape(-1, 1))[:, 1]

    # ── 5. Comparaison des métriques ──────────────────────────────────
    print(f"\n  [5/5] Comparaison des calibrateurs...")
    print(f"\n  {'─'*68}")
    print(f"  {'Métrique':<30} {'Brut':>10} {'Isotonic':>10} {'Platt':>10}")
    print(f"  {'─'*68}")

    # Brier score (plus bas = mieux calibré)
    brier_raw = brier_score_loss(y_valid, p_valid)
    brier_iso = brier_score_loss(y_valid, p_iso_valid)
    brier_platt = brier_score_loss(y_valid, p_platt_valid)
    print(f"  {'Brier score (↓)':<30} {brier_raw:>10.4f} {brier_iso:>10.4f} {brier_platt:>10.4f}")

    # Log loss
    ll_raw = log_loss(y_valid, np.clip(p_valid, 1e-6, 1-1e-6))
    ll_iso = log_loss(y_valid, p_iso_valid)
    ll_platt = log_loss(y_valid, p_platt_valid)
    print(f"  {'Log loss (↓)':<30} {ll_raw:>10.4f} {ll_iso:>10.4f} {ll_platt:>10.4f}")

    # PR-AUC (ne devrait pas changer — calibration ne change pas le ranking)
    pr_raw = average_precision_score(y_valid, p_valid)
    pr_iso = average_precision_score(y_valid, p_iso_valid)
    pr_platt = average_precision_score(y_valid, p_platt_valid)
    print(f"  {'PR-AUC (=)':<30} {pr_raw:>10.4f} {pr_iso:>10.4f} {pr_platt:>10.4f}")

    # Distribution des probas après calibration
    print(f"\n  {'─'*68}")
    print(f"  DISTRIBUTIONS APRÈS CALIBRATION (validation)")
    print(f"  {'─'*68}")
    print(f"  {'Groupe':<20} {'Min':>10} {'Moyenne':>10} {'Max':>10}")
    for name, p in [("Brut", p_valid),
                     ("Isotonic", p_iso_valid),
                     ("Platt", p_platt_valid)]:
        p_norm = p[y_valid == 0]
        p_fraud = p[y_valid == 1]
        print(f"  {name+' - Normales':<20} {p_norm.min():>10.4f} "
              f"{p_norm.mean():>10.4f} {p_norm.max():>10.4f}")
        print(f"  {name+' - Fraudes':<20} {p_fraud.min():>10.4f} "
              f"{p_fraud.mean():>10.4f} {p_fraud.max():>10.4f}")

    # ── Choix du meilleur calibrateur ─────────────────────────────────
    if brier_iso <= brier_platt:
        winner = "isotonic"
        calibrator = iso_calib
        p_final = p_iso_valid
    else:
        winner = "platt"
        calibrator = platt_calib
        p_final = p_platt_valid

    print(f"\n  🏆 Calibrateur retenu : {winner.upper()} "
          f"(meilleur Brier score)")

    # ── Nouveau seuil interprétable ───────────────────────────────────
    print(f"\n  Recherche du nouveau seuil optimal (F1)...")
    best_thr, best_f1 = 0.5, 0.0
    for thr in np.linspace(0.05, 0.95, 100):
        y_pred = (p_final >= thr).astype(int)
        f1 = f1_score(y_valid, y_pred, zero_division=0)
        if f1 > best_f1:
            best_f1 = f1
            best_thr = float(thr)

    print(f"  Seuil calibré  : {best_thr:.4f}  (au lieu de {old_threshold:.4f})")
    print(f"  F1 validation  : {best_f1:.4f}")

    # ── Sauvegarde ────────────────────────────────────────────────────
    calibrator_path = BASE / f"{PREFIX}_calibrator.pkl"
    with open(calibrator_path, "wb") as f:
        pickle.dump({
            "method": winner,
            "calibrator": calibrator,
            "old_threshold": old_threshold,
            "new_threshold": best_thr,
            "metrics": {
                "brier_before": float(brier_raw),
                "brier_after": float(brier_iso if winner == "isotonic" else brier_platt),
                "pr_auc_after": float(pr_iso if winner == "isotonic" else pr_platt),
                "f1_calibrated": float(best_f1),
            },
        }, f)

    print(f"\n  ✅ Calibrateur sauvegardé : {calibrator_path}")

    # Met à jour le meta.json avec le nouveau seuil
    meta["threshold_calibrated"] = best_thr
    meta["calibration_method"] = winner
    with open(BASE / f"{PREFIX}_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"  ✅ Meta mise à jour : threshold_calibrated={best_thr:.4f}")
    print(f"\n  ℹ️  Le pipeline continue d'utiliser le seuil ORIGINAL par défaut.")
    print(f"     Pour activer la calibration, modifie MLPredictor (Étape C.2).")


if __name__ == "__main__":
    main()
