"""
train_demo_ml.py — LightGBM SEUL (sans calibration) + dump sample pour Swagger
==============================================================================
ARCHITECTURE FINALE retenue :
  • LightGBM unique (pas d'ensemble)
  • LSTM-Autoencoder comme feature reconstruction_error (#1 du modèle)
  • Pas de calibration : sur dataset déséquilibré 1:115, Platt scaling
    produit des probas saturées à 0.99 (testé et écarté)
  • Seuil saturé (~0.9999) : honnête, reflète la difficulté du problème AML
  • Justification jury : "Le ML ne décide jamais seul — il est combiné avec
    le Compliance Agent neuro-symbolique"

Sortie :
  aml_ml_demo_lgb.txt    — modèle LightGBM
  aml_ml_demo_meta.json  — features + seuil + métriques
  aml_ml_demo_sample.csv — 200 fraudes + 200 normales pour /sample dans Swagger
"""

# Ce script vit dans un sous-dossier : on expose la racine du projet aux imports.
import sys as _sys, pathlib as _pathlib
_RACINE = _pathlib.Path(__file__).resolve().parents[1]
if str(_RACINE) not in _sys.path:
    _sys.path.insert(0, str(_RACINE))

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (average_precision_score, f1_score,
                             precision_score, recall_score)

import ml_fraud_detector_v4 as ml

DATA = r"C:\Users\louhi\Downloads\projet_pfe\PFE_AML_KYC\IBM\HI-Small_Trans.csv"
ACCT = r"C:\Users\louhi\Downloads\projet_pfe\PFE_AML_KYC\IBM\HI-Small_accounts.csv"
PREFIX = "aml_ml_demo"
SAMPLE = 600_000

