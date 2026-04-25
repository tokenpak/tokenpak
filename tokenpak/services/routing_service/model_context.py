# SPDX-License-Identifier: Apache-2.0
"""Dynamic per-model context-window registry — provider-agnostic.

Why this exists
---------------

Models have hard input-token caps (Claude Haiku 4.5 = 200k, Opus 4.7 =
1M, gpt-5.4 = 128k, etc.). When tokenpak's bridge dispatches a request
to a model with a smaller window than the inbound payload, the
upstream provider returns ``invalid_request`` after a wasted round
trip. Right behavior: reject *fast* at the proxy boundary so callers
(OpenClaw, etc.) get a clean signal, can compact + retry without
paying network latency.

Self-maintaining + provider-agnostic
------------------------------------

Per the standing rule (``feedback_always_dynamic`` 2026-04-16 + Kevin
2026-04-24): tokenpak is provider/model/platform agnostic; what gets
set up must be self-maintaining and dynamic — users never edit a
hand-written model list.

This module is structured around a thin
:class:`ModelRegistryProvider` Protocol. Each provider implements
``fetch() -> Iterable[ModelLimits]`` against its own model-listing
API. The federation in :class:`ModelContextRegistry` walks every
registered provider, merges results, and caches at
``~/.tokenpak/model_context_cache.json`` with a 24h TTL. New
providers (OpenAI, Google, future) plug in with one ``register()``
call at module import — no changes to the consumer code.

Built-in providers
~~~~~~~~~~~~~~~~~~

- :class:`AnthropicModelRegistryProvider` — hits
  ``GET https://api.anthropic.com/v1/models`` with the user's Claude
  CLI OAuth. Returns one record per Anthropic model with
  ``max_input_tokens`` + ``max_tokens``.

Adding a new provider
~~~~~~~~~~~~~~~~~~~~~

```python
class OpenAIModelRegistryProvider:
    name = "openai"
    def fetch(self) -> Iterable[ModelLimits]:
        # GET /v1/models with the user's OpenAI key, parse into ModelLimits
        ...

register_provider(OpenAIModelRegistryProvider())
```

That's the whole contract. Cache, TTL, fail-open semantics, and
consumer integration are inherited.

Fail-open everywhere
~~~~~~~~~~~~~~~~~~~~

If a provider's API is unreachable, we keep the previous cache. If
no cache exists, we return ``None`` for unknown models and the
caller skips validation (lets upstream return the real error
unchanged). Tokenpak never *blocks* traffic on an unknown model —
the registry is a fast-fail optimization, not a gate.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol

logger = logging.getLogger(__name__)


_DEFAULT_CACHE = Path.home() / ".tokenpak" / "model_context_cache.json"
_DEFAULT_TTL_SECONDS = 24 * 3600  # 24 hours
_REQUEST_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class ModelLimits:
    """What we know about a model's context economy."""

    model_id: str
    max_input_tokens: int
    max_output_tokens: Optional[int] = None
    provider: Optional[str] = None  # e.g. "anthropic", "openai"


class ModelRegistryProvider(Protocol):
    """One provider's model-listing contract."""

    name: str

    def fetch(self) -> Iterable[ModelLimits]:
        """Return every model this provider knows about.

        Implementations should raise on unrecoverable error (network,
        auth) — the federation catches + falls back gracefully.
        """
        ...


_PROVIDERS: List[ModelRegistryProvider] = []
_PROVIDERS_LOCK = threading.Lock()


def register_provider(provider: ModelRegistryProvider) -> None:
    """Add a provider to the registry. Idempotent on ``name``."""
    with _PROVIDERS_LOCK:
        for i, p in enumerate(_PROVIDERS):
            if p.name == provider.name:
                _PROVIDERS[i] = provider
                return
        _PROVIDERS.append(provider)


def registered_providers() -> List[ModelRegistryProvider]:
    """Introspection helper for tests + /health."""
    with _PROVIDERS_LOCK:
        return list(_PROVIDERS)


# ── Federation: lazy-loaded, file-cached snapshot ─────────────────


