"""FastAPI middleware: rate limiting, request timing, CORS."""

from __future__ import annotations

import time

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Adds X-Response-Time header and logs slow requests."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        if elapsed_ms > 1000:
            logger.warning(
                "Slow request: {} {} took {:.0f}ms",
                request.method,
                request.url.path,
                elapsed_ms,
            )
        return response


def register_middleware(app: FastAPI) -> None:
    """Register all middleware on the FastAPI app."""
    app.add_middleware(RequestTimingMiddleware)
    # Per-tenant rate limits are enforced in-route via `enforce_tenant_rate_limit`
    # so limits apply after API key resolution (middleware runs before dependencies).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
