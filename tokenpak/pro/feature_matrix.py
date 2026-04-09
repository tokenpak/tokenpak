"""TokenPak Pro — Feature detection matrix for Pro vs OSS adapters."""

from __future__ import annotations

FEATURES = [
    "workflow",
    "deterministic",
    "agentic",
    "structured_output",
    "function_calling",
    "streaming",
]

ADAPTERS = [
    "anthropic",
    "openai",
    "google",
    "tokenpak-anthropic",
    "tokenpak-openai",
    "tokenpak-google",
]

# Base support matrix per adapter
_BASE_MATRIX: dict[str, dict[str, bool]] = {
    "anthropic": {
        "workflow": False,
        "deterministic": True,
        "agentic": True,
        "structured_output": True,
        "function_calling": True,
        "streaming": True,
    },
    "openai": {
        "workflow": False,
        "deterministic": True,
        "agentic": True,
        "structured_output": True,
        "function_calling": True,
        "streaming": True,
    },
    "google": {
        "workflow": False,
        "deterministic": False,
        "agentic": False,
        "structured_output": False,
        "function_calling": False,
        "streaming": True,
    },
}

# tokenpak-* variants inherit from their base adapter but also enable workflow
_TOKENPAK_EXTRAS: dict[str, bool] = {
    "workflow": True,
}

# Fallback strategies per feature
_FALLBACKS: dict[str, str] = {
    "workflow": "Use tokenpak-* adapter variant or implement manual orchestration",
    "deterministic": "Set temperature=0 and seed parameter explicitly",
    "agentic": "Use anthropic or openai adapter which support agentic mode",
    "structured_output": "Use anthropic or openai adapter; parse JSON from plain text as fallback",
    "function_calling": "Use anthropic or openai adapter which support function calling",
    "streaming": "Fall back to non-streaming (batch) request mode",
}


def _resolve_base(adapter: str) -> str | None:
    """Return the base adapter name for a tokenpak-* variant, or None."""
    if adapter.startswith("tokenpak-"):
        base = adapter[len("tokenpak-"):]
        return base if base in _BASE_MATRIX else None
    return None


class FeatureMatrix:
    """Feature detection matrix for Pro vs OSS adapters."""

    def is_supported(self, adapter: str, feature: str, model: str = None) -> bool:
        """Check if a feature is supported for the given adapter/model combo."""
        if feature not in FEATURES:
            return False

        base = _resolve_base(adapter)
        if base is not None:
            # tokenpak-* variant: inherit base + extras
            base_support = _BASE_MATRIX.get(base, {})
            return _TOKENPAK_EXTRAS.get(feature, base_support.get(feature, False))

        row = _BASE_MATRIX.get(adapter)
        if row is None:
            return False
        return row.get(feature, False)

    def get_fallback(self, adapter: str, feature: str) -> str:
        """Return fallback strategy string if feature not supported."""
        return _FALLBACKS.get(feature, f"No fallback available for feature '{feature}'")

    def get_matrix(self) -> dict[str, dict[str, bool]]:
        """Return full {adapter: {feature: bool}} dict for all known adapters."""
        result: dict[str, dict[str, bool]] = {}
        for adapter in ADAPTERS:
            result[adapter] = {f: self.is_supported(adapter, f) for f in FEATURES}
        return result
