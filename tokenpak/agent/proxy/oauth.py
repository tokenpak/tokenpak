"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.oauth``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.oauth is a deprecated re-export; "
    "import from tokenpak.proxy.oauth instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.oauth import *  # noqa: F401,F403,E402

__all__ = ["AUTH_TYPE_APIKEY", "AUTH_TYPE_NONE", "AUTH_TYPE_OAUTH", "CODEX_CHAT_PATH", "CODEX_RESPONSES_PATH", "Dict", "OAuthContext", "analyze_request", "dataclass", "detect_auth_type", "detect_token_format", "is_codex_model", "oauth_telemetry_tags"]
