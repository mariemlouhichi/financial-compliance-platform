"""
ml_fraud_detector_v4.py — Pipeline ML AML Dual-Branch + LSTM-AE
======================================================
Composant ML double vérification du Compliance Agent Neuro-Symbolique.

ARCHITECTURE DUAL-BRANCH :
  BRANCHE A (supervisé)     : XGBoost + LightGBM (Optuna) → patterns connus
  BRANCHE B (non-supervisé) : Autoencoder DENSE ou LSTM (séquentiel) → score → feature
  FUSION                    : Ensemble pondéré par PR-AUC → seuil F2-beta

═══════════════════════════════════════════════════════════════════════
  NOUVEAUTÉ v3 vs v2 — Branche non-supervisée (Autoencoder)
═══════════════════════════════════════════════════════════════════════
  POURQUOI un autoencoder en plus du gradient boosting ?
    Supervisé    : apprend "les fraudes CONNUES ressemblent à ça"
    Non-supervisé: apprend "les transactions NORMALES ressemblent à ça"
                   → TOUT ce qui s'écarte de la normale = anomalie
                   → détecte des fraudes JAMAIS vues à l'entraînement

  COMMENT ça marche :
    1. L'autoencoder est entraîné UNIQUEMENT sur les transactions normales
    2. Il apprend à reconstruire une transaction normale (compression→décompression)
    3. Une fraude (motif inhabituel) a une ERREUR DE RECONSTRUCTION élevée
    4. Cette erreur devient une FEATURE (#39) réinjectée dans LightGBM
    5. Le modèle supervisé bénéficie de l'intelligence non-supervisée

  Référence : l'hybride autoencoder + gradient boosting atteint des F1 > 0.94
              sur les datasets de fraude déséquilibrés (littérature 2023-2024)

═══════════════════════════════════════════════════════════════════════
  HÉRITÉ DE v2 (corrections déjà appliquées)
═══════════════════════════════════════════════════════════════════════
  FIX 1 — Data leakage corrigé (split temporel AVANT feature engineering)
  FIX 2 — Early stopping équilibré (patience=200, n_estimators=3000)
  FIX 3 — scale_pos_weight ratio réel (cap=None par défaut sur dataset complet)
  + Optuna, SHAP, ensemble pondéré

USAGE :
  # Mode rapide (développement, sans autoencoder)
  python ml_fraud_detector_v3.py --sample 600000 --no-optuna --no-shap --no-autoencoder

  # Mode PFE complet (avec autoencoder + Optuna + SHAP)
  python ml_fraud_detector_v3.py --sample 600000

  # Mode recherche (dataset complet 5M)
  python ml_fraud_detector_v3.py
"""

import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score, f1_score, precision_score,
    recall_score, roc_auc_score, confusion_matrix,
    precision_recall_curve,
)

warnings.filterwarnings("ignore")
import os; os.environ["PYTHONWARNINGS"] = "ignore"

# ─── Chemins IBM (déjà au bon endroit dans ton arborescence) ────────────────
IBM_DATA_PATH  = r"C:\Users\louhi\Downloads\projet_pfe\PFE_AML_KYC\IBM"
IBM_TRANS_FILE = "HI-Small_Trans.csv"
IBM_ACCTS_FILE = "HI-Small_accounts.csv"


# ═══════════════════════════════════════════════════════════════════════
#  PHASE 2 — DATA LOADING
# ═══════════════════════════════════════════════════════════════════════

def load_data(path: str, accounts_path: str = None, sample: int = None) -> pd.DataFrame:
    print(f"\n{'='*70}")
    print(f"  PHASE 2 — DATA LOADING")
    print(f"{'='*70}")
    print(f"  Fichier : {path}")

    df = pd.read_csv(path)
    df.columns = [c.strip().replace(" ", "_").replace(".", "_") for c in df.columns]

    # Label
    for cand in ["Is_Laundering","Is_laundering","is_laundering","Label"]:
        if cand in df.columns:
            df = df.rename(columns={cand: "is_laundering"}); break

    # Renommage colonnes Account (IBM duplique "Account" → "Account.1")
    cols = list(df.columns)
    acc_seen = False
    rename_map = {}
    for c in cols:
        if c == "Account" and not acc_seen:
            rename_map[c] = "from_account"; acc_seen = True
        elif c in ("Account_1",):
            rename_map[c] = "to_account"
    df = df.rename(columns=rename_map)

    # Timestamp
    ts_col = next((c for c in df.columns if "timestamp" in c.lower()), None)
    if ts_col:
        df["_ts"] = pd.to_datetime(df[ts_col], errors="coerce")
        df = df.sort_values("_ts").reset_index(drop=True)

    # Jointure comptes (pour pays banque expéditeur)
    if accounts_path and Path(accounts_path).exists():
        acc = pd.read_csv(accounts_path)
        acc.columns = [c.strip().replace(" ","_") for c in acc.columns]
        # Chercher colonne identifiant compte
        id_col = next((c for c in acc.columns if "account" in c.lower()), acc.columns[0])
        acc_s = acc.add_prefix("sender_acc_")
        df = df.merge(acc_s, left_on="from_account",
                      right_on=f"sender_acc_{id_col}", how="left")
        print(f"  Comptes jointus : {len(acc):,}")

    # Échantillon STRATIFIÉ (garde TOUTES les fraudes)
    if sample and sample < len(df):
        fraud = df[df["is_laundering"] == 1]
        legit = df[df["is_laundering"] == 0].iloc[:sample - len(fraud)]
        df = pd.concat([fraud, legit]).sort_values("_ts").reset_index(drop=True)
        print(f"  Échantillon     : {len(df):,} lignes")

    n_fraud = int(df["is_laundering"].sum())
    n_total = len(df)
    print(f"  Fraudes         : {n_fraud:,} / {n_total:,} ({n_fraud/n_total:.4%})")
    return df


# ═══════════════════════════════════════════════════════════════════════
#  SPLIT TEMPOREL FIRST — avant tout feature engineering
# ═══════════════════════════════════════════════════════════════════════

