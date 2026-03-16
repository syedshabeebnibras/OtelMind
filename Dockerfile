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
