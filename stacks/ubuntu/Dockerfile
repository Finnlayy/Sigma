# =========================================================
# Projekt:Alpha — Linux Core (Blueprint v1.2.0)
# Host: Ubuntu 24.04, 192.168.178.50 (Always-on)
#   Ingestion (OmniStream/Glint/CCXT WS) · M8 Judge (Noir)
#   Shadow Execution (Jaune) · Redis :6379 AOF · DuckDB/Parquet
# Build-Kontext: REPO-ROOT
#   docker build -f stacks/ubuntu/Dockerfile -t alpha-core:1.2.0 .
# =========================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ALPHA_DATA_DIR=/data

WORKDIR /srv/alpha

# Redis-Client-Tools für Healthchecks (Server selbst läuft separat)
RUN apt-get update \
    && apt-get install -y --no-install-recommends redis-tools ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY bin ./bin
COPY setup_alpha.py ./setup_alpha.py
COPY .env.example ./env.example

RUN mkdir -p /data \
    && chmod +x bin/m8-ctl

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/dashboard/init',timeout=4)" || exit 1

CMD ["uvicorn", "app.server.main:app", "--host", "0.0.0.0", "--port", "8000"]