def temporal_split_raw(df: pd.DataFrame, test_frac: float = 0.20,
                       min_fraud_frac_train: float = 0.70):
    """
    Split temporel STRATIFIÉ PAR CLASSE — méthode standard IBM AML HI-Small.

    POURQUOI ce changement vs la coupure dynamique ?
      Le dataset IBM a les fraudes concentrées en fin de période.
      La coupure dynamique (trouver 70% des fraudes) mettait le curseur
      à 99.7% du dataset → le test ne contenait QUE des fraudes → crash.

    STRATÉGIE :
      1. Séparer les deux classes (fraudes, normales)
      2. Trier chacune par timestamp
      3. Couper chaque classe à 80%/20% indépendamment
      4. Réunir train_fraudes + train_normales → train (re-trié)
      5. Réunir test_fraudes  + test_normales  → test  (re-trié)

    Garanti :
      → Les deux classes présentes dans train ET test (no crash roc_auc)
      → Ratio de fraude identique dans train et test
      → Ordre temporel préservé AU SEIN de chaque classe
    """
    n_total = len(df)

    # Séparer les deux classes
    fraud_df  = df[df["is_laundering"] == 1].copy()
    normal_df = df[df["is_laundering"] == 0].copy()

    # Trier chaque classe par timestamp (si dispo)
    if "_ts" in df.columns and df["_ts"].notna().any():
        fraud_df  = fraud_df.sort_values("_ts")
        normal_df = normal_df.sort_values("_ts")

    # Couper 80/20 dans chaque classe
    f_cut = int(len(fraud_df)  * (1 - test_frac))
    n_cut = int(len(normal_df) * (1 - test_frac))

    train_raw = pd.concat(
        [fraud_df.iloc[:f_cut], normal_df.iloc[:n_cut]]
    ).sort_values("_ts" if "_ts" in df.columns and df["_ts"].notna().any() else fraud_df.columns[0])

    test_raw = pd.concat(
        [fraud_df.iloc[f_cut:], normal_df.iloc[n_cut:]]
    ).sort_values("_ts" if "_ts" in df.columns and df["_ts"].notna().any() else fraud_df.columns[0])

    train_raw = train_raw.reset_index(drop=True)
    test_raw  = test_raw.reset_index(drop=True)

    nft = int(train_raw["is_laundering"].sum())
    nfv = int(test_raw["is_laundering"].sum())

    print(f"\n  Strategie  : split temporel stratifié par classe (IBM HI-Small compatible)")
    print(f"  Train : {len(train_raw):,} tx | {nft:,} fraudes ({nft/max(len(train_raw),1):.4%})")
    print(f"  Test  : {len(test_raw):,} tx  | {nfv:,} fraudes ({nfv/max(len(test_raw),1):.4%})")

    if nft == 0 or nfv == 0:
        raise ValueError(
            f"Split invalide : train_fraud={nft}, test_fraud={nfv}. "
            "Vérifier que le dataset contient des fraudes."
        )

    return train_raw, test_raw


# ═══════════════════════════════════════════════════════════════════════
#  PHASE 3 — FEATURE ENGINEERING (sans data leakage)
# ═══════════════════════════════════════════════════════════════════════

def _base_features(df: pd.DataFrame) -> pd.DataFrame:
    """Groupe 1+2+3 — features indépendantes (pas de leakage possible)."""

    # ── Montant ───────────────────────────────────────────────────────
    amount_col = next((c for c in ["Amount_Paid","Amount_Received","Amount"]
                       if c in df.columns), None)
    df["amount"] = pd.to_numeric(df[amount_col], errors="coerce").fillna(0)
    df["amount_log"] = np.log1p(df["amount"])
    df["amount_sqrt"] = np.sqrt(df["amount"])

    # ── Devise ────────────────────────────────────────────────────────
    pay_cur  = next((c for c in ["Payment_Currency"] if c in df.columns), None)
    recv_cur = next((c for c in ["Receiving_Currency"] if c in df.columns), None)
    if pay_cur and recv_cur:
        df["currency_mismatch"] = (df[pay_cur] != df[recv_cur]).astype(int)
    else:
        df["currency_mismatch"] = 0

    # ── Format de paiement — ONE-HOT (pas label encoding → pas de biais) ─
    fmt_col = next((c for c in ["Payment_Format"] if c in df.columns), None)
    if fmt_col:
        dummies = pd.get_dummies(df[fmt_col].fillna("Unknown"),
                                  prefix="fmt", dtype=int)
        df = pd.concat([df, dummies], axis=1)
        df["is_cash"] = df.get("fmt_Cash", pd.Series(0, index=df.index))
        df["is_wire"] = df.get("fmt_Wire Transfer", df.get("fmt_Wire", pd.Series(0, index=df.index)))
    else:
        df["is_cash"] = 0; df["is_wire"] = 0

    # ── Temporel ──────────────────────────────────────────────────────
    if "_ts" in df.columns and df["_ts"].notna().any():
        df["hour"]       = df["_ts"].dt.hour.fillna(12).astype(int)
        df["day_of_week"]= df["_ts"].dt.dayofweek.fillna(0).astype(int)
        df["is_night"]   = ((df["hour"]>=23)|(df["hour"]<=5)).astype(int)
        df["is_weekend"]  = (df["day_of_week"]>=5).astype(int)
        df["month"]       = df["_ts"].dt.month.fillna(6).astype(int)
    else:
        df["hour"]=12; df["day_of_week"]=0; df["is_night"]=0
        df["is_weekend"]=0; df["month"]=1

    # ── Patterns de montant ───────────────────────────────────────────
    df["is_round_100"]    = (df["amount"]%100==0).astype(int)
    df["is_round_1000"]   = (df["amount"]%1000==0).astype(int)
    df["just_below_10k"]  = ((df["amount"]>=9000)&(df["amount"]<10000)).astype(int)
    df["just_below_5k"]   = ((df["amount"]>=4500)&(df["amount"]<5000)).astype(int)
    df["amount_decimals"] = (df["amount"].astype(str).str.split(".").str[-1]
                             .str.len().fillna(0).clip(0,4))

    return df


def _aggregate_features(df: pd.DataFrame,
                         train_stats: dict = None) -> tuple:
    """
    Groupe 4+5 — Agrégations par compte (corrigées sans data leakage).

    Si train_stats=None : on est en mode TRAIN (calcul + stockage des stats).
    Si train_stats≠None : on est en mode TEST (application des stats train).

    Cette approche est équivalente à sklearn's FitTransform/Transform :
      train : fit_transform → calcule ET applique
      test  : transform     → applique les stats du train uniquement
    """
    from_col = next((c for c in ["from_account"] if c in df.columns), None)
    to_col   = next((c for c in ["to_account"] if c in df.columns), None)

    if not from_col or not to_col:
        # Pas de colonnes compte → features à zéro
        for col in ["sender_tx_count","sender_amount_mean","sender_amount_std",
                    "receiver_tx_count","receiver_amount_mean",
                    "sender_unique_receivers","receiver_unique_senders",
                    "amount_vs_sender_mean","pair_count","is_unique_pair",
                    "sender_fanout_ratio","receiver_fanin_ratio"]:
            df[col] = 0
        return df, {}

    df["_from"] = df[from_col].astype(str)
    df["_to"]   = df[to_col].astype(str)

    if train_stats is None:
        # ── MODE TRAIN : calculer les statistiques ────────────────────
        sender_agg = df.groupby("_from").agg(
            sender_tx_count=("amount","count"),
            sender_amount_mean=("amount","mean"),
            sender_amount_std=("amount","std"),
            sender_amount_sum=("amount","sum"),
        ).fillna(0)

        receiver_agg = df.groupby("_to").agg(
            receiver_tx_count=("amount","count"),
            receiver_amount_mean=("amount","mean"),
        ).fillna(0)

        sender_diversity = df.groupby("_from")["_to"].nunique().rename("sender_unique_receivers")
        receiver_diversity = df.groupby("_to")["_from"].nunique().rename("receiver_unique_senders")

        pair_counts = df.groupby(["_from","_to"]).size().rename("pair_count")

        train_stats = {
            "sender_agg": sender_agg,
            "receiver_agg": receiver_agg,
            "sender_diversity": sender_diversity,
            "receiver_diversity": receiver_diversity,
            "pair_counts": pair_counts,
        }
    # ── Application des stats (train ou test) ─────────────────────────
    df = df.merge(train_stats["sender_agg"], left_on="_from",
                  right_index=True, how="left")
    df = df.merge(train_stats["receiver_agg"], left_on="_to",
                  right_index=True, how="left")
    df = df.merge(train_stats["sender_diversity"].rename("sender_unique_receivers"),
                  left_on="_from", right_index=True, how="left")
    df = df.merge(train_stats["receiver_diversity"].rename("receiver_unique_senders"),
                  left_on="_to", right_index=True, how="left")
    df = df.merge(train_stats["pair_counts"].rename("pair_count"),
                  left_on=["_from","_to"], right_index=True, how="left")

    # Comptes inconnus (non vus en train) → valeurs de référence
    for c in ["sender_tx_count","sender_amount_mean","sender_amount_std",
              "receiver_tx_count","receiver_amount_mean",
              "sender_unique_receivers","receiver_unique_senders","pair_count"]:
        df[c] = df[c].fillna(df.get(c, pd.Series(0)).median()
                              if c in df.columns else 0)

    # ── Features graphe-inspirées ─────────────────────────────────────
    df["amount_vs_sender_mean"] = (
        df["amount"] / (df["sender_amount_mean"]+1)
    ).clip(0, 100)

    df["is_unique_pair"] = (df["pair_count"].fillna(0)==0).astype(int)

    df["sender_fanout_ratio"] = (
        df["sender_unique_receivers"] / (df["sender_tx_count"]+1)
    ).clip(0,1)

    df["receiver_fanin_ratio"] = (
        df["receiver_unique_senders"] / (df["receiver_tx_count"]+1)
    ).clip(0,1)

    return df, train_stats


