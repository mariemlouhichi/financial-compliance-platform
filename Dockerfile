# syntax=docker/dockerfile:1.7
# ============================================================================
#  Image de l'API AML/KYC (FastAPI + pipeline d'analyse multi-agent)
#
#  Points d'attention :
#   • torch est installé depuis l'index CPU — la variante CUDA ajoute ~2 Go
#     pour rien (aucun GPU en production ici).
#   • Le corpus PDF (68 Mo) et les secrets ne sont PAS copiés : ils sont
#     montés en volume / injectés par variables d'environnement.
#   • Exécution sous un utilisateur non privilégié.
#
#  Construction :  docker compose build api
# ============================================================================
# Python 3.13 obligatoire : plusieurs dépendances figées (shap 0.52, pandas 3.0)
# exigent >= 3.12. C'est aussi la version de l'environnement de développement,
# donc celle sur laquelle les versions ont été validées.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

# ── Dépendances système ────────────────────────────────────────────────────
#  build-essential : compilation de certaines roues (lightgbm, psycopg2)
#  libgomp1        : OpenMP, requis par LightGBM et XGBoost à l'exécution
#  curl            : sonde de santé du conteneur
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Dépendances Python ─────────────────────────────────────────────────────
#  torch CPU EN PREMIER : sinon pip résout la variante CUDA (~2 Go inutiles).
#  ── Stratégie de téléchargement adaptée à une connexion lente/instable ──
#  torch pèse 191 Mo et pip ne sait PAS reprendre un téléchargement interrompu :
#  une coupure à 90 % relance tout depuis zéro. Trois protections :
#
#   1. un CACHE BuildKit sur /wheels → le fichier partiel SURVIT entre deux
#      builds, donc une nouvelle tentative reprend là où elle s'est arrêtée ;
#   2. curl -C - (reprise) en boucle, le serveur PyTorch accepte les ranges ;
#   3. des ÉTAPES SÉPARÉES : torch, puis torchvision, puis le reste — une
#      étape réussie est mise en cache et n'est jamais refaite.
#
#  torch et torchvision sont installés EN PREMIER depuis l'index CPU car
#  docling dépend de torchvision : sans ce pré-positionnement, pip tirerait la
#  variante CUDA (nvidia-*, cuda-toolkit, triton) — environ 4 Go inutiles.
RUN pip install --upgrade pip

#  NOTE : le fichier téléchargé est conservé sous un nom neutre dans le cache,
#  puis COPIÉ sous son nom normalisé PEP 427 avant l'installation. pip refuse
#  en effet un fichier nommé « torch.whl » (« Invalid wheel filename »), et on
#  ne veut surtout pas retélécharger 182 Mo juste pour un renommage.
ARG TORCH_URL=https://download.pytorch.org/whl/cpu/torch-2.13.0%2Bcpu-cp313-cp313-manylinux_2_28_x86_64.whl
ARG TORCH_WHEEL=torch-2.13.0+cpu-cp313-cp313-manylinux_2_28_x86_64.whl
ARG TORCH_SIZE=191815667
RUN --mount=type=cache,target=/wheels,sharing=locked <<'SH'
set -eu
DEST=/wheels/torch.whl
for i in $(seq 1 40); do
    SZ=$(stat -c%s "$DEST" 2>/dev/null || echo 0)
    [ "$SZ" = "$TORCH_SIZE" ] && break
    echo "  torch : $SZ / $TORCH_SIZE octets — tentative $i (reprise)"
    curl -fL --retry 5 --retry-all-errors --retry-delay 5 \
         --connect-timeout 30 -C - -o "$DEST" "$TORCH_URL" || true
done
SZ=$(stat -c%s "$DEST" 2>/dev/null || echo 0)
[ "$SZ" = "$TORCH_SIZE" ] || { echo "torch incomplet ($SZ octets) — relance le build"; exit 1; }
cp "$DEST" "/tmp/$TORCH_WHEEL"
pip install "/tmp/$TORCH_WHEEL"
rm -f "/tmp/$TORCH_WHEEL"
SH

ARG TVISION_URL=https://download.pytorch.org/whl/cpu/torchvision-0.28.0%2Bcpu-cp313-cp313-manylinux_2_28_x86_64.whl
ARG TVISION_WHEEL=torchvision-0.28.0+cpu-cp313-cp313-manylinux_2_28_x86_64.whl
ARG TVISION_SIZE=1846831
RUN --mount=type=cache,target=/wheels,sharing=locked <<'SH'
set -eu
DEST=/wheels/torchvision.download
for i in $(seq 1 20); do
    SZ=$(stat -c%s "$DEST" 2>/dev/null || echo 0)
    [ "$SZ" = "$TVISION_SIZE" ] && break
    echo "  torchvision : $SZ / $TVISION_SIZE octets — tentative $i (reprise)"
    curl -fL --retry 5 --retry-all-errors --retry-delay 5 \
         --connect-timeout 30 -C - -o "$DEST" "$TVISION_URL" || true
done
SZ=$(stat -c%s "$DEST" 2>/dev/null || echo 0)
[ "$SZ" = "$TVISION_SIZE" ] || { echo "torchvision incomplet ($SZ octets)"; exit 1; }
cp "$DEST" "/tmp/$TVISION_WHEEL"
pip install "/tmp/$TVISION_WHEEL"
rm -f "/tmp/$TVISION_WHEEL"
SH

#  Le cache pip conserve les roues déjà téléchargées : une reprise après
#  coupure ne retélécharge que ce qui manque réellement.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 20 --timeout 120 -r requirements.txt

# ── Code applicatif ────────────────────────────────────────────────────────
#  Le .dockerignore exclut venv/, node_modules/, entities.ftm.json (853 Mo),
#  les embeddings déjà indexés dans Weaviate et les secrets.
COPY api/            ./api/
COPY level1_rules/   ./level1_rules/
COPY neo4j_graph/    ./neo4j_graph/
COPY config/         ./config/
COPY core/           ./core/
COPY data/           ./data/
COPY test_documents/ ./test_documents/
COPY *.py            ./
COPY aml_ml_demo_*   ./
COPY clients_db.json fatf_checklist_cache.json ./
COPY output/chunks_aml_kyc.json ./output/

# Comptes de démonstration pré-vérifiés (généré par seed_accounts.py).
# Sur Azure Container Apps le stockage est éphémère et platform.db n'est PAS
# monté → ces comptes survivent aux redémarrages. En local, docker-compose monte
# le vrai platform.db, qui prend le dessus sur celui-ci.
COPY platform_seed.db ./platform.db

# Dossiers d'écriture (dossiers de secours, cache LLM, cache modèles)
RUN mkdir -p output/jobs sars .cache/huggingface

# ── Utilisateur non privilégié ─────────────────────────────────────────────
RUN useradd --create-home --uid 1000 amlapp \
    && chown -R amlapp:amlapp /app
USER amlapp

EXPOSE 8001

# ── Sonde de santé ─────────────────────────────────────────────────────────
#  start-period long : le chargement des listes de sanctions et des PEP
#  (660 000 entrées) prend une trentaine de secondes au démarrage.
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD curl -fsS http://localhost:8001/api/health || exit 1

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8001"]
