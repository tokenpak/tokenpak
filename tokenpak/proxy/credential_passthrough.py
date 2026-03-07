"""
tokenpak.proxy.credential_passthrough
======================================

Credential passthrough for TokenPak proxy.

ZERO-STORAGE GUARANTEE
-----------------------
This module handles API credentials from incoming client requests. It upholds
the following guarantees unconditionally:

  1. ZERO LOGGING  — credential values are NEVER written to logs, stdout, or
     any telemetry stream. Even debug-level logging excludes key material.
  2. ZERO DISK WRITES — credentials are never written to files, databases,
     or caches of any kind.
  3. ZERO RETENTION — no reference to credential values is kept beyond the
     lifetime of a single request. No class state holds key material.
  4. PASSTHROUGH ONLY — this module extracts the Authorization / x-api-key
     header from an incoming request and includes it unchanged in the outgoing
     upstream request. Credential values are not inspected or transformed.

Supported providers and header conventions:

  ``anthropic``
      Upstream: ``x-api-key: <value>``
      Also accepts: ``Authorization: Bearer <token>`` on inbound

  ``openai``
      Upstream: ``Authorization: Bearer <value>``
      Also accepts: ``x-api-key: <value>`` on inbound (re-wrapped)

  ``google``
      Upstream: ``Authorization: Bearer <value>`` (Gemini API style)
      Also accepts: ``x-api-key: <value>`` on inbound (re-wrapped)

Usage
-----
::

    from tokenpak.proxy.credential_passthrough import CredentialPassthrough

    cp = CredentialPassthrough()
    ok, err = cp.validate_auth(request_headers)
    if not ok:
        return http_401(err)

    fwd = cp.build_forward_headers(request_headers, provider="anthropic")
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Hop-by-hop and proxy-specific headers that must never be forwarded upstream.
_HOP_BY_HOP: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
        "accept-encoding",
    }
)

# Header names whose values are sensitive and must be redacted in logs.
_SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "x-api-key",
        "api-key",
    }
)

# Supported upstream providers.
_SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"anthropic", "openai", "google"})

# Format: Authorization header must be "Bearer <non-empty-token>"
_BEARER_RE = re.compile(r"^Bearer\s+\S+$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# CredentialPassthrough
# ---------------------------------------------------------------------------


class CredentialPassthrough:
    """
    Stateless credential-forwarding utility.

    All methods are pure functions that operate on a headers dict;
    no instance state ever holds credential values.

    Parameters
    ----------
    require_auth : bool
        When *True* (default) ``validate_auth`` rejects requests that
        carry no recognisable auth header. Set to *False* for open endpoints.
    """

    def __init__(self, *, require_auth: bool = True) -> None:
        self._require_auth = require_auth

    # ------------------------------------------------------------------
    # validate_auth
    # ------------------------------------------------------------------

    def validate_auth(
        self,
        request_headers: Dict[str, str],
    ) -> Tuple[bool, Optional[str]]:
        """
        Check that *request_headers* contains a well-formed auth credential.

        SECURITY: only the *format* of the credential is checked — its value
        is never inspected, stored, or included in error messages.

        Parameters
        ----------
        request_headers : dict
            Raw headers from the incoming client request (case-insensitive).

        Returns
        -------
        (True, None)
            Auth header is present and well-formed.
        (False, error_message)
            Auth header is missing or malformed; caller should return HTTP 401.
        """
        if not self._require_auth:
            return True, None

        lc = {k.lower(): v for k, v in request_headers.items()}

        # x-api-key: any non-blank value accepted
        if "x-api-key" in lc:
            val = lc["x-api-key"].strip()
            if val:
                return True, None
            return False, "x-api-key header is present but empty."

        # Authorization: must be "Bearer <token>"
        if "authorization" in lc:
            val = lc["authorization"].strip()
            if not val:
                return False, "Authorization header is present but empty."
            if not _BEARER_RE.match(val):
                return False, (
                    "Malformed Authorization header — expected 'Bearer <token>'. "
                    "Only the Bearer scheme is supported."
                )
            return True, None

        return False, (
            "Missing API credentials. "
            "Supply an 'Authorization: Bearer <token>' or 'x-api-key: <value>' header."
        )

    # ------------------------------------------------------------------
    # build_forward_headers
    # ------------------------------------------------------------------

    def build_forward_headers(
        self,
        request_headers: Dict[str, str],
        provider: str,
    ) -> Dict[str, str]:
        """
        Construct the headers dict to forward to an upstream *provider*.

        Hop-by-hop headers are stripped.  Auth credentials are re-mapped
        to the canonical header format expected by each provider:

        * ``anthropic`` — auth forwarded as ``x-api-key``
        * ``openai``    — auth forwarded as ``Authorization: Bearer …``
        * ``google``    — auth forwarded as ``Authorization: Bearer …``

        Unknown providers raise ``ValueError`` immediately so misconfiguration
        is caught at call time rather than silently ignored.

        SECURITY: credential values are forwarded byte-for-byte with no
        inspection, transformation, or storage.

        Parameters
        ----------
        request_headers : dict
            Raw headers from the incoming client request.
        provider : str
            Target upstream provider.  Must be one of:
            ``"anthropic"``, ``"openai"``, ``"google"``.

        Returns
        -------
        dict
            Headers ready to attach to the upstream request.
            ``Host`` and ``Content-Length`` must be set by the caller.

        Raises
        ------
        ValueError
            If *provider* is not in the supported set.
        """
        provider_lc = provider.lower()
        if provider_lc not in _SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unknown provider {provider!r}. "
                f"Supported providers: {sorted(_SUPPORTED_PROVIDERS)}"
            )

        lc_incoming = {k.lower(): v for k, v in request_headers.items()}

        # Extract the raw credential value (prefer Authorization, fall back to x-api-key)
        auth_value: Optional[str] = None
        if "authorization" in lc_incoming:
            auth_value = lc_incoming["authorization"].strip()
        elif "x-api-key" in lc_incoming:
            raw_key = lc_incoming["x-api-key"].strip()
            # Wrap bare key into Bearer token for providers that expect it
            auth_value = f"Bearer {raw_key}"

        # Start with a clean forwarded dict — strip hop-by-hop and auth headers
        # (we will re-add auth in provider-canonical form below)
        forwarded: Dict[str, str] = {}
        for key, value in request_headers.items():
            key_lc = key.lower()
            if key_lc in _HOP_BY_HOP:
                continue
            if key_lc in _SENSITIVE_HEADERS:
                continue  # re-added below in canonical form
            forwarded[key] = value

        # Re-add auth in provider-canonical form
        if auth_value is not None:
            if provider_lc == "anthropic":
                # Anthropic prefers x-api-key with the raw key value
                raw = (
                    auth_value.removeprefix("Bearer ").strip()
                    if auth_value.startswith("Bearer ")
                    else auth_value
                )
                forwarded["x-api-key"] = raw
            else:
                # openai / google — Authorization: Bearer <token>
                if not auth_value.lower().startswith("bearer "):
                    auth_value = f"Bearer {auth_value}"
                forwarded["Authorization"] = auth_value

        return forwarded

    # ------------------------------------------------------------------
    # mask_for_logging
    # ------------------------------------------------------------------

    def mask_for_logging(
        self,
        headers: Dict[str, str],
    ) -> Dict[str, str]:
        """
        Return a copy of *headers* safe for debug logging.

        Sensitive header values are replaced with ``"[REDACTED]"``.

        .. warning::
            Use ONLY in debug paths — never pass raw headers to a logger.
        """
        masked: Dict[str, str] = {}
        for key, value in headers.items():
            if key.lower() in _SENSITIVE_HEADERS:
                masked[key] = "[REDACTED]"
            else:
                masked[key] = value
        return masked
