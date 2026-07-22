"""
ml_api.py — Service FastAPI / Swagger (LightGBM SEUL — architecture finale)
============================================================================
Sert LightGBM seul (non calibré) via 5 endpoints :
  • /health          : statut + features + seuil
  • /model/info      : métadonnées + métriques
  • /predict         : prédiction depuis un vecteur de features
  • /predict/raw     : prédiction depuis une transaction brute
  • /sample          : prédiction sur de VRAIES transactions du jeu de test
  • /predict/explain : décomposition SHAP de la décision

LANCEMENT :
  python -m uvicorn ml_api:app --reload --port 8000
  Ouvrir : http://localhost:8000/docs
"""
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import lightgbm as lgb
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

# ── Chargement des artefacts ────────────────────────────────────────────
BASE = Path(__file__).parent

def _pick_prefix() -> str:
    for p in ("aml_ml_demo", "aml_ml"):
        if (BASE / f"{p}_meta.json").exists() and \
           (BASE / f"{p}_lgb.txt").exists():
            return p
    return "aml_ml_demo"

PREFIX = _pick_prefix()
_META = json.loads((BASE / f"{PREFIX}_meta.json").read_text(encoding="utf-8"))
FEATURES: list = _META["features"]
THRESHOLD: float = float(_META["threshold"])
_METRICS: dict = _META.get("metrics", {})

# LightGBM booster
_lgb = lgb.Booster(model_file=str(BASE / f"{PREFIX}_lgb.txt"))

# Échantillon test pour /sample
_SAMPLE_PATH = BASE / f"{PREFIX}_sample.csv"
_SAMPLE = pd.read_csv(_SAMPLE_PATH) if _SAMPLE_PATH.exists() else None

# SHAP explainer (paresseux)
_explainer = None
def _get_explainer():
    global _explainer
    if _explainer is None:
        import shap
        _explainer = shap.TreeExplainer(_lgb)
    return _explainer

PAYMENT_FORMATS = [f[4:] for f in FEATURES if f.startswith("fmt_")]


# ═══════════════════════════════════════════════════════════════════════
#  PRÉDICTION
# ═══════════════════════════════════════════════════════════════════════

def _vectorize(feat: dict) -> np.ndarray:
    return np.array([[float(feat.get(f, 0.0) or 0.0) for f in FEATURES]],
                    dtype=np.float32)

def _predict(feat: dict) -> dict:
    """Prédiction LightGBM directe (sans calibration)."""
    X = _vectorize(feat)
    proba = float(_lgb.predict(X)[0])
    is_fraud = proba >= THRESHOLD
    return {
        "proba": round(proba, 6),
        "threshold": round(THRESHOLD, 6),
        "is_fraud": bool(is_fraud),
        "decision": "FRAUDE_SUSPECTÉE" if is_fraud else "LÉGITIME",
    }


def _engineer_raw(tx: "RawTransaction") -> dict:
    """Transaction brute → vecteur de features."""
    f = {k: 0.0 for k in FEATURES}
    amt = float(tx.amount or 0)
    f["amount"] = amt
    f["amount_log"] = math.log1p(amt)
    f["amount_sqrt"] = math.sqrt(amt) if amt >= 0 else 0.0
    f["currency_mismatch"] = int(
        (tx.payment_currency or "") != (tx.receiving_currency or ""))

    key = f"fmt_{tx.payment_format}"
    if key in f:
        f[key] = 1
    f["is_cash"] = int(tx.payment_format == "Cash")
    f["is_wire"] = int(tx.payment_format in ("Wire", "Wire Transfer"))

    dt = None
    if tx.timestamp:
        for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(tx.timestamp, fmt); break
            except ValueError:
                continue
    if dt:
        f["hour"] = dt.hour
        f["day_of_week"] = dt.weekday()
        f["is_night"] = int(dt.hour >= 23 or dt.hour <= 5)
        f["is_weekend"] = int(dt.weekday() >= 5)
        f["month"] = dt.month
    else:
        f["hour"] = 12; f["month"] = 6

    f["is_round_100"] = int(amt % 100 == 0)
    f["is_round_1000"] = int(amt % 1000 == 0)
    f["just_below_10k"] = int(9000 <= amt < 10000)
    f["just_below_5k"] = int(4500 <= amt < 5000)
    f["amount_decimals"] = min(
        len(str(amt).split(".")[-1]) if "." in str(amt) else 0, 4)

    if tx.advanced:
        for k, v in tx.advanced.items():
            if k in f and v is not None:
                f[k] = float(v)
    return f


# ═══════════════════════════════════════════════════════════════════════
#  SCHÉMAS
# ═══════════════════════════════════════════════════════════════════════

class PredictRequest(BaseModel):
    features: dict = Field(
        ...,
        description="Dictionnaire feature→valeur. Voir GET /model/info.",
        json_schema_extra={"example": {
            "amount": 9800, "amount_log": 9.19, "hour": 3, "is_night": 1,
            "sender_fanout_ratio": 0.95, "reconstruction_error": 6.0,
            "fmt_Wire": 1, "is_wire": 1, "currency_mismatch": 1,
            "just_below_10k": 1,
        }})

