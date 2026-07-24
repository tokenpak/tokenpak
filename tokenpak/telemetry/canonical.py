"""Canonical types for the TokenPak telemetry pipeline.

All provider adapters normalise raw API payloads into these shared
structures so that downstream FinOps logic never has to know which
provider produced the data.

Classes
-------
UsageSource : str enum — how token counts were obtained
Confidence  : str enum — quality/reliability of extracted data
CanonicalRequest  — normalised inbound LLM request
CanonicalResponse — normalised LLM response
CanonicalUsage    — normalised token-usage with provenance metadata
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Enum-like string literals (kept as plain strings to avoid stdlib Enum
# overhead; validated in CanonicalUsage.__post_init__ for safety)
# ---------------------------------------------------------------------------


class UsageSource:
    """Controlled vocabulary for ``CanonicalUsage.usage_source``."""

    PROVIDER_REPORTED: str = "provider_reported"
    PROXY_ESTIMATE: str = "proxy_estimate"
    TOKEN_COUNTED: str = "token_counted"
    UNKNOWN: str = "unknown"

    _values: tuple[str, ...] = (
        "provider_reported",
        "proxy_estimate",
        "token_counted",
        "unknown",
    )

    @classmethod
    def validate(cls, value: str) -> str:
        """Return *value* if valid, else raise ``ValueError``."""
        if value not in cls._values:
            raise ValueError(f"Invalid usage_source {value!r}. Must be one of {cls._values}.")
        return value


class Confidence:
    """Controlled vocabulary for ``CanonicalUsage.confidence``."""

    HIGH: str = "high"
    MEDIUM: str = "medium"
    LOW: str = "low"

    _values: tuple[str, ...] = ("high", "medium", "low")

    @classmethod
    def validate(cls, value: str) -> str:
        """Return *value* if valid, else raise ``ValueError``."""
        if value not in cls._values:
            raise ValueError(f"Invalid confidence {value!r}. Must be one of {cls._values}.")
        return value


# ---------------------------------------------------------------------------
# Canonical data-classes
# ---------------------------------------------------------------------------


@dataclass
class CanonicalRequest:
    """Normalised representation of a request sent to an LLM provider.

    Parameters
    ----------
    provider:
        Lower-case provider identifier, e.g. ``"anthropic"``, ``"openai"``,
        ``"gemini"``, ``"unknown"``.
    model:
        Model identifier as returned / reported by the provider.
    messages:
        Conversation turns.  Each turn is a plain ``dict`` preserving the
        original shape so no information is lost.
    tools:
        Tool/function definitions attached to the request (may be empty).
    params:
        Any other request-level parameters (temperature, max_tokens, …).
    raw:
        The original raw payload for audit purposes.
    """

    provider: str = ""
    model: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    raw: Optional[dict[str, Any]] = field(default=None, repr=False)


@dataclass
class CanonicalResponse:
    """Normalised representation of a response received from an LLM provider.

    Parameters
    ----------
    output:
        Text or structured content produced by the model.  May be a plain
        string for simple text completions or a list of content-block dicts
        for multimodal / tool-use responses.
    finish_reason:
        Normalised finish reason string.  Adapters map provider-specific
        values (``end_turn``, ``stop``, ``STOP``, …) to a common set:
        ``"stop"``, ``"max_tokens"``, ``"tool_use"``, ``"stop_sequence"``,
        ``"error"``, ``"unknown"``.
    error:
        Error message if the call failed; ``None`` on success.
    raw:
        The original raw payload for audit purposes.
    """

    output: Any = None
    finish_reason: str = "unknown"
    error: Optional[str] = None
    raw: Optional[dict[str, Any]] = field(default=None, repr=False)


@dataclass
class CanonicalUsage:
    """Normalised token-usage record with provenance metadata.

    Parameters
    ----------
    input_billed:
        Input tokens actually billed by the provider (may include cached
        read tokens depending on provider billing).
    output_billed:
        Output tokens actually billed.
    input_est:
        Estimated input tokens (set when ``usage_source != "provider_reported"``).
    output_est:
        Estimated output tokens.
    cache_read:
        Tokens served from the provider's prompt-cache (read hit).
    cache_write:
        Tokens written into the provider's prompt-cache.
    usage_source:
        How the counts were obtained.  One of ``UsageSource.*``.
    confidence:
        Confidence in the accuracy of these numbers.  One of
        ``Confidence.*``.
    """

    input_billed: int = 0
    output_billed: int = 0
    input_est: int = 0
    output_est: int = 0
    cache_read: int = 0
    cache_write: int = 0
    usage_source: str = UsageSource.UNKNOWN
    confidence: str = Confidence.LOW

    def __post_init__(self) -> None:  # noqa: D105
        UsageSource.validate(self.usage_source)
        Confidence.validate(self.confidence)
