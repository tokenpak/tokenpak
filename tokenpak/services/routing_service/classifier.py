"""``RouteClassifier`` — the **only** place that decides what a request is.

Every other subsystem gets its answer by asking
``RouteClassifier.classify(request)``. Components MUST NOT re-derive
"is this Claude Code?" from headers or URLs elsewhere — that's the
architectural rule enforced by :mod:`tokenpak.core.routing`.

Signals the classifier consults, in priority order:

1. ``User-Agent`` header — Claude Code's binary sets this to
   ``claude-cli/<version>`` or similar; SDKs set ``anthropic-python/…``,
   ``openai-python/…``, etc.
2. ``x-claude-code-session-id`` header — if present, this is definitively
   Claude Code traffic.
3. ``authorization: Bearer …`` + ``anthropic-beta: oauth-2025-04-20``
   header pair — the Claude Code OAuth billing path.
4. Body fingerprint (first N bytes) — shape heuristics for SDK family
   detection when headers are ambiguous.
5. Environment markers — ``CLAUDECODE=1`` (Claude Code sets this for
   the hooks), ``CLAUDE_CODE_SESSION_ID``.

Mode disambiguation for the Claude Code family (TUI vs CLI vs CRON vs
SDK vs IDE) uses:

- ``TERM`` env (``dumb`` → CRON / non-interactive)
- ``CLAUDE_CODE_ENTRYPOINT`` header/env if Claude Code ever emits it
- Presence of ``--print`` flag hint in User-Agent suffix
- Fall-through: CLI when a TTY context can't be inferred, TUI when
  we see a streaming request over SSE

The classifier **never raises**. Unidentifiable traffic returns
:data:`RouteClass.GENERIC`.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

from tokenpak.core.routing.route_class import RouteClass
from tokenpak.services.request import Request

# Header names are lowercased before lookup because HTTP headers are
# case-insensitive and different callers normalise differently.
_CC_USER_AGENT_PREFIXES = ("claude-cli", "claude-code", "anthropic-cli")
_ANTHROPIC_SDK_PREFIXES = ("anthropic-python", "anthropic-sdk", "anthropic/")
_OPENAI_SDK_PREFIXES = ("openai-python", "openai-sdk", "openai/")


def _lower_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


class RouteClassifier:
    """Stateless classifier — construct once, call ``.classify()`` per request.

    Intended instantiation: one per pipeline / one per companion session.
    The classifier holds no mutable state, so sharing across threads is
    safe.
    """

    def classify(self, request: Request) -> RouteClass:
        """Return the :class:`RouteClass` for ``request``.

        Never raises. Unknown traffic returns :data:`RouteClass.GENERIC`.
        """
        headers = _lower_headers(request.headers or {})

        # Signal 2 first (strongest): Claude Code session id header.
        if headers.get("x-claude-code-session-id"):
            return self._claude_code_mode(headers, request)

        # Signal 1: User-Agent.
        ua = headers.get("user-agent", "").lower()
        if any(ua.startswith(p) for p in _CC_USER_AGENT_PREFIXES):
            return self._claude_code_mode(headers, request)
        if any(p in ua for p in _ANTHROPIC_SDK_PREFIXES):
            return RouteClass.ANTHROPIC_SDK
        if any(p in ua for p in _OPENAI_SDK_PREFIXES):
            return RouteClass.OPENAI_SDK

        # Signal 3: OAuth header pair — Claude Code's billing path.
        auth = headers.get("authorization", "").lower()
        anthropic_beta = headers.get("anthropic-beta", "").lower()
        if auth.startswith("bearer ") and "oauth-" in anthropic_beta:
            return self._claude_code_mode(headers, request)

        # Signal 5: environment markers (for companion-side in-process
        # classification where request has no HTTP envelope).
        if os.environ.get("CLAUDECODE") == "1" or os.environ.get("CLAUDE_CODE_SESSION_ID"):
            return self._claude_code_mode(headers, request)

        # Fallback SDK family detection by body fingerprint (cheap peek).
        # Order matters: OpenAI's `"model":"gpt-"` is the more specific
        # signal. Both APIs use ``messages`` so that key alone is
        # ambiguous and must come after the OpenAI check.
        body = request.body or b""
        if b'"model":"gpt-' in body[:1024] or b'"chat/completions"' in body[:256]:
            return RouteClass.OPENAI_SDK
        if b'"anthropic_version"' in body[:1024] or b'"messages"' in body[:256]:
            return RouteClass.ANTHROPIC_SDK

        return RouteClass.GENERIC

    def classify_from_env(self) -> RouteClass:
        """Classify when there's no HTTP request in hand.

        Used by the companion hook (``pre_send.py``) — it has no
        ``Request`` object, just environment. Inspects the same
        environment markers ``classify()`` consults as a last resort.
        """
        if os.environ.get("CLAUDECODE") == "1" or os.environ.get("CLAUDE_CODE_SESSION_ID"):
            entry = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").lower()
            if entry == "cron":
                return RouteClass.CLAUDE_CODE_CRON
            if entry == "cli" or "--print" in os.environ.get("CLAUDE_CODE_ARGV", ""):
                return RouteClass.CLAUDE_CODE_CLI
            if entry == "sdk":
                return RouteClass.CLAUDE_CODE_SDK
            if entry == "ide":
                return RouteClass.CLAUDE_CODE_IDE
            return RouteClass.CLAUDE_CODE_TUI  # default mode
        return RouteClass.GENERIC

    # ── internal helpers ────────────────────────────────────────────────

    def _claude_code_mode(
        self, headers: Mapping[str, str], request: Request
    ) -> RouteClass:
        """Disambiguate Claude Code consumption mode."""
        # Explicit header wins.
        entry = headers.get("x-claude-code-entrypoint", "").lower()
        if entry == "cron":
            return RouteClass.CLAUDE_CODE_CRON
        if entry == "cli":
            return RouteClass.CLAUDE_CODE_CLI
        if entry == "sdk":
            return RouteClass.CLAUDE_CODE_SDK
        if entry == "ide":
            return RouteClass.CLAUDE_CODE_IDE
        if entry == "tmux":
            return RouteClass.CLAUDE_CODE_TMUX

        # Env marker fallback (companion-internal).
        env_entry = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").lower()
        if env_entry in ("cron", "cli", "sdk", "ide", "tmux"):
            return RouteClass(f"claude-code-{env_entry}")

        # Body-shape hint: streaming requests (``stream: true``) bias toward
        # TUI since the TUI always streams; scripted ``--print`` flushes
        # non-streaming JSON.
        body = request.body or b""
        if b'"stream":true' in body[:512] or b'"stream": true' in body[:512]:
            return RouteClass.CLAUDE_CODE_TUI
        return RouteClass.CLAUDE_CODE_CLI


_default: Optional[RouteClassifier] = None


def get_classifier() -> RouteClassifier:
    """Shared module-level classifier — stateless, safe to reuse."""
    global _default
    if _default is None:
        _default = RouteClassifier()
    return _default


__all__ = ["RouteClassifier", "get_classifier"]