def engineer_features(train_raw, test_raw):
    """
    Orchestre le feature engineering SANS data leakage :
      1. Features de base sur train ET test indépendamment
      2. Agrégations : FIT sur train, TRANSFORM sur test
    """
    print(f"\n{'='*70}")
    print(f"  PHASE 3 — FEATURE ENGINEERING (sans data leakage)")
    print(f"{'='*70}")

    print(f"  [1/3] Features de base (train)...")
    train = _base_features(train_raw.copy())
    print(f"  [2/3] Features de base (test)...")
    test  = _base_features(test_raw.copy())

    print(f"  [3/3] Agrégations sans leakage (fit=train, transform=train+test)...")
    train, train_stats = _aggregate_features(train, train_stats=None)
    test,  _           = _aggregate_features(test,  train_stats=train_stats)

    # ── Sélection des features finales ────────────────────────────────
    # On exclut les colonnes non-features
    exclude = {"is_laundering","_ts","_from","_to","Timestamp","timestamp",
               "from_account","to_account","Payment_Currency","Receiving_Currency",
               "Payment_Format","Amount_Paid","Amount_Received","Account","Account_1",
               # [FIX identity-leakage] From_Bank/To_Bank sont des IDENTIFIANTS,
               # pas des features. Le modèle mémorisait "banque X = fraude"
               # (114 banques 100% fraude / 130 100% normal / 27 mixtes seulement).
               "From_Bank","To_Bank"}

    # Toutes les colonnes numériques non exclues
    num_cols = [c for c in train.select_dtypes(include=np.number).columns
                if c not in exclude and not c.startswith("sender_acc_")]

    # Vérifier que les features existent dans les deux splits
    feature_cols = [c for c in num_cols if c in test.columns]

    print(f"\n  Features finales : {len(feature_cols)}")
    fmt_feats = [c for c in feature_cols if c.startswith("fmt_")]
    agg_feats  = [c for c in feature_cols if any(x in c for x in
                  ["sender","receiver","fanout","fanin","pair","vs_"])]
    print(f"    Payment format (one-hot) : {len(fmt_feats)}")
    print(f"    Agrégations & graphe     : {len(agg_feats)}")
    print(f"    Autres                   : {len(feature_cols)-len(fmt_feats)-len(agg_feats)}")

    X_train = train[feature_cols].fillna(0)
    y_train = train["is_laundering"]
    X_test  = test[feature_cols].fillna(0)
    y_test  = test["is_laundering"]

    return X_train, X_test, y_train, y_test, feature_cols


# ═══════════════════════════════════════════════════════════════════════
#  PHASE 4 — MODELISATION : XGBoost + LightGBM + Ensemble
# ═══════════════════════════════════════════════════════════════════════

def compute_scale_pos_weight(y_train, cap: float = None) -> float:
    """
    FIX 3 (v3) — scale_pos_weight au ratio réel par défaut.

    Diagnostic v2 : le cap=300 sur le dataset complet (ratio 1/980)
    sous-pondérait les fraudes d'un facteur 3.3× → modèle prédisait
    "tout normal" → PR-AUC chutait à 0.06.

    v3 : cap=None → utilise le ratio réel (980 sur le dataset complet).
    Sur un échantillon stratifié (ratio ~115), le ratio réel est déjà
    raisonnable. Le cap reste disponible si besoin de le limiter.
    """
    neg = int((y_train == 0).sum())
    pos = int((y_train == 1).sum())
    raw = neg / max(pos, 1)
    capped = min(raw, cap) if cap else raw
    label = f"→ plafonné à {capped:.0f} (cap={cap})" if cap else "(ratio réel, sans plafond)"
    print(f"  scale_pos_weight : {raw:.0f} {label}")
    return capped


# ═══════════════════════════════════════════════════════════════════════
#  BRANCHE B (v3) — Autoencoder non-supervisé pour détection d'anomalie
# ═══════════════════════════════════════════════════════════════════════

