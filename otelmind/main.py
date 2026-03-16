"""Unified entry point — starts all OtelMind services together.

Services launched:
1. Database connection pool (asyncpg) + Alembic migrations
2. Collector BatchWriter for span ingestion
3. Watchdog agent for failure detection
4. FastAPI API server via uvicorn
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from typing import Any

import uvicorn
from loguru import logger

from otelmind.config import settings


async def _run_migrations() -> None:
    """Run Alembic migrations programmatically."""
    from alembic import command
    from alembic.config import Config

    def _migrate() -> None:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _migrate)
    logger.info("Database migrations applied successfully")


async def _create_db_pool() -> Any:
    """Create and return an asyncpg connection pool."""
    import asyncpg

    # Extract connection params from the SQLAlchemy-style URL
    dsn = settings.database_url
    # Convert sqlalchemy async URL to raw postgres DSN for asyncpg
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    pool = await asyncpg.create_pool(
        dsn,
        min_size=5,
        max_size=settings.db_pool_size,
    )
    logger.info("asyncpg connection pool created (max_size={})", settings.db_pool_size)
    return pool


async def _start_collector() -> asyncio.Task:
    """Start the collector's batch-writer loop as a background task."""
    from otelmind.collector.collector import collector

    async def _collector_loop() -> None:
        logger.info("Collector batch-writer started")
        while True:
            try:
                if collector.buffer_size > 0:
                    await collector._flush()
            except Exception:
                logger.exception("Collector flush error")
            await asyncio.sleep(5.0)

    task = asyncio.create_task(_collector_loop())
    return task


async def _start_watchdog() -> asyncio.Task:
    """Start the watchdog agent as a background task."""
    from otelmind.watchdog.watchdog_agent import WatchdogAgent

    agent = WatchdogAgent()
    task = asyncio.create_task(agent.start())
    logger.info("Watchdog agent started")
    return task


def _start_api_server(shutdown_event: asyncio.Event) -> asyncio.Task:
    """Start the FastAPI server as a background task via uvicorn."""

    async def _run_server() -> None:
        config = uvicorn.Config(
            "otelmind.api.main:app",
            host=settings.api_host,
            port=settings.api_port,
            reload=settings.api_reload,
            log_level="info",
        )
        server = uvicorn.Server(config)

        # Override uvicorn's default signal handling so we control shutdown
        server.install_signal_handlers = lambda: None  # type: ignore[assignment,attr-defined]

        serve_task = asyncio.create_task(server.serve())

        # Wait until external shutdown signal or server exits
        done, _ = await asyncio.wait(
            [
                serve_task,
                asyncio.create_task(shutdown_event.wait()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if not serve_task.done():
            server.should_exit = True
            await serve_task

    task = asyncio.create_task(_run_server())
    return task


async def run() -> None:
    """Main async entry point — orchestrates all services."""
    logger.info("OtelMind starting up")

    shutdown_event = asyncio.Event()
    tasks: list[asyncio.Task] = []

    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    pool = None

    try:
        # 1. Create database pool
        pool = await _create_db_pool()

        # 2. Run migrations
        await _run_migrations()

        # 3. Start collector batch-writer
        collector_task = await _start_collector()
        tasks.append(collector_task)

        # 4. Start watchdog
        watchdog_task = await _start_watchdog()
        tasks.append(watchdog_task)

        # 5. Start API server
        api_task = _start_api_server(shutdown_event)
        tasks.append(api_task)

        logger.info(
            "All services running — API on {}:{}",
            settings.api_host,
            settings.api_port,
        )

        # Block until shutdown is requested
        await shutdown_event.wait()

    except Exception:
        logger.exception("Fatal error during startup")
        shutdown_event.set()
    finally:
        # Graceful shutdown
        logger.info("Shutting down services...")

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if pool is not None:
            await pool.close()
            logger.info("asyncpg pool closed")

        # Shut down tracer
        from otelmind.instrumentation.tracer import shutdown_tracer

        shutdown_tracer()

        logger.info("OtelMind shut down cleanly")


def main() -> None:
    """CLI entry point."""
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())
    sys.exit(0)


if __name__ == "__main__":
    main()
