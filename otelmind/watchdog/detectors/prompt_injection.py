"""Prompt injection detector — catches adversarial inputs in tool outputs.

Scans span inputs and outputs for common prompt injection patterns.
This is a heuristic first-pass; LLM judge provides semantic analysis.
"""

from __future__ import annotations

import re
from typing import Any

# Known injection patterns (regex)
_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.I),
        "instruction override",
    ),
    (re.compile(r"you\s+are\s+now\s+(?:a|an|the)\s+\w+", re.I), "persona hijack"),
    (
        re.compile(
            r"(?:forget|disregard|discard)\s+(?:your|all|the)\s+(?:previous|prior|system)", re.I
        ),
        "context wipe",
    ),
    (
        re.compile(
            r"(?:print|output|reveal|show|tell me)\s+(?:your|the)\s+(?:system\s+)?prompt", re.I
        ),
        "prompt extraction",
    ),
    (
        re.compile(r"(?:act|behave|respond)\s+as\s+(?:if\s+)?(?:you\s+(?:are|were|have no))", re.I),
        "role break",
    ),
    (re.compile(r"<\|(?:im_start|im_end|system|endoftext)\|>", re.I), "control token injection"),
    (re.compile(r"\[(?:INST|\/INST|SYS|\/SYS)\]", re.I), "llama instruction injection"),
    (re.compile(r"###\s*(?:Human|Assistant|System):", re.I), "chat template injection"),
]


def _scan_text(text: str) -> list[str]:
    """Return list of matched injection pattern names."""
    if not text:
        return []
    matches = []
    for pattern, name in _INJECTION_PATTERNS:
        if pattern.search(text):
            matches.append(name)
    return matches


def detect_prompt_injection(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Scan span inputs and tool outputs for prompt injection patterns."""
    for span in spans:
        # Check tool outputs (most likely injection vector)
        for field in ("output_preview", "output", "outputs"):
            value = span.get(field)
            if isinstance(value, dict):
                value = str(value)
            hits = _scan_text(str(value or ""))
            if hits:
                name = span.get("span_name") or span.get("name") or "unknown"
                return {
                    "failure_type": "prompt_injection",
                    "confidence": min(0.6 + len(hits) * 0.1, 1.0),
                    "judge_model": "heuristic",
                    "reasoning": (
                        f"Span '{name}' output matched {len(hits)} injection pattern(s): "
                        + ", ".join(hits)
                    ),
                    "evidence": {
                        "span_name": name,
                        "patterns_matched": hits,
                        "field": field,
                    },
                }

        # Also check inputs (attacker-controlled data entering the pipeline)
        for field in ("input_preview", "input", "inputs"):
            value = span.get(field)
            if isinstance(value, dict):
                value = str(value)
            hits = _scan_text(str(value or ""))
            if len(hits) >= 2:  # Require 2+ matches in inputs (lower recall, higher precision)
                name = span.get("span_name") or span.get("name") or "unknown"
                return {
                    "failure_type": "prompt_injection",
                    "confidence": min(0.5 + len(hits) * 0.1, 1.0),
                    "judge_model": "heuristic",
                    "reasoning": (
                        f"Span '{name}' input matched {len(hits)} injection pattern(s) in inputs: "
                        + ", ".join(hits)
                    ),
                    "evidence": {
                        "span_name": name,
                        "patterns_matched": hits,
                        "field": field,
                    },
                }

    return None