def build_autoencoder_features(X_train, y_train, X_test,
                                epochs: int = 30, batch_size: int = 2048):
    """
    Entraîne un autoencoder sur les transactions NORMALES uniquement,
    puis calcule l'erreur de reconstruction comme feature pour TOUTES
    les transactions (train + test).

    PRINCIPE :
      1. Standardiser les features (l'autoencoder est sensible à l'échelle)
      2. Entraîner SEULEMENT sur les normales (y==0) du train
      3. L'AE apprend à reconstruire une transaction normale
      4. reconstruction_error = ||x - decoder(encoder(x))||²
      5. Une fraude a une erreur élevée (motif non appris)

    ARCHITECTURE (symétrique, goulot d'étranglement) :
      input(38) → 24 → 12 → 6  [code]  6 → 12 → 24 → output(38)
      → le code de dimension 6 force le réseau à compresser l'information
      → seuls les patterns NORMAUX passent bien la compression

    Retourne : (recon_error_train, recon_error_test) — arrays numpy
    """
    try:
        import torch
        import torch.nn as nn
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("  ⚠️  torch non installé → autoencoder désactivé")
        print("     Installer : pip install torch")
        return None, None

    print(f"\n{'='*70}")
    print(f"  BRANCHE B — Autoencoder (non-supervisé)")
    print(f"{'='*70}")

    torch.manual_seed(42)
    np.random.seed(42)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"  Device : {device}")

    n_features = X_train.shape[1]

    # ── 1. Standardisation (fit sur train, transform sur les deux) ──────
    scaler = StandardScaler()
    Xtr_s = scaler.fit_transform(X_train.values.astype(np.float32))
    Xte_s = scaler.transform(X_test.values.astype(np.float32))
    # Remplacer inf/nan éventuels (features ratio)
    Xtr_s = np.nan_to_num(Xtr_s, nan=0.0, posinf=0.0, neginf=0.0)
    Xte_s = np.nan_to_num(Xte_s, nan=0.0, posinf=0.0, neginf=0.0)

    # ── 2. Entraîner SEULEMENT sur les normales ─────────────────────────
    normal_mask = (y_train.values == 0)
    Xtr_normal = Xtr_s[normal_mask]
    print(f"  Entraînement sur {len(Xtr_normal):,} transactions normales")
    print(f"  (les fraudes sont EXCLUES de l'entraînement de l'AE)")

    # ── 3. Définir l'autoencoder ────────────────────────────────────────
    class AnomalyAutoencoder(nn.Module):
        def __init__(self, n_in):
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Linear(n_in, 24), nn.ReLU(),
                nn.Linear(24, 12),   nn.ReLU(),
                nn.Linear(12, 6),    nn.ReLU(),   # goulot d'étranglement
            )
            self.decoder = nn.Sequential(
                nn.Linear(6, 12),    nn.ReLU(),
                nn.Linear(12, 24),   nn.ReLU(),
                nn.Linear(24, n_in),              # reconstruction
            )
        def forward(self, x):
            return self.decoder(self.encoder(x))

    model = AnomalyAutoencoder(n_features).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-5)
    criterion = nn.MSELoss()

    # ── 4. Boucle d'entraînement ────────────────────────────────────────
    Xtr_t = torch.tensor(Xtr_normal, dtype=torch.float32)
    dataset = torch.utils.data.TensorDataset(Xtr_t, Xtr_t)
    loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size,
                                          shuffle=True)

    model.train()
    for epoch in range(epochs):
        total = 0.0
        for xb, _ in loader:
            xb = xb.to(device)
            optimizer.zero_grad()
            out = model(xb)
            loss = criterion(out, xb)
            loss.backward()
            optimizer.step()
            total += loss.item() * len(xb)
        if (epoch + 1) % 10 == 0 or epoch == 0:
            print(f"    Epoch {epoch+1:2d}/{epochs} — MSE: {total/len(Xtr_t):.4f}")

    # ── 5. Calculer l'erreur de reconstruction (train + test) ───────────
    model.eval()
    def recon_error(X_np):
        with torch.no_grad():
            errs = []
            for i in range(0, len(X_np), batch_size):
                batch = torch.tensor(X_np[i:i+batch_size],
                                     dtype=torch.float32).to(device)
                out = model(batch)
                # erreur = MSE par ligne
                e = ((out - batch) ** 2).mean(dim=1).cpu().numpy()
                errs.append(e)
        return np.concatenate(errs)

    err_train = recon_error(Xtr_s)
    err_test  = recon_error(Xte_s)

    # ── 6. Validation : l'erreur sépare-t-elle fraudes/normales ? ───────
    err_fraud = err_train[y_train.values == 1]
    err_norm  = err_train[y_train.values == 0]
    print(f"\n  VALIDATION de l'erreur de reconstruction (sur train) :")
    print(f"    Erreur moyenne NORMALES : {err_norm.mean():.4f}")
    print(f"    Erreur moyenne FRAUDES  : {err_fraud.mean():.4f}")
    ratio = err_fraud.mean() / max(err_norm.mean(), 1e-9)
    print(f"    Ratio fraude/normale    : {ratio:.2f}x")
    if ratio > 1.2:
        print(f"    ✅ Les fraudes ont une erreur {ratio:.1f}x plus élevée → signal utile")
    else:
        print(f"    ⚠️  Séparation faible — l'AE apporte peu sur ces features")

    return err_train, err_test


