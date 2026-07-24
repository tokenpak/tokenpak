"""Proxy request and response types for the modular proxy architecture."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ProxyRequest:
    """Incoming proxy request — captures method, URL, headers, and body.

    Used by registry adapters to pass requests through the proxy pipeline
    without coupling to the HTTP server implementation.
    """

    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    session_id: Optional[str] = None
    source_platform: str = "unknown"

    def get_header(self, name: str, default: str = "") -> str:
        """Case-insensitive header lookup."""
        lower = name.lower()
        for k, v in self.headers.items():
            if k.lower() == lower:
                return v
        return default


@dataclass
class ProxyResponse:
    """Upstream proxy response — captures status, headers, and body."""

    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def get_header(self, name: str, default: str = "") -> str:
        """Case-insensitive header lookup."""
        lower = name.lower()
        for k, v in self.headers.items():
            if k.lower() == lower:
                return v
        return default


# Route constants for request classification
ROUTE_CLAUDE_CODE = "claude-code"
ROUTE_OPENCLAW = "openclaw"
ROUTE_SDK = "sdk"


# ---------------------------------------------------------------------------
# Byte-level system array injection (extracted from proxy.py:5124-5235)
# ---------------------------------------------------------------------------


def _find_system_array_close(body: bytes) -> int:
    """Find the byte offset of the closing ] of the top-level "system" array.

    Scans through raw JSON bytes tracking string state and nesting depth.
    Returns the offset of the ] character, or -1 if the system key is not
    found, is not an array, or the JSON is malformed.
    """
    n = len(body)
    i = 0
    in_string = False
    depth = 0  # brace depth (top-level object is depth 1)
    system_key = b'"system"'
    system_key_len = len(system_key)

    while i < n:
        c = body[i]
        if in_string:
            if c == ord("\\"):
                i += 2  # skip escaped character
                continue
            if c == ord('"'):
                in_string = False
            i += 1
            continue

        if c == ord('"'):
            # Check if this starts "system" at depth 1 (top-level key)
            if depth == 1 and body[i : i + system_key_len] == system_key:
                # Found the key — skip past it and the colon
                j = i + system_key_len
                while j < n and body[j] in (ord(" "), ord("\t"), ord("\n"), ord("\r")):
                    j += 1
                if j < n and body[j] == ord(":"):
                    j += 1
                    while j < n and body[j] in (ord(" "), ord("\t"), ord("\n"), ord("\r")):
                        j += 1
                    if j < n and body[j] == ord("["):
                        # Found start of system array — bracket-match to find close
                        bracket_depth = 1
                        k = j + 1
                        in_str = False
                        while k < n and bracket_depth > 0:
                            ck = body[k]
                            if in_str:
                                if ck == ord("\\"):
                                    k += 2
                                    continue
                                if ck == ord('"'):
                                    in_str = False
                            else:
                                if ck == ord('"'):
                                    in_str = True
                                elif ck == ord("["):
                                    bracket_depth += 1
                                elif ck == ord("]"):
                                    bracket_depth -= 1
                                    if bracket_depth == 0:
                                        return k
                            k += 1
                        return -1  # malformed — never closed
                    else:
                        return -1  # system value is not an array
            in_string = True
            i += 1
            continue

        if c == ord("{"):
            depth += 1
        elif c == ord("}"):
            depth -= 1
        i += 1

    return -1  # "system" key not found


def _byte_inject_system_block(
    body: bytes, injection_text: str, *, request: "Optional[ProxyRequest]" = None
) -> bytes:
    """Inject a text block into the system array via byte splicing.

    Finds the closing ] of the "system" array and inserts a new block
    just before it. Does NOT re-serialize the rest of the JSON body,
    preserving the original byte representation exactly.

    Returns the original body unchanged if injection fails for any reason.
    """
    if request is not None:
        body = request.body
    if not injection_text:
        return body

    close_pos = _find_system_array_close(body)
    if close_pos < 0:
        return body  # fail-open: no system array found

    # JSON-escape the injection text
    escaped_text = json.dumps(injection_text, ensure_ascii=False)

    # Build the fragment: {"type": "text", "text": <escaped>}
    fragment_str = '{"type": "text", "text": ' + escaped_text + "}"

    # Check if the array is empty (no leading comma needed)
    scan = close_pos - 1
    while scan >= 0 and body[scan] in (ord(" "), ord("\t"), ord("\n"), ord("\r")):
        scan -= 1
    if scan >= 0 and body[scan] == ord("["):
        # Empty array — no comma
        fragment = fragment_str.encode("utf-8")
    else:
        fragment = (", " + fragment_str).encode("utf-8")

    return body[:close_pos] + fragment + body[close_pos:]


class HTTPProxy:
    """Proxy dispatch interface for registry adapters.

    Provides a clean API for adapters to forward requests through the
    proxy pipeline. The actual pipeline logic lives in proxy.py (production)
    or can be overridden for testing.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def handle_request(
        self,
        request: ProxyRequest,
        route: str = ROUTE_SDK,
        model: Optional[str] = None,
    ) -> ProxyResponse:
        """Forward a request through the proxy pipeline.

        Args:
            request: The incoming proxy request.
            route: Route classification (ROUTE_CLAUDE_CODE, ROUTE_OPENCLAW, etc.)
                   Controls which pipeline path is used.
            model: Model name for session tracking.

        Returns:
            ProxyResponse from the upstream provider.
        """
        import json as _json
        import urllib.error
        import urllib.request

        # Build forwarded headers
        fwd_headers = dict(request.headers)
        fwd_headers.setdefault("Content-Type", "application/json")

        req = urllib.request.Request(
            request.url,
            data=request.body if request.body else None,
            headers=fwd_headers,
            method=request.method,
        )

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                resp_body = resp.read()
                resp_headers = {k: v for k, v in resp.getheaders()}
                return ProxyResponse(
                    status_code=resp.status,
                    headers=resp_headers,
                    body=resp_body,
                )
        except urllib.error.HTTPError as e:
            resp_body = e.read()
            return ProxyResponse(
                status_code=e.code,
                headers={},
                body=resp_body,
            )
        except Exception as e:
            error_body = _json.dumps({"error": {"type": "proxy_error", "message": str(e)}}).encode()
            return ProxyResponse(
                status_code=502,
                headers={},
                body=error_body,
            )
