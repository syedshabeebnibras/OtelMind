"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from loguru import logger

from otelmind import __version__
from otelmind.api.middleware import register_middleware
from otelmind.api.rbac_routes import rbac_router
from otelmind.api.routes import router
from otelmind.config import settings
from otelmind.instrumentation.tracer import init_tracer, shutdown_tracer
from otelmind.storage.partitioning import drop_expired_partitions, ensure_partitions
from otelmind.watchdog.watchdog_agent import WatchdogAgent


async def _partition_maintenance_loop() -> None:
    """Rolls the partition window forward and drops expired months daily."""
    while True:
        try:
            await ensure_partitions()
            await drop_expired_partitions()
        except Exception as exc:  # pragma: no cover — best-effort
            logger.warning("partition maintenance failed: {}", exc)
        await asyncio.sleep(24 * 60 * 60)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — start OTel and watchdog on startup, clean up on shutdown."""
    logger.info("OtelMind v{} starting up", __version__)

    # Initialise OpenTelemetry
    init_tracer()

    # Make sure partitions exist for the current window before any INSERT
    # lands. Runs best-effort — a DB that isn't partitioned (e.g. SQLite
    # in tests) will simply error, which we swallow.
    try:
        await ensure_partitions()
    except Exception as exc:
        logger.warning("initial partition ensure skipped: {}", exc)

    partition_task = asyncio.create_task(_partition_maintenance_loop())

    # Start watchdog in background
    watchdog = WatchdogAgent()
    watchdog_task = asyncio.create_task(watchdog.start())

    yield

    # Shutdown
    watchdog.stop()
    watchdog_task.cancel()
    partition_task.cancel()
    with suppress(asyncio.CancelledError):
        await watchdog_task
    with suppress(asyncio.CancelledError):
        await partition_task
    shutdown_tracer()
    logger.info("OtelMind shut down cleanly")


app = FastAPI(
    title="OtelMind",
    description="LLM Observability and Self-Healing Operations Platform",
    version=__version__,
    lifespan=lifespan,
)

register_middleware(app)

# Mount routes at both /api/v1 (versioned) and /api + / (guide-compatible)
app.include_router(router, prefix="/api/v1")
app.include_router(router, prefix="/api")
app.include_router(router, prefix="")

# RBAC admin routes — roles, members, audit log
app.include_router(rbac_router, prefix="/api/v1")
app.include_router(rbac_router, prefix="/api")


def main() -> None:
    """Run the API server via uvicorn."""
    import uvicorn

    uvicorn.run(
        "otelmind.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )


if __name__ == "__main__":
    main()
