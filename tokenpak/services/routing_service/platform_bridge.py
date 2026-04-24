# SPDX-License-Identifier: Apache-2.0
"""Platform bridge — translate external-platform request signals into tokenpak routing decisions.

Problem it solves
-----------------

Agent platforms like OpenClaw + Codex route their Anthropic / OpenAI traffic
through the tokenpak proxy at ``http://127.0.0.1:8766``. They each carry their
own platform-specific session / identity markers (``X-OpenClaw-Session``, etc.)
and pick one of tokenpak's "providers" — ``tokenpak-claude-code``,
``tokenpak-anthropic``, ``tokenpak-openai-codex`` — to declare billing /
backend intent.

Without this module, tokenpak's ``RouteClassifier`` looks for Claude Code's
own markers (``claude-cli`` User-Agent, ``x-claude-code-session-id``) and
treats anything else as ``ANTHROPIC_SDK`` or ``GENERIC`` — which the
``BackendSelector`` routes to the api-key backend. OpenClaw (which has no
Anthropic api-key) gets 401s.

What it does
------------

Two things:

1. **Defines the bridge contract** — a ``PlatformOrigin`` record
   (``platform_name``, ``session_id``, ``declared_provider``, optional
   ``extra`` dict) plus a thin ``PlatformSignal`` Protocol that any adapter
   implements to extract a ``PlatformOrigin`` from an incoming request.
2. **Maintains a registry** — new platforms register a ``PlatformSignal``
   instance; ``detect_origin(headers)`` walks the registry and returns the
   first match. No hardcoded enumeration of platforms at the call site
   (``feedback_always_dynamic`` 2026-04-16).

How callers use it
------------------

``RouteClassifier`` peeks the registry before falling through to its
historic (Claude-Code-specific) signal list — an OpenClaw session id is
enough to classify the request as Claude-Code-family. ``BackendSelector``
reads the ``declared_provider`` on the origin record plus the request's
auth shape to pick between the api-key backend and the OAuth (companion)
backend.

Kevin's ratification 2026-04-24:
    - ``tokenpak-claude-code`` provider → always companion path (OAuth
      backend) regardless of caller auth shape. Caller auth is stripped,
      Claude CLI OAuth from ``~/.claude/.credentials.json`` is injected
      via ``claude --continue`` subprocess (Part 2b).
    - ``tokenpak-anthropic`` provider → caller's credentials. api-key
      → api backend; Bearer / OAuth → companion backend.
    - OpenClaw default (no explicit ``X-TokenPak-Provider`` header) →
      ``tokenpak-claude-code`` (the failure mode users are hitting is
      api-key; routing to the companion unblocks them).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional

HeaderMap = Mapping[str, str]


@dataclass(frozen=True)
class PlatformOrigin:
    """What tokenpak learned about the caller.

    ``session_id`` is the caller's own identifier — *not* a Claude Code
    session id. ``declared_provider`` is the tokenpak provider name the
    caller claims (``tokenpak-claude-code`` / ``tokenpak-anthropic`` /
    ``tokenpak-openai-codex`` / etc.); when absent, the selector falls
    back to the platform's default_provider.
    """

    platform_name: str
    session_id: Optional[str] = None
    declared_provider: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PlatformSignal:
    """Signal extractor for one platform.

    ``extract(headers)`` returns a :class:`PlatformOrigin` if this
    platform's markers are present, else ``None``. The first matching
    signal in registration order wins.
    """

    name: str
    default_provider: str
    extract: Callable[[HeaderMap], Optional[PlatformOrigin]]


_REGISTRY: List[PlatformSignal] = []


def register(signal: PlatformSignal) -> None:
    """Add a signal to the registry. Idempotent on ``name``."""
    for i, existing in enumerate(_REGISTRY):
        if existing.name == signal.name:
            _REGISTRY[i] = signal
            return
    _REGISTRY.append(signal)


def registered() -> List[PlatformSignal]:
    """Return all registered signals (for tests + introspection)."""
    return list(_REGISTRY)


def _lower(headers: HeaderMap) -> Dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


# ── Well-known header that any platform adapter can set ───────────────────────

TOKENPAK_PROVIDER_HEADER = "x-tokenpak-provider"
"""Explicit provider declaration — when set, overrides the adapter's default.

