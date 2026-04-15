"""Route classification policy matrix for the tokenpak proxy.

Centralizes per-route behavior decisions that were previously scattered
as inline conditionals across proxy.py (~6 code paths). Each route
(claude-code, openclaw, sdk) maps to a behavior dict that pipeline
stages consult instead of checking ad-hoc flags.

Usage::

    from tokenpak.proxy.route_policy import get_policy, ROUTE_CLAUDE_CODE

    policy = get_policy(route)
    if policy["body"] == "byte_preserved":
        body = _original_body  # don't re-serialize
"""
from __future__ import annotations

from typing import Any, Dict

from tokenpak.proxy.request import ROUTE_CLAUDE_CODE, ROUTE_OPENCLAW, ROUTE_SDK

# ---------------------------------------------------------------------------
# Behavior matrix
# ---------------------------------------------------------------------------

ROUTE_POLICIES: Dict[str, Dict[str, Any]] = {
    ROUTE_CLAUDE_CODE: {
        # Auth: pass client's Authorization: Bearer through unchanged
        "auth": "passthrough",
        # Body: preserve original bytes — no json.loads/json.dumps
        # (JSON re-serialization breaks Anthropic billing routing)
        "body": "byte_preserved",
        # Vault injection: byte-level splice into system array
        "vault_injection": "byte_splice",
        # Compaction: disabled — requires JSON re-serialization
        "compaction": "disabled",
        # Cache control: client manages its own TTL ordering
        "cache_control": "client_managed",
        # Headers: forward ALL client headers (no allowlist filtering)
        "headers": "forward_all",
        # Platform tag for telemetry/monitor.db
        "platform_tag": "claude-code",
        # Cache poison removal: enabled (text-only, doesn't re-serialize
        # unless content actually changes, and we restore original bytes after)
        "cache_poison_removal": "enabled",
        # Stable cache control stamps: skip — would add default-TTL markers
        # that break client's 1h TTL ordering
        "stable_cache_stamps": "disabled",
        # Cache cap and TTL hotfix: skip — client manages ordering
        "cache_cap": "disabled",
    },
    ROUTE_OPENCLAW: {
        "auth": "inject",
        "body": "full_pipeline",
        "vault_injection": "json_inject",
        "compaction": "enabled",
        "cache_control": "proxy_managed",
        "headers": "allowlist",
        "platform_tag": "openclaw",
        "cache_poison_removal": "enabled",
        "stable_cache_stamps": "enabled",
        "cache_cap": "enabled",
        # Backend: "api" (default, forward to provider HTTP API) or
        # "claude_code" (delegate to tokenpak claude -p --resume for
        # tool use, CLAUDE.md, subscription billing, persistent sessions).
        # Selected per-request via X-TokenPak-Backend header.
        "backend": "api",
    },
    ROUTE_SDK: {
        "auth": "passthrough",
        "body": "full_pipeline",
        "vault_injection": "json_inject",
        "compaction": "enabled",
        "cache_control": "proxy_managed",
        "headers": "sanitize",
        "platform_tag": "sdk",
        "cache_poison_removal": "enabled",
        "stable_cache_stamps": "enabled",
        "cache_cap": "enabled",
    },
}

# Default policy for unrecognized routes — full pipeline, sanitized headers
_DEFAULT_POLICY: Dict[str, Any] = dict(ROUTE_POLICIES[ROUTE_SDK])
_DEFAULT_POLICY["platform_tag"] = "unknown"


def get_policy(route: str) -> Dict[str, Any]:
    """Look up the behavior policy for a route classification.

    Args:
        route: Route string from ``_classify_route()`` — one of
               ``ROUTE_CLAUDE_CODE``, ``ROUTE_OPENCLAW``, ``ROUTE_SDK``,
               or any string (falls back to SDK/default policy).

    Returns:
        Dict of behavior keys. Callers check individual keys like
        ``policy["body"]`` or ``policy["auth"]`` to determine stage behavior.
    """
    return ROUTE_POLICIES.get(route, _DEFAULT_POLICY)


def is_byte_preserved(route: str) -> bool:
    """Convenience: does this route require byte-level body preservation?"""
    return get_policy(route)["body"] == "byte_preserved"


def is_auth_passthrough(route: str) -> bool:
    """Convenience: does this route pass client auth through unchanged?"""
    return get_policy(route)["auth"] == "passthrough"


def is_compaction_enabled(route: str) -> bool:
    """Convenience: is compaction allowed for this route?"""
    return get_policy(route)["compaction"] == "enabled"


def is_cache_client_managed(route: str) -> bool:
    """Convenience: does the client manage its own caching for this route?

    When True, cache hits reported by the upstream provider were caused by the
    client (e.g. Claude Code setting its own ``cache_control`` blocks), NOT by
    tokenpak.  Savings reports must not attribute these cache hits to tokenpak.
    """
    return get_policy(route)["cache_control"] == "client_managed"


def platform_tag(route: str) -> str:
    """Convenience: get the telemetry platform tag for this route."""
    return get_policy(route)["platform_tag"]
