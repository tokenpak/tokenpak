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
import uuid
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
    - ``merge_headers``: headers whose value is *appended* to any
      caller-supplied value with a comma separator. Intended for
      ``anthropic-beta`` where we need our Claude Code markers in the
      header but also want to preserve the caller's feature-gate
      markers (``fine-grained-tool-streaming-*`` etc.). Applied AFTER
      strip + add so the caller's original value still reaches the
      merge step. Empty caller → falls back to our value.
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
    merge_headers: Dict[str, str] = field(default_factory=dict)
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


# ── Proxy-scoped session id for Claude Code billing ──────────────────
#
# Anthropic requires ``X-Claude-Code-Session-Id`` on Claude Code
# traffic. One UUID per proxy process = one logical Claude session
# serving all platform-bridged traffic, which matches how
# interactive ``claude`` CLI uses a stable session-id for the life of
# an instance. Persists across requests so billing attributes them
# coherently.

_PROXY_SESSION_ID: Optional[str] = None
_PROXY_SESSION_LOCK = threading.Lock()


def _get_proxy_session_id() -> str:
    """Return a stable UUID used as ``X-Claude-Code-Session-Id`` for all
    platform-bridged Claude Code traffic in this proxy process."""
    global _PROXY_SESSION_ID
    if _PROXY_SESSION_ID is None:
        with _PROXY_SESSION_LOCK:
            if _PROXY_SESSION_ID is None:
                _PROXY_SESSION_ID = str(uuid.uuid4())
    return _PROXY_SESSION_ID


def _reset_proxy_session_id() -> None:
    """Test helper: force a fresh proxy session id on the next call."""
    global _PROXY_SESSION_ID
    with _PROXY_SESSION_LOCK:
        _PROXY_SESSION_ID = None


# ── Built-in: Claude Code OAuth ──────────────────────────────────────


