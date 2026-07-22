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
#  torch ET torchvision depuis l'index CPU, EN PREMIER : docling dépend de
#  torchvision, et sans ce pré-positionnement pip installerait la variante CUDA
#  (nvidia-*, cuda-toolkit, triton) — environ 4 Go inutiles sans GPU.
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --retries 5 --timeout 60 \
        torch==2.13.0 torchvision==0.28.0 \
        --index-url https://download.pytorch.org/whl/cpu \
    && pip install --retries 5 --timeout 60 -r requirements.txt

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
