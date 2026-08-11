# docrag container -- OS-agnostic runtime so migrating between Windows/Linux
# never re-fights venv activation or path separators. Data (the SQLite index +
# corpora) is NOT baked in: it's mounted as a volume at /data, and the app is
# pointed there via DOCRAG_INDEX_DIR / DOCRAG_DOCS_ROOT. Secrets come from .env
# (env_file in compose), never copied into the image.
#
# Two targets:
#   --target serve  (default)  slim: query/answer over a prebuilt index. No torch.
#   --target build             full requirements.txt: can re-index documents.
#
#   docker build --target serve -t docrag:serve .
#   docker build --target build -t docrag:build .

# ---- shared base -------------------------------------------------------------
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DOCRAG_INDEX_DIR=/data/.index \
    DOCRAG_DOCS_ROOT=/data/corpora
WORKDIR /app

# ---- serve: slim image, prebuilt index only ---------------------------------
FROM base AS serve
COPY requirements.serve.txt .
RUN pip install --upgrade pip && pip install -r requirements.serve.txt
COPY docrag/ ./docrag/
COPY server/ ./server/
EXPOSE 8099
# Web UI in the foreground, bound to all interfaces (container networking).
CMD ["python", "server/server.py", "--start", "--host", "0.0.0.0", "--port", "8099"]

# ---- build: full stack, can re-index ----------------------------------------
FROM base AS build
# Native build deps for the heavier extraction/ML wheels when no wheel exists.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY docrag/ ./docrag/
COPY server/ ./server/
COPY scraper/ ./scraper/
COPY evalset/ ./evalset/
EXPOSE 8099
CMD ["python", "server/server.py", "--start", "--host", "0.0.0.0", "--port", "8099"]