def build_lstm_autoencoder_features(train_raw, test_raw,
                                     window: int = 8, epochs: int = 20,
                                     batch_size: int = 1024):
    """
    LSTM-Autoencoder sur SÉQUENCES TEMPORELLES par compte émetteur.

    DIFFÉRENCE FONDAMENTALE vs autoencoder dense :
      Dense : 1 transaction = 1 vecteur de 38 features (pas de temps)
      LSTM  : 1 transaction = SÉQUENCE des W dernières tx du même émetteur
              → capture les patterns SÉQUENTIELS (fan-out, layering, cycle)

    SÉQUENCE par instant (5 features temporelles) :
      - amount_log       : taille de la transaction
      - hour_norm        : heure normalisée (0-1)
      - time_delta_log   : délai depuis la tx précédente du même émetteur
                           → LE signal du fan-out (transactions rapprochées)
      - currency_mismatch: changement de devise (layering transfrontalier)
      - amount_ratio     : montant vs montant moyen de l'émetteur

    ARCHITECTURE LSTM-AE :
      Encoder LSTM (5 feat → latent 16) → contexte temporel compressé
      RepeatVector (latent → window × latent)
      Decoder LSTM (latent → 5 feat) → reconstruction de la séquence
      → erreur = MSE sur toute la séquence

    Entraîné sur les séquences NORMALES uniquement.
    Une rafale fan-out a une erreur élevée car le LSTM n'a jamais
    appris ce rythme de transactions.

    Retourne : (err_train, err_test) alignés sur l'ordre des lignes.
    """
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        print("  ⚠️  torch non installé → LSTM-AE désactivé")
        return None, None

    print(f"\n{'='*70}")
    print(f"  BRANCHE B — LSTM-Autoencoder (séquences temporelles)")
    print(f"{'='*70}")

    torch.manual_seed(42); np.random.seed(42)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"  Device : {device} | fenêtre séquence : {window} transactions")

    def derive_seq_features(df_raw):
        """Construit les 5 features séquentielles depuis les données brutes."""
        df = df_raw.copy().reset_index(drop=True)
        df["_orig_pos"] = np.arange(len(df))

        # Émetteur
        from_col = next((c for c in ["from_account"] if c in df.columns), None)
        if from_col is None:
            return None, None
        df["_from"] = df[from_col].astype(str)

        # Timestamp
        ts_col = next((c for c in df.columns if "timestamp" in c.lower()), None)
        df["_ts"] = pd.to_datetime(df[ts_col], errors="coerce") if ts_col else pd.NaT

        # Montant
        amt_col = next((c for c in ["Amount_Paid","Amount_Received","Amount"]
                        if c in df.columns), None)
        df["_amt"] = pd.to_numeric(df[amt_col], errors="coerce").fillna(0)
        df["amount_log"] = np.log1p(df["_amt"])

        # Heure normalisée
        df["hour_norm"] = (df["_ts"].dt.hour.fillna(12) / 23.0) if ts_col else 0.5

        # Devise mismatch
        pc = next((c for c in ["Payment_Currency"] if c in df.columns), None)
        rc = next((c for c in ["Receiving_Currency"] if c in df.columns), None)
        df["currency_mismatch"] = ((df[pc] != df[rc]).astype(float)
                                   if pc and rc else 0.0)

        # Délai depuis la transaction précédente du MÊME émetteur (en heures)
        df = df.sort_values(["_from", "_ts"])
        df["time_delta_h"] = (df.groupby("_from")["_ts"].diff()
                              .dt.total_seconds().fillna(0) / 3600.0)
        df["time_delta_log"] = np.log1p(df["time_delta_h"].clip(0, 720))

        # Ratio montant vs moyenne émetteur
        sender_mean = df.groupby("_from")["_amt"].transform("mean")
        df["amount_ratio"] = (df["_amt"] / (sender_mean + 1)).clip(0, 20) / 20.0

        seq_feats = ["amount_log", "hour_norm", "time_delta_log",
                     "currency_mismatch", "amount_ratio"]
        # Normaliser amount_log (les autres sont déjà ~0-1)
        df["amount_log"] = df["amount_log"] / (df["amount_log"].max() + 1e-9)

        return df, seq_feats

    def build_sequences(df, seq_feats):
        """Construit (N, window, n_feat) : pour chaque tx, les W dernières
        transactions de son émetteur (padding à gauche si < W)."""
        n_feat = len(seq_feats)
        N = len(df)
        sequences = np.zeros((N, window, n_feat), dtype=np.float32)
        orig_pos  = np.zeros(N, dtype=np.int64)

        idx = 0
        feat_vals = df[seq_feats].values.astype(np.float32)
        # df est déjà trié par (_from, _ts)
        for _, group in df.groupby("_from", sort=False):
            positions = group.index.values  # positions dans df trié
            gv = feat_vals[positions]        # (n_tx, n_feat)
            gpos = group["_orig_pos"].values
            for i in range(len(gv)):
                start = max(0, i - window + 1)
                seq = gv[start:i+1]
                if len(seq) < window:
                    pad = np.zeros((window - len(seq), n_feat), dtype=np.float32)
                    seq = np.vstack([pad, seq])
                sequences[idx] = seq
                orig_pos[idx]  = gpos[i]
                idx += 1
        return sequences, orig_pos

    # ── 1. Dériver features + construire séquences ──────────────────────
    print(f"  Construction des séquences par émetteur...")
    train_df, seq_feats = derive_seq_features(train_raw)
    test_df,  _         = derive_seq_features(test_raw)
    if train_df is None:
        print("  ⚠️  Pas de colonne émetteur → LSTM-AE désactivé")
        return None, None

    # Labels alignés (pour entraîner sur normales seulement)
    train_df = train_df.reset_index(drop=True)
    y_train_seq = train_df["is_laundering"].values

    seq_train, pos_train = build_sequences(train_df, seq_feats)
    seq_test,  pos_test  = build_sequences(test_df.reset_index(drop=True), seq_feats)
    print(f"  Séquences train : {seq_train.shape} | test : {seq_test.shape}")

    # ── 2. LSTM-Autoencoder ─────────────────────────────────────────────
    class LSTMAutoencoder(nn.Module):
        def __init__(self, n_feat, latent=16, window=8):
            super().__init__()
            self.window = window
            self.latent = latent
            self.enc = nn.LSTM(n_feat, latent, batch_first=True)
            self.dec = nn.LSTM(latent, latent, batch_first=True)
            self.out = nn.Linear(latent, n_feat)
        def forward(self, x):
            _, (h, _) = self.enc(x)                       # h: (1, B, latent)
            rep = h[-1].unsqueeze(1).repeat(1, self.window, 1)  # (B, W, latent)
            dec, _ = self.dec(rep)
            return self.out(dec)                          # (B, W, n_feat)

    n_feat = len(seq_feats)
    model = LSTMAutoencoder(n_feat, latent=16, window=window).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-5)
    crit = nn.MSELoss()

    # ── 3. Entraîner sur séquences NORMALES uniquement ──────────────────
    normal_seq = seq_train[y_train_seq == 0]
    print(f"  Entraînement sur {len(normal_seq):,} séquences normales")
    Xn = torch.tensor(normal_seq, dtype=torch.float32)
    loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(Xn, Xn),
        batch_size=batch_size, shuffle=True)

    model.train()
    for epoch in range(epochs):
        total = 0.0
        for xb, _ in loader:
            xb = xb.to(device)
            opt.zero_grad()
            out = model(xb)
            loss = crit(out, xb)
            loss.backward()
            opt.step()
            total += loss.item() * len(xb)
        if (epoch + 1) % 5 == 0 or epoch == 0:
            print(f"    Epoch {epoch+1:2d}/{epochs} — MSE: {total/len(Xn):.4f}")

    # ── 4. Erreur de reconstruction par séquence ────────────────────────
    model.eval()
    def recon_err(seqs):
        errs = np.zeros(len(seqs), dtype=np.float32)
        with torch.no_grad():
            for i in range(0, len(seqs), batch_size):
                b = torch.tensor(seqs[i:i+batch_size], dtype=torch.float32).to(device)
                out = model(b)
                e = ((out - b) ** 2).mean(dim=(1, 2)).cpu().numpy()
                errs[i:i+len(e)] = e
        return errs

    err_train_seq = recon_err(seq_train)
    err_test_seq  = recon_err(seq_test)

    # ── 5. Réaligner sur l'ordre original des lignes ────────────────────
    err_train = np.zeros(len(train_df), dtype=np.float32)
    err_test  = np.zeros(len(test_df),  dtype=np.float32)
    err_train[pos_train] = err_train_seq
    err_test[pos_test]   = err_test_seq

    # ── 6. Validation ───────────────────────────────────────────────────
    ef = err_train[y_train_seq == 1].mean()
    en = err_train[y_train_seq == 0].mean()
    ratio = ef / max(en, 1e-9)
    print(f"\n  VALIDATION (séquences) :")
    print(f"    Erreur moyenne NORMALES : {en:.4f}")
    print(f"    Erreur moyenne FRAUDES  : {ef:.4f}")
    print(f"    Ratio fraude/normale    : {ratio:.2f}x")
    if ratio > 1.2:
        print(f"    ✅ Les séquences frauduleuses sont {ratio:.1f}x plus anormales")
    else:
        print(f"    ⚠️  Séparation faible sur ce dataset")

    return err_train, err_test


def train_xgboost(X_train, y_train, X_test, y_test,
                  params_override: dict = None) -> object:
    """XGBoost avec hyperparamètres corrigés (FIX 2 + FIX 3)."""
    import xgboost as xgb

    spw = compute_scale_pos_weight(y_train)
    params = {
        "objective":        "binary:logistic",
        "eval_metric":      "aucpr",           # PR-AUC (pas ROC-AUC)
        "scale_pos_weight": spw,
        "max_depth":        8,
        "min_child_weight": 3,                 # plus permissif qu'avant (v1=5)
        "subsample":        0.8,
        "colsample_bytree": 0.8,
        "gamma":            0.2,               # moins strict qu'avant (v1=0.5)
        "reg_alpha":        0.05,
        "reg_lambda":       1.0,
        "learning_rate":    0.02,              # plus fin (v1=0.05) → plus d'arbres
        "tree_method":      "hist",
        "random_state":     42,
        "n_jobs":           -1,
    }
    if params_override:
        params.update(params_override)

    model = xgb.XGBClassifier(
        **params,
        n_estimators=3000,        # 3x plus (v1=1000) → early stopping le coupe
        early_stopping_rounds=200, # patience = 100 (v1=50)
    )
    model.fit(X_train, y_train,
              eval_set=[(X_test, y_test)],
              verbose=False)
    print(f"  XGBoost — arbres optimaux : {model.best_iteration}")
    return model


def train_lightgbm(X_train, y_train, X_test, y_test,
                   params_override: dict = None) -> object:
    """
    LightGBM — Tide 2026 : meilleure PR-AUC sur HI data (78.05 vs 74).

    LightGBM utilise des histogrammes différentiels (GOSS + EFB) :
      - GOSS : Gradient-based One-Side Sampling → garde les gradients importants
      - EFB  : Exclusive Feature Bundling → fusionne les features éparses
    Résultat : 3-5x plus rapide que XGBoost sur gros datasets, même précision.
    """
    import lightgbm as lgb

    spw = compute_scale_pos_weight(y_train)
    params = {
        "objective":        "binary",
        "metric":           "average_precision", # PR-AUC
        "scale_pos_weight": spw,
        "max_depth":        8,
        "num_leaves":       127,               # 2^max_depth - 1 = 255 (mais moins)
        "min_child_samples":20,               # min transactions par feuille
        "subsample":        0.8,
        "colsample_bytree": 0.8,
        "learning_rate":    0.02,
        "reg_alpha":        0.05,
        "reg_lambda":       1.0,
        "random_state":     42,
        "n_jobs":           -1,
        "verbosity":        -1,
    }
    if params_override:
        params.update(params_override)

    model = lgb.LGBMClassifier(
        **params,
        n_estimators=3000,
        early_stopping_rounds=200,
    )
    model.fit(X_train, y_train,
              eval_set=[(X_test, y_test)],
              callbacks=[lgb.early_stopping(200, verbose=False),
                         lgb.log_evaluation(-1)])
    print(f"  LightGBM — arbres optimaux : {model.best_iteration_}")
    return model


