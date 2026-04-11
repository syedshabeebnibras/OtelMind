"""LLM pricing tables and cost calculation.

Prices are per 1 000 000 tokens (USD). Updated April 2026.
Add new models by extending MODEL_PRICING.
"""

from __future__ import annotations

# (input_cost_per_1m, output_cost_per_1m) in USD
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (5.00, 15.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "o3-mini": (1.10, 4.40),
    # Anthropic
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-opus": (15.00, 75.00),
    "claude-3-sonnet": (3.00, 15.00),
    "claude-3-haiku": (0.25, 1.25),
    # Google
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-2.0-flash": (0.10, 0.40),
    # Meta
    "llama-3-70b": (0.59, 0.79),
    "llama-3-8b": (0.20, 0.20),
    # Mistral
    "mistral-large": (2.00, 6.00),
    "mistral-medium": (0.40, 1.20),
}

_PROVIDER_PATTERNS: list[tuple[str, str]] = [
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("claude-", "anthropic"),
    ("gemini-", "google"),
    ("llama-", "meta"),
    ("mistral-", "mistral"),
]


def detect_provider(model: str) -> str:
    model_lower = model.lower()
    for pattern, provider in _PROVIDER_PATTERNS:
        if pattern in model_lower:
            return provider
    return "unknown"


def _find_pricing(model: str) -> tuple[float, float]:
    model_lower = model.lower()
    # Exact match first
    if model_lower in MODEL_PRICING:
        return MODEL_PRICING[model_lower]
    # Prefix match
    for key, rates in MODEL_PRICING.items():
        if model_lower.startswith(key) or key in model_lower:
            return rates
    # Default: GPT-4o pricing (conservative)
    return (5.00, 15.00)


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return estimated cost in USD for a given model and token counts."""
    input_rate, output_rate = _find_pricing(model)
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000


def cost_breakdown_row(model: str, prompt_tokens: int, completion_tokens: int) -> dict:
    """Return a cost breakdown dict suitable for API responses."""
    input_rate, output_rate = _find_pricing(model)
    cost = calculate_cost(model, prompt_tokens, completion_tokens)
    return {
        "model": model,
        "provider": detect_provider(model),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "input_cost_per_1m_usd": input_rate,
        "output_cost_per_1m_usd": output_rate,
        "cost_usd": round(cost, 8),
    }
