# SPDX-License-Identifier: Apache-2.0
"""Independent token-count checks for the savings reporting surface.

Request stores retain token counts, not the source text needed for a genuine
recount.  The default check therefore compares TokenPak's fast UTF-8 byte
estimate with ``tiktoken`` on a deterministic corpus shipped in this module.
It validates the counting pipeline; it does not re-measure stored requests or
change any savings calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, Sequence, cast

from tokenpak.telemetry.tokens import estimate_tokens

__all__ = (
    "TokenCountVerification",
    "TokenizerUnavailableError",
    "verify_packaged_corpus",
    "verify_token_count",
)

_ENCODING_NAME = "cl100k_base"
_PACKAGED_CORPUS = "\n\n".join(
    (
        "Summarize the incident, preserve the failed evidence, and list the next safe action.",
        '{"messages":[{"role":"user","content":"Compare the measured baseline."}]}',
        "def total(values: list[int]) -> int:\n    return sum(values)",
        "Token counts vary across prose, code, structured data, and multilingual text: こんにちは世界。",
    )
)


class _Encoder(Protocol):
    def encode(self, text: str) -> Sequence[int]: ...


class _TiktokenModule(Protocol):
    def get_encoding(self, name: str) -> _Encoder: ...


class TokenizerUnavailableError(RuntimeError):
    """Independent token verification cannot run in this installation."""


@dataclass(frozen=True)
class TokenCountVerification:
    """Difference between a reported count and an independent recount.

    ``relative_divergence`` is the absolute difference divided by the
    reported count.  A denominator of one is used when the reported count is
    zero so the value remains finite and JSON-safe.
    """

    reported_count: int
    independent_count: int
    absolute_divergence: int
    relative_divergence: float


def _load_encoder() -> _Encoder:
    try:
        module = cast(_TiktokenModule, import_module("tiktoken"))
    except ImportError as exc:
        raise TokenizerUnavailableError(
            "Independent verification requires tiktoken; install "
            "the optional dependency with `pip install 'tokenpak[tokens]'`."
        ) from exc
    try:
        return module.get_encoding(_ENCODING_NAME)
    except (ImportError, OSError, RuntimeError, ValueError):
        raise TokenizerUnavailableError(
            "Independent verification could not initialize cl100k_base. "
            "Check that its tokenizer data is available in a readable cache, "
            "or reinstall `tokenpak[tokens]`, then retry."
        ) from None


def verify_token_count(text: str, reported_count: int) -> TokenCountVerification:
    """Recount ``text`` with cl100k_base and compare it with ``reported_count``."""
    if reported_count < 0:
        raise ValueError("reported_count must be non-negative")

    independent_count = len(_load_encoder().encode(text)) if text else 0
    absolute_divergence = abs(independent_count - reported_count)
    relative_divergence = absolute_divergence / max(reported_count, 1)
    return TokenCountVerification(
        reported_count=reported_count,
        independent_count=independent_count,
        absolute_divergence=absolute_divergence,
        relative_divergence=relative_divergence,
    )


def verify_packaged_corpus() -> TokenCountVerification:
    """Compare the shipped byte estimate with cl100k_base on the test corpus."""
    return verify_token_count(_PACKAGED_CORPUS, estimate_tokens(_PACKAGED_CORPUS))