Platform adapters (OpenClaw, Codex, future) can set this to pin which
tokenpak backend class the request should hit. Accepted values: any
provider name defined in the caller's config (we never validate the
string; the selector routes based on prefix / known values and falls
through gracefully).
"""


def read_declared_provider(headers: HeaderMap) -> Optional[str]:
    """Return the explicit provider name from ``X-TokenPak-Provider``, if any."""
    v = _lower(headers).get(TOKENPAK_PROVIDER_HEADER, "").strip()
    return v or None


def detect_origin(headers: HeaderMap) -> Optional[PlatformOrigin]:
    """Walk the registry and return the first platform that matches.

    Stateless. Returns ``None`` when no registered platform recognises
    the request, which is the common case for direct SDK traffic.
    """
    lheaders = _lower(headers)
    explicit_provider = read_declared_provider(lheaders)
    for sig in _REGISTRY:
        origin = sig.extract(lheaders)
        if origin is None:
            continue
        # Let the explicit header override the signal's default.
        if explicit_provider and origin.declared_provider is None:
            origin = PlatformOrigin(
                platform_name=origin.platform_name,
                session_id=origin.session_id,
                declared_provider=explicit_provider,
                extra=origin.extra,
            )
        return origin
    return None


# ── Built-in signals ──────────────────────────────────────────────────────────
#
# Kept here for first-class platforms; third-party adapters call register()
# at import time. Follow the feedback_always_dynamic rule: no enumeration at
# the call site.


def _openclaw_extract(headers: HeaderMap) -> Optional[PlatformOrigin]:
    # Highest-confidence signal: explicit session header (aspirational;
    # real OpenClaw traffic doesn't currently set this, but adapters
    # that wrap OpenClaw can, and we preserve it for session-mapper
    # integration).
    session_id = headers.get("x-openclaw-session", "").strip()
    if session_id:
        return PlatformOrigin(
            platform_name="openclaw",
            session_id=session_id,
            declared_provider=None,
        )
    # Live-traffic signal: OpenClaw's Node runtime sets User-Agent to
    # ``openclaw`` (or ``OpenClaw-Gateway/1.0``). Case-insensitive.
    # The inspection was done against the installed binary at
    # /home/sue/.nvm/.../openclaw/dist — no other client uses this UA.
    ua = headers.get("user-agent", "").strip().lower()
    if ua.startswith("openclaw"):
        return PlatformOrigin(
            platform_name="openclaw",
            session_id=None,  # no per-request session to map
            declared_provider=None,
        )
    return None


def _codex_extract(headers: HeaderMap) -> Optional[PlatformOrigin]:
    """Detect Codex-bound traffic: /v1/responses endpoint + JWT bearer.

    Codex clients (the OpenAI Codex CLI + OpenClaw's Codex provider)
    authenticate with JWT access tokens that start with ``eyJ``. Unlike
    API-key Bearer (``sk-…``), JWT is the Codex / ChatGPT path. Combined
    with the ``/v1/responses`` endpoint, this is the canonical signal.
    """
    # The request object carries the path via :header:`x-forwarded-uri`
    # we emit below, but we also read the request line path when the
    # proxy hands it to us. For the bridge to stay stateless + purely
    # header-driven, check Authorization shape here; path handling
    # happens in the proxy's credential-injection hook which knows the
    # real URL.
    auth = headers.get("authorization", "").strip()
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    # JWTs have three base64url segments separated by dots — quickest
    # heuristic is the ``eyJ`` prefix (base64(`{"`)).
    if not token.startswith("eyJ"):
        return None
    return PlatformOrigin(
        platform_name="codex",
        session_id=None,
        declared_provider="tokenpak-openai-codex",
    )


_openclaw_signal = PlatformSignal(
    name="openclaw",
    default_provider="tokenpak-claude-code",
    extract=_openclaw_extract,
)
_codex_signal = PlatformSignal(
    name="codex",
    default_provider="tokenpak-openai-codex",
    extract=_codex_extract,
)

register(_openclaw_signal)
register(_codex_signal)


# ── Helpers consumed by BackendSelector ───────────────────────────────────────


def resolve_provider(headers: HeaderMap) -> Optional[str]:
    """Best effort: return the tokenpak provider name for this request.

    Precedence:
      1. Explicit ``X-TokenPak-Provider`` header (wins).
      2. The first registered platform signal's default_provider.
      3. ``None`` — selector falls back to its legacy RouteClass default.
    """
    lheaders = _lower(headers)
    explicit = read_declared_provider(lheaders)
    if explicit:
        return explicit
    for sig in _REGISTRY:
        origin = sig.extract(lheaders)
        if origin is not None:
            return origin.declared_provider or sig.default_provider
    return None


__all__ = [
    "HeaderMap",
    "PlatformOrigin",
    "PlatformSignal",
    "TOKENPAK_PROVIDER_HEADER",
    "detect_origin",
    "read_declared_provider",
    "register",
    "registered",
    "resolve_provider",
]
