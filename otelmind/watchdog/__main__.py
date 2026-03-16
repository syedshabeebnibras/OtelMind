"""Entry point for running the watchdog as a standalone service."""

import asyncio

from otelmind.watchdog.watchdog_agent import run_watchdog

if __name__ == "__main__":
    asyncio.run(run_watchdog())