def main():
    print("\n### DEMO TRAINING — LightGBM SEUL + LSTM-AE (sans calibration) ###")

    # ── Phase 2 : chargement ───────────────────────────────────────────
    df = ml.load_data(DATA, ACCT, sample=SAMPLE)
    train_raw, test_raw = ml.temporal_split_raw(df, test_frac=0.20)

    # ── Phase 3 : feature engineering ──────────────────────────────────
    X_train, X_test, y_train, y_test, feature_cols = ml.engineer_features(
        train_raw, test_raw)

    # ── Branche B : LSTM-Autoencoder → reconstruction_error ────────────
    err_train, err_test = ml.build_lstm_autoencoder_features(
        train_raw, test_raw, window=8, epochs=20)
    if err_train is not None:
        X_train = X_train.copy(); X_test = X_test.copy()
        X_train["reconstruction_error"] = err_train
        X_test["reconstruction_error"] = err_test
        feature_cols = feature_cols + ["reconstruction_error"]
        print(f"  → reconstruction_error ajoutée (feature #{len(feature_cols)})")

    # ── Branche C : Isolation Forest → isolation_score ─────────────────
    #  Signal d'anomalie NON supervisé qui marche pour TOUS les clients
    #  (y compris les nouveaux, sans historique).
    iso_train, iso_test = ml.build_isolation_forest_features(
        X_train, y_train, X_test, y_test, prefix=PREFIX)
    X_train = X_train.copy(); X_test = X_test.copy()
    X_train["isolation_score"] = iso_train
    X_test["isolation_score"] = iso_test
    feature_cols = feature_cols + ["isolation_score"]
    print(f"  → isolation_score ajoutée (feature #{len(feature_cols)})")

    # ── Entraîner LightGBM seul ────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  ENTRAÎNEMENT — LightGBM (architecture finale)")
    print(f"{'='*70}")
    lgb_model = ml.train_lightgbm(X_train, y_train, X_test, y_test)

    # ── Évaluation ─────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  ÉVALUATION")
    print(f"{'='*70}")
    proba = lgb_model.predict_proba(X_test)[:, 1]
    pr_auc = average_precision_score(y_test, proba)
    print(f"  Distribution proba : min={proba.min():.4f} "
          f"max={proba.max():.4f} médiane={np.median(proba):.4f}")

    # Seuil optimal sur F2 (favorise le recall en AML).
    # NOTE : le seuil par coût (optimize_threshold_by_cost) a été testé et
    # ÉCARTÉ — avec fn=15%×montant sur des montants IBM en millions, l'optimum
    # économique (0.999) produit 70 003 FP = 58% du flux alerté, opérationnellement
    # intraitable. Documenté comme expérience négative dans le mémoire.
    thr = ml.optimize_threshold_fbeta(y_test, proba, beta=2.0)
    y_pred = (proba >= thr).astype(int)

    f1 = f1_score(y_test, y_pred, zero_division=0)
    prec = precision_score(y_test, y_pred, zero_division=0)
    rec = recall_score(y_test, y_pred, zero_division=0)
    tp = int(((y_pred == 1) & (y_test == 1)).sum())
    fp = int(((y_pred == 1) & (y_test == 0)).sum())
    fn = int(((y_pred == 0) & (y_test == 1)).sum())
    tn = int(((y_pred == 0) & (y_test == 0)).sum())

    print(f"\n  ─── LightGBM FINAL (seuil={thr:.4f}) ───")
    print(f"  PR-AUC     : {pr_auc:.4f}")
    print(f"  F1-score   : {f1:.4f}")
    print(f"  Précision  : {prec:.4f}")
    print(f"  Recall     : {rec:.4f}")
    print(f"  Matrice    : TP={tp:,} | FP={fp:,} | FN={fn:,} | TN={tn:,}")

    # ── Sauvegarde ─────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  SAUVEGARDE")
    print(f"{'='*70}")

    lgb_path = f"{PREFIX}_lgb.txt"
    meta_path = f"{PREFIX}_meta.json"

    lgb_model.booster_.save_model(lgb_path)
    print(f"  ├─ LightGBM : {lgb_path}")

    metrics = {
        "name": "LightGBM seul + LSTM-AE",
        "pr_auc": float(pr_auc),
        "f1": float(f1),
        "precision": float(prec),
        "recall": float(rec),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "threshold": float(thr),
    }
    meta = {
        "threshold": float(thr),
        "features": feature_cols,
        "n_features": len(feature_cols),
        "metrics": metrics,
        "models": {"lightgbm": lgb_path},
        "architecture": "LightGBM seul + LSTM-AE feature",
        "calibration": "Aucune (Platt testé et écarté — saturation sur "
                       "dataset 1:115)",
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"  └─ Meta     : {meta_path}")

    # ── DUMP échantillon de TEST pour Swagger ──────────────────────────
    test_df = X_test[feature_cols].copy()
    test_df["is_laundering"] = y_test.values
    fraud = test_df[test_df["is_laundering"] == 1].head(200)
    normal = test_df[test_df["is_laundering"] == 0].sample(
        n=min(200, int((test_df["is_laundering"] == 0).sum())),
        random_state=42)
    sample = pd.concat([fraud, normal]).reset_index(drop=True)
    sample.to_csv(f"{PREFIX}_sample.csv", index=False)
    print(f"\n  ✅ Échantillon test : {PREFIX}_sample.csv "
          f"({len(fraud)} fraudes + {len(normal)} normales)")

    # ── Récapitulatif ─────────────────────────────────────────────────
    print(f"\n{'#'*70}")
    print(f"#  RÉCAPITULATIF — LightGBM seul (architecture finale)")
    print(f"{'#'*70}")
    print(f"  PR-AUC               : {pr_auc:.4f}")
    print(f"  F1                   : {f1:.4f}")
    print(f"  Precision            : {prec:.4f}")
    print(f"  Recall               : {rec:.4f}")
    print(f"  Seuil                : {thr:.4f}")
    print(f"  Faux positifs        : {fp}")
    print(f"  Faux négatifs        : {fn}")
    print(f"{'#'*70}\n")
    print(f"  ✅ Artefacts prêts (prefix={PREFIX}). Redémarrer l'API.")

if __name__ == "__main__":
    main()