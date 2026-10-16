"""
measure_baseline_v1.py — Mesure la référence v1 (régression logistique)
=======================================================================
Réentraîne la baseline sur EXACTEMENT le même split temporel et les mêmes
variables que le détecteur final, afin d'obtenir des chiffres comparables
à ceux du tableau 5.6 du rapport.

Ne touche à aucun modèle existant : le script se contente d'afficher
PR-AUC et F1. Rien n'est écrit sur disque.

Lancer :
    python measure_baseline_v1.py                    # jeu complet (~3-4 min)
    python measure_baseline_v1.py --sample 600000    # plus léger en mémoire
"""

# Ce script vit dans un sous-dossier : on expose la racine du projet aux imports.
import sys as _sys, pathlib as _pathlib
_RACINE = _pathlib.Path(__file__).resolve().parents[1]
if str(_RACINE) not in _sys.path:
    _sys.path.insert(0, str(_RACINE))

import argparse
import time

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score
from sklearn.preprocessing import StandardScaler

from ml_fraud_detector_v4 import load_data, temporal_split_raw, engineer_features

DATA = r"C:\Users\louhi\Downloads\projet_pfe\PFE_AML_KYC\IBM\HI-Small_Trans.csv"
ACCT = r"C:\Users\louhi\Downloads\projet_pfe\PFE_AML_KYC\IBM\HI-Small_accounts.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--accounts", default=ACCT)
    ap.add_argument("--sample", type=int, default=None)
    args = ap.parse_args()

    t0 = time.time()

    # ── 1. Mêmes données, même split que le détecteur final ───────────
    df = load_data(args.data, args.accounts, sample=args.sample)
    train_raw, test_raw = temporal_split_raw(df, test_frac=0.20)
    X_train, X_test, y_train, y_test, feature_cols = engineer_features(train_raw, test_raw)

    print(f"\n{'='*70}")
    print("  RÉFÉRENCE v1 — RÉGRESSION LOGISTIQUE")
    print(f"{'='*70}")
    print(f"  Variables      : {len(feature_cols)}")
    print(f"  Train          : {len(y_train):,} lignes / {int(y_train.sum()):,} fraudes")
    print(f"  Test           : {len(y_test):,} lignes / {int(y_test.sum()):,} fraudes")

    # ── 2. Standardisation : indispensable pour une régression logistique
    #     (contrairement aux arbres, elle est sensible à l'échelle).
    #     fit sur le train uniquement → pas de fuite.
    scaler = StandardScaler()
    Xtr = scaler.fit_transform(X_train.values.astype(np.float64))
    Xte = scaler.transform(X_test.values.astype(np.float64))

    # ── 3. Entraînement ───────────────────────────────────────────────
    print("\n  Entraînement de la régression logistique...")
    clf = LogisticRegression(
        class_weight="balanced",   # compense le déséquilibre, comme scale_pos_weight
        max_iter=1000,
        n_jobs=-1,
        solver="lbfgs",
    )
    clf.fit(Xtr, y_train)
    proba = clf.predict_proba(Xte)[:, 1]

    # ── 4. PR-AUC (indépendante du seuil) ─────────────────────────────
    pr_auc = average_precision_score(y_test, proba)

    # ── 5. F1 au meilleur seuil — même méthode que le détecteur final ─
    best = {"f1": -1.0}
    for thr in np.unique(np.quantile(proba, np.linspace(0.90, 0.9999, 400))):
        pred = (proba >= thr).astype(int)
        f1 = f1_score(y_test, pred, zero_division=0)
        if f1 > best["f1"]:
            best = {"f1": f1, "thr": float(thr),
                    "precision": precision_score(y_test, pred, zero_division=0),
                    "recall": recall_score(y_test, pred, zero_division=0)}

    base = float(y_test.mean())   # PR-AUC d'un classifieur aléatoire

    print(f"\n{'='*70}")
    print("  RÉSULTATS — à reporter dans le tableau 5.6")
    print(f"{'='*70}")
    print(f"  PR-AUC            : {pr_auc:.4f}      (aléatoire = {base:.4f})")
    print(f"  F1 (meilleur)     : {best['f1']:.4f}")
    print(f"  Précision         : {best['precision']:.4f}")
    print(f"  Rappel            : {best['recall']:.4f}")
    print(f"  Seuil retenu      : {best['thr']:.6f}")
    print(f"\n  Durée totale      : {time.time()-t0:.0f} s")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
