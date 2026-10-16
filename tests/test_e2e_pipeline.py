"""
test_e2e_pipeline.py — Pipeline End-to-End complet (Python natif)
==================================================================
Orchestration en Python pur de tous les agents pour chaque transaction :

  Transaction
      ↓
  [1] Level 1 Orchestrator    → décision (PASS/STR/BLOCK) + signaux
      ↓
  [2] Client Scoring Agent    → profil structurel (PEP/KYC/STR)
      ↓
  [3] Planning Agent          → questions réglementaires
      ↓
  [4] Retrieval Pipeline      → chunks BCT/FATF
      ↓
  [5] Compliance Agent        → décision + violations + lois respectées
      ↓
  [6] ML Fraud Detector       → score + SHAP top 5
      ↓
  [7] SAR Generator           → rapport Markdown + JSON

USAGE :
  python test_e2e_pipeline.py                       # tous
  python test_e2e_pipeline.py --tx TX-FRAUD-001     # une seule
  python test_e2e_pipeline.py --no-llm              # mode offline (skip Compliance LLM)
"""

# Ce script vit dans un sous-dossier : on expose la racine du projet aux imports.
import sys as _sys, pathlib as _pathlib
_RACINE = _pathlib.Path(__file__).resolve().parents[1]
if str(_RACINE) not in _sys.path:
    _sys.path.insert(0, str(_RACINE))

import sys
import json
import argparse
import time
from pathlib import Path
from datetime import datetime

# Path setup
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "level1_rules"))

# ── Imports des 7 composants ────────────────────────────────────────────
from level1_rules.level1_orchestrator import Level1Orchestrator
from client_scoring_agent import (ClientScoringAgent, ClientProfile,
                                   client_score_to_signals)
from planning_agent import PlanningAgent
from retrieval_agent import RetrievalPipeline
from compliance_agent import ComplianceAgent
from sar_generator import SARGenerator
from client_history_store import get_history_store
from clients_db_loader import ClientDBLoader

# ML imports
import lightgbm as lgb
import numpy as np
import pandas as pd
import shap

# Neo4j (optionnel)
try:
    from neo4j import GraphDatabase
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False



# ═══════════════════════════════════════════════════════════════════════
#  ML PREDICTOR — Option A : LSTM-AE conditionnel
# ═══════════════════════════════════════════════════════════════════════

