"""Swap-tool remediation strategy — recommends a fallback tool based on YAML config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from otelmind.remediation.base import RemediationStrategy

# Default path for the fallback-tools mapping file
_DEFAULT_FALLBACK_PATH = Path(__file__).resolve().parents[2] / "config" / "fallback_tools.yaml"


class SwapToolStrategy(RemediationStrategy):
    """Recommend an alternative tool when the original tool has failed.

    Reads a YAML mapping file (``config/fallback_tools.yaml``) that defines
    fallback tools keyed by the original tool name::

        # config/fallback_tools.yaml
        search_web:
          fallback: search_web_backup
          description: "Backup web-search provider"
        sql_query:
          fallback: sql_query_readonly
          description: "Read-only SQL endpoint"

    If no mapping exists for the failed tool, the strategy returns a
    ``"no_fallback_available"`` status.
    """

    def __init__(self, fallback_path: str | Path | None = None) -> None:
        self._fallback_path = Path(fallback_path) if fallback_path else _DEFAULT_FALLBACK_PATH
        self._mappings: dict[str, dict[str, Any]] | None = None

    def _load_mappings(self) -> dict[str, dict[str, Any]]:
        """Load and cache the fallback-tool mappings from YAML."""
        if self._mappings is not None:
            return self._mappings

        if not self._fallback_path.exists():
            logger.warning(
                "Fallback tools config not found at {}; no mappings available",
                self._fallback_path,
            )
            self._mappings = {}
            return self._mappings

        try:
            with open(self._fallback_path, "r") as fh:
                raw = yaml.safe_load(fh)
            if not isinstance(raw, dict):
                logger.error(
                    "fallback_tools.yaml must be a top-level mapping; got {}",
                    type(raw).__name__,
                )
                self._mappings = {}
            else:
                self._mappings = raw
        except Exception:
            logger.exception("Failed to parse {}", self._fallback_path)
            self._mappings = {}

        return self._mappings

    async def execute(
        self,
        classification: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        failed_tool: str | None = context.get("failed_tool") or classification.get(
            "evidence", {}
        ).get("failed_tool")

        if not failed_tool:
            logger.warning(
                "SwapToolStrategy: no failed_tool specified for trace {}",
                classification.get("trace_id", "unknown"),
            )
            return {
                "status": "skipped",
                "reason": "no_failed_tool_specified",
                "trace_id": classification.get("trace_id"),
            }

        mappings = self._load_mappings()
        fallback_entry = mappings.get(failed_tool)

        if fallback_entry is None:
            logger.info(
                "SwapToolStrategy: no fallback mapping for tool '{}' (trace {})",
                failed_tool,
                classification.get("trace_id", "unknown"),
            )
            return {
                "status": "no_fallback_available",
                "failed_tool": failed_tool,
                "trace_id": classification.get("trace_id"),
            }

        fallback_tool = fallback_entry.get("fallback", "unknown")
        description = fallback_entry.get("description", "")

        logger.info(
            "SwapToolStrategy: recommending '{}' as fallback for '{}' (trace {})",
            fallback_tool,
            failed_tool,
            classification.get("trace_id", "unknown"),
        )

        return {
            "status": "success",
            "trace_id": classification.get("trace_id"),
            "failed_tool": failed_tool,
            "fallback_tool": fallback_tool,
            "fallback_description": description,
            "fallback_mapping": fallback_entry,
        }
