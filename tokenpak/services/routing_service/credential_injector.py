# SPDX-License-Identifier: Apache-2.0
"""Credential injector — per-provider auth rewrite for byte-preserved forwards.

Why this exists
---------------

Agent platforms (OpenClaw, Codex, future adapters) route their LLM traffic
through the tokenpak proxy at ``http://127.0.0.1:8766``. Each platform
authenticates to tokenpak with whatever credential shape it has on hand:

- OpenClaw often carries a Bearer token from its own auth profile or an
  ``x-api-key`` placeholder. Neither works against Anthropic's OAuth path,
  so forwarding byte-preserved produces ``401 invalid x-api-key``.
- Codex carries a JWT from its own ChatGPT OAuth, but it needs
  ``chatgpt-account-id`` + ``originator`` headers + path rewrite before
  ``chatgpt.com/backend-api`` accepts it.

This module resolves *what auth the upstream actually expects* given the
declared tokenpak provider, and returns an :class:`InjectionPlan` the
proxy applies in the forward path. The caller's original auth headers
are stripped; the provider's real credentials are injected from the
right on-disk file (``~/.claude/.credentials.json`` for Claude OAuth,
``~/.codex/auth.json`` for Codex). Byte-level payload stays intact
unless the plan specifies a transform.

Design
------

- **One provider = one CredentialProvider.** Each declares its slug
  (``tokenpak-claude-code`` / ``tokenpak-openai-codex`` / …) + a
  ``resolve()`` method returning an :class:`InjectionPlan`. Third-party
  adapters register at import time via :func:`register`.
- **Resolve is the hot path.** ``resolve(provider_name)`` walks the
  registry and returns the first match. Short, stateless, and safe to
  call per-request; file reads are cached with a short TTL so token
  rotation surfaces within seconds but we don't hit the disk on every
  inbound Messages request.
- **No secrets in logs, ever.** Tokens never pass through ``print`` /
  ``logger.info``. The injector only reads the file and emits header
  dicts; callers must not dump those headers with the values intact.

Honors ``feedback_always_dynamic`` (2026-04-16): no provider enumeration
at the call site. Proxy asks ``resolve(provider_slug)`` and applies
whatever plan comes back. Unknown providers return ``None`` and the
proxy preserves its byte-forward default.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List, Optional, Protocol

logger = logging.getLogger(__name__)


# ── Plan record ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class InjectionPlan:
    """What the proxy should do to the forward request for a provider.

    - ``strip_headers``: case-insensitive names to remove before forwarding.
    - ``add_headers``: headers to add (overrides strip when the same key
      appears in both; add wins).
    - ``target_url_override``: when set, replaces the original target URL
      entirely (e.g. ``chatgpt.com/backend-api/codex/responses`` for
      Codex). ``None`` means keep the original target.
    - ``body_transform``: optional bytes → bytes transform applied
      before forward. Codex needs a few payload-normalization tweaks
      (``stream=true``, ``store=false``, drop ``max_output_tokens``).
      None = leave body alone (byte-preserve).
    """

    strip_headers: FrozenSet[str] = frozenset()
    add_headers: Dict[str, str] = field(default_factory=dict)
    target_url_override: Optional[str] = None
    body_transform: Optional[Callable[[bytes], bytes]] = None


# ── Provider protocol + registry ─────────────────────────────────────


class CredentialProvider(Protocol):
    """One provider's credential-resolution contract."""

    name: str

    def resolve(self) -> Optional[InjectionPlan]:
        """Return an InjectionPlan, or None when this provider's creds aren't available."""
        ...


_REGISTRY: List[CredentialProvider] = []
_REGISTRY_LOCK = threading.Lock()


def register(provider: CredentialProvider) -> None:
    """Add a provider to the registry. Idempotent on ``name``."""
    with _REGISTRY_LOCK:
        for i, existing in enumerate(_REGISTRY):
            if existing.name == provider.name:
                _REGISTRY[i] = provider
                return
        _REGISTRY.append(provider)


def registered() -> List[CredentialProvider]:
    """Introspection helper for tests."""
    with _REGISTRY_LOCK:
        return list(_REGISTRY)


def resolve(provider_name: str) -> Optional[InjectionPlan]:
    """Ask the registry for a plan. Returns ``None`` when no match."""
    with _REGISTRY_LOCK:
        providers = list(_REGISTRY)
    for p in providers:
        if p.name == provider_name:
            try:
                return p.resolve()
            except Exception as err:  # noqa: BLE001
                logger.warning(
                    "credential_injector: %s.resolve() raised %s: %s",
                    p.name, type(err).__name__, err,
                )
                return None
    return None


# ── TTL cache for credential file reads ──────────────────────────────


_TTL_SECONDS = 30.0


@dataclass
class _Cached:
    value: Optional[InjectionPlan]
    expires_at: float


_CACHE: Dict[str, _Cached] = {}
_CACHE_LOCK = threading.Lock()


def _cached_resolve(key: str, reader: Callable[[], Optional[InjectionPlan]]) -> Optional[InjectionPlan]:
    """Memoize one provider's resolve() for ``_TTL_SECONDS`` seconds.

    Rationale: OAuth tokens rotate on hour-scale. Reading the JSON file
    every request is wasteful under parallel OpenClaw worker traffic;
    a 30-second TTL picks up rotations promptly without hammering the
    filesystem or the credential lock on ``~/.claude/``.
    """
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is not None and entry.expires_at > now:
            return entry.value
    plan = reader()
    with _CACHE_LOCK:
        _CACHE[key] = _Cached(value=plan, expires_at=now + _TTL_SECONDS)
    return plan


def invalidate_cache() -> None:
    """Drop the cache — tests + explicit token-rotation notifications."""
    with _CACHE_LOCK:
        _CACHE.clear()


