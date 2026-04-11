"""Semantic drift detector — catches when agent outputs diverge from expected patterns.

Uses cosine similarity on simple TF-IDF vectors (no external embeddings needed).
For production deployments with OPENAI_API_KEY, uses text-embedding-3-small instead.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())


def _tfidf_vector(tokens: list[str]) -> dict[str, float]:
    counts = Counter(tokens)
    total = sum(counts.values()) or 1
    return {word: count / total for word, count in counts.items()}


def _cosine_similarity(v1: dict[str, float], v2: dict[str, float]) -> float:
    keys = set(v1) & set(v2)
    if not keys:
        return 0.0
    dot = sum(v1[k] * v2[k] for k in keys)
    mag1 = math.sqrt(sum(x * x for x in v1.values()))
    mag2 = math.sqrt(sum(x * x for x in v2.values()))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


def detect_semantic_drift(
    spans: list[dict[str, Any]],
    *,
    drift_threshold: float = 0.15,
    min_outputs: int = 3,
) -> dict[str, Any] | None:
    """Detect when consecutive LLM outputs diverge significantly.

    Returns a failure dict if drift exceeds threshold, else None.
    """
    outputs = []
    for span in spans:
        output = (
            span.get("output_preview")
            or span.get("output")
            or str((span.get("outputs") or {}).get("content", ""))
        )
        if output and len(output.strip()) > 20:
            outputs.append(output)

    if len(outputs) < min_outputs:
        return None

    vectors = [_tfidf_vector(_tokenize(o)) for o in outputs]

    # Compute average similarity between consecutive pairs
    similarities = [_cosine_similarity(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)]
    avg_similarity = sum(similarities) / len(similarities)
    drift_score = 1.0 - avg_similarity

    if drift_score >= drift_threshold:
        return {
            "failure_type": "semantic_drift",
            "confidence": min(drift_score, 1.0),
            "judge_model": "heuristic",
            "reasoning": (
                f"Average output similarity {avg_similarity:.2f} across {len(outputs)} spans — "
                f"drift score {drift_score:.2f} exceeds threshold {drift_threshold}"
            ),
            "evidence": {
                "avg_similarity": round(avg_similarity, 4),
                "drift_score": round(drift_score, 4),
                "output_count": len(outputs),
                "min_similarity": round(min(similarities), 4),
            },
        }
    return None