class ModelContextRegistry:
    """Lazy-loaded, file-cached, federation across registered providers.

    Methods are thread-safe. First ``get_*`` call triggers a cache
    load; refresh happens on TTL expiry or model-not-found-but-cache-
    is-stale.
    """

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        env_override = os.environ.get("TOKENPAK_MODEL_CONTEXT_CACHE", "").strip()
        if env_override:
            cache_path = Path(env_override).expanduser()
        self._cache_path = Path(cache_path) if cache_path else _DEFAULT_CACHE
        self._ttl = float(ttl_seconds)
        self._lock = threading.Lock()
        self._snapshot: Optional[Dict[str, ModelLimits]] = None
        self._snapshot_loaded_at: float = 0.0

    # ── Public API ──────────────────────────────────────────────────

    def get_context_window(self, model_id: str) -> Optional[int]:
        """Return the max input tokens for ``model_id``, or ``None``."""
        rec = self.get_limits(model_id)
        return rec.max_input_tokens if rec else None

    def get_limits(self, model_id: str) -> Optional[ModelLimits]:
        """Return the full :class:`ModelLimits` record, or ``None``.

        Lookup is normalization-aware. The /v1/models API returns
        both versioned (``claude-haiku-4-5-20251001``) and
        unversioned (``claude-opus-4-7``) ids depending on the
        model. Callers commonly send the unversioned form. If the
        first lookup misses, we try stripping a trailing
        ``-YYYYMMDD`` date and re-search for the unversioned id;
        if that misses, we try matching any cached id that begins
        with the requested unversioned prefix (which covers the
        opposite case where the caller sends versioned + cache has
        unversioned).
        """
        if not model_id:
            return None
        snapshot = self._ensure_snapshot()

        rec = self._lookup_with_aliases(snapshot, model_id)
        if rec is not None:
            return rec

        # Not in cache + try one refresh in case the model is new.
        if self._refresh_if_stale_or_missing(model_id):
            snapshot = self._snapshot or {}
            rec = self._lookup_with_aliases(snapshot, model_id)
            if rec is not None:
                return rec
        return None

    @staticmethod
    def _lookup_with_aliases(
        snapshot: Dict[str, ModelLimits], model_id: str
    ) -> Optional[ModelLimits]:
        # 1. Exact match
        if model_id in snapshot:
            return snapshot[model_id]
        # 2. Strip trailing -YYYYMMDD if present (versioned → unversioned)
        import re

        m = re.match(r"^(.+)-\d{8}$", model_id)
        if m and m.group(1) in snapshot:
            return snapshot[m.group(1)]
        # 3. Look for any cached id that starts with the unversioned
        #    form of the request (covers caller sends unversioned, cache
        #    has versioned + unversioned alias not present).
        prefix = m.group(1) + "-" if m else model_id + "-"
        for cached_id, rec in snapshot.items():
            if cached_id.startswith(prefix):
                return rec
        return None

    def all_limits(self) -> Dict[str, ModelLimits]:
        """Every record currently known. Useful for /health + diagnostics."""
        return dict(self._ensure_snapshot())

    def force_refresh(self) -> bool:
        """Bypass TTL + refetch from every registered provider.

        Returns True if at least one provider responded successfully.
        """
        with self._lock:
            return self._refresh_locked()

    # ── Cache lifecycle ─────────────────────────────────────────────

    def _ensure_snapshot(self) -> Dict[str, ModelLimits]:
        with self._lock:
            if self._snapshot is None:
                self._snapshot = self._load_cache_file()
                if self._snapshot is None or self._is_stale_locked():
                    self._refresh_locked()
            elif self._is_stale_locked():
                self._refresh_locked()
            return dict(self._snapshot or {})

    def _is_stale_locked(self) -> bool:
        if self._snapshot is None:
            return True
        return (time.time() - self._snapshot_loaded_at) > self._ttl

    def _refresh_if_stale_or_missing(self, model_id: str) -> bool:
        with self._lock:
            if (
                self._snapshot is None
                or self._is_stale_locked()
                or model_id not in self._snapshot
            ):
                return self._refresh_locked()
            return False

    def _refresh_locked(self) -> bool:
        """Walk every registered provider; merge results into cache.

        Returns True if any provider returned a result. Failures are
        logged at INFO (we keep the previous cache on partial outage).
        """
        merged: Dict[str, ModelLimits] = {}
        if self._snapshot:
            # Carry forward existing entries; let provider results overwrite.
            merged.update(self._snapshot)
        with _PROVIDERS_LOCK:
            providers = list(_PROVIDERS)
        any_ok = False
        for prov in providers:
            try:
                records = list(prov.fetch())
            except Exception as err:  # noqa: BLE001
                logger.info(
                    "model_context: provider=%s fetch failed (%s: %s)",
                    prov.name, type(err).__name__, err,
                )
                continue
            for rec in records:
                if not isinstance(rec, ModelLimits):
                    continue
                if not rec.model_id or rec.max_input_tokens <= 0:
                    continue
                merged[rec.model_id] = rec
            any_ok = True

        if not any_ok and self._snapshot is None:
            # First run + every provider failed — set empty snapshot
            # so we don't keep retrying on every access.
            self._snapshot = {}
            self._snapshot_loaded_at = time.time()
            return False

        self._snapshot = merged
        self._snapshot_loaded_at = time.time()
        self._write_cache_file(merged)
        logger.info(
            "model_context: registry refreshed (%d models across %d providers)",
            len(merged), sum(1 for p in providers),
        )
        return True

    # ── Disk persistence ────────────────────────────────────────────

    def _load_cache_file(self) -> Optional[Dict[str, ModelLimits]]:
        if not self._cache_path.is_file():
            return None
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        models = data.get("models")
        ts = data.get("loaded_at")
        if not isinstance(models, dict) or not isinstance(ts, (int, float)):
            return None
        snapshot: Dict[str, ModelLimits] = {}
        for mid, rec in models.items():
            if not isinstance(rec, dict):
                continue
            mit = rec.get("max_input_tokens")
            if not isinstance(mit, int) or mit <= 0:
                continue
            mot = rec.get("max_output_tokens")
            prov = rec.get("provider")
            snapshot[mid] = ModelLimits(
                model_id=mid,
                max_input_tokens=mit,
                max_output_tokens=mot if isinstance(mot, int) and mot > 0 else None,
                provider=prov if isinstance(prov, str) else None,
            )
        self._snapshot_loaded_at = float(ts)
        return snapshot

    def _write_cache_file(self, snapshot: Dict[str, ModelLimits]) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "loaded_at": self._snapshot_loaded_at,
                "models": {
                    mid: {
                        "max_input_tokens": rec.max_input_tokens,
                        "max_output_tokens": rec.max_output_tokens,
                        "provider": rec.provider,
                    }
                    for mid, rec in snapshot.items()
                },
            }
            tmp = self._cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._cache_path)
        except OSError as err:
            logger.info("model_context: cache write failed (%s)", err)


