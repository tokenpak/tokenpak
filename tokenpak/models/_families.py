# SPDX-License-Identifier: Apache-2.0
"""Family pattern rules — infer model properties from naming conventions.

When a model ID isn't in the seed catalog, the registry matches it against
these family rules to infer provider, tier, pricing, and translation templates.
This is what makes the system dynamic: ``claude-opus-4-7`` auto-resolves to
Opus-family pricing without any code or config change.

Rules are matched longest-pattern-first so ``gpt-4o-mini`` beats ``gpt-4o``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FamilyRule:
    """A pattern-based rule for inferring model properties."""

    pattern: str  # prefix string or regex (prefix if no special chars)
    provider: str
    tier: int
    input_per_mtok: float
    output_per_mtok: float
    cache_read_mult: float | None = None  # fraction of input (e.g. 0.10)
    cache_write_mult: float | None = None
    bedrock_template: str | None = None  # e.g. "anthropic.{model_id}-v1:0"
    vertex_template: str | None = None  # e.g. "{model_id}@latest"

    def matches(self, model_id: str) -> bool:
        """Check if model_id matches this family rule."""
        return model_id.startswith(self.pattern)

    def infer_cache_read(self, input_cost: float) -> float | None:
        if self.cache_read_mult is None:
            return None
        return round(input_cost * self.cache_read_mult, 6)

    def infer_cache_write(self, input_cost: float) -> float | None:
        if self.cache_write_mult is None:
            return None
        return round(input_cost * self.cache_write_mult, 6)

    def infer_translation(self, model_id: str) -> dict[str, str]:
        """Generate provider-specific translations from templates."""
        result: dict[str, str] = {}
        if self.bedrock_template:
            result["bedrock"] = self.bedrock_template.replace("{model_id}", model_id)
        if self.vertex_template:
            result["vertex"] = self.vertex_template.replace("{model_id}", model_id)
        return result


# Built-in family rules — ordered by specificity (more specific first).
# The registry sorts by pattern length descending, so longer patterns
# match before shorter ones (e.g. "gpt-4o-mini" before "gpt-4o").
BUILTIN_FAMILIES: list[FamilyRule] = [
    # ── Anthropic ──────────────────────────────────────────────
    FamilyRule(
        pattern="claude-opus",
        provider="anthropic",
        tier=4,
        input_per_mtok=15.0,
        output_per_mtok=75.0,
        cache_read_mult=0.10,
        cache_write_mult=1.25,
        bedrock_template="anthropic.{model_id}-v1:0",
        vertex_template="{model_id}@latest",
    ),
    FamilyRule(
        pattern="claude-sonnet",
        provider="anthropic",
        tier=2,
        input_per_mtok=3.0,
        output_per_mtok=15.0,
        cache_read_mult=0.10,
        cache_write_mult=1.25,
        bedrock_template="anthropic.{model_id}-v1:0",
        vertex_template="{model_id}@latest",
    ),
    FamilyRule(
        pattern="claude-haiku",
        provider="anthropic",
        tier=1,
        input_per_mtok=0.80,
        output_per_mtok=4.0,
        cache_read_mult=0.10,
        cache_write_mult=1.25,
        bedrock_template="anthropic.{model_id}-v1:0",
        vertex_template="{model_id}@latest",
    ),
    # Legacy Claude 3.x naming
    FamilyRule(
        pattern="claude-3-opus",
        provider="anthropic",
        tier=3,
        input_per_mtok=15.0,
        output_per_mtok=75.0,
        cache_read_mult=0.10,
        cache_write_mult=1.25,
        bedrock_template="anthropic.{model_id}-v1:0",
        vertex_template="{model_id}@latest",
    ),
    FamilyRule(
        pattern="claude-3-5-sonnet",
        provider="anthropic",
        tier=2,
        input_per_mtok=3.0,
        output_per_mtok=15.0,
        cache_read_mult=0.10,
        cache_write_mult=1.25,
        bedrock_template="anthropic.{model_id}-v2:0",
        vertex_template="{model_id}@latest",
    ),
    FamilyRule(
        pattern="claude-3-sonnet",
        provider="anthropic",
        tier=2,
        input_per_mtok=3.0,
        output_per_mtok=15.0,
        cache_read_mult=0.10,
        cache_write_mult=1.25,
        bedrock_template="anthropic.{model_id}-v1:0",
        vertex_template="{model_id}@latest",
    ),
    FamilyRule(
        pattern="claude-3-5-haiku",
        provider="anthropic",
        tier=1,
        input_per_mtok=0.80,
        output_per_mtok=4.0,
        cache_read_mult=0.10,
        cache_write_mult=1.25,
        bedrock_template="anthropic.{model_id}-v1:0",
        vertex_template="{model_id}@latest",
    ),
    FamilyRule(
        pattern="claude-3-haiku",
        provider="anthropic",
        tier=1,
        input_per_mtok=0.25,
        output_per_mtok=1.25,
        cache_read_mult=0.10,
        cache_write_mult=1.25,
        bedrock_template="anthropic.{model_id}-v1:0",
        vertex_template="{model_id}@latest",
    ),
    # Catch-all for any claude- model not matched above
    FamilyRule(
        pattern="claude-",
        provider="anthropic",
        tier=2,
        input_per_mtok=3.0,
        output_per_mtok=15.0,
        cache_read_mult=0.10,
        cache_write_mult=1.25,
        bedrock_template="anthropic.{model_id}-v1:0",
        vertex_template="{model_id}@latest",
    ),
    # ── OpenAI ─────────────────────────────────────────────────
    FamilyRule(
        pattern="gpt-4o-mini",
        provider="openai",
        tier=1,
        input_per_mtok=0.15,
        output_per_mtok=0.60,
    ),
    FamilyRule(
        pattern="gpt-4o",
        provider="openai",
        tier=2,
        input_per_mtok=2.50,
        output_per_mtok=10.0,
    ),
    FamilyRule(
        pattern="gpt-4.1-nano",
        provider="openai",
        tier=1,
        input_per_mtok=0.10,
        output_per_mtok=0.40,
    ),
    FamilyRule(
        pattern="gpt-4.1-mini",
        provider="openai",
        tier=1,
        input_per_mtok=0.40,
        output_per_mtok=1.60,
    ),
    FamilyRule(
        pattern="gpt-4.1",
        provider="openai",
        tier=2,
        input_per_mtok=2.0,
        output_per_mtok=8.0,
    ),
    FamilyRule(
        pattern="gpt-5",
        provider="openai",
        tier=4,
        input_per_mtok=10.0,
        output_per_mtok=40.0,
    ),
    FamilyRule(
        pattern="gpt-4",
        provider="openai",
        tier=3,
        input_per_mtok=10.0,
        output_per_mtok=30.0,
    ),
    FamilyRule(
        pattern="gpt-3.5",
        provider="openai",
        tier=1,
        input_per_mtok=0.50,
        output_per_mtok=1.50,
    ),
    FamilyRule(
        pattern="o4-mini",
        provider="openai",
        tier=1,
        input_per_mtok=1.10,
        output_per_mtok=4.40,
    ),
    FamilyRule(
        pattern="o3-mini",
        provider="openai",
        tier=1,
        input_per_mtok=1.10,
        output_per_mtok=4.40,
    ),
    FamilyRule(
        pattern="o1-mini",
        provider="openai",
        tier=1,
        input_per_mtok=1.10,
        output_per_mtok=4.40,
    ),
    FamilyRule(
        pattern="o3",
        provider="openai",
        tier=3,
        input_per_mtok=10.0,
        output_per_mtok=40.0,
    ),
    FamilyRule(
        pattern="o1",
        provider="openai",
        tier=3,
        input_per_mtok=15.0,
        output_per_mtok=60.0,
    ),
    FamilyRule(
        pattern="o4",
        provider="openai",
        tier=3,
        input_per_mtok=10.0,
        output_per_mtok=40.0,
    ),
    FamilyRule(
        pattern="codex",
        provider="openai",
        tier=2,
        input_per_mtok=3.0,
        output_per_mtok=12.0,
    ),
    # ── Google ─────────────────────────────────────────────────
    FamilyRule(
        pattern="gemini-",
        provider="google",
        tier=2,
        input_per_mtok=1.25,
        output_per_mtok=5.0,
    ),
    # ── Local / OSS ────────────────────────────────────────────
    FamilyRule(pattern="llama", provider="ollama", tier=1, input_per_mtok=0.0, output_per_mtok=0.0),
    FamilyRule(
        pattern="mistral", provider="ollama", tier=1, input_per_mtok=0.0, output_per_mtok=0.0
    ),
    FamilyRule(
        pattern="mixtral", provider="ollama", tier=2, input_per_mtok=0.0, output_per_mtok=0.0
    ),
    FamilyRule(pattern="qwen", provider="ollama", tier=1, input_per_mtok=0.0, output_per_mtok=0.0),
    FamilyRule(
        pattern="deepseek", provider="ollama", tier=2, input_per_mtok=0.0, output_per_mtok=0.0
    ),
    FamilyRule(pattern="phi", provider="ollama", tier=1, input_per_mtok=0.0, output_per_mtok=0.0),
]


def get_sorted_families() -> list[FamilyRule]:
    """Return family rules sorted by pattern length descending (most specific first)."""
    return sorted(BUILTIN_FAMILIES, key=lambda r: len(r.pattern), reverse=True)


# Provider prefix map — used for provider detection from model name.
# This replaces detector.py's _MODEL_PREFIX_MAP.
PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("claude-", "anthropic"),
    ("claude", "anthropic"),
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o1", "openai"),
    ("o3-", "openai"),
    ("o3", "openai"),
    ("o4-", "openai"),
    ("o4", "openai"),
    ("text-davinci", "openai"),
    ("codex", "openai"),
    ("gemini-", "google"),
    ("gemini", "google"),
    ("palm", "google"),
    ("llama", "ollama"),
    ("mistral", "ollama"),
    ("mixtral", "ollama"),
    ("qwen", "ollama"),
    ("deepseek", "ollama"),
    ("phi-", "ollama"),
    ("phi", "ollama"),
]
