"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from loguru import logger

from otelmind import __version__
from otelmind.api.routes import router
from otelmind.config import settings
from otelmind.instrumentation.tracer import init_tracer, shutdown_tracer
from otelmind.watchdog.watchdog_agent import WatchdogAgent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — start OTel and watchdog on startup, clean up on shutdown."""
    logger.info("OtelMind v{} starting up", __version__)

    # Initialise OpenTelemetry
    init_tracer()

    # Start watchdog in background
    watchdog = WatchdogAgent()
    watchdog_task = asyncio.create_task(watchdog.start())

    yield

    # Shutdown
    watchdog.stop()
    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass
    shutdown_tracer()
    logger.info("OtelMind shut down cleanly")


app = FastAPI(
    title="OtelMind",
    description="LLM Observability and Self-Healing Operations Platform",
    version=__version__,
    lifespan=lifespan,
)

# Mount routes at both /api/v1 (versioned) and /api + / (guide-compatible)
app.include_router(router, prefix="/api/v1")
app.include_router(router, prefix="/api")
app.include_router(router, prefix="")


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
