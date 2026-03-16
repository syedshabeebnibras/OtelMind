"""Entry point for running the collector as a standalone service."""

import uvicorn

from otelmind.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "otelmind.collector.server:app",
        host=settings.api_host,
        port=4318,
        reload=False,
    )