# ── Built-in: Claude Code OAuth ──────────────────────────────────────


class ClaudeCodeCredentialProvider:
    """Inject Claude CLI OAuth from ``~/.claude/.credentials.json``.

    The CLI keeps a rotated access token at
    ``claudeAiOauth.accessToken`` plus an ``expiresAt`` timestamp.
    We read the token + pair it with the ``anthropic-beta:
    oauth-2025-04-20`` marker Anthropic's OAuth path requires. Any
    caller auth (Bearer placeholder, ``x-api-key``) is stripped.
    """

    name = "tokenpak-claude-code"

    def __init__(self, creds_path: Optional[Path] = None) -> None:
        self._path = creds_path or (Path.home() / ".claude" / ".credentials.json")

    def _load(self) -> Optional[InjectionPlan]:
        try:
            raw = self._path.read_text()
        except OSError:
            logger.info(
                "credential_injector[claude-code]: creds file not found at %s",
                self._path,
            )
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "credential_injector[claude-code]: creds file at %s is not JSON",
                self._path,
            )
            return None
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        if not isinstance(oauth, dict):
            logger.warning(
                "credential_injector[claude-code]: missing claudeAiOauth block"
            )
            return None
        access_token = oauth.get("accessToken")
        if not isinstance(access_token, str) or not access_token.strip():
            logger.warning(
                "credential_injector[claude-code]: missing or empty accessToken"
            )
            return None
        # Expiry check: informational only. If the token is expired
        # Anthropic will 401 and the caller sees the error — we do
        # not refresh proactively here (the CLI is the refresh owner).
        try:
            expires_at = int(oauth.get("expiresAt") or 0)
            if expires_at and expires_at < int(time.time() * 1000):
                logger.info(
                    "credential_injector[claude-code]: access token looks expired; "
                    "the CLI should refresh on next invocation"
                )
        except (TypeError, ValueError):
            pass
        return InjectionPlan(
            strip_headers=frozenset({"authorization", "x-api-key"}),
            add_headers={
                "Authorization": f"Bearer {access_token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
        )

    def resolve(self) -> Optional[InjectionPlan]:
        return _cached_resolve(self.name, self._load)


# ── Built-in: Codex (ChatGPT OAuth) ──────────────────────────────────


class CodexCredentialProvider:
    """Inject Codex OAuth from ``~/.codex/auth.json``.

    Codex's ``tokens.access_token`` is the JWT to forward; ``account_id``
    goes in ``chatgpt-account-id``. ``originator: codex_cli_rs`` is a
    required marker for the ChatGPT backend to accept the call on the
    Codex path. Path rewrite + payload normalization also land here so
    a tokenpak-openai-codex request against ``/v1/responses`` gets routed
    to ``chatgpt.com/backend-api/codex/responses`` with the Codex-
    specific body constraints (``stream=true``, ``store=false``, drop
    ``max_output_tokens``).
    """

    name = "tokenpak-openai-codex"
    # Canonical ChatGPT-backend endpoint per the Apr 10-12 working
    # path preserved in the vault snapshot. Not configurable — this is
    # where Codex tokens are valid; any other upstream rejects.
    _UPSTREAM = "https://chatgpt.com/backend-api/codex/responses"

    def __init__(self, creds_path: Optional[Path] = None) -> None:
        self._path = creds_path or (Path.home() / ".codex" / "auth.json")

    @staticmethod
    def _normalize_payload(body: bytes) -> bytes:
        """Apply the Codex payload constraints to ``body``.

        Byte-identity is NOT a Codex requirement (unlike Anthropic's
        cache_control routing, which depends on preserved bytes). Codex
        just needs a JSON body with ``stream=true``, ``store=false``,
        and without ``max_output_tokens``. If the body is unparseable,
        we forward it as-is and let the backend reject.
        """
        if not body:
            return body
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body
        if not isinstance(data, dict):
            return body
        data["stream"] = True
        data["store"] = False
        data.pop("max_output_tokens", None)
        try:
            return json.dumps(data).encode("utf-8")
        except (TypeError, ValueError):
            return body

    def _load(self) -> Optional[InjectionPlan]:
        try:
            raw = self._path.read_text()
        except OSError:
            logger.info(
                "credential_injector[codex]: creds file not found at %s",
                self._path,
            )
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "credential_injector[codex]: creds file at %s is not JSON",
                self._path,
            )
            return None
        tokens = data.get("tokens") if isinstance(data, dict) else None
        if not isinstance(tokens, dict):
            logger.warning(
                "credential_injector[codex]: missing tokens block"
            )
            return None
        access_token = tokens.get("access_token")
        account_id = tokens.get("account_id")
        if not isinstance(access_token, str) or not access_token.strip():
            logger.warning(
                "credential_injector[codex]: missing or empty access_token"
            )
            return None
        add_headers: Dict[str, str] = {
            "Authorization": f"Bearer {access_token}",
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
        }
        if isinstance(account_id, str) and account_id.strip():
            add_headers["chatgpt-account-id"] = account_id
        return InjectionPlan(
            strip_headers=frozenset({"authorization", "x-api-key"}),
            add_headers=add_headers,
            target_url_override=self._UPSTREAM,
            body_transform=self._normalize_payload,
        )

    def resolve(self) -> Optional[InjectionPlan]:
        return _cached_resolve(self.name, self._load)


# ── Register built-ins at import ─────────────────────────────────────


register(ClaudeCodeCredentialProvider())
register(CodexCredentialProvider())


__all__ = [
    "ClaudeCodeCredentialProvider",
    "CodexCredentialProvider",
    "CredentialProvider",
    "InjectionPlan",
    "invalidate_cache",
    "register",
    "registered",
    "resolve",
]
