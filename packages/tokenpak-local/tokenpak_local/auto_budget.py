"""
auto_budget.py — Model-aware context budget computation.

Computes safe TokenPak input budgets based on known model context lengths,
reserving a fraction for the model's output.
"""

from __future__ import annotations

from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Model context window registry
# ---------------------------------------------------------------------------
# Format: "model_name_or_prefix": context_tokens
# Keys are matched case-insensitively; longer/more-specific matches win.

MODEL_CONTEXT_LENGTHS: Dict[str, int] = {
    # Llama 3.x family
    "llama3": 8192,
    "llama3.1": 131072,
    "llama3.2": 131072,
    "llama3.3": 131072,
    "llama3:8b": 8192,
    "llama3:70b": 8192,
    "llama3.1:8b": 131072,
    "llama3.1:70b": 131072,
    "llama3.2:1b": 131072,
    "llama3.2:3b": 131072,
    # Llama 2 family
    "llama2": 4096,
    "llama2:7b": 4096,
    "llama2:13b": 4096,
    "llama2:70b": 4096,
    # Mistral family
    "mistral": 32768,
    "mistral:7b": 32768,
    "mistral-nemo": 131072,
    "mistral-large": 131072,
    "mixtral": 32768,
    "mixtral:8x7b": 32768,
    "mixtral:8x22b": 65536,
    # Phi family (Microsoft)
    "phi3": 4096,
    "phi3:mini": 4096,
    "phi3:medium": 4096,
    "phi3:small": 4096,
    "phi3.5": 131072,
    "phi4": 16384,
    # Gemma family (Google)
    "gemma": 8192,
    "gemma2": 8192,
    "gemma2:2b": 8192,
    "gemma2:9b": 8192,
    "gemma2:27b": 8192,
    # Qwen family (Alibaba)
    "qwen": 32768,
    "qwen2": 131072,
    "qwen2.5": 131072,
    "qwen2.5:0.5b": 32768,
    "qwen2.5:1.5b": 32768,
    "qwen2.5:3b": 32768,
    "qwen2.5:7b": 131072,
    "qwen2.5:14b": 131072,
    "qwen2.5:72b": 131072,
    # DeepSeek
    "deepseek-r1": 65536,
    "deepseek-v2": 131072,
    "deepseek-coder": 16384,
    # Command R (Cohere)
    "command-r": 131072,
    "command-r-plus": 131072,
    # CodeLlama
    "codellama": 16384,
    "codellama:7b": 16384,
    "codellama:13b": 16384,
    "codellama:34b": 16384,
    # Nomic
    "nomic-embed-text": 8192,
    # Fallback
    "default": 4096,
}

# Fraction of context reserved for INPUT (rest goes to model output)
DEFAULT_OUTPUT_FRACTION: float = 0.25  # reserve 25% for output


def get_context_length(model: str, fallback: int = 4096) -> int:
    """
    Return the known context length for a model name.

    Matching strategy (case-insensitive):
    1. Exact match
    2. Longest prefix match from registry
    3. `fallback` if no match found

    Examples:
        get_context_length("llama3.1:8b")   → 131072
        get_context_length("llama3")        → 8192
        get_context_length("unknown-model") → 4096
    """
    model_lower = model.lower().strip()

    # Exact match
    if model_lower in MODEL_CONTEXT_LENGTHS:
        return MODEL_CONTEXT_LENGTHS[model_lower]

    # Longest prefix match
    best_key: Optional[str] = None
    best_len = 0
    for key in MODEL_CONTEXT_LENGTHS:
        if model_lower.startswith(key) and len(key) > best_len:
            best_key = key
            best_len = len(key)

    if best_key is not None:
        return MODEL_CONTEXT_LENGTHS[best_key]

    return fallback


def auto_budget(
    model: str,
    output_fraction: float = DEFAULT_OUTPUT_FRACTION,
    context_length: Optional[int] = None,
    minimum: int = 512,
) -> int:
    """
    Compute a safe TokenPak input budget for a given model.

    Args:
        model:           Model name (e.g. "llama3", "mistral:7b").
        output_fraction: Fraction of context to reserve for output tokens.
                         Default 0.25 (25% output, 75% input).
        context_length:  Override auto-detected context length.
        minimum:         Minimum budget (prevents absurdly small values).

    Returns:
        Integer token budget for TokenPak input.

    Examples:
        auto_budget("llama3")       → 6144  (75% of 8192)
        auto_budget("phi3")         → 3072  (75% of 4096)
        auto_budget("llama3.1:8b") → 98304 (75% of 131072)
    """
    if output_fraction < 0.0 or output_fraction > 1.0:
        raise ValueError(f"output_fraction must be between 0 and 1, got {output_fraction}")

    ctx = context_length if context_length is not None else get_context_length(model)
    budget = int(ctx * (1.0 - output_fraction))
    return max(budget, minimum)


def budget_info(model: str, output_fraction: float = DEFAULT_OUTPUT_FRACTION) -> dict:
    """
    Return a dict with full budget breakdown for a model.

    Useful for logging/debugging context planning.
    """
    ctx = get_context_length(model)
    budget = auto_budget(model, output_fraction=output_fraction)
    return {
        "model": model,
        "context_length": ctx,
        "output_fraction": output_fraction,
        "input_budget": budget,
        "output_reserved": ctx - budget,
    }
