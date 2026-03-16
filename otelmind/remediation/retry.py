"""Retry remediation strategy using tenacity for exponential backoff."""

from __future__ import annotations

from typing import Any

from loguru import logger
from tenacity import (
    RetryError,
    retry,
    stop_after_attempt,
    wait_exponential,
)

from otelmind.config import settings
from otelmind.remediation.base import RemediationStrategy


class RetryStrategy(RemediationStrategy):
    """Re-execute the failed operation with exponential backoff.

    Configuration is read from ``settings``:
    - ``remediation_max_retries`` — maximum number of retry attempts (default 3).

    An optional ``backoff_base`` can be supplied in the *context* dict
    (defaults to 2 seconds).
    """

    async def execute(
        self,
        classification: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        max_attempts: int = context.get(
            "max_attempts", settings.remediation_max_retries
        )
        backoff_base: float = context.get("backoff_base", 2.0)
        callable_fn = context.get("callable")

        if callable_fn is None:
            logger.warning(
                "RetryStrategy invoked without a callable in context for trace {}",
                classification.get("trace_id", "unknown"),
            )
            return {
                "status": "skipped",
                "reason": "no_callable_provided",
                "trace_id": classification.get("trace_id"),
            }

        callable_args: tuple = context.get("callable_args", ())
        callable_kwargs: dict[str, Any] = context.get("callable_kwargs", {})

        attempt_count = 0
        last_error: str | None = None

        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=backoff_base, min=backoff_base, max=backoff_base * 30),
            reraise=True,
        )
        async def _attempt() -> Any:
            nonlocal attempt_count
            attempt_count += 1
            logger.info(
                "Retry attempt {}/{} for trace {}",
                attempt_count,
                max_attempts,
                classification.get("trace_id", "unknown"),
            )
            return await callable_fn(*callable_args, **callable_kwargs)

        try:
            result = await _attempt()
            return {
                "status": "success",
                "attempts": attempt_count,
                "trace_id": classification.get("trace_id"),
                "result": result,
            }
        except RetryError as exc:
            last_error = str(exc.last_attempt.exception()) if exc.last_attempt.exception() else str(exc)
            logger.error(
                "RetryStrategy exhausted after {} attempts for trace {}: {}",
                attempt_count,
                classification.get("trace_id", "unknown"),
                last_error,
            )
            return {
                "status": "failed",
                "attempts": attempt_count,
                "trace_id": classification.get("trace_id"),
                "error": last_error,
            }
        except Exception as exc:
            last_error = str(exc)
            logger.error(
                "RetryStrategy failed on attempt {} for trace {}: {}",
                attempt_count,
                classification.get("trace_id", "unknown"),
                last_error,
            )
            return {
                "status": "failed",
                "attempts": attempt_count,
                "trace_id": classification.get("trace_id"),
                "error": last_error,
            }