class MLPredictor:
    """LightGBM + LSTM-AE feature avec activation CONDITIONNELLE.
    
    Stratégie (Option A — décidée le 29/06/2026) :
      • Le LSTM-AE reste entraîné sur l'ensemble complet (modèle inchangé).
      • À l'inférence : si le client a ≥ 3 transactions dans Redis, on
        calcule de VRAIES features comportementales depuis l'historique :
          - sender_amount_mean    : moyenne historique réelle
          - sender_amount_sum     : volume total réel
          - sender_tx_count       : nombre de transactions réel
          - sender_fanout_ratio   : diversité des bénéficiaires réelle
          - amount_vs_sender_mean : ratio actuel/historique
          - reconstruction_error  : heuristique basée sur les patterns
      • Si historique < 3 → utilise les médianes (comportement actuel)
        + marque PROFIL_NOUVEAU dans le résultat.
    
    Cette stratégie élimine les faux positifs ML sur les comptes matures
    tout en restant honnête sur les comptes nouveaux (ML moins fiable).
    """
    
    MIN_HISTORY_FOR_LSTM = 3  # seuil minimum pour activer LSTM-AE
    
    def __init__(self, prefix="aml_ml_demo", history_store=None):
        self.meta = json.loads(
            (BASE / f"{prefix}_meta.json").read_text(encoding="utf-8"))
        self.features = self.meta["features"]
        self.threshold = self.meta["threshold"]
        self.model = lgb.Booster(model_file=str(BASE / f"{prefix}_lgb.txt"))
        self.explainer = shap.TreeExplainer(self.model)
        self.sample = pd.read_csv(BASE / f"{prefix}_sample.csv")
        self.history_store = history_store

        # BRANCHE C : Isolation Forest (anomalies non supervisées) — chargé
        # s'il a été produit au training (train_demo_ml.py). Sert à calculer la
        # feature 'isolation_score' au runtime, pour TOUS les clients.
        self._iforest = None
        iforest_path = BASE / f"{prefix}_iforest.pkl"
        if iforest_path.exists():
            try:
                import joblib
                self._iforest = joblib.load(iforest_path)
                print(f"  ✓ Isolation Forest chargé ({iforest_path.name})")
            except Exception as e:
                print(f"  ⚠️ Isolation Forest illisible ({e})")

        # NOUVEAU (Étape C) : charge le calibrateur s'il existe
        self.calibrator = None
        self.calibration_method = None
        calibrator_path = BASE / f"{prefix}_calibrator.pkl"
        if calibrator_path.exists():
            import pickle
            with open(calibrator_path, "rb") as f:
                calib_data = pickle.load(f)
            # Format dict produit par calibrate_ml.py ; ignore les anciens
            # pickles (CalibratedClassifierCV brut de train_demo_ml.py)
            if isinstance(calib_data, dict) and "calibrator" in calib_data:
                self.calibrator = calib_data["calibrator"]
                self.calibration_method = calib_data["method"]
                self.threshold = calib_data["new_threshold"]  # seuil calibré
                print(f"  ✓ Calibrateur {self.calibration_method} chargé "
                      f"(seuil : {self.threshold:.4f})")
            else:
                print(f"  ⚠️ {calibrator_path.name} au format ancien — "
                      f"relancer calibrate_ml.py pour l'activer")
    
    def predict(self, tx: dict, client_id: str = None) -> dict:
        """Prédit la proba à partir d'une transaction.
        
        Args:
            tx        : la transaction (dict avec amount, tx_type, etc.)
            client_id : identifiant du compte pour interroger Redis (optionnel)
        """
        feat = {f: 0.0 for f in self.features}
        amt = float(tx.get("amount", 0))
        
        # ── A. Features de base (toujours calculées) ──────────────────
        feat["amount"] = amt
        feat["amount_log"] = np.log1p(amt)
        feat["amount_sqrt"] = np.sqrt(amt) if amt >= 0 else 0
        feat["currency_mismatch"] = 0
        
        fmt = tx.get("payment_format", "")
        fmt_key = f"fmt_{fmt}"
        if fmt_key in feat:
            feat[fmt_key] = 1
        feat["is_cash"] = int("Cash" in fmt)
        feat["is_wire"] = int("Wire" in fmt)
        
        # Temporel
        ts = tx.get("timestamp", "")
        dt = self._parse_timestamp(ts)
        feat["hour"] = dt.hour
        feat["day_of_week"] = dt.weekday()
        feat["is_night"] = int(dt.hour >= 23 or dt.hour <= 5)
        feat["is_weekend"] = int(dt.weekday() >= 5)
        feat["month"] = dt.month
        
        feat["is_round_100"] = int(amt % 100 == 0)
        feat["is_round_1000"] = int(amt % 1000 == 0)
        feat["just_below_10k"] = int(9000 <= amt < 10000)
        feat["just_below_5k"] = int(4500 <= amt < 5000)
        feat["amount_decimals"] = min(
            len(str(amt).split(".")[-1]) if "." in str(amt) else 0, 4)
        
        # ── B. Features comportementales (CONDITIONNELLES Option A) ───
        use_real_history = False
        history_size = 0
        profile_status = "PROFIL_NOUVEAU"
        
        if self.history_store is not None and client_id:
            prof = self.history_store.behavioral_profile(client_id)
            history_size = prof.transaction_count_30d
            
            if history_size >= self.MIN_HISTORY_FOR_LSTM:
                # === Client MATURE : utilise vraies features ===
                use_real_history = True
                profile_status = "PROFIL_MATURE"
                
                # sender_amount_mean : moyenne historique réelle
                feat["sender_amount_mean"] = (
                    prof.avg_amount_30d if prof.avg_amount_30d > 0
                    else float(self.sample["sender_amount_mean"].median()))
                
                # sender_amount_sum : volume total réel
                feat["sender_amount_sum"] = prof.total_volume_30d
                
                # sender_tx_count : nombre réel
                feat["sender_tx_count"] = float(prof.transaction_count_30d)
                
                # sender_fanout_ratio : diversité réelle des bénéficiaires
                receivers_30d = self._count_unique_receivers(
                    client_id, days=30)
                feat["sender_fanout_ratio"] = min(
                    receivers_30d / max(prof.transaction_count_30d, 1), 1.0)
                
                # sender_unique_receivers
                feat["sender_unique_receivers"] = float(receivers_30d)

                # sender_amount_std : écart-type réel — AVANT ce fix, cette
                # feature n'était jamais renseignée pour les profils matures
                # (restait au défaut 0.0), alors que la médiane training est
                # ≈345 (seuls 28% des lignes training sont à 0) → gap réel.
                hist_evts = self.history_store._events_since(client_id, 30 * 86400)
                hist_amounts = [float(e.get("amount", 0)) for e in hist_evts]
                feat["sender_amount_std"] = (
                    float(np.std(hist_amounts)) if len(hist_amounts) > 1
                    else float(self.sample["sender_amount_std"].median()))

                # amount_vs_sender_mean : RATIO CRITIQUE pour smurfing
                if prof.avg_amount_30d > 0:
                    feat["amount_vs_sender_mean"] = min(
                        amt / prof.avg_amount_30d, 100.0)
                
                # pair_count, is_unique_pair : approximation
                feat["pair_count"] = 1.0
                feat["is_unique_pair"] = 0
                
                # receiver_amount_mean, receiver_tx_count : approximation
                feat["receiver_amount_mean"] = (
                    prof.avg_amount_30d if prof.avg_amount_30d > 0
                    else float(self.sample["receiver_amount_mean"].median()))
                feat["receiver_tx_count"] = 1.0
                feat["receiver_fanin_ratio"] = 0.5
                feat["receiver_unique_senders"] = 1.0
                
                # reconstruction_error : HEURISTIQUE basée sur patterns
                # Logique : si la transaction présente des anomalies
                # comportementales, on simule une erreur de reconstruction
                # élevée (comme si l'AE n'avait jamais vu ce pattern)
                feat["reconstruction_error"] = self._compute_recon_error(
                    prof, amt, feat["sender_fanout_ratio"])
            
        if not use_real_history:
            # === Profil nouveau : utilise médianes (comportement actuel) ===
            for agg_feat in ["sender_tx_count", "sender_amount_mean",
                             "sender_amount_std",
                             "sender_fanout_ratio", "receiver_fanin_ratio",
                             "reconstruction_error", "receiver_amount_mean",
                             "pair_count", "sender_amount_sum",
                             "amount_vs_sender_mean", "sender_unique_receivers",
                             "receiver_tx_count", "receiver_unique_senders"]:
                if agg_feat in feat and agg_feat in self.sample.columns:
                    feat[agg_feat] = float(self.sample[agg_feat].median())
        
        # ── Branche C : Isolation Forest → isolation_score (runtime) ──
        #  Calculé sur les features déjà construites → marche pour TOUS les
        #  clients (y compris les nouveaux, sans historique).
        if self._iforest is not None and "isolation_score" in self.features:
            try:
                _cols = self._iforest["columns"]
                _Xif = np.array([[float(feat.get(c, 0.0) or 0.0) for c in _cols]],
                                dtype=float)
                _raw = float(self._iforest["model"].score_samples(_Xif)[0])
                _lo, _hi = self._iforest["lo"], self._iforest["hi"]
                _rng = (_hi - _lo) or 1.0
                feat["isolation_score"] = max(0.0, min(1.0, (_hi - _raw) / _rng))
            except Exception:
                pass

        # ── C. Prédiction LightGBM ────────────────────────────────────
        X = np.array([[feat.get(f, 0.0) for f in self.features]],
                     dtype=np.float32)
        # ── Calibration (si disponible) ────────────────────────────────
        proba_raw = float(self.model.predict(X)[0])
        if self.calibrator is not None:
            if self.calibration_method == "isotonic":
                proba = float(self.calibrator.predict([proba_raw])[0])
            else:  # platt
                proba = float(self.calibrator.predict_proba([[proba_raw]])[0, 1])
            proba = max(0.0, min(1.0, proba))
        else:
            proba = proba_raw
        
        # ── D. SHAP top 5 ─────────────────────────────────────────────
        sv = self.explainer.shap_values(X)
        if isinstance(sv, list):
            sv = sv[-1]
        sv = np.asarray(sv).reshape(-1)
        contribs = sorted(
            [{"feature": self.features[i],
              "value": round(float(X[0][i]), 4),
              "shap": round(float(sv[i]), 4),
              "pushes": "FRAUDE" if sv[i] > 0 else "NORMAL"}
             for i in range(len(self.features))],
            key=lambda d: -abs(d["shap"]))[:5]
        
        return {
            "ml_score": proba,           # proba calibrée (si calibrateur)
            "ml_score_raw": proba_raw,   # proba brute (avant calibration)
            "ml_decision": "FRAUDE_SUSPECTÉE" if proba >= self.threshold
                          else "LÉGITIME",
            "calibrated": self.calibrator is not None,
            "shap_top": contribs,
            "profile_status": profile_status,
            "history_size": history_size,
            "used_lstm_ae": use_real_history,
            "isolation_score": round(float(feat.get("isolation_score", 0.0)), 4),
            "all_features": dict(feat),   # NOUVEAU : vecteur complet (Fix 6.5)
        }
    
    def _parse_timestamp(self, ts: str) -> datetime:
        """Parse un timestamp dans différents formats."""
        if not ts:
            return datetime.now()
        ts_clean = str(ts).split(".")[0]
        for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(ts_clean, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(ts_clean)
        except (ValueError, TypeError):
            return datetime.now()
    
    def _count_unique_receivers(self, client_id: str, days: int = 30) -> int:
        """Compte les bénéficiaires uniques sur N jours via Redis."""
        if not self.history_store:
            return 1
        evts = self.history_store._events_since(
            client_id, days * 86400)
        receivers = set(e.get("receiver_id", "") for e in evts
                        if e.get("receiver_id"))
        return max(len(receivers), 1)
    
    # Bornes RÉELLES de reconstruction_error mesurées sur le training
    # (aml_ml_demo_sample.csv, 400 lignes, colonne reconstruction_error) :
    #   NORMAL (is_laundering=0) : médiane 0.00142, P95 0.00738
    #   FRAUDE (is_laundering=1) : médiane 0.00351, P95 0.4835 (queue longue)
    # L'ancienne échelle (base 0.001 → jusqu'à 2.5) était 5x à 340x AU-DESSUS
    # du maximum jamais vu à l'entraînement (0.571) → feature hors-distribution,
    # ce qui saturait/déstabilisait le modèle (cf. échec calibration isotonic).
    _RECON_NORMAL_TYPICAL = 0.0015   # ≈ médiane NORMAL
    _RECON_FRAUD_P95 = 0.48          # ≈ P95 FRAUDE réel (cas les plus anormaux)

    def _compute_recon_error(self, prof, amt: float,
                              fanout_ratio: float) -> float:
        """Calcule une heuristique de reconstruction_error basée sur les
        patterns comportementaux du client, RECALIBRÉE sur la distribution
        réelle vue à l'entraînement (voir bornes ci-dessus).

        L'idée : sans recalculer le LSTM-AE en live (coûteux), on simule
        son comportement attendu. Une transaction anormale par rapport
        au profil aura une erreur de reconstruction élevée — mais toujours
        dans la plage effectivement observée par le modèle, jamais au-delà.

        Signaux d'anomalie (inchangés) :
          - fanout élevé (smurfing) → erreur ÉLEVÉE
          - velocity 24h élevée (rafale) → erreur ÉLEVÉE
          - montant >> moyenne historique → erreur MOYENNE
          - sinon → erreur FAIBLE (transaction typique)
        """
        anomaly_score = 0.0   # 0 = comportement typique, 1 = anomalie maximale

        # Signal 1 : fan-out élevé (pattern smurfing)
        if fanout_ratio > 0.7:
            anomaly_score = max(anomaly_score, 0.9)
        elif fanout_ratio > 0.5:
            anomaly_score = max(anomaly_score, 0.5)

        # Signal 2 : rafale de transactions (velocity 24h)
        if prof.velocity_24h > 5:
            anomaly_score = max(anomaly_score, 1.0)
        elif prof.velocity_24h > 3:
            anomaly_score = max(anomaly_score, 0.6)

        # Signal 3 : montant anormal vs historique
        if prof.avg_amount_30d > 0:
            ratio = amt / prof.avg_amount_30d
            if ratio > 10:  # 10x la moyenne → très anormal
                anomaly_score = max(anomaly_score, 0.8)
            elif ratio > 5:  # 5x la moyenne → anormal
                anomaly_score = max(anomaly_score, 0.4)

        # Signal 4 : transactions internationales fréquentes
        if prof.international_wire_count_30d > 10:
            anomaly_score = max(anomaly_score, 0.25)

        # Interpolation dans la plage RÉELLE (typique normal → P95 fraude)
        recon = self._RECON_NORMAL_TYPICAL + anomaly_score * (
            self._RECON_FRAUD_P95 - self._RECON_NORMAL_TYPICAL)
        return round(recon, 5)

# ═══════════════════════════════════════════════════════════════════════
#  MAPPING ENTRE COMPOSANTS
# ═══════════════════════════════════════════════════════════════════════

def l1_to_planning(tx: dict) -> dict:
    """Mappe le format Level 1 → format Planning Agent."""
    return {
        "transaction_type": tx.get("tx_type", "SWIFT"),
        "amount": tx.get("amount", 0),
        "currency": tx.get("currency", ""),
        "timestamp": tx.get("timestamp"),
        "motive": tx.get("motive", ""),
        "sender_name": tx.get("sender_name", ""),
        "sender_country": tx.get("sender_country", ""),
        "receiver_name": tx.get("beneficiary_name", ""),
        "receiver_country": tx.get("destination_country", ""),
        "has_justification": tx.get("has_justification"),
    }


def infer_jurisdiction(tx: dict) -> str:
    sc = (tx.get("sender_country") or "").lower()
    dc = (tx.get("destination_country") or "").lower()
    if "tunisia" in sc or "tunisia" in dc:
        return "Tunisia"
    return None


class _Rec:
    def __init__(self, rid): self.rec_id = rid


def fatf_checklist(tx_type: str) -> list:
    rec_map = {
        "SWIFT": ["R6","R10","R11","R12","R13","R16","R19","R20","R24"],
        "VIREMENT_LOCAL": ["R10","R11","R13","R20"],
        "DEPOT_ESPECES": ["R10","R11","R20"],
        "MANDAT": ["R10","R14","R16","R20"],
    }
    return [_Rec(r) for r in rec_map.get(tx_type, rec_map["SWIFT"])]


def deduplicate_chunks(chunks):
    from collections import OrderedDict
    seen = OrderedDict()
    for c in chunks:
        key = (c.source_doc, c.article_ref)
        if key not in seen or c.score > seen[key].score:
            seen[key] = c
    return sorted(seen.values(), key=lambda c: -c.score)


# ═══════════════════════════════════════════════════════════════════════
#  PIPELINE END-TO-END POUR UNE TRANSACTION
# ═══════════════════════════════════════════════════════════════════════

def run_pipeline(tx_data: dict, components: dict, verbose: bool = True):
    """Exécute le pipeline complet pour UNE transaction."""
    tx_id = tx_data["tx_id"]
    tx = tx_data["transaction"]
    expected = tx_data["expected_label"]
    
    print(f"\n{'='*75}")
    print(f"  TRANSACTION : {tx_id}  (attendu : {expected})")
    print(f"{'='*75}")
    print(f"  Type      : {tx['tx_type']}")
    print(f"  Montant   : {tx['amount']:,} {tx['currency']}")
    print(f"  De        : {tx.get('sender_name', '?')} ({tx.get('sender_country', '?')})")
    print(f"  Vers      : {tx.get('beneficiary_name', '?')} ({tx.get('destination_country', '?')})")
    
    timings = {}
    
    # ── [1] LEVEL 1 ────────────────────────────────────────────────────
    print(f"\n  ┌─ [1] LEVEL 1 ORCHESTRATOR ─────────────────────────────")
    t = time.time()
    l1 = components["l1"].check_transaction(tx)
    timings["l1"] = (time.time() - t) * 1000
    print(f"  │   Décision    : {l1.decision}")
    print(f"  │   Violations  : {len(l1.violations)}")
    print(f"  │   Signaux     : {l1.risk_signals}")
    print(f"  │   Durée       : {timings['l1']:.0f}ms")
    print(f"  └─────────────────────────────────────────────────────────")
    
    # ── [2] CLIENT SCORING (depuis clients_db.json) ────────────────────
    client_result = None
    client_signals = []

    # Extraire client_id depuis sender_name (format: "Account-XXXXX")
    sender_name = tx.get("sender_name", "")
    client_id = (sender_name.replace("Account-", "")
                 if "Account-" in sender_name else sender_name)

    print(f"\n  ┌─ [2] CLIENT SCORING AGENT ─────────────────────────────")
    t = time.time()

    if components["clients_db"].exists(client_id):
        print(f"  │   Client ID     : {client_id} (✓ trouvé dans la base)")
        profile = components["clients_db"].get_profile(client_id)
    else:
        print(f"  │   Client ID     : {client_id} (⚠️ INCONNU → DEFAULT)")
        profile = components["clients_db"].get_profile(client_id,
                                                        fallback_default=True)

    if profile:
        client_result = components["client"].score_client(profile)
        client_signals = client_score_to_signals(client_result)
        timings["client"] = (time.time() - t) * 1000

        print(f"  │   Nationalité   : {profile.nationality}")
        print(f"  │   Pays résidence: {profile.country_residence}")
        print(f"  │   PEP           : {'OUI (' + profile.pep_type + ')' if profile.is_pep else 'non'}")
        print(f"  │   KYC complet   : {'OUI' if profile.kyc_complete else 'NON'}")
        print(f"  │   STR antérieurs: {profile.previous_strs}")
        print(f"  │   Type compte   : {profile.account_type}")
        print(f"  │")
        print(f"  │   ─── Résultat scoring ───")
        print(f"  │   Niveau risque : {client_result.level} "
              f"(score {client_result.score:.0f}/100)")
        if client_result.components:
            print(f"  │   Facteurs     :")
            for factor, weight in sorted(client_result.components.items(),
                                         key=lambda x: -x[1]):
                print(f"  │     • {factor:<25} +{weight:.1f}")
        if client_signals:
            print(f"  │   Signaux      : {client_signals}")
        print(f"  │   Durée         : {timings['client']:.0f}ms")
    else:
        timings["client"] = (time.time() - t) * 1000
        print(f"  │   ⚠️ Aucun profil disponible")

    print(f"  └─────────────────────────────────────────────────────────")
    
    # ── [3] PLANNING AGENT ─────────────────────────────────────────────
    print(f"\n  ┌─ [3] PLANNING AGENT ───────────────────────────────────")
    t = time.time()
    planning_tx = l1_to_planning(tx)
    all_signals = list(dict.fromkeys(list(l1.risk_signals) + client_signals))
    plan = components["planner"].plan(planning_tx, all_signals, None)
    timings["planning"] = (time.time() - t) * 1000
    print(f"  │   Questions   : {len(plan.questions)}")
    for i, q in enumerate(plan.questions[:3], 1):
        print(f"  │     Q{i}: {q[:80]}")
    if len(plan.questions) > 3:
        print(f"  │     ... + {len(plan.questions)-3} autres")
    print(f"  │   Durée       : {timings['planning']:.0f}ms")
    print(f"  └─────────────────────────────────────────────────────────")
    
    # ── [4] RETRIEVAL PIPELINE ─────────────────────────────────────────
    print(f"\n  ┌─ [4] RETRIEVAL PIPELINE ───────────────────────────────")
    t = time.time()
    jurisdiction = infer_jurisdiction(tx)
    all_chunks = []
    for q in plan.questions:
        try:
            chunks = components["retriever"].retrieve(
                query=q, tx_type=tx["tx_type"],
                jurisdiction=jurisdiction, skip_cache=True, top_k=5)
            all_chunks.extend(chunks)
        except Exception as e:
            print(f"  │   ⚠️ Erreur retrieval : {e}")
    deduped = deduplicate_chunks(all_chunks)
    timings["retrieval"] = (time.time() - t) * 1000
    print(f"  │   Chunks bruts    : {len(all_chunks)}")
    print(f"  │   Chunks uniques  : {len(deduped)}")
    print(f"  │   Sources         : {len(set(c.source_doc for c in deduped))}")
    print(f"  │   Durée           : {timings['retrieval']/1000:.1f}s")
    print(f"  └─────────────────────────────────────────────────────────")
    
    # ── [5] COMPLIANCE AGENT ───────────────────────────────────────────
    print(f"\n  ┌─ [5] COMPLIANCE AGENT ─────────────────────────────────")
    t = time.time()
    decision = components["compliance"].analyze(
        transaction=planning_tx,
        chunks=deduped,
        # all_signals = L1 ∪ client_signals (déjà fusionnés pour le Planning
        # à la step [3]) — les CLIENT_* sont mappés à poids 0 dans
        # SOFT_SIGNALS (compliance_agent.py) : visibles dans l'audit_trail,
        # zéro impact sur le score (déjà compté via client_profile→
        # client_level→CLIENT_RISK_CONTRIB/CLIENT_FLOOR, pas de double comptage).
        risk_signals=all_signals,
        fatf_checklist=fatf_checklist(tx["tx_type"]),
        level1_decision=l1.decision,
        client_profile=client_result)
    timings["compliance"] = (time.time() - t) * 1000
    print(f"  │   Décision        : {decision.decision}")
    print(f"  │   Score risque    : {decision.risk_score}/100")
    print(f"  │   Règles applic.  : {len(decision.applicable_rules)}")
    print(f"  │   Violations      : {len(decision.violated_rules)}")
    if decision.violated_rules:
        for v in decision.violated_rules[:3]:
            print(f"  │     • [{v.severity}] {v.source_doc} {v.rule_ref}")
    print(f"  │   Durée           : {timings['compliance']:.0f}ms")
    print(f"  └─────────────────────────────────────────────────────────")
    
    # ── [6] ML FRAUD DETECTOR (avec LSTM-AE conditionnel) ──────────────
    print(f"\n  ┌─ [6] ML FRAUD DETECTOR ────────────────────────────────")
    t = time.time()
    ml_result = components["ml"].predict(tx, client_id=client_id)
    timings["ml"] = (time.time() - t) * 1000

    ml_proba = ml_result["ml_score"]
    ml_threshold = components["ml"].threshold

    if ml_result.get("calibrated"):
        print(f"  │   Proba brute     : {ml_result['ml_score_raw']:.4f}")
        print(f"  │   Proba calibrée  : {ml_proba:.4f} (seuil : {ml_threshold:.4f})")
        print(f"  │   Calibration     : ✓ ACTIVÉE ({components['ml'].calibration_method})")
    else:
        print(f"  │   Proba ML        : {ml_proba:.4f} (seuil : {ml_threshold:.4f})")
        print(f"  │   Calibration     : ✗ non activée")
    print(f"  │   Décision ML     : {ml_result['ml_decision']}")
    print(f"  │   Profil client   : {ml_result['profile_status']} "
          f"(historique : {ml_result['history_size']} tx/30j)")
    print(f"  │   LSTM-AE         : {'✓ ACTIVÉ' if ml_result['used_lstm_ae'] else '✗ médianes par défaut'}")
    print(f"  │   Top SHAP        :")
    for f in ml_result["shap_top"][:3]:
        print(f"  │     • {f['feature']:<25} → {f['pushes']} (SHAP {f['shap']:+.2f})")
    print(f"  │   Durée           : {timings['ml']:.0f}ms")
    print(f"  └─────────────────────────────────────────────────────────")
    
    # ── [7] SAR GENERATOR ──────────────────────────────────────────────
    print(f"\n  ┌─ [7] SAR GENERATOR ────────────────────────────────────")
    t = time.time()
    sar = components["sar"].generate(
        compliance_decision=decision,
        ml_result=ml_result,
        client_result=client_result,
        transaction=tx,
        l1_signals=list(l1.risk_signals))   # NOUVEAU (Fix 6.5)
    
    # Export
    Path("sars").mkdir(exist_ok=True)
    md_path = f"sars/sar_{tx_id}.md"
    json_path = f"sars/sar_{tx_id}.json"
    sar.to_markdown(md_path)
    sar.to_json(json_path)
    timings["sar"] = (time.time() - t) * 1000
    
    print(f"  │   SAR ID          : {sar.sar_id}")
    print(f"  │   Score combiné   : {sar.risk_score_combined}/100")
    print(f"  │   Niveau          : {sar.risk_level}")
    print(f"  │   Recommandation  : {sar.recommendation[:60]}")
    print(f"  │   Fichiers        : {md_path}")
    print(f"  │                     {json_path}")
    print(f"  │   Durée           : {timings['sar']:.0f}ms")
    print(f"  └─────────────────────────────────────────────────────────")
    
    # ── Récapitulatif ──────────────────────────────────────────────────
    total = sum(timings.values())
    print(f"\n  ⏱️  TIMING TOTAL  : {total/1000:.1f}s "
          f"(L1:{timings['l1']:.0f}ms | Plan:{timings['planning']:.0f}ms | "
          f"Retr:{timings['retrieval']/1000:.1f}s | "
          f"Comp:{timings['compliance']:.0f}ms | "
          f"ML:{timings['ml']:.0f}ms | SAR:{timings['sar']:.0f}ms)")
    
    # ── Enregistre la transaction dans Redis pour les futures ────────────
    try:
        # On utilise un format que le history_store sait comprendre
        tx_for_history = {
            "client_id": client_id if client_id else "UNKNOWN",
            "sender_account_id": client_id if client_id else "UNKNOWN",
            "tx_type": tx.get("tx_type", "UNKNOWN"),
            "amount": tx.get("amount", 0),
            "currency": tx.get("currency", "TND"),
            "destination_country": tx.get("destination_country", ""),
            "beneficiary_name": tx.get("beneficiary_name", ""),
            "receiver_account_id": tx.get("beneficiary_name", "").replace("Account-", ""),
            "timestamp": tx.get("timestamp", ""),
        }
        components["history"].record(
            transaction=tx_for_history,
            decision=decision.decision,
            is_str=(decision.decision in ("STR", "BLOCK")),
        )
        print(f"  📥 Transaction enregistrée dans Redis (client {client_id})")
    except Exception as e:
        print(f"  ⚠️ Erreur enregistrement Redis : {e}")

    return {
        "tx_id": tx_id,
        "expected": expected,
        "l1_decision": l1.decision,
        "ca_decision": decision.decision,
        "ml_decision": ml_result["ml_decision"],
        "sar_level": sar.risk_level,
        "combined_score": sar.risk_score_combined,
        "sar_path": md_path,
        "timings": timings,
    }


# ═══════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx", type=str, default=None,
                    help="ID transaction spécifique (ex: TX-FRAUD-001)")
    ap.add_argument("--file", default="test_transactions_e2e.json")
    args = ap.parse_args()
    
    print(f"\n{'#'*75}")
    print(f"#  TEST END-TO-END — Pipeline AML/KYC complet")
    print(f"#  Date : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'#'*75}")
    
    # ── Charge les transactions ───────────────────────────────────────
    txs = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if args.tx:
        txs = [t for t in txs if t["tx_id"] == args.tx]
        if not txs:
            print(f"❌ Transaction {args.tx} non trouvée")
            return
    
    # ── Initialise tous les composants ────────────────────────────────
    print(f"\n  Initialisation des composants...")
    
    print(f"  [1/7] Level 1 Orchestrator...")
    l1 = Level1Orchestrator()
    
    print(f"  [2/9] Client Scoring Agent...")
    from level1_rules.risk_country_checker import RiskCountryChecker
    client_agent = ClientScoringAgent(country_checker=RiskCountryChecker())

    print(f"  [3/9] Client History Store (Redis)...")
    history_store = get_history_store(verbose=True)

    print(f"  [4/9] Clients DB (JSON)...")
    clients_db = ClientDBLoader("clients_db.json", verbose=True)
    
    print(f"  [5/9] Retrieval Pipeline...")
    retriever = RetrievalPipeline(verbose=False)
    
    print(f"  [6/9] Planning Agent...")
    planner = PlanningAgent(verbose=False, retriever=retriever)
    
    print(f"  [7/9] Compliance Agent...")
    neo4j_driver = None
    if NEO4J_AVAILABLE:
        try:
            neo4j_driver = GraphDatabase.driver(
                "bolt://localhost:7687", auth=("neo4j", "amlkyc2025"))
            neo4j_driver.verify_connectivity()
            print(f"        ✓ Neo4j connecté")
        except:
            neo4j_driver = None
            print(f"        ⚠️ Neo4j indisponible")
    compliance = ComplianceAgent(neo4j_driver=neo4j_driver, verbose=False)
    
    print(f"  [8/9] ML Fraud Detector...")
    ml = MLPredictor(prefix="aml_ml_demo", history_store=history_store)
    
    print(f"  [9/9] SAR Generator...")
    sar_gen = SARGenerator()
    
    components = {
    "l1": l1, 
    "client": client_agent, 
    "planner": planner,
    "retriever": retriever, 
    "compliance": compliance,
    "ml": ml, 
    "sar": sar_gen,
    "history": history_store,       # NOUVEAU — Redis
    "clients_db": clients_db,       # NOUVEAU — JSON
}
    
    print(f"  ✅ Tous les composants prêts")
    
    # ── Exécute le pipeline pour chaque transaction ───────────────────
    results = []
    for tx_data in txs:
        try:
            result = run_pipeline(tx_data, components)
            results.append(result)
        except Exception as e:
            print(f"\n  ❌ ERREUR pour {tx_data['tx_id']} : {e}")
            import traceback
            traceback.print_exc()
    
    retriever.close()
    history_store.close()  # NOUVEAU
    if neo4j_driver:
        neo4j_driver.close()
    
    # ── Récapitulatif final ───────────────────────────────────────────
    print(f"\n\n{'='*75}")
    print(f"  RÉCAPITULATIF — {len(results)} transactions traitées")
    print(f"{'='*75}\n")
    print(f"  {'TX-ID':<20} {'L1':<7} {'CA':<7} {'ML':<20} {'Niveau':<10} {'Score':>6}")
    print(f"  {'─'*20} {'─'*7} {'─'*7} {'─'*20} {'─'*10} {'─'*6}")
    for r in results:
        print(f"  {r['tx_id']:<20} {r['l1_decision']:<7} {r['ca_decision']:<7} "
              f"{r['ml_decision']:<20} {r['sar_level']:<10} "
              f"{r['combined_score']:>6.0f}")
    
    print(f"\n  📁 Rapports SAR dans : ./sars/")
    print(f"  Ouvre n'importe quel .md dans VSCode pour voir le rapport")


if __name__ == "__main__":
    main()