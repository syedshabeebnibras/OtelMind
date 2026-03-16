"""Heuristic and pattern-based failure detection for LangGraph traces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from otelmind.storage.models import Span


@dataclass
class DetectedFailure:
    """A failure detected by analysis of trace spans."""

    trace_id: str
    failure_type: str  # hallucination, tool_timeout, infinite_loop, tool_misuse, context_overflow
    confidence: float  # 0.0 – 1.0
    evidence: dict[str, Any]
    detection_method: str  # heuristic, pattern, llm_judge


class FailureDetector:
    """Runs detection heuristics over a set of spans belonging to one trace."""

    # Thresholds (tunable)
    TIMEOUT_THRESHOLD_MS: float = 30_000  # 30 s
    LOOP_NODE_REPEAT_THRESHOLD: int = 5
    CONTEXT_TOKEN_THRESHOLD: int = 120_000
    EMPTY_OUTPUT_RATIO_THRESHOLD: float = 0.5

    def analyze(self, trace_id: str, spans: list[Span]) -> list[DetectedFailure]:
        """Run all detectors and return list of detected failures."""
        failures: list[DetectedFailure] = []

        failures.extend(self._detect_tool_timeout(trace_id, spans))
        failures.extend(self._detect_infinite_loop(trace_id, spans))
        failures.extend(self._detect_context_overflow(trace_id, spans))
        failures.extend(self._detect_tool_misuse(trace_id, spans))
        failures.extend(self._detect_hallucination(trace_id, spans))

        if failures:
            logger.warning(
                "Detected {} failures in trace {}: {}",
                len(failures),
                trace_id,
                [f.failure_type for f in failures],
            )
        return failures

    # ── Individual detectors ────────────────────────────────────────────

    def _detect_tool_timeout(
        self, trace_id: str, spans: list[Span]
    ) -> list[DetectedFailure]:
        failures: list[DetectedFailure] = []
        for span in spans:
            if span.duration_ms and span.duration_ms > self.TIMEOUT_THRESHOLD_MS:
                failures.append(
                    DetectedFailure(
                        trace_id=trace_id,
                        failure_type="tool_timeout",
                        confidence=min(span.duration_ms / (self.TIMEOUT_THRESHOLD_MS * 2), 1.0),
                        evidence={
                            "span_id": span.span_id,
                            "span_name": span.name,
                            "duration_ms": span.duration_ms,
                            "threshold_ms": self.TIMEOUT_THRESHOLD_MS,
                        },
                        detection_method="heuristic",
                    )
                )
        return failures

    def _detect_infinite_loop(
        self, trace_id: str, spans: list[Span]
    ) -> list[DetectedFailure]:
        node_counts: dict[str, int] = {}
        for span in spans:
            node_counts[span.name] = node_counts.get(span.name, 0) + 1

        failures: list[DetectedFailure] = []
        for name, count in node_counts.items():
            if count >= self.LOOP_NODE_REPEAT_THRESHOLD:
                failures.append(
                    DetectedFailure(
                        trace_id=trace_id,
                        failure_type="infinite_loop",
                        confidence=min(count / (self.LOOP_NODE_REPEAT_THRESHOLD * 2), 1.0),
                        evidence={
                            "node_name": name,
                            "execution_count": count,
                            "threshold": self.LOOP_NODE_REPEAT_THRESHOLD,
                        },
                        detection_method="pattern",
                    )
                )
        return failures

    def _detect_context_overflow(
        self, trace_id: str, spans: list[Span]
    ) -> list[DetectedFailure]:
        failures: list[DetectedFailure] = []
        for span in spans:
            attrs = span.attributes or {}
            total_tokens = attrs.get("llm.token.total_tokens", 0)
            if isinstance(total_tokens, (int, float)) and total_tokens > self.CONTEXT_TOKEN_THRESHOLD:
                failures.append(
                    DetectedFailure(
                        trace_id=trace_id,
                        failure_type="context_overflow",
                        confidence=min(total_tokens / (self.CONTEXT_TOKEN_THRESHOLD * 1.5), 1.0),
                        evidence={
                            "span_id": span.span_id,
                            "total_tokens": total_tokens,
                            "threshold": self.CONTEXT_TOKEN_THRESHOLD,
                        },
                        detection_method="heuristic",
                    )
                )
        return failures

    def _detect_tool_misuse(
        self, trace_id: str, spans: list[Span]
    ) -> list[DetectedFailure]:
        failures: list[DetectedFailure] = []
        error_spans = [s for s in spans if s.status_code == "ERROR"]
        if len(error_spans) >= 2:
            failures.append(
                DetectedFailure(
                    trace_id=trace_id,
                    failure_type="tool_misuse",
                    confidence=min(len(error_spans) / 5.0, 1.0),
                    evidence={
                        "error_span_count": len(error_spans),
                        "error_spans": [
                            {"span_id": s.span_id, "name": s.name, "error": s.error_message}
                            for s in error_spans[:5]
                        ],
                    },
                    detection_method="pattern",
                )
            )
        return failures

    def _detect_hallucination(
        self, trace_id: str, spans: list[Span]
    ) -> list[DetectedFailure]:
        """Simple heuristic: spans with LLM output but empty/null outputs may indicate hallucination issues."""
        failures: list[DetectedFailure] = []
        llm_spans = [s for s in spans if "llm" in s.name.lower() or "generate" in s.name.lower()]

        if not llm_spans:
            return failures

        empty_output_count = sum(1 for s in llm_spans if not s.outputs)
        ratio = empty_output_count / len(llm_spans) if llm_spans else 0

        if ratio >= self.EMPTY_OUTPUT_RATIO_THRESHOLD and len(llm_spans) >= 2:
            failures.append(
                DetectedFailure(
                    trace_id=trace_id,
                    failure_type="hallucination",
                    confidence=ratio * 0.7,  # Lower confidence — heuristic only
                    evidence={
                        "llm_span_count": len(llm_spans),
                        "empty_output_count": empty_output_count,
                        "empty_output_ratio": round(ratio, 2),
                    },
                    detection_method="heuristic",
                )
            )
        return failures