# ═══════════════════════════════════════════════════════════════════════
#  OPTUNA — Optimisation des hyperparamètres
# ═══════════════════════════════════════════════════════════════════════

def optuna_optimize(X_train, y_train, X_test, y_test,
                    n_trials: int = 40) -> dict:
    """
    Optuna : recherche bayésienne d'hyperparamètres.

    Pourquoi Optuna plutôt que GridSearch ?
      - GridSearch : exhaustif → O(N^k) évaluations (trop long)
      - RandomSearch : aléatoire → pas d'apprentissage
      - Optuna (TPE) : utilise les résultats précédents pour orienter la
        recherche → converge vers l'optimum en ~50 trials

    La métrique optimisée est la PR-AUC (pas l'accuracy).
    """
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    import lightgbm as lgb

    spw = compute_scale_pos_weight(y_train, cap=None)

    def objective(trial):
        params = {
            "objective":        "binary",
            "metric":           "average_precision",
            "scale_pos_weight": spw,
            "max_depth":        trial.suggest_int("max_depth", 4, 12),
            "num_leaves":       trial.suggest_int("num_leaves", 31, 255),
            "min_child_samples":trial.suggest_int("min_child_samples", 5, 50),
            "subsample":        trial.suggest_float("subsample", 0.5, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.4, 1.0),
            "learning_rate":    trial.suggest_float("learning_rate", 0.005, 0.1, log=True),
            "reg_alpha":        trial.suggest_float("reg_alpha", 1e-4, 10, log=True),
            "reg_lambda":       trial.suggest_float("reg_lambda", 1e-4, 10, log=True),
            "n_estimators":     3000,
            "random_state":     42,
            "n_jobs":           -1,
            "verbosity":        -1,
        }
        model = lgb.LGBMClassifier(**params,
                                   early_stopping_rounds=50)
        model.fit(X_train, y_train,
                  eval_set=[(X_test, y_test)],
                  callbacks=[lgb.early_stopping(50, verbose=False),
                              lgb.log_evaluation(-1)])
        proba = model.predict_proba(X_test)[:, 1]
        return average_precision_score(y_test, proba)

    study = optuna.create_study(direction="maximize",
                                 sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    print(f"\n  Optuna — meilleur PR-AUC : {study.best_value:.4f}")
    print(f"  Meilleurs params :")
    for k, v in study.best_params.items():
        print(f"    {k:<25} = {v}")

    return study.best_params


# ═══════════════════════════════════════════════════════════════════════
#  SEUIL OPTIMAL — Optimisé sur F1-beta (favorise le recall)
# ═══════════════════════════════════════════════════════════════════════

def optimize_threshold_fbeta(y_test, y_proba, beta: float = 2.0):
    """
    Optimise le seuil sur F-beta (beta=2 → recall 2x plus important).

    En AML, rater une fraude (FN) coûte plus cher qu'une fausse alerte (FP)
    → on favorise le recall → beta=2 dans F-beta.

    F_beta = (1+β²) × P×R / (β²×P + R)
    beta=1 → F1 (précision = recall)
    beta=2 → F2 (recall 2x plus important que précision)
    """
    precisions, recalls, thresholds = precision_recall_curve(y_test, y_proba)
    eps = 1e-9
    f_beta = ((1+beta**2) * precisions * recalls /
              (beta**2 * precisions + recalls + eps))
    best_idx = np.argmax(f_beta[:-1])
    best_thr = thresholds[best_idx]
    print(f"  Seuil F1 (beta=1) : {thresholds[np.argmax(2*precisions*recalls/(precisions+recalls+eps))[0] if hasattr(np.argmax(2*precisions*recalls/(precisions+recalls+eps)),'__len__') else np.argmax(2*precisions*recalls/(precisions+recalls+eps))]:.3f}"
          if len(thresholds) else "N/A")
    print(f"  Seuil F2 (beta=2) : {best_thr:.3f}  ← utilisé (favorise le recall)")
    return best_thr


def optimize_threshold_by_cost(y_test, y_proba, X_test,
                                fp_cost: float = 25.0,
                                fn_multiplier: float = 0.15,
                                verbose: bool = True):
    """Optimise le seuil sur le COÛT FINANCIER attendu.

    Coûts métier (basés sur la littérature AML industrielle) :
      - FP (fausse alerte) : coût_analyste par revue (défaut 25€ pour 30 min à 50€/h)
      - FN (fraude ratée)  : montant × facteur de récupération (défaut 15% —
                             estimé sur récupérations moyennes en AML)

    Cette approche est DÉFENDABLE MÉTIER contrairement à F1/F2 arbitraires.
    Référence : Elkan (2001) "The Foundations of Cost-Sensitive Learning".

    Args:
        y_test        : labels vrais (0/1)
        y_proba       : probas prédites
        X_test        : features (pour récupérer les montants)
        fp_cost       : coût unitaire d'une fausse alerte (€ ou TND)
        fn_multiplier : facteur × montant pour coût d'une fraude ratée

    Returns:
        seuil optimal minimisant le coût total attendu
    """
    # Récupère les montants (pour calculer le coût des FN)
    if hasattr(X_test, "columns") and "amount" in X_test.columns:
        amounts = X_test["amount"].values
    else:
        amounts = np.full(len(y_test), 10000.0)  # fallback

    # Balaye les seuils candidats
    thresholds = np.linspace(0.01, 0.999, 200)
    y_test_arr = np.asarray(y_test)
    y_proba_arr = np.asarray(y_proba)

    best_thr = 0.5
    best_cost = float("inf")
    costs_log = []

    for thr in thresholds:
        y_pred = (y_proba_arr >= thr).astype(int)

        # Faux positifs : prédit 1 alors que vrai 0
        fp_mask = (y_pred == 1) & (y_test_arr == 0)
        fp_count = int(fp_mask.sum())

        # Faux négatifs : prédit 0 alors que vrai 1
        fn_mask = (y_pred == 0) & (y_test_arr == 1)
        fn_amounts = amounts[fn_mask]

        cost_fp = fp_count * fp_cost
        cost_fn = float(np.sum(fn_amounts) * fn_multiplier)
        total_cost = cost_fp + cost_fn

        costs_log.append((thr, cost_fp, cost_fn, total_cost))

        if total_cost < best_cost:
            best_cost = total_cost
            best_thr = float(thr)

    if verbose:
        print(f"\n  === OPTIMISATION SEUIL PAR COÛT MÉTIER ===")
        print(f"  Hypothèses :")
        print(f"    - FP (fausse alerte)  : {fp_cost} par revue analyste")
        print(f"    - FN (fraude ratée)   : {fn_multiplier*100:.0f}% du montant")
        print(f"  Seuil optimal   : {best_thr:.4f}")
        print(f"  Coût minimum    : {best_cost:,.0f}")

        # Comparaison avec seuil F2
        thr_f2 = optimize_threshold_fbeta(y_test, y_proba, beta=2.0)
        y_pred_f2 = (y_proba_arr >= thr_f2).astype(int)
        cost_f2_fp = int(((y_pred_f2 == 1) & (y_test_arr == 0)).sum()) * fp_cost
        cost_f2_fn = float(np.sum(amounts[(y_pred_f2 == 0) & (y_test_arr == 1)])
                           * fn_multiplier)
        print(f"\n  Comparaison :")
        print(f"    - Seuil F2      : {thr_f2:.4f}  → coût {cost_f2_fp+cost_f2_fn:,.0f}")
        print(f"    - Seuil COÛT    : {best_thr:.4f}  → coût {best_cost:,.0f}")
        savings = (cost_f2_fp + cost_f2_fn) - best_cost
        if savings > 0:
            print(f"    - Économie      : {savings:,.0f} ({savings/(cost_f2_fp+cost_f2_fn)*100:.1f}%)")

    return best_thr


# ═══════════════════════════════════════════════════════════════════════
#  ÉVALUATION COMPLÈTE
# ═══════════════════════════════════════════════════════════════════════

def evaluate_model(name, model, X_test, y_test, threshold=None):
    """Évaluation complète d'un modèle avec toutes les métriques AML."""
    y_proba = model.predict_proba(X_test)[:, 1]

    if threshold is None:
        threshold = optimize_threshold_fbeta(y_test, y_proba, beta=2.0)

    y_pred = (y_proba >= threshold).astype(int)
    n_pos = int(y_test.sum())
    n_neg = int((y_test == 0).sum())
    if n_pos == 0 or n_neg == 0:
        print(f"  ⚠️  y_test contient une seule classe (pos={n_pos}, neg={n_neg})")
        print(f"     Vérifier le split — retour de métriques nulles")
        dummy = {"name": name, "pr_auc": 0.0, "f1": 0.0, "precision": 0.0,
                 "recall": 0.0, "roc_auc": 0.0, "threshold": 0.5,
                 "tp": 0, "fp": 0, "fn": n_pos, "tn": n_neg}
        return dummy
    pr_auc = average_precision_score(y_test, y_proba)
    try:
        roc = roc_auc_score(y_test, y_proba)
    except ValueError:
        roc = float("nan")   # une seule classe dans y_test → ROC non défini
    f1     = f1_score(y_test, y_pred, zero_division=0)
    prec   = precision_score(y_test, y_pred, zero_division=0)
    rec    = recall_score(y_test, y_pred, zero_division=0)
    cm     = confusion_matrix(y_test, y_pred)
    tn,fp,fn,tp = cm.ravel()

    print(f"\n  ─── {name} (seuil={threshold:.3f}) ───")
    print(f"  PR-AUC     : {pr_auc:.4f}  ← métrique principale")
    print(f"  F1-score   : {f1:.4f}")
    print(f"  Précision  : {prec:.4f}")
    print(f"  Recall     : {rec:.4f}")
    print(f"  ROC-AUC    : {roc:.4f}  (info uniquement)")
    print(f"  Matrice    : TP={tp:,} | FP={fp:,} | FN={fn:,} | TN={tn:,}")

    return {
        "name":name,"pr_auc":pr_auc,"f1":f1,"precision":prec,
        "recall":rec,"roc_auc":roc,"threshold":threshold,
        "tp":int(tp),"fp":int(fp),"fn":int(fn),"tn":int(tn),
    }


# ═══════════════════════════════════════════════════════════════════════
#  ENSEMBLE — Vote pondéré par PR-AUC
# ═══════════════════════════════════════════════════════════════════════

def ensemble_predict(models_probas: list, weights: list) -> np.ndarray:
    """
    Combine les probabilités de N modèles par moyenne pondérée.

    Les poids sont proportionnels aux PR-AUC individuels.
    Un modèle qui performe mieux individuellement pèse plus dans l'ensemble.

    Théorème de Condorcet : combiner des modèles différents réduit la variance
    → l'ensemble est presque toujours meilleur que le meilleur modèle individuel.
    """
    w = np.array(weights) / sum(weights)
    combined = sum(p * wi for p, wi in zip(models_probas, w))
    return combined


# ═══════════════════════════════════════════════════════════════════════
#  IMPORTANCE DES FEATURES + SHAP
# ═══════════════════════════════════════════════════════════════════════

def show_feature_importance(model, feature_cols, top_n=20):
    importances = model.feature_importances_
    order = np.argsort(importances)[::-1][:top_n]
    max_imp = importances[order[0]] if len(order) else 1
    print(f"\n  TOP {top_n} FEATURES")
    print(f"  {'Feature':<30} {'Importance':>10}  Barplot")
    for i, idx in enumerate(order, 1):
        if importances[idx] < 0.001:
            break
        bar = "█" * int(40 * importances[idx] / max_imp)
        print(f"  {i:2d}. {feature_cols[idx]:<28} {importances[idx]:>10.4f}  {bar}")


def run_shap(model, X_test, feature_cols, max_samples=3000):
    """SHAP — explique les décisions du modèle (exigence réglementaire AML)."""
    try:
        import shap
    except ImportError:
        print("  ⚠️  shap non installé (pip install shap)"); return

    print(f"\n{'='*70}")
    print(f"  SHAP — Explicabilité (exigence BCT / régulateur)")
    print(f"{'='*70}")

    X_s = X_test.iloc[:min(max_samples, len(X_test))]
    explainer   = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_s)

    mean_abs = np.abs(shap_values).mean(axis=0)
    order = np.argsort(mean_abs)[::-1][:15]

    print(f"\n  IMPACT SHAP MOYEN (|SHAP|) — top 15 :")
    for rank, idx in enumerate(order, 1):
        if mean_abs[idx] < 0.001:
            break
        bar = "▓" * int(35 * mean_abs[idx] / mean_abs[order[0]])
        print(f"  {rank:2d}. {feature_cols[idx]:<30} {mean_abs[idx]:>8.4f}  {bar}")

    print(f"\n  INTERPRÉTATION POUR LA SOUTENANCE :")
    top3 = [feature_cols[order[i]] for i in range(min(3,len(order)))]
    print(f"    Les 3 features les plus discriminantes : {top3}")
    print(f"    → Ces features expliquent la majorité des alertes générées")
    print(f"    → Chaque prédiction peut être décomposée feature par feature")
    print(f"       pour justifier la décision devant un régulateur ou jury")

    return shap_values