class RawTransaction(BaseModel):
    timestamp: Optional[str] = Field("2022/09/01 03:15")
    amount: float = Field(9800)
    payment_currency: str = Field("US Dollar")
    receiving_currency: str = Field("Euro")
    payment_format: str = Field("Wire")
    advanced: Optional[dict] = Field(
        default={"sender_fanout_ratio": 0.95,
                 "reconstruction_error": 6.0,
                 "sender_tx_count": 42})


# ═══════════════════════════════════════════════════════════════════════
#  APPLICATION
# ═══════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="ML Fraud Detection API — LightGBM seul",
    version="4.2",
    description=(
        "Service de détection de fraude AML — LightGBM seul + LSTM-AE.\n\n"
        "**Pourquoi LightGBM seul :** comparé à XGBoost, F1 0.964 vs 0.957, "
        "et 15 faux positifs vs 28. En AML, chaque FP mobilise un compliance "
        "officer.\n\n"
        "**Pourquoi pas de calibration :** Platt scaling a été testé sur un "
        "set séparé de 72 000 transactions. Sur dataset à déséquilibre "
        "extrême (1:115), il produit des probas saturées à 0.99 — pas "
        "d'amélioration. Le modèle reste donc non calibré, le seuil élevé "
        "(~0.9999) reflète la difficulté intrinsèque du problème AML."))


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok",
            "model": "LightGBM seul + LSTM-AE",
            "n_features": len(FEATURES),
            "threshold": round(THRESHOLD, 6)}


@app.get("/model/info", tags=["meta"])
def model_info():
    return {
        "architecture": "LightGBM seul + LSTM-AE feature (sans calibration)",
        "n_features": len(FEATURES),
        "features": FEATURES,
        "threshold": round(THRESHOLD, 6),
        "payment_formats": PAYMENT_FORMATS,
        "training_metrics": _METRICS,
    }


@app.post("/predict", tags=["prediction"])
def predict(req: PredictRequest):
    result = _predict(req.features)
    provided = {k: v for k, v in req.features.items() if k in FEATURES}
    return {**result,
            "features_used": len(provided),
            "features_ignored": [k for k in req.features if k not in FEATURES]}


@app.post("/predict/raw", tags=["prediction"])
def predict_raw(tx: RawTransaction):
    feat = _engineer_raw(tx)
    result = _predict(feat)
    active = {k: round(v, 4) for k, v in feat.items() if abs(v) > 1e-9}
    return {**result, "engineered_features": active}


@app.get("/sample", tags=["prediction"])
def sample(fraud: Optional[bool] = None, n: int = 1):
    """Tire n transactions RÉELLES du jeu de test et les prédit."""
    if _SAMPLE is None:
        return {"error": "Échantillon absent — lancer : python train_demo_ml.py"}
    df = _SAMPLE
    if fraud is True:
        df = df[df["is_laundering"] == 1]
    elif fraud is False:
        df = df[df["is_laundering"] == 0]
    if len(df) == 0:
        return {"error": "Aucune ligne correspondante"}
    rows = df.sample(n=min(max(n, 1), len(df)))
    out = []
    for _, r in rows.iterrows():
        feat = {f: float(r[f]) for f in FEATURES if f in r}
        pred = _predict(feat)
        out.append({
            "true_label": "FRAUDE" if int(r["is_laundering"]) == 1 else "NORMALE",
            "prediction": pred,
            "correct": pred["is_fraud"] == bool(int(r["is_laundering"])),
            "key_features": {k: round(float(r[k]), 4) for k in
                ["amount", "hour", "is_night", "sender_fanout_ratio",
                 "receiver_fanin_ratio", "reconstruction_error"] if k in r},
        })
    return {"model": PREFIX, "n": len(out), "samples": out}


@app.post("/predict/explain", tags=["prediction"])
def predict_explain(req: PredictRequest, top: int = 10):
    feat = req.features
    X = _vectorize(feat)
    expl = _get_explainer()
    sv = expl.shap_values(X)
    if isinstance(sv, list):
        sv = sv[-1]
    sv = np.asarray(sv).reshape(-1)
    base = expl.expected_value
    if isinstance(base, (list, np.ndarray)):
        base = float(np.asarray(base).reshape(-1)[-1])
    contribs = sorted(
        [{"feature": FEATURES[i], "value": round(float(X[0][i]), 4),
          "shap": round(float(sv[i]), 4),
          "pushes": "FRAUDE" if sv[i] > 0 else "NORMAL"}
         for i in range(len(FEATURES))],
        key=lambda d: -abs(d["shap"]))[:top]
    return {"prediction": _predict(feat),
            "base_value": round(float(base), 4),
            "top_contributions": contribs,
            "lecture": "shap>0 pousse vers FRAUDE, shap<0 vers NORMAL"}