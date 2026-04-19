"""
TokenPak Credential Passthrough
================================

ZERO-STORAGE GUARANTEE
-----------------------
This module handles API credentials from incoming client requests. It upholds
the following guarantees unconditionally:

  1. ZERO LOGGING — credential values are NEVER written to logs, stdout, or
     any telemetry stream. Even debug-level logging excludes key material.
  2. ZERO DISK WRITES — credentials are never written to files, databases,
     or caches of any kind.
  3. ZERO RETENTION — no reference to credential values is kept beyond the
     lifetime of a single request. No class state holds key material.
  4. PASSTHROUGH ONLY — this module's sole job is to extract the Authorization
     header from an incoming request and include it unchanged in the outgoing
     upstream request. It does not inspect, transform, or validate key values.

Supported provider formats (forwarded without modification):
  - Anthropic / OpenAI:  "Bearer sk-..."  or  "Bearer sk-ant-..."
  - Google:              "Bearer AIza..."
  - Raw key headers:     "x-api-key: <value>"  (forwarded as-is)

If no Authorization or x-api-key header is present on a request bound for
an intercepted provider, the proxy returns HTTP 401 before any upstream
connection is made.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class PassthroughConfig:
    """
    Configuration for credential passthrough behavior.

    Attributes
    ----------
    strip_headers : set[str]
        Lower-cased header names that should NOT be forwarded upstream.
        Excludes hop-by-hop and proxy-specific headers.
    safe_to_log : set[str]
        Lower-cased header names whose values may appear in debug logs.
        All other headers must be redacted before logging.
    require_auth : bool
        When True (default), requests without a recognisable auth header
        are rejected with HTTP 401 before reaching the upstream provider.
    """

    strip_headers: Set[str] = field(
        default_factory=lambda: {
            "host",
            "proxy-connection",
            "proxy-authorization",
            "connection",
            "keep-alive",
            "transfer-encoding",
            "te",
            "trailer",
            "upgrade",
            "content-length",
            "accept-encoding",
        }
    )

    safe_to_log: Set[str] = field(
        default_factory=lambda: {
            "content-type",
            "anthropic-version",
            "user-agent",
        }
    )

    require_auth: bool = True


# Singleton default — module-level, never holds key material
_DEFAULT_CONFIG = PassthroughConfig()

# Patterns for auth header validation (format check only — values not inspected)
_BEARER_RE = re.compile(r"^Bearer\s+\S+$", re.IGNORECASE)
_AUTH_HEADERS = frozenset({"authorization", "x-api-key"})


# ---------------------------------------------------------------------------
# CredentialPassthrough — primary class
# ---------------------------------------------------------------------------


class CredentialPassthrough:
    """
    Stateless credential-forwarding utility.

    All methods are pure functions operating on the request headers dict.
    No instance state holds credential values between calls.

    Usage
    -----
    ::

        pt = CredentialPassthrough()
        ok, err = pt.validate_auth(request_headers)
        if not ok:
            return 401, err

        fwd_headers = pt.build_forward_headers(request_headers, config)
    """

    def __init__(self, config: Optional[PassthroughConfig] = None) -> None:
        # Config controls behaviour only — never stores credentials
        self._config = config or _DEFAULT_CONFIG

    # ------------------------------------------------------------------
    # Auth validation
    # ------------------------------------------------------------------

    def validate_auth(
        self,
        headers: Dict[str, str],
    ) -> Tuple[bool, Optional[str]]:
        """
        Check that the request carries a recognisable auth header.

        SECURITY: This method checks for the *presence* and *format* of auth
        headers only.  The credential value itself is not inspected, stored,
        or logged here or anywhere downstream.

        Returns
        -------
        (True, None)
            Auth header present and well-formed.
        (False, error_message)
            Auth header missing or malformed — caller should return HTTP 401.
        """
        if not self._config.require_auth:
            return True, None

        lower_headers = {k.lower(): v for k, v in headers.items()}

        # Accept x-api-key (raw key, no format constraints)
        if "x-api-key" in lower_headers:
            val = lower_headers["x-api-key"].strip()
            if val:
                return True, None
            return False, "x-api-key header is present but empty"

        # Accept Authorization: Bearer <token>
        if "authorization" in lower_headers:
            val = lower_headers["authorization"].strip()
            if not val:
                return False, "Authorization header is present but empty"
            if not _BEARER_RE.match(val):
                return False, (
                    "Malformed Authorization header — expected 'Bearer <token>'. "
                    "Supported formats: 'Bearer sk-...', 'Bearer AIza...'"
                )
            return True, None

        return False, (
            "Missing API credentials. Provide an Authorization (Bearer token) "
            "or x-api-key header with a valid API key."
        )

    # ------------------------------------------------------------------
    # Header forwarding
    # ------------------------------------------------------------------

    def build_forward_headers(
        self,
        incoming_headers: Dict[str, str],
        config: Optional[PassthroughConfig] = None,
    ) -> Dict[str, str]:
        """
        Build the headers dict to forward to the upstream provider.

        Authorization / x-api-key headers are forwarded UNTOUCHED.
        Hop-by-hop and proxy headers are stripped (see PassthroughConfig).

        SECURITY: This method does not log, store, or modify credential values.

        Parameters
        ----------
        incoming_headers : dict
            Raw headers from the incoming client request.
        config : PassthroughConfig, optional
            Override for strip/safe-log lists. Falls back to instance config.

        Returns
        -------
        dict
            Headers ready to attach to the upstream request.
            Host and Content-Length must be set by the caller after this call.
        """
        cfg = config or self._config
        forwarded: Dict[str, str] = {}

        for key, value in incoming_headers.items():
            if key.lower() in cfg.strip_headers:
                continue
            # All other headers — including auth — forwarded unchanged
            forwarded[key] = value

        return forwarded

    # ------------------------------------------------------------------
    # Safe logging helper
    # ------------------------------------------------------------------

    def mask_for_logging(
        self,
        headers: Dict[str, str],
        config: Optional[PassthroughConfig] = None,
    ) -> Dict[str, str]:
        """
        Return a copy of ``headers`` safe for debug logging.

        Credential headers are replaced with "[REDACTED]".
        Use this ONLY in debug paths — never pass raw headers to a logger.
        """
        cfg = config or self._config
        masked: Dict[str, str] = {}

        for key, value in headers.items():
            key_lower = key.lower()
            if key_lower in cfg.safe_to_log:
                masked[key] = value
            elif key_lower in _AUTH_HEADERS or "key" in key_lower or "token" in key_lower:
                masked[key] = "[REDACTED]"
            else:
                masked[key] = value if len(value) <= 20 else f"{value[:4]}...[{len(value)} chars]"

        return masked


# ---------------------------------------------------------------------------
# Module-level convenience shim (keeps server.py import working)
# ---------------------------------------------------------------------------

_DEFAULT_PASSTHROUGH = CredentialPassthrough()


def forward_headers(
    incoming_headers: Dict[str, str],
    config: Optional[PassthroughConfig] = None,
) -> Dict[str, str]:
    """
    Module-level convenience wrapper around CredentialPassthrough.build_forward_headers.

    Does NOT set Host or Content-Length — caller must add those after.

    SECURITY: See CredentialPassthrough docstring for zero-storage guarantees.
    """
    return _DEFAULT_PASSTHROUGH.build_forward_headers(incoming_headers, config)


def validate_auth(
    headers: Dict[str, str],
    config: Optional[PassthroughConfig] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Module-level convenience wrapper around CredentialPassthrough.validate_auth.
    """
    pt = CredentialPassthrough(config)
    return pt.validate_auth(headers)
