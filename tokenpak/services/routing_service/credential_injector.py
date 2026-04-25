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
from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Protocol

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
      Codex). ``None`` means keep the original target. Static — does
      not depend on the request body.
    - ``target_url_resolver``: optional ``(body, headers) -> Optional[str]``
      callable for providers that compute the URL from the request
      payload. Azure OpenAI is the canonical case: the deployment id
      lives in the body's ``model`` field but Azure routes it via the
      URL path
      (``<endpoint>/openai/deployments/<deployment>/chat/completions``).
      When set, overrides ``target_url_override``. Returning ``None``
      from the resolver falls back to ``target_url_override`` (then to
      the original target).
    - ``body_transform``: optional bytes → bytes transform applied
      before forward. Codex needs a few payload-normalization tweaks
      (``stream=true``, ``store=false``, drop ``max_output_tokens``).
      None = leave body alone (byte-preserve).
    """

    strip_headers: FrozenSet[str] = frozenset()
    add_headers: Dict[str, str] = field(default_factory=dict)
    merge_headers: Dict[str, str] = field(default_factory=dict)
    target_url_override: Optional[str] = None
    target_url_resolver: Optional[
        Callable[[bytes, Mapping[str, str]], Optional[str]]
    ] = None
    body_transform: Optional[Callable[[bytes], bytes]] = None
    # Dynamic per-request headers — computed AFTER body_transform +
    # URL resolution. Used when a provider's auth depends on the exact
    # bytes being sent (AWS SigV4 is the canonical case: the Authorization
    # header is a hash of the request including method, URL, headers,
    # and body content). Result is merged into the request headers,
    # OVERRIDING ``add_headers`` for any conflicting keys (because the
    # dynamic value is the authoritative one).
    header_resolver: Optional[
        Callable[[bytes, str, str, Mapping[str, str]], Dict[str, str]]
    ] = None


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


# ── OpenAI-Chat-compatible providers (Phase 1 adapter pack) ──────────
#
# Mistral, Groq, Together, DeepSeek, OpenRouter all speak OpenAI Chat
# Completions wire format. ``OpenAIChatAdapter`` already handles the
# JSON shape; what differs per provider is the upstream URL and the
# auth header value. Each gets a tiny CredentialProvider that reads
# its own ``<NAME>_API_KEY`` env var and emits an InjectionPlan with
# a target_url_override + Authorization: Bearer header.
#
# Adding another OpenAI-Chat-compatible provider is a 5-line class —
# subclass ``_EnvKeyBearerProvider``, set name / upstream / env var.
# No new format adapter, no edits to proxy core.


import os as _os


class _EnvKeyBearerProvider:
    """Base for OpenAI-Chat-compatible providers using a single env-var key.

    Subclasses set:
      - ``name``: tokenpak provider slug (``tokenpak-mistral`` etc.).
      - ``_UPSTREAM``: full URL the proxy rewrites the request to.
      - ``_ENV_VAR``: environment variable holding the API key.
      - ``_EXTRA_HEADERS`` (optional): extra static headers to inject
        (e.g. OpenRouter requires ``HTTP-Referer`` + ``X-Title``).
    """

    name: str = ""
    _UPSTREAM: str = ""
    _ENV_VAR: str = ""
    _EXTRA_HEADERS: Dict[str, str] = {}

    def _load(self) -> Optional[InjectionPlan]:
        api_key = _os.environ.get(self._ENV_VAR, "").strip()
        if not api_key:
            logger.info(
                "credential_injector[%s]: env var %s not set; skipping",
                self.name, self._ENV_VAR,
            )
            return None
        headers: Dict[str, str] = {"Authorization": f"Bearer {api_key}"}
        headers.update(self._EXTRA_HEADERS)
        return InjectionPlan(
            strip_headers=frozenset({"authorization", "x-api-key"}),
            add_headers=headers,
            target_url_override=self._UPSTREAM,
        )

    def resolve(self) -> Optional[InjectionPlan]:
        return _cached_resolve(self.name, self._load)


class MistralCredentialProvider(_EnvKeyBearerProvider):
    """Mistral AI — OpenAI-Chat-compatible, ``MISTRAL_API_KEY``."""

    name = "tokenpak-mistral"
    _UPSTREAM = "https://api.mistral.ai/v1/chat/completions"
    _ENV_VAR = "MISTRAL_API_KEY"


class GroqCredentialProvider(_EnvKeyBearerProvider):
    """Groq — OpenAI-Chat-compatible, ``GROQ_API_KEY``."""

    name = "tokenpak-groq"
    _UPSTREAM = "https://api.groq.com/openai/v1/chat/completions"
    _ENV_VAR = "GROQ_API_KEY"


class TogetherCredentialProvider(_EnvKeyBearerProvider):
    """Together AI — OpenAI-Chat-compatible, ``TOGETHER_API_KEY``."""

    name = "tokenpak-together"
    _UPSTREAM = "https://api.together.xyz/v1/chat/completions"
    _ENV_VAR = "TOGETHER_API_KEY"


class DeepSeekCredentialProvider(_EnvKeyBearerProvider):
    """DeepSeek — OpenAI-Chat-compatible, ``DEEPSEEK_API_KEY``."""

    name = "tokenpak-deepseek"
    _UPSTREAM = "https://api.deepseek.com/v1/chat/completions"
    _ENV_VAR = "DEEPSEEK_API_KEY"


class AzureOpenAICredentialProvider:
    """Azure OpenAI — same wire format as OpenAI Chat Completions but
    routes by deployment id in the URL path, uses ``api-key`` header
    (not ``Authorization: Bearer``), and requires an ``api-version``
    query param.

    Three env vars, all required:

      - ``AZURE_OPENAI_API_KEY`` — the resource's API key.
      - ``AZURE_OPENAI_ENDPOINT`` — the resource URL, e.g.
        ``https://my-resource.openai.azure.com``. Trailing slash is
        tolerated.
      - ``AZURE_OPENAI_API_VERSION`` — the API version to pin
        (default ``2024-10-21``). Azure breaks compat across versions
        more often than other providers; the default is a stable GA
        line as of 2026.

    Routing model
    -------------

    Azure customers create *deployments* of OpenAI models. The
    deployment name (which the customer picks) — not the canonical
    model id — is what Azure routes by. Two ways to supply it:

      - **Default**: caller sets ``model: "<deployment-name>"`` in the
        request body. We pull it and build
        ``<endpoint>/openai/deployments/<deployment>/chat/completions?api-version=...``.
      - **Override**: caller sets ``X-Azure-Deployment: <name>`` header.
        Wins over the body field. Useful when the caller wants to keep
        the canonical model id in the body for telemetry / cost
        tracking but route to a deployment with a different name.

    No body-side ``model`` rewrite — Azure tolerates it (and some
    middleware chains rely on it being preserved).
    """

    name = "tokenpak-azure-openai"
    _DEFAULT_API_VERSION = "2024-10-21"
    _PATH_TEMPLATE = "/openai/deployments/{deployment}/chat/completions"

    def _load(self) -> Optional[InjectionPlan]:
        api_key = _os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        endpoint = _os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        if not api_key:
            logger.info(
                "credential_injector[azure-openai]: AZURE_OPENAI_API_KEY "
                "not set; skipping"
            )
            return None
        if not endpoint:
            logger.info(
                "credential_injector[azure-openai]: AZURE_OPENAI_ENDPOINT "
                "not set; skipping"
            )
            return None

        api_version = (
            _os.environ.get("AZURE_OPENAI_API_VERSION", "").strip()
            or self._DEFAULT_API_VERSION
        )
        endpoint = endpoint.rstrip("/")

        def _resolve_url(body: bytes, headers: Mapping[str, str]) -> Optional[str]:
            # Header override wins.
            for k, v in (headers or {}).items():
                if k.lower() == "x-azure-deployment" and v:
                    deployment = v.strip()
                    if deployment:
                        return self._build_url(endpoint, deployment, api_version)
            # Else read deployment from body's model field.
            if not body:
                return None
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
            if not isinstance(data, dict):
                return None
            deployment = data.get("model")
            if not isinstance(deployment, str) or not deployment.strip():
                return None
            return self._build_url(endpoint, deployment.strip(), api_version)

        return InjectionPlan(
            strip_headers=frozenset({"authorization", "x-api-key"}),
            add_headers={"api-key": api_key},
            target_url_resolver=_resolve_url,
        )

    @classmethod
    def _build_url(cls, endpoint: str, deployment: str, api_version: str) -> str:
        path = cls._PATH_TEMPLATE.format(deployment=deployment)
        return f"{endpoint}{path}?api-version={api_version}"

    def resolve(self) -> Optional[InjectionPlan]:
        return _cached_resolve(self.name, self._load)


class CohereCredentialProvider(_EnvKeyBearerProvider):
    """Cohere Chat v2 — OpenAI-Chat-compatible, ``COHERE_API_KEY``.

    Cohere's v2 chat endpoint mirrors the OpenAI Chat Completions
    request/response shape, so the existing ``OpenAIChatAdapter``
    handles the wire format without modification. Bearer auth.

    The older ``/v1/chat`` endpoint had Cohere's distinct shape and
    would need a dedicated FormatAdapter — explicitly NOT what we
    target here.
    """

    name = "tokenpak-cohere"
    _UPSTREAM = "https://api.cohere.ai/v2/chat"
    _ENV_VAR = "COHERE_API_KEY"


class OpenRouterCredentialProvider(_EnvKeyBearerProvider):
    """OpenRouter — meta-provider proxying ~100 models. ``OPENROUTER_API_KEY``.

    Requires ``HTTP-Referer`` + ``X-Title`` headers on every request
    (per OpenRouter's docs); without them OpenRouter rejects with a
    400. The Referer is informational; we use the tokenpak homepage.
    """

    name = "tokenpak-openrouter"
    _UPSTREAM = "https://openrouter.ai/api/v1/chat/completions"
    _ENV_VAR = "OPENROUTER_API_KEY"
    _EXTRA_HEADERS = {
        "HTTP-Referer": "https://tokenpak.ai",
        "X-Title": "TokenPak",
    }


class BedrockClaudeCredentialProvider:
    """AWS Bedrock for Anthropic Claude — Anthropic Messages format
    wrapped in Bedrock's InvokeModel envelope, signed with AWS SigV4.

    Bedrock takes the Messages payload directly (almost — it wants
    ``anthropic_version: "bedrock-2023-05-31"`` and forbids the
    ``model`` field, which is encoded in the URL instead). Auth is
    SigV4 over the request bytes; we delegate signing to ``boto3`` to
    avoid maintaining a hand-rolled HMAC implementation.

    Required:

      - ``boto3`` installed (standard AWS Python ecosystem dep).
      - ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY`` env vars
        (or any boto3-discoverable credentials: profile, IAM role,
        SSO, etc. — we use ``boto3.Session()`` which resolves all of
        them).
      - ``AWS_REGION`` or ``AWS_DEFAULT_REGION`` env var. Default
        ``us-east-1`` if neither set.

    Routing
    -------

    Bedrock specifies the model via URL path
    (``/model/<id>/invoke``); the request body MUST NOT contain a
    ``model`` field. The caller is expected to put the Bedrock model
    id (e.g. ``anthropic.claude-3-5-sonnet-20241022-v2:0``, or an
    inference-profile ARN) in the body's ``model`` field — we strip
    it out before forwarding and use it to build the URL.

    Streaming uses the ``/invoke-with-response-stream`` suffix; we
    pick that variant when the request body sets ``stream: true``.

    Plan composition
    ----------------

      - ``target_url_resolver`` — body-aware (model + stream flag).
      - ``body_transform`` — strips ``model``, adds
        ``anthropic_version``.
      - ``header_resolver`` — SigV4 signs the FINAL body + URL +
        method on every request.

    No ``add_headers`` / ``Authorization`` — SigV4 puts everything in
    the dynamic resolver because all four (Authorization, x-amz-date,
    x-amz-content-sha256, host) depend on the exact final bytes.
    """

    name = "tokenpak-bedrock-claude"
    _DEFAULT_REGION = "us-east-1"
    _SERVICE = "bedrock"
    _ANTHROPIC_VERSION = "bedrock-2023-05-31"

    def _load(self) -> Optional[InjectionPlan]:
        try:
            import boto3  # noqa: F401  (presence check only here)
        except ImportError:
            logger.info(
                "credential_injector[bedrock]: boto3 not installed; skipping. "
                "`pip install boto3` to enable Bedrock routing."
            )
            return None

        # Resolve region: env first, then boto3's session default.
        region = (
            _os.environ.get("AWS_REGION", "").strip()
            or _os.environ.get("AWS_DEFAULT_REGION", "").strip()
            or self._DEFAULT_REGION
        )

        # Build a session lazily so credential errors surface per-request
        # (not at provider-resolve time when boto3 may not have looked
        # at env vars yet).
        endpoint_host = f"bedrock-runtime.{region}.amazonaws.com"

        def _resolve_url(body: bytes, headers: Mapping[str, str]) -> Optional[str]:
            if not body:
                return None
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
            if not isinstance(data, dict):
                return None
            model = data.get("model")
            if not isinstance(model, str) or not model.strip():
                return None
            stream = bool(data.get("stream"))
            suffix = "invoke-with-response-stream" if stream else "invoke"
            # Inference-profile ARNs contain ``:`` which must NOT be
            # URL-encoded for Bedrock — use as-is.
            return f"https://{endpoint_host}/model/{model.strip()}/{suffix}"

        def _transform_body(body: bytes) -> bytes:
            if not body:
                return body
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return body
            if not isinstance(data, dict):
                return body
            data.pop("model", None)
            data.setdefault("anthropic_version", self._ANTHROPIC_VERSION)
            try:
                return json.dumps(data).encode("utf-8")
            except (TypeError, ValueError):
                return body

        def _sign_request(
            body: bytes,
            url: str,
            method: str,
            _existing_headers: Mapping[str, str],
        ) -> Dict[str, str]:
            # Use boto3's SigV4Auth — battle-tested + handles all the
            # canonical-request edge cases (case folding, empty bodies,
            # query string normalisation) we'd otherwise have to
            # replicate by hand.
            from boto3 import Session as _Session
            from botocore.auth import SigV4Auth
            from botocore.awsrequest import AWSRequest

            session = _Session()
            creds = session.get_credentials()
            if creds is None:
                logger.warning(
                    "credential_injector[bedrock]: no AWS credentials "
                    "discoverable by boto3 — request will fail upstream."
                )
                return {}
            frozen = creds.get_frozen_credentials()
            req = AWSRequest(method=method, url=url, data=body or b"")
            # ``Host`` and ``content-type`` are required for signing;
            # set them explicitly so the signature reflects what we'll
            # actually send.
            from urllib.parse import urlparse as _urlparse

            req.headers["Host"] = _urlparse(url).netloc
            req.headers["Content-Type"] = "application/json"
            SigV4Auth(frozen, self._SERVICE, region).add_auth(req)
            # SigV4Auth mutates req.headers in-place. Pull the resulting
            # set out as a plain dict.
            return dict(req.headers.items())

        return InjectionPlan(
            strip_headers=frozenset({"authorization", "x-api-key"}),
            add_headers={"Content-Type": "application/json"},
            target_url_resolver=_resolve_url,
            body_transform=_transform_body,
            header_resolver=_sign_request,
        )

    def resolve(self) -> Optional[InjectionPlan]:
        return _cached_resolve(self.name, self._load)


class VertexAIGeminiCredentialProvider:
    """Google Vertex AI for Gemini — same wire format as the public
    Generative AI API but a different endpoint, project-scoped URLs,
    and OAuth-2-access-token auth via Application Default Credentials.

    Required:

      - ``google-auth`` installed (standard GCP Python ecosystem dep).
        Graceful skip with logged INFO if not — ``pip install
        google-auth`` to enable.
      - ``GOOGLE_CLOUD_PROJECT`` env var (or ``GCLOUD_PROJECT``).
      - GCP credentials discoverable by ADC: ``GOOGLE_APPLICATION_CREDENTIALS``
        pointing at a service-account JSON key, ``gcloud auth
        application-default login``, GCE/GKE metadata server, or
        workload identity.

    Optional:

      - ``VERTEX_REGION`` / ``GOOGLE_CLOUD_REGION`` (default
        ``us-central1``).

    Routing
    -------

    Vertex routes by model id in the URL path
    (``publishers/google/models/<model>:streamGenerateContent``)
    similar to Bedrock. Picks ``:streamGenerateContent`` when the
    request body sets ``"stream": true`` (or the body's content type
    suggests SSE), else ``:generateContent``.

    Auth
    ----

    Like Bedrock, the auth header is computed per-request — the
    ``google-auth`` library returns a short-lived OAuth2 access token
    that auto-refreshes. Cached internally by google-auth so the
    network round-trip happens at most every ~50 minutes.

    Body shape
    ----------

    Vertex accepts the same body as the public Gemini API
    (``contents`` array, ``generationConfig``, etc.). The existing
    ``GoogleGenerativeAIAdapter`` handles the wire format. We strip
    the ``model`` field (Vertex routes by URL) but leave everything
    else byte-stable so the user's ``contents`` flow through cleanly.
    """

    name = "tokenpak-vertex-gemini"
    _DEFAULT_REGION = "us-central1"
    _SCOPE = "https://www.googleapis.com/auth/cloud-platform"

    def _load(self) -> Optional[InjectionPlan]:
        try:
            import google.auth  # noqa: F401  (presence check only)
        except ImportError:
            logger.info(
                "credential_injector[vertex-gemini]: google-auth not "
                "installed; skipping. `pip install google-auth` to enable "
                "Vertex AI routing."
            )
            return None

        project = (
            _os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
            or _os.environ.get("GCLOUD_PROJECT", "").strip()
        )
        if not project:
            logger.info(
                "credential_injector[vertex-gemini]: GOOGLE_CLOUD_PROJECT "
                "not set; skipping."
            )
            return None

        region = (
            _os.environ.get("VERTEX_REGION", "").strip()
            or _os.environ.get("GOOGLE_CLOUD_REGION", "").strip()
            or self._DEFAULT_REGION
        )

        host = f"{region}-aiplatform.googleapis.com"

        def _resolve_url(body: bytes, headers: Mapping[str, str]) -> Optional[str]:
            if not body:
                return None
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
            if not isinstance(data, dict):
                return None
            model = data.get("model")
            if not isinstance(model, str) or not model.strip():
                return None
            stream = bool(data.get("stream"))
            verb = "streamGenerateContent" if stream else "generateContent"
            # Vertex inference profile / publisher path. Anthropic
            # Claude on Vertex would use ``publishers/anthropic/...``
            # but that's a separate provider (different body shape).
            return (
                f"https://{host}/v1/projects/{project}/locations/{region}"
                f"/publishers/google/models/{model.strip()}:{verb}"
            )

        def _transform_body(body: bytes) -> bytes:
            if not body:
                return body
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return body
            if not isinstance(data, dict):
                return body
            data.pop("model", None)
            data.pop("stream", None)  # Vertex encodes via URL verb, not body field.
            try:
                return json.dumps(data).encode("utf-8")
            except (TypeError, ValueError):
                return body

        # Cache the credentials object across requests; google-auth
        # itself memoizes the access token until expiry, so a single
        # ``creds.refresh()`` call costs nothing on subsequent hits.
        _creds_cache = {"creds": None}

        def _sign_request(
            body: bytes,
            url: str,
            method: str,
            _existing_headers: Mapping[str, str],
        ) -> Dict[str, str]:
            from google.auth import default as _ga_default
            from google.auth.transport.requests import Request as _GARequest

            creds = _creds_cache["creds"]
            if creds is None:
                try:
                    creds, _proj = _ga_default(scopes=[self._SCOPE])
                    _creds_cache["creds"] = creds
                except Exception as exc:
                    logger.warning(
                        "credential_injector[vertex-gemini]: "
                        "google.auth.default() failed (%s: %s) — "
                        "request will fail upstream",
                        type(exc).__name__, exc,
                    )
                    return {}
            try:
                # Refresh only when needed; google-auth checks expiry.
                if not creds.valid:
                    creds.refresh(_GARequest())
            except Exception as exc:
                logger.warning(
                    "credential_injector[vertex-gemini]: token refresh "
                    "failed (%s: %s)",
                    type(exc).__name__, exc,
                )
                return {}
            token = getattr(creds, "token", None)
            if not token:
                return {}
            return {"Authorization": f"Bearer {token}"}

        return InjectionPlan(
            strip_headers=frozenset({"authorization", "x-api-key", "x-goog-api-key"}),
            add_headers={"Content-Type": "application/json"},
            target_url_resolver=_resolve_url,
            body_transform=_transform_body,
            header_resolver=_sign_request,
        )

    def resolve(self) -> Optional[InjectionPlan]:
        return _cached_resolve(self.name, self._load)


# ── Register built-ins at import ─────────────────────────────────────


register(ClaudeCodeCredentialProvider())
register(CodexCredentialProvider())
register(MistralCredentialProvider())
register(GroqCredentialProvider())
register(TogetherCredentialProvider())
register(DeepSeekCredentialProvider())
register(CohereCredentialProvider())
register(OpenRouterCredentialProvider())
register(AzureOpenAICredentialProvider())
register(BedrockClaudeCredentialProvider())
register(VertexAIGeminiCredentialProvider())


__all__ = [
    "AzureOpenAICredentialProvider",
    "BedrockClaudeCredentialProvider",
    "ClaudeCodeCredentialProvider",
    "CodexCredentialProvider",
    "CohereCredentialProvider",
    "CredentialProvider",
    "DeepSeekCredentialProvider",
    "GroqCredentialProvider",
    "InjectionPlan",
    "MistralCredentialProvider",
    "OpenRouterCredentialProvider",
    "TogetherCredentialProvider",
    "VertexAIGeminiCredentialProvider",
    "invalidate_cache",
    "register",
    "registered",
    "resolve",
]
