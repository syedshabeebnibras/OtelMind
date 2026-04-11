# ── Stage 1: Build dependencies ──
FROM python:3.11-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime image ──
FROM python:3.11-slim AS base
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && \
    rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
COPY otelmind/ otelmind/
COPY config/ config/
COPY migrations/ migrations/
COPY alembic.ini .
ENV PYTHONPATH=/app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# ── API target ──
FROM base AS api
EXPOSE 8000
CMD ["python", "-m", "otelmind.api.main"]

# ── Watchdog target ──
FROM base AS watchdog
CMD ["python", "-c", "import asyncio; from otelmind.watchdog.watchdog_agent import run_watchdog; asyncio.run(run_watchdog())"]

# ── Unified target (all services) ──
FROM base AS unified
EXPOSE 8000 4318
CMD ["python", "-m", "otelmind.main"]

# ── Railway target (default — last stage wins) ──
# Binds uvicorn to Railway's injected $PORT and runs alembic on boot so
# the schema stays synced across deploys. All background workers (eval
# queue, autoscorer, daily golden, watchdog, partition maintenance)
# start automatically inside the FastAPI lifespan. otelmind/ is already
# on PYTHONPATH from the base stage, so no -e install is needed.
#
# EXPOSE is required — Railway's edge proxy reads the Dockerfile's
# exposed port to know where to route healthcheck + public traffic.
# PYTHONUNBUFFERED forces stdout to flush each line so crashes actually
# show up in `railway logs`. Use `python -m` prefixes so we don't rely
# on the alembic/uvicorn scripts being on PATH — pip install --prefix
# drops scripts in /install/bin but the runtime PATH may not include it
# after the /install → /usr/local COPY.
FROM base AS railway
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "echo '>>> booting' && python -m alembic upgrade head && echo '>>> alembic ok' && exec python -m uvicorn otelmind.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