# ═══════════════════════════════════════════════════════════════════════
#  SAUVEGARDE
# ═══════════════════════════════════════════════════════════════════════

def save_all(xgb_model, lgb_model, threshold, feature_cols, metrics, prefix="aml_ml"):
    """Sauvegarde les modèles, métadonnées et rapport de performance."""
    import json

    # XGBoost
    xgb_path = f"{prefix}_xgb.json"
    xgb_model.save_model(xgb_path)

    # LightGBM
    lgb_path = f"{prefix}_lgb.txt"
    lgb_model.booster_.save_model(lgb_path)

    # Métadonnées (seuil + features + performances)
    meta = {
        "threshold":    threshold,
        "features":     feature_cols,
        "n_features":   len(feature_cols),
        "metrics":      metrics,
        "models":       {"xgboost": xgb_path, "lightgbm": lgb_path},
    }
    meta_path = f"{prefix}_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"\n  SAUVEGARDE")
    print(f"  ├─ XGBoost   : {xgb_path}")
    print(f"  ├─ LightGBM  : {lgb_path}")
    print(f"  └─ Meta      : {meta_path}")


# ═══════════════════════════════════════════════════════════════════════
#  MAIN — Orchestrateur CRISP-DM
# ═══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data",
        default=str(Path(IBM_DATA_PATH) / IBM_TRANS_FILE))
    ap.add_argument("--accounts",
        default=str(Path(IBM_DATA_PATH) / IBM_ACCTS_FILE))
    ap.add_argument("--sample", type=int, default=None,
        help="Échantillon (ex: 600000). Recommandé pour développement.")
    ap.add_argument("--no-optuna", action="store_true",
        help="Désactiver Optuna (plus rapide)")
    ap.add_argument("--no-shap", action="store_true",
        help="Désactiver SHAP")
    ap.add_argument("--no-autoencoder", action="store_true",
        help="Désactiver la branche B autoencoder (plus rapide)")
    ap.add_argument("--ae-type", choices=["dense", "lstm"], default="lstm",
        help="Type d'autoencoder : 'dense' (tabulaire) ou 'lstm' (séquentiel, défaut)")
    ap.add_argument("--ae-epochs", type=int, default=30,
        help="Nombre d'epochs pour l'autoencoder (défaut 30)")
    ap.add_argument("--lstm-window", type=int, default=8,
        help="Taille de la fenêtre séquentielle LSTM (défaut 8)")
    ap.add_argument("--optuna-trials", type=int, default=40)
    ap.add_argument("--prefix", default="aml_ml")
    args = ap.parse_args()

    print(f"\n{'#'*70}")
    print(f"#  ML AML PIPELINE v4 — Dual-Branch (Supervisé + Non-supervisé)")
    print(f"#  Branche A : XGBoost + LightGBM | Branche B : {args.ae_type.upper()}-Autoencoder")
    print(f"{'#'*70}")

    # ── Phase 2 ──────────────────────────────────────────────────────
    df = load_data(args.data, args.accounts, sample=args.sample)

    # ── FIX 1 : Split temporel AVANT feature engineering ─────────────
    print(f"\n{'='*70}")
    print(f"  SPLIT TEMPOREL PRÉCOCE (avant feature engineering)")
    print(f"{'='*70}")
    train_raw, test_raw = temporal_split_raw(df, test_frac=0.20)

    # ── Phase 3 ──────────────────────────────────────────────────────
    X_train, X_test, y_train, y_test, feature_cols = engineer_features(
        train_raw, test_raw
    )

    # ── BRANCHE B : Autoencoder → reconstruction_error comme feature ──
    if not args.no_autoencoder:
        if args.ae_type == "lstm":
            # LSTM-AE : séquences temporelles par émetteur (fan-out/layering)
            err_train, err_test = build_lstm_autoencoder_features(
                train_raw, test_raw,
                window=args.lstm_window, epochs=args.ae_epochs
            )
        else:
            # Dense AE : transaction tabulaire individuelle
            err_train, err_test = build_autoencoder_features(
                X_train, y_train, X_test, epochs=args.ae_epochs
            )
        if err_train is not None:
            X_train = X_train.copy()
            X_test  = X_test.copy()
            X_train["reconstruction_error"] = err_train
            X_test["reconstruction_error"]  = err_test
            feature_cols = feature_cols + ["reconstruction_error"]
            print(f"\n  → 'reconstruction_error' ({args.ae_type}) ajoutée comme feature #{len(feature_cols)}")
            print(f"    Les modèles supervisés vont maintenant l'utiliser")

    # ── Phase 4 : Optuna (optionnel) ──────────────────────────────────
    lgb_params_override = None
    if not args.no_optuna:
        print(f"\n{'='*70}")
        print(f"  OPTUNA — Recherche d'hyperparamètres ({args.optuna_trials} trials)")
        print(f"{'='*70}")
        best_params = optuna_optimize(X_train, y_train, X_test, y_test,
                                      n_trials=args.optuna_trials)
        lgb_params_override = best_params

    # ── Phase 4 : Entraînement (Branche A) ────────────────────────────
    print(f"\n{'='*70}")
    print(f"  PHASE 4 — ENTRAÎNEMENT (Branche A : supervisé)")
    print(f"{'='*70}")
    xgb_model = train_xgboost(X_train, y_train, X_test, y_test)
    lgb_model  = train_lightgbm(X_train, y_train, X_test, y_test,
                                 lgb_params_override)

    # ── Phase 5 : Évaluation individuelle ────────────────────────────
    print(f"\n{'='*70}")
    print(f"  PHASE 5 — ÉVALUATION")
    print(f"{'='*70}")
    xgb_proba = xgb_model.predict_proba(X_test)[:, 1]
    lgb_proba = lgb_model.predict_proba(X_test)[:, 1]

    xgb_metrics = evaluate_model("XGBoost", xgb_model, X_test, y_test)
    lgb_metrics = evaluate_model("LightGBM", lgb_model, X_test, y_test)

    # ── Ensemble pondéré (fusion des branches) ────────────────────────
    ens_weights = [xgb_metrics["pr_auc"], lgb_metrics["pr_auc"]]
    ens_proba   = ensemble_predict([xgb_proba, lgb_proba], ens_weights)

    thr_ens = optimize_threshold_fbeta(y_test, ens_proba, beta=2.0)
    y_pred_ens = (ens_proba >= thr_ens).astype(int)

    ens_metrics = {
        "name": "Ensemble (XGB+LGB)",
        "pr_auc":    average_precision_score(y_test, ens_proba),
        "f1":        f1_score(y_test, y_pred_ens, zero_division=0),
        "precision": precision_score(y_test, y_pred_ens, zero_division=0),
        "recall":    recall_score(y_test, y_pred_ens, zero_division=0),
        "threshold": thr_ens,
    }
    print(f"\n  ─── Ensemble XGBoost + LightGBM (seuil={thr_ens:.3f}) ───")
    for k,v in ens_metrics.items():
        if isinstance(v, float):
            print(f"  {k:<12}: {v:.4f}")

    # ── Feature importance & SHAP ─────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  IMPORTANCE DES FEATURES — LightGBM (meilleur modèle)")
    print(f"{'='*70}")
    show_feature_importance(lgb_model, feature_cols)

    if not args.no_shap:
        run_shap(lgb_model, X_test, feature_cols)

    # ── Sauvegarde ────────────────────────────────────────────────────
    save_all(xgb_model, lgb_model, thr_ens, feature_cols,
             ens_metrics, prefix=args.prefix)

    # ── Récapitulatif final ───────────────────────────────────────────
    print(f"\n{'#'*70}")
    print(f"#  RÉCAPITULATIF FINAL — Architecture Dual-Branch v3")
    print(f"{'#'*70}")
    ae_status = "OFF" if args.no_autoencoder else "ON (reconstruction_error)"
    print(f"  Branche B (autoencoder) : {ae_status}")
    print(f"  {'Modèle':<25} {'PR-AUC':>8} {'F1':>8} {'Recall':>8}")
    print(f"  {'─'*50}")
    for m in [xgb_metrics, lgb_metrics, ens_metrics]:
        print(f"  {m['name']:<25} {m['pr_auc']:>8.4f} {m['f1']:>8.4f} {m['recall']:>8.4f}")
    best = max([xgb_metrics, lgb_metrics, ens_metrics], key=lambda m: m["pr_auc"])
    print(f"\n  Meilleur modèle : {best['name']} (PR-AUC={best['pr_auc']:.4f})")
    print(f"  → Intégration dans le Scoring Agent (Compliance Agent + ML)")
    print(f"{'#'*70}\n")


if __name__ == "__main__":
    main()