class ClaudeCodeCredentialProvider:
    """Inject Claude CLI OAuth + full Claude Code client profile.

    The CLI keeps a rotated access token at
    ``claudeAiOauth.accessToken`` plus an ``expiresAt`` timestamp.
    We read the token and *also reproduce every header Claude Code
    CLI itself sends on the wire* — not just the OAuth beta. This is
    the v1.3.17 fix: Anthropic treats incoming traffic differently
    based on the full Claude Code client profile (billing tier,
    caching, quota rules). Without the ``claude-code-20250219``
    beta marker and the ``claude-cli`` User-Agent, traffic gets
    OAuth'd but routed as generic Anthropic API usage — which
    exhausts the user's Claude Max 'extra usage' pool while the
    interactive CLI doesn't, creating the divergence Kevin flagged
    2026-04-24 (same OAuth token, different billing behavior).

    The full Claude Code wire profile we reproduce:
      - ``Authorization: Bearer <access_token>``
      - ``anthropic-beta: claude-code-20250219,oauth-2025-04-20,context-1m-2025-08-07,interleaved-thinking``
      - ``anthropic-dangerous-direct-browser-access: true``
      - ``User-Agent: claude-cli/<version> (external, cli)``
      - ``x-app: cli``

    Caller's own ``Authorization`` / ``x-api-key`` / ``anthropic-beta``
    headers are stripped before the injection so there's no residue
    from OpenClaw's SDK auth-shape.
    """

    name = "tokenpak-claude-code"

    # Minimum anthropic-beta set that identifies traffic to Anthropic
    # as "Claude Code OAuth" rather than "generic API OAuth". Verified
    # against live CLI traffic 2026-04-24. We deliberately do NOT
    # forward the CLI's full beta list (``context-1m-2025-08-07``,
    # date-versioned ``interleaved-thinking-YYYY-MM-DD``) because
    # those are feature-gated beta tracks that change over CLI
    # versions; including stale ones produces ``400 invalid beta``.
    # If the caller shipped their own safe beta markers, we MERGE
    # them with ours below so tool-use betas (``fine-grained-tool-
    # streaming-*``) still work from OpenClaw's SDK.
    _CLAUDE_CODE_BETA_BASE = "claude-code-20250219,oauth-2025-04-20"
    # User-Agent profile for the Claude CLI binary. Kept here as a
    # fallback constant; ``_detect_cli_version()`` tries to read the
    # actual installed ``claude`` binary's version first so the
    # profile follows whatever the user has installed (dynamic +
    # no hardcoded version, per feedback_always_dynamic).
    _CLI_UA_FALLBACK = "claude-cli/2.1.119 (external, cli)"

    def __init__(self, creds_path: Optional[Path] = None) -> None:
        self._path = creds_path or (Path.home() / ".claude" / ".credentials.json")

    @staticmethod
    def _detect_cli_version() -> str:
        """Probe ``claude --version`` and return the UA the binary
        identifies itself as. Result cached at module scope for the
        lifetime of the process (version is stable until claude
        upgrades).
        """
        import shutil
        import subprocess

        global _CACHED_CLI_UA
        cached = globals().get("_CACHED_CLI_UA")
        if cached:
            return cached

        binary = shutil.which("claude")
        if not binary:
            ua = ClaudeCodeCredentialProvider._CLI_UA_FALLBACK
        else:
            try:
                out = subprocess.run(
                    [binary, "--version"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                line = out.stdout.decode("utf-8", errors="replace").strip()
                # Expected: "2.1.119 (Claude Code)"
                version = line.split()[0] if line else ""
                if version:
                    ua = f"claude-cli/{version} (external, cli)"
                else:
                    ua = ClaudeCodeCredentialProvider._CLI_UA_FALLBACK
            except Exception:
                ua = ClaudeCodeCredentialProvider._CLI_UA_FALLBACK
        globals()["_CACHED_CLI_UA"] = ua
        return ua

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
        # X-Claude-Code-Session-Id is REQUIRED for Anthropic to route
        # traffic through the Claude Code billing pool (the one
        # interactive ``claude`` CLI uses). Without it, even with full
        # CLI-profile betas + User-Agent, Anthropic billing returns
        # ``You're out of extra usage`` — we verified this end-to-end
        # 2026-04-24: v1.3.17 applied the profile but omitted the
        # session-id header; OpenClaw traffic hit the restricted pool
        # while interactive CLI (which sends a session-id) worked
        # cleanly on the same OAuth token.
        #
        # We derive a stable per-process UUID so every OpenClaw request
        # through this proxy shares one session-id. Aligns with Claude
        # Max's per-session usage tracking; matches the behavior of
        # interactive ``claude`` (one CLI instance = one session-id for
        # the life of that instance).
        session_id = _get_proxy_session_id()

        return InjectionPlan(
            strip_headers=frozenset({
                "authorization",
                "x-api-key",
                # Caller's User-Agent (typically ``Anthropic/JS <ver>``)
                # is replaced with the Claude CLI's UA so billing
                # treats the request as Claude Code.
                "user-agent",
                # x-app identifies the Claude client flavor to the
                # backend (cli / web / ide). Strip the caller's if any.
                "x-app",
                # If the caller tried to set its own session-id, drop
                # it — we inject a tokenpak-scoped one so Anthropic
                # sees coherent session usage across platform traffic.
                "x-claude-code-session-id",
            }),
            add_headers={
                "Authorization": f"Bearer {access_token}",
                "anthropic-dangerous-direct-browser-access": "true",
                "User-Agent": self._detect_cli_version(),
                "x-app": "cli",
                "X-Claude-Code-Session-Id": session_id,
            },
            # anthropic-beta is MERGED with whatever the caller sent —
            # OpenClaw's SDK emits feature-gate markers (``fine-grained-
            # tool-streaming-*``, date-versioned ``interleaved-thinking-
            # YYYY-MM-DD``) that we want to preserve. We just append our
            # Claude Code + OAuth identity markers on the end.
            merge_headers={
                "anthropic-beta": self._CLAUDE_CODE_BETA_BASE,
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
