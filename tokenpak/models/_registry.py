# SPDX-License-Identifier: Apache-2.0
"""Core model registry — single source of truth for model metadata.

Thread-safe, hot-reloadable, zero heavy dependencies.
Imports only: json, re, threading, pathlib, dataclasses, time, logging.
Safe to import from proxy.py's fast path.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ._families import PROVIDER_PREFIXES, FamilyRule, get_sorted_families

log = logging.getLogger(__name__)

# Path to the bundled seed catalog
_SEED_CATALOG_PATH = Path(__file__).parent / "data" / "seed_catalog.json"

# Strip trailing date suffixes like -20261015 or -20241022
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")

# Long-context tier marker, e.g. "claude-fable-5[1m]" — the provider's
# 1M-input-token tier of the base model.
_LONG_CONTEXT_SUFFIX = "[1m]"
_LONG_CONTEXT_TOKENS = 1_000_000


@dataclass(frozen=True)
class ModelInfo:
    """Everything any consumer needs to know about a model."""

    model_id: str
    provider: str  # "anthropic", "openai", "google", "ollama"
    tier: int  # 1=budget, 2=mid, 3=premium, 4=frontier
    input_per_mtok: float  # USD per 1M input tokens
    output_per_mtok: float  # USD per 1M output tokens
    cache_read_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None
    translations: dict[str, str] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    source: str = "seed"  # "seed", "discovered", "inferred"
    # Max input-context window in tokens (provider Models API
    # ``max_input_tokens``). None when unknown — consumers must fall back
    # to their configured static threshold rather than inventing a value.
    max_input_tokens: int | None = None


# Singleton default for completely unknown models
_UNKNOWN_DEFAULT = ModelInfo(
    model_id="unknown",
    provider="unknown",
    tier=2,
    input_per_mtok=3.0,
    output_per_mtok=15.0,
    cache_read_per_mtok=0.30,
    cache_write_per_mtok=3.75,
    source="inferred",
)


class ModelRegistry:
    """Thread-safe, hot-reloadable model registry."""

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {}
        self._aliases: dict[str, str] = {}  # alias -> canonical model_id
        self._families: list[FamilyRule] = get_sorted_families()
        self._shadow_targets: dict[str, dict[str, str]] = {}
        self._context_windows: dict[str, int] = {}
        self._provider_cache_multipliers: dict[str, dict[str, float]] = {}
        self._lock = threading.RLock()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            with self._lock:
                if not self._loaded:
                    self._load_seed_catalog()
                    self._loaded = True

    def _load_seed_catalog(self, path: Path | None = None) -> None:
        """Load the seed catalog JSON."""
        catalog_path = path or _SEED_CATALOG_PATH
        try:
            raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            log.warning("Failed to load seed catalog from %s: %s", catalog_path, exc)
            return

        # Provider cache multipliers
        self._provider_cache_multipliers = raw.get("provider_cache_multipliers", {})

        # Shadow targets
        self._shadow_targets = raw.get("shadow_targets", {})

        # Max input-context windows (tokens), keyed by dateless model id
        self._context_windows = {
            str(mid).lower(): int(tokens)
            for mid, tokens in raw.get("context_windows", {}).items()
        }

        # Models
        models_raw = raw.get("models", {})
        for model_id, data in models_raw.items():
            info = ModelInfo(
                model_id=model_id,
                provider=data.get("provider", "unknown"),
                tier=data.get("tier", 2),
                input_per_mtok=data.get("input", 3.0),
                output_per_mtok=data.get("output", 15.0),
                cache_read_per_mtok=data.get("cache_read"),
                cache_write_per_mtok=data.get("cache_write"),
                translations=data.get("translations", {}),
                aliases=data.get("aliases", []),
                source="seed",
                max_input_tokens=self._lookup_context_window(model_id.lower()),
            )
            self._models[model_id] = info
            for alias in info.aliases:
                self._aliases[alias] = model_id

        log.debug("Loaded %d models from seed catalog", len(self._models))

    def resolve(self, model: str) -> ModelInfo:
        """Resolve a model ID to its ModelInfo.

        Resolution chain:
        1. Exact match in registry
        2. Alias match
        3. Strip date suffix and retry
        4. Longest prefix match in registry
        5. Family pattern match (inferred)
        6. Provider-aware default
        """
        self._ensure_loaded()

        if not model:
            return _UNKNOWN_DEFAULT

        with self._lock:
            # 1. Exact match
            if model in self._models:
                return self._models[model]

            # 2. Alias match
            canonical = self._aliases.get(model)
            if canonical and canonical in self._models:
                return self._models[canonical]

            # 3. Strip date suffix
            stripped = _DATE_SUFFIX_RE.sub("", model)
            if stripped != model:
                if stripped in self._models:
                    return self._models[stripped]
                canonical = self._aliases.get(stripped)
                if canonical and canonical in self._models:
                    return self._models[canonical]

            # 4. Longest prefix match in seed catalog
            best_match: ModelInfo | None = None
            best_len = 0
            for mid, info in self._models.items():
                if model.startswith(mid) and len(mid) > best_len:
                    best_match = info
                    best_len = len(mid)
            if best_match is not None:
                return best_match

        # 5. Family pattern match (no lock needed — families are immutable)
        for rule in self._families:
            if rule.matches(model):
                return ModelInfo(
                    model_id=model,
                    provider=rule.provider,
                    tier=rule.tier,
                    input_per_mtok=rule.input_per_mtok,
                    output_per_mtok=rule.output_per_mtok,
                    cache_read_per_mtok=rule.infer_cache_read(rule.input_per_mtok),
                    cache_write_per_mtok=rule.infer_cache_write(rule.input_per_mtok),
                    translations=rule.infer_translation(model),
                    source="inferred",
                )

        # 6. Provider-aware default
        return self._provider_aware_default(model)

    def _provider_aware_default(self, model: str) -> ModelInfo:
        """Last-resort default based on provider prefix detection."""
        provider = self.detect_provider(model)
        if provider == "openai":
            return ModelInfo(
                model_id=model,
                provider="openai",
                tier=2,
                input_per_mtok=2.50,
                output_per_mtok=10.0,
                source="inferred",
            )
        # Default to sonnet-class pricing
        return ModelInfo(
            model_id=model,
            provider=provider if provider != "unknown" else "anthropic",
            tier=2,
            input_per_mtok=3.0,
            output_per_mtok=15.0,
            cache_read_per_mtok=0.30,
            cache_write_per_mtok=3.75,
            source="inferred",
        )

    def _lookup_context_window(self, model: str) -> int | None:
        """Match a normalized (lowercase, trimmed) model id against the
        context-window table. Returns None when unknown."""
        windows = self._context_windows

        # 1. Exact match.
        if model in windows:
            return windows[model]

        # 2. Strip provider prefix ("anthropic/claude-…") and retry.
        if "/" in model:
            suffix = model.split("/", 1)[1]
            if suffix in windows:
                return windows[suffix]
            model = suffix

        # 3. Strip trailing 8-digit date suffix and retry.
        stripped = _DATE_SUFFIX_RE.sub("", model)
        if stripped != model and stripped in windows:
            return windows[stripped]

        # 4. Longest-prefix match.
        best_key: str | None = None
        best_len = 0
        for key in windows:
            if (model.startswith(key) or stripped.startswith(key)) and len(key) > best_len:
                best_key = key
                best_len = len(key)
        if best_key is not None:
            return windows[best_key]

        return None

    def get_max_context(self, model: str | None) -> int | None:
        """Resolve the max input-context window in tokens for a model id.

        Values come from the seed catalog's ``context_windows`` section
        (verified against the provider's published Models API
        ``max_input_tokens``). Returns ``None`` when the model is unknown —
        the caller is responsible for falling back to its configured static
        threshold rather than silently assuming a default.

        A trailing ``[1m]`` marker selects the provider's 1M-input-token
        long-context tier of the base model: the base model must still be
        known (unknown bases stay ``None``), and the result is floored at
        1,000,000 tokens.
        """
        self._ensure_loaded()

        if not model:
            return None

        m = model.lower().strip()
        if not m:
            return None

        long_context = m.endswith(_LONG_CONTEXT_SUFFIX)
        if long_context:
            m = m[: -len(_LONG_CONTEXT_SUFFIX)].strip()
            if not m:
                return None

        with self._lock:
            base = self._lookup_context_window(m)

        if base is None:
            return None
        if long_context:
            return max(base, _LONG_CONTEXT_TOKENS)
        return base

    def context_window_models(self) -> list[str]:
        """Return the context-window table's model-id keys (sorted)."""
        self._ensure_loaded()
        with self._lock:
            return sorted(self._context_windows.keys())

    def detect_provider(self, model: str) -> str:
        """Detect provider from model name using prefix matching."""
        lower = model.lower()
        for prefix, provider in PROVIDER_PREFIXES:
            if lower.startswith(prefix):
                return provider
        return "unknown"

    def translate_model(self, model_id: str, provider: str) -> str:
        """Translate Anthropic model ID to provider-specific ID.

        Returns original model_id if no translation exists (pass-through).
        """
        info = self.resolve(model_id)
        return info.translations.get(provider, model_id)

    def get_shadow_target(self, shadow_provider: str) -> tuple[str, str]:
        """Map shadow provider string to (upstream_url, model_name).

        Returns ("", "") if unknown (fail-open: shadow silently skipped).
        """
        self._ensure_loaded()
        key = shadow_provider.lower().strip()
        with self._lock:
            target = self._shadow_targets.get(key)
        if target:
            return (target.get("url", ""), target.get("model", ""))
        return ("", "")

    def get_cheaper_alternative(self, model: str) -> tuple[str, float] | None:
        """Find a cheaper model in the same provider, return (model_id, savings_fraction).

        Uses tier ordering: frontier→premium→mid→budget.
        Returns None if no cheaper alternative is known.
        """
        info = self.resolve(model)
        if info.tier <= 1:
            return None

        self._ensure_loaded()
        # Step down one tier — find the best model at exactly (tier - 1)
        # If nothing at that tier, try lower tiers
        best: ModelInfo | None = None
        with self._lock:
            for search_tier in range(info.tier - 1, 0, -1):
                for mid, candidate in self._models.items():
                    if candidate.provider != info.provider:
                        continue
                    if candidate.tier != search_tier:
                        continue
                    if candidate.source != "seed":
                        continue
                    # Skip alias-style entries (e.g. "claude-opus" without version)
                    if not any(c.isdigit() for c in mid) and mid not in ("codex", "o3", "o1"):
                        continue
                    # Prefer models with higher input cost within the tier (more capable)
                    if best is None or candidate.input_per_mtok > best.input_per_mtok:
                        best = candidate
                if best is not None:
                    break

        if best is None:
            return None

        if info.input_per_mtok > 0:
            savings = 1.0 - (best.input_per_mtok / info.input_per_mtok)
        else:
            savings = 0.0
        return (best.model_id, round(savings, 2))

    def get_all_tiers(self) -> dict[str, int]:
        """Return model_id → tier mapping for all known models."""
        self._ensure_loaded()
        with self._lock:
            result = {mid: info.tier for mid, info in self._models.items()}
            # Also add provider-prefixed versions for backward compat
            for mid, info in self._models.items():
                prefixed = f"{info.provider}/{mid}"
                result[prefixed] = info.tier
        return result

    def get_default_routes(self) -> dict[str, str]:
        """Return model_id → provider mapping for all known models."""
        self._ensure_loaded()
        with self._lock:
            return {mid: info.provider for mid, info in self._models.items()}

    def all_models(self) -> list[ModelInfo]:
        """Return all registered models."""
        self._ensure_loaded()
        with self._lock:
            return list(self._models.values())

    def register(self, info: ModelInfo) -> None:
        """Register or update a model at runtime (e.g. from discovery)."""
        with self._lock:
            self._models[info.model_id] = info
            for alias in info.aliases:
                self._aliases[alias] = info.model_id

    def reload(self, path: Path | None = None) -> None:
        """Reload the seed catalog (hot-reload for config changes)."""
        with self._lock:
            self._models.clear()
            self._aliases.clear()
            self._shadow_targets.clear()
            self._context_windows.clear()
            self._provider_cache_multipliers.clear()
            self._load_seed_catalog(path)

    @property
    def provider_cache_multipliers(self) -> dict[str, dict[str, float]]:
        self._ensure_loaded()
        with self._lock:
            return dict(self._provider_cache_multipliers)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: ModelRegistry | None = None
_instance_lock = threading.Lock()


def get_registry() -> ModelRegistry:
    """Get the global ModelRegistry singleton (lazy-loaded)."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ModelRegistry()
    return _instance
