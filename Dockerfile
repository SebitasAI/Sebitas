# Misterr runtime image.
#
# Deploys to Render (Web Service, docker runtime). Render injects PORT;
# we honor it. Secrets come from Render env vars (synced from Doppler via
# the Doppler-Render integration).
#
# System deps:
# - ffmpeg: audio extraction from video uploads (slice video+YT).
# - libpq5: runtime libs for psycopg (LangGraph checkpointer driver).
# - tini: signal handling so Render's SIGTERM cleanly drains in-flight tasks.
# - curl + ca-certificates: outbound HTTPS to Slack / Convex / Pipedream /
#   Cloudflare Workers AI + the in-container HEALTHCHECK.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libpq5 \
        curl \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# uv is our package manager; lockfile-faithful + fast.
RUN pip install --no-cache-dir uv

WORKDIR /app

# Install deps in their own layer so app-code changes don't bust the cache.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# App code.
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Render hits this to know we're live; same path Misterr exposes locally.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
# On boot: run alembic migrations to head, then start the server.
# Migrations failing aborts the boot (intentional: bad migration -> no traffic).
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