# ── Built-in: Anthropic provider ──────────────────────────────────


class AnthropicModelRegistryProvider:
    """Fetch model list from ``GET https://api.anthropic.com/v1/models``.

    Auth: reuses the user's Claude CLI OAuth token from
    ``~/.claude/.credentials.json``. Same credential the OAuth
    backend already consults — no separate setup required.
    """

    name = "anthropic"
    _URL = "https://api.anthropic.com/v1/models"

    def fetch(self) -> Iterable[ModelLimits]:
        token = self._read_oauth_token()
        if not token:
            raise RuntimeError(
                "no Claude CLI OAuth token available "
                "(~/.claude/.credentials.json missing or empty)"
            )
        req = urllib.request.Request(
            self._URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "tokenpak-model-context-registry",
            },
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("/v1/models returned non-object")
        for entry in payload.get("data") or []:
            if not isinstance(entry, dict):
                continue
            mid = entry.get("id")
            mit = entry.get("max_input_tokens")
            mot = entry.get("max_tokens")
            if not isinstance(mid, str) or not isinstance(mit, int) or mit <= 0:
                continue
            yield ModelLimits(
                model_id=mid,
                max_input_tokens=mit,
                max_output_tokens=mot if isinstance(mot, int) and mot > 0 else None,
                provider="anthropic",
            )

    @staticmethod
    def _read_oauth_token() -> Optional[str]:
        path = Path.home() / ".claude" / ".credentials.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        if not isinstance(oauth, dict):
            return None
        token = oauth.get("accessToken")
        return token if isinstance(token, str) and token.strip() else None


# Register the Anthropic provider at import. Future providers
# (OpenAI / Google / Codex / etc.) call register_provider() at their
# own module's import time.
register_provider(AnthropicModelRegistryProvider())


# ── Process-wide singleton ─────────────────────────────────────────

_registry: Optional[ModelContextRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ModelContextRegistry:
    """Return the shared :class:`ModelContextRegistry`."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ModelContextRegistry()
    return _registry


def get_context_window(model_id: str) -> Optional[int]:
    """Convenience: max input tokens for ``model_id`` (or ``None``)."""
    return get_registry().get_context_window(model_id)


__all__ = [
    "AnthropicModelRegistryProvider",
    "ModelContextRegistry",
    "ModelLimits",
    "ModelRegistryProvider",
    "get_context_window",
    "get_registry",
    "register_provider",
    "registered_providers",
]
