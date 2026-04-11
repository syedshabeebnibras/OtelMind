"""Hallucination detection for LLM outputs.

Uses GPT-4o for semantic grounding checks when OPENAI_API_KEY is set.
Falls back to keyword-overlap heuristic otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

HALLUCINATION_SYSTEM_PROMPT = """You are a hallucination detector for LLM outputs.

Given an LLM output and its source context, determine whether the output contains
claims that are NOT supported by (or that directly contradict) the source context.

Respond with ONLY a JSON object:
{
  "is_grounded": true,
  "confidence": 0.0,
  "reasoning": "brief explanation",
  "unsupported_claims": ["claim1", "claim2"]
}

Rules:
- is_grounded = true means the output is largely faithful to the source context.
- is_grounded = false means the output contains hallucinated or unsupported claims.
- unsupported_claims should list specific phrases or facts not found in context.
- If the context is empty or very short, be lenient and lean toward is_grounded = true."""


def _tokenize(text: str) -> set[str]:
    """Extract lowercase words of 3+ characters."""
    return {w for w in re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())}


def _keyword_overlap_score(llm_output: str, source_context: str) -> float:
    """Fraction of output keywords that also appear in source_context (0–1)."""
    output_words = _tokenize(llm_output)
    context_words = _tokenize(source_context)

    if not output_words:
        return 0.0
    if not context_words:
        # No context to verify against — can't determine hallucination
        return 1.0

    overlap = output_words & context_words
    return len(overlap) / len(output_words)


async def _llm_hallucination_check(
    llm_output: str,
    source_context: str,
    api_key: str,
) -> dict | None:
    try:
        import openai
    except ImportError:
        logger.warning("openai not installed — falling back to heuristic")
        return None

    user_msg = (
        f"Source context:\n{source_context}\n\n"
        f"LLM output to verify:\n{llm_output}"
    )
    try:
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": HALLUCINATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        content = response.choices[0].message.content or ""
        return json.loads(content)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse hallucination check response: %s", e)
        return None
    except Exception as e:
        logger.error("LLM hallucination check failed: %s", e)
        return None


async def check_hallucination(llm_output: str, source_context: str) -> dict:
    """Check whether an LLM output is grounded in the provided source context.

    Args:
        llm_output: The text produced by the LLM.
        source_context: The reference text the LLM should have used.

    Returns a dict with is_grounded, confidence, reasoning, method, and
    (in LLM mode) unsupported_claims.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")

    if api_key:
        result = await _llm_hallucination_check(llm_output, source_context, api_key)
        if result is not None:
            result["method"] = "llm_judge"
            result.setdefault("unsupported_claims", [])
            return result

    # --- Keyword-overlap heuristic fallback ---
    overlap = _keyword_overlap_score(llm_output, source_context)
    THRESHOLD = 0.30  # 30% keyword overlap required to consider output grounded

    is_grounded = overlap >= THRESHOLD

    if is_grounded:
        # Confidence scales from 0.5 (at threshold) to 1.0 (full overlap)
        denom = 1.0 - THRESHOLD or 1.0
        confidence = 0.5 + (overlap - THRESHOLD) / denom * 0.5
    else:
        # Confidence scales from 0.5 (at threshold) to 1.0 (zero overlap)
        denom = THRESHOLD or 1.0
        confidence = 0.5 + (THRESHOLD - overlap) / denom * 0.5

    confidence = round(min(confidence, 1.0), 4)

    return {
        "is_grounded": is_grounded,
        "confidence": confidence,
        "reasoning": (
            f"Keyword overlap between output and context: {overlap:.1%}. "
            + (
                "Output shares sufficient vocabulary with context."
                if is_grounded
                else "Low keyword overlap — output may contain unsupported claims. "
                "Set OPENAI_API_KEY for semantic grounding analysis."
            )
        ),
        "unsupported_claims": [],
        "method": "keyword_overlap",
        "overlap_score": round(overlap, 4),
    }
