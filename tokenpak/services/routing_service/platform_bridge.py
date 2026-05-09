# SPDX-License-Identifier: Apache-2.0
"""Platform-origin extraction (Path C, OAS-11).

When inbound traffic arrives at the proxy, we need to attribute it to the
calling platform/session so wire-side telemetry can be grouped by the
operator-visible conversation. Path C uses a filesystem rendezvous: the
``openclaw-adapter`` hook writes the active session UUID to
``~/.openclaw/sessions/active.json``; this module reads it on traffic
keyed by ``User-Agent: openclaw*``.

Public surface
--------------
* :class:`PlatformOrigin` — dataclass returned to callers (``platform_name``,
  ``session_id``, ``attribution_source``).
* :func:`_openclaw_extract(headers, body)` — extractor. Returns
  ``PlatformOrigin`` for OpenClaw traffic, ``None`` otherwise.

Attribution-source enum
-----------------------
Per ``feedback_status_attribution_contract`` ("never over-claim certainty").
Every PlatformOrigin returned for OpenClaw traffic carries one of:

* :data:`ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE` — fresh active.json
  with valid UUID + last_event_ts within TTL window.
* :data:`ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY` — User-Agent says openclaw
  but the rendezvous file is missing / stale / malformed / non-UUID.
* :data:`ATTRIBUTION_UNKNOWN` — default for non-OpenClaw extractors and
  pre-Path-C journal rows.

Constraints (from ``03-SPEC.md §Component 7``)
----------------------------------------------
1. **Read-only.** Proxy never writes to active.json. Hook owns writes.
2. **Stale TTL = 300s** by default; configurable via env
   ``OPENCLAW_ACTIVE_TTL_SEC``.
3. **Schema validated.** Refuse if ``schema_version != "1.0"`` or
   ``session_uuid`` is not a UUID string.
4. **Malformed → fallback.** ``FileNotFoundError``, ``PermissionError``,
   ``json.JSONDecodeError``, generic ``OSError`` all degrade gracefully
   to ``ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY``.
5. **Never raise.** Extraction always returns a ``PlatformOrigin`` or
   ``None`` — never lets an exception escape into the request path.
6. **1-second mtime-keyed cache** prevents per-request file thrash.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Attribution flag — fresh active.json read with a valid UUID.
ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE = "openclaw_active_session_file"

#: Attribution flag — User-Agent says openclaw but session info was unavailable.
ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY = "anonymous_user_agent_only"

#: Attribution flag — default for non-OpenClaw extractors / legacy rows.
ATTRIBUTION_UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Module config
# ---------------------------------------------------------------------------

_DEFAULT_ACTIVE_FILE = Path.home() / ".openclaw" / "sessions" / "active.json"
_DEFAULT_TTL_SEC = 300

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _active_file_path() -> Path:
    """Return the active.json path (env-overridable for tests)."""
    override = os.environ.get("OPENCLAW_ACTIVE_FILE")
    if override:
        return Path(override)
    return _DEFAULT_ACTIVE_FILE


def _ttl_sec() -> int:
    """Return the staleness TTL in seconds (env-overridable)."""
    raw = os.environ.get("OPENCLAW_ACTIVE_TTL_SEC")
    if not raw:
        return _DEFAULT_TTL_SEC
    try:
        ttl = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TTL_SEC
    return ttl if ttl > 0 else _DEFAULT_TTL_SEC


# ---------------------------------------------------------------------------
# PlatformOrigin
# ---------------------------------------------------------------------------


@dataclass
class PlatformOrigin:
    """Platform/session attribution for an inbound proxy request.

    Attributes:
        platform_name: Stable platform identifier (e.g. ``"openclaw"``,
            ``"claude-code"``, ``"codex"``). Future extractors will populate
            their own values; this module emits ``"openclaw"`` only.
        session_id: External-side session identifier, if known. ``None``
            when extraction succeeded (we know it's our traffic) but the
            session UUID was not resolvable.
        attribution_source: One of the ``ATTRIBUTION_*`` constants. Required
            non-empty per ``feedback_status_attribution_contract``; defaults
            to ``ATTRIBUTION_UNKNOWN`` for backward compatibility with any
            future extractor that doesn't (yet) set the field.
    """

    platform_name: str
    session_id: Optional[str] = None
    attribution_source: str = ATTRIBUTION_UNKNOWN


# ---------------------------------------------------------------------------
# active.json reader (1-second mtime-keyed cache)
# ---------------------------------------------------------------------------

# Mutable module-level cache. Single-process; `Monitor.log` is called on the
# request thread, so contention is bounded by that hot path. The 1-second
# wall-clock window keeps the cache from going stale across pre-send +
# response-finalize pairs in a single conversation turn.
_cache: dict = {
    "path": None,         # cache key: which file path was last read
    "mtime": None,        # mtime stamp at last read
    "payload": None,      # parsed JSON payload (or None on miss)
    "read_ts": 0.0,       # wall-clock time of last read
}


def _read_active_json() -> Optional[dict]:
    """Read and parse ``~/.openclaw/sessions/active.json``.

    Returns the parsed dict on success, or ``None`` when the file is
    absent, unreadable, or malformed. Never raises.

    Implements a 1-second mtime-keyed in-memory cache to avoid per-request
    filesystem reads under burst traffic.
    """
    path = _active_file_path()
    try:
        try:
            stat = path.stat()
        except (FileNotFoundError, PermissionError, OSError):
            return None

        # Cache hit: same path, same mtime, within the 1-second window.
        if (
            _cache["path"] == path
            and _cache["mtime"] == stat.st_mtime
            and _cache["payload"] is not None
            and (time.time() - _cache["read_ts"]) < 1.0
        ):
            return _cache["payload"]

        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return None

        _cache["path"] = path
        _cache["mtime"] = stat.st_mtime
        _cache["payload"] = payload
        _cache["read_ts"] = time.time()
        return payload
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def _reset_cache_for_tests() -> None:
    """Reset the module cache. Test-only helper."""
    _cache["path"] = None
    _cache["mtime"] = None
    _cache["payload"] = None
    _cache["read_ts"] = 0.0


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


def _anonymous() -> PlatformOrigin:
    """Build the anonymous-fallback PlatformOrigin for OpenClaw traffic."""
    return PlatformOrigin(
        platform_name="openclaw",
        session_id=None,
        attribution_source=ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY,
    )


def _user_agent(headers: Mapping[str, Any]) -> str:
    """Case-insensitively extract User-Agent from a header mapping."""
    if hasattr(headers, "get"):
        ua = headers.get("user-agent")
        if ua is None:
            ua = headers.get("User-Agent")
        if ua is None:
            # last resort: linear scan for any case combination
            for k, v in headers.items():
                if isinstance(k, str) and k.lower() == "user-agent":
                    ua = v
                    break
        if ua is None:
            return ""
        return str(ua)
    return ""


def _openclaw_extract(headers: Mapping[str, Any], body: bytes) -> Optional[PlatformOrigin]:
    """Resolve the OpenClaw platform origin for an inbound request.

    Args:
        headers: Mapping of HTTP request headers (case-insensitive).
        body: Raw request body bytes (currently unused; reserved for future
            extractors that key off body shape).

    Returns:
        ``PlatformOrigin`` when ``User-Agent`` starts with ``openclaw``
        (case-insensitive). The returned instance always carries
        ``attribution_source`` set to one of:

            * :data:`ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE` (fresh + valid)
            * :data:`ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY` (any failure mode)

        ``None`` when the User-Agent does not match — caller should let
        other extractors try. **No filesystem access happens for non-OpenClaw
        traffic** (per Kevin's gate G6).
    """
    del body  # reserved for future extractors

    ua = _user_agent(headers).lower()
    if not ua.startswith("openclaw"):
        return None

    # User-Agent says openclaw — we own the attribution decision now.
    payload = _read_active_json()
    if payload is None:
        return _anonymous()

    if payload.get("schema_version") != "1.0":
        return _anonymous()

    uuid = payload.get("session_uuid")
    if not isinstance(uuid, str) or not _UUID_RE.match(uuid):
        return _anonymous()

    raw_ts = payload.get("last_event_ts")
    try:
        last_ts = float(raw_ts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _anonymous()

    age = time.time() - last_ts
    if age > _ttl_sec() or age < 0:
        return _anonymous()

    return PlatformOrigin(
        platform_name="openclaw",
        session_id=uuid,
        attribution_source=ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE,
    )
