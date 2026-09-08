# SPDX-License-Identifier: Apache-2.0
"""Authenticated observations from the proxy that owns pending spend.

This is a diagnostic input to future live guard resolution, not permission
to recommend or spend. Existing process-local accounting has incomplete
coverage; every response preserves that ineligibility explicitly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_MAX_BODY = 4096
_ROLLING_FIELDS = (
    "rolling_caps_enabled",
    "rolling_caps_window_seconds",
    "rolling_caps_per_agent_max_cost_usd",
    "rolling_caps_per_agent_max_tokens_total",
    "rolling_caps_per_agent_max_cache_read_tokens",
    "rolling_caps_per_fleet_max_cost_usd",
    "rolling_caps_per_fleet_max_tokens_total",
    "rolling_caps_per_fleet_max_cache_read_tokens",
)


def _effective_policy() -> tuple[dict[str, Any], str]:
    """Use the effective policy parser without config migration or writes."""
    from tokenpak import _paths
    from tokenpak.core import config_loader
    from tokenpak.proxy.spend_guard.policy import load_config

    override = os.environ.get("TOKENPAK_CONFIG", "").strip()
    path = Path(override).expanduser() if override else _paths.config_read_path()
    raw: dict[str, Any] = {}
    if path is not None:
        yaml_parser = getattr(config_loader, "_yaml", None)
        parser = (
            getattr(yaml_parser, "safe_load", None)
            if yaml_parser is not None
            else getattr(getattr(config_loader, "_json", None), "load", None)
        )
        if not callable(parser):
            raise ValueError("config parser unavailable")
        with path.open(encoding="utf-8") as handle:
            raw = parser(handle)
        if not isinstance(raw, dict) or not all(isinstance(k, str) for k in raw):
            raise ValueError("config must be an object")
    resolved = asdict(load_config(raw_config=raw))
    digest = hashlib.sha256(
        json.dumps(resolved, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    policy = {name: resolved[name] for name in ("enabled", *_ROLLING_FIELDS)}
    for name, value in policy.items():
        if name.endswith("enabled"):
            if type(value) is not bool:
                raise ValueError("invalid policy switch")
        elif type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid rolling policy value")
    return policy, digest


def _send(handler: Any, status: int, body: dict[str, Any]) -> None:
    raw = json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(raw)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def handle_post(handler: Any) -> None:
    """Serve only an explicit session, with a configured existing app key.

    Localhost alone is not proof of same-user access. This route requires
    the existing app key even when the general proxy permits loopback.
    """
    handler.close_connection = True
    key = os.environ.get("TOKENPAK_PROXY_KEY", "").strip()
    if not key:
        _send(handler, 503, {"error": "snapshot_auth_unconfigured"})
        return
    supplied = handler.headers.get("X-TPK-Key", "")
    if handler.client_address[0] not in ("127.0.0.1", "::1") or not hmac.compare_digest(
        supplied.encode(), key.encode()
    ):
        _send(handler, 401, {"error": "unauthorized"})
        return
    # No browser-origin access, including on deployments allowing app CORS.
    if handler.headers.get("Origin") is not None:
        _send(handler, 403, {"error": "browser_origin_forbidden"})
        return
    try:
        if urlsplit(handler.path).query:
            raise ValueError("query parameters forbidden")
        lengths = handler.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or handler.headers.get("Transfer-Encoding"):
            raise ValueError("one Content-Length required")
        length = int(lengths[0])
        if not 0 < length <= _MAX_BODY:
            _send(handler, 413, {"error": "snapshot_body_size"})
            return
        if handler.headers.get_content_type() != "application/json":
            raise ValueError("JSON content type required")
        handler.connection.settimeout(2.0)
        raw = handler.rfile.read(length)
        if len(raw) != length:
            raise ValueError("incomplete body")
        payload = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(payload, dict) or set(payload) != {"session_id"}:
            raise ValueError("exact session input required")
        session_id = payload["session_id"]
        if (
            not isinstance(session_id, str)
            or not session_id
            or len(session_id) > 512
            or session_id.strip() != session_id
            or any(ord(c) < 32 for c in session_id)
        ):
            raise ValueError("invalid explicit session")
        from tokenpak.proxy.request_pipeline import _resolve_session_id

        header_session = _resolve_session_id(handler.headers, "")
        if header_session and header_session != session_id:
            raise ValueError("conflicting session identity")
        stable_headers = {
            "x-claude-code-session-id",
            "x-tokenpak-session",
            "session-id",
            "thread-id",
        }
        if any(
            name.lower() in stable_headers and value != session_id
            for name, value in handler.headers.items()
        ):
            raise ValueError("contradictory stable session headers")
    except (ValueError, TypeError, UnicodeError, OSError, RecursionError):
        _send(handler, 400, {"error": "invalid_snapshot_input"})
        return

    owner = getattr(handler.server, "proxy_server", None)
    monitor = getattr(owner, "monitor", None)
    owner_id = getattr(owner, "_guard_snapshot_owner_id", None)
    if monitor is None or not owner_id:
        _send(handler, 503, {"error": "snapshot_owner_unavailable"})
        return
    try:
        from tokenpak.proxy.spend_guard.rolling_caps import _capture_rolling_snapshot

        policy, policy_hash = _effective_policy()
        components = _capture_rolling_snapshot(
            session_id,
            policy["rolling_caps_window_seconds"],
            monitor_db_path=str(monitor.db_path),
        )
    except Exception:
        # Do not expose config paths, input text, credentials or raw errors.
        _send(handler, 503, {"error": "snapshot_resolution_unavailable"})
        return
    _send(
        handler,
        200,
        {
            "schema_version": "native-guard-snapshot/1",
            "session_id": session_id,
            "owner_instance_id": owner_id,
            "scope": "serving_proxy_process",
            "produced_at": datetime.now(timezone.utc).isoformat(),
            "rolling_policy": policy,
            "effective_policy_sha256": policy_hash,
            **components,
        },
    )
