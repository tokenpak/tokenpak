# SPDX-License-Identifier: Apache-2.0
"""Opt-in response-egress capture intake (OSS side of MultiPak Pro capture).

This is the OSS half of the MultiPak Pro response→Interaction-Pak path
(the MultiPak Pro capture pipeline; OSS-contract-first — the OSS proxy never
depends on the Pro daemon and always works without it). Its single job
is to **forward** an opt-in-flagged model response to the loopback Pro daemon's
``POST /pak/v1/promote`` endpoint. It does the absolute minimum so the
"OSS never captures, never stores" invariant is *structural*, not just policy:

* **OSS persists nothing.** This module reads the already-produced response,
  builds a daemon-shaped payload, and POSTs it to the loopback daemon. There is
  no local store, no DB write, no file write anywhere in this path.
* **Opt-in is per-request and explicit.** A response is only forwarded when the
  inbound request carried ``X-TokenPak-Capture: opt-in`` **and** the operator
  has set ``TOKENPAK_PROXY_CAPTURE_INTAKE=1``. Absence of either → inert no-op.
  There is no global "capture everything" switch.
* **Pro-daemon-gated.** If no daemon is reachable (sock-info probe), the path is
  a no-op. No daemon, no header, or disabled flag → nothing happens.
* **Fail-silent, never blocks the client.** The proxy entrypoint
  (:func:`maybe_forward_capture`) hands the work to a short-lived daemon thread
  and returns immediately. The client already has its response by the time this
  runs (the proxy writes the response to the client *before* the post-response
  hook). Any error is swallowed — capture intake must never break a request.

Wire contract (kept in sync with the Pro daemon's capture-event JSON shape; the
OSS proxy intentionally does NOT import any Pro types — it constructs the
documented JSON shape the daemon's promote endpoint accepts):

    {
      "source":      "llm_response",   # CaptureSource enum value
      "content":     "<response text>",
      "captured_at": "<ISO-8601 UTC>",
      "platform":    "<provider/app>",
      "session_id":  "<optional>",
      "metadata":    {"model": "...", "via": "proxy-capture-intake"}
    }

Default is **OFF**. Public-default proxy behavior is unchanged when the flag is
unset, which is the shipped default.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

# ── Opt-in surface ──────────────────────────────────────────────────────────
OPTIN_HEADER = "X-TokenPak-Capture"
OPTIN_VALUE = "opt-in"
ENABLE_ENV = "TOKENPAK_PROXY_CAPTURE_INTAKE"

# CaptureSource value for a model response observed at the proxy.
_SOURCE_LLM_RESPONSE = "llm_response"
# Loopback forward timeout. The daemon is local; if it can't answer a tiny POST
# within this, treat it as unavailable. Keep small so a hung daemon cannot pin
# the worker thread.
_FORWARD_TIMEOUT_S = 2.0
# Defensive ceiling on response text we will forward (protects the daemon and
# avoids shipping pathological bodies over loopback). The capture pipeline
# payloads are expected to be small.
_MAX_CONTENT_CHARS = 256_000

_TRUTHY = {"1", "true", "yes", "on"}


def _enabled() -> bool:
    """True when the operator explicitly enabled capture intake."""
    return (os.environ.get(ENABLE_ENV, "") or "").strip().lower() in _TRUTHY


def opt_in_requested(headers: Any) -> bool:
    """True when the inbound request carried ``X-TokenPak-Capture: opt-in``.

    ``headers`` is anything with a ``.get`` (``http.client.HTTPMessage`` or a
    plain dict). Comparison is case- and whitespace-insensitive on the value.
    """
    try:
        val = headers.get(OPTIN_HEADER)
    except Exception:
        return False
    if not val:
        return False
    return str(val).strip().lower() == OPTIN_VALUE


def should_attempt(headers: Any) -> bool:
    """Two-factor gate: operator flag AND explicit per-request opt-in header."""
    return _enabled() and opt_in_requested(headers)


def extract_response_text(response_body: Any, model: str = "") -> Optional[str]:
    """Best-effort extraction of assistant text from a buffered response body.

    Read-only. Returns ``None`` (→ no-op upstream) on anything unparseable.
    Supports the Anthropic Messages shape (``content`` = list of blocks) and the
    OpenAI Chat Completions shape (``choices[0].message.content``). Never raises.
    """
    try:
        if isinstance(response_body, (bytes, bytearray)):
            raw = bytes(response_body).decode("utf-8", errors="replace")
        elif isinstance(response_body, str):
            raw = response_body
        else:
            return None
        raw = raw.strip()
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None

        # Anthropic Messages: {"content": [{"type": "text", "text": "..."}, ...]}
        content = data.get("content")
        if isinstance(content, list):
            parts = [
                blk.get("text", "")
                for blk in content
                if isinstance(blk, dict) and blk.get("type") == "text"
            ]
            text = "".join(p for p in parts if p)
            if text.strip():
                return text[:_MAX_CONTENT_CHARS]

        # OpenAI Chat Completions: {"choices": [{"message": {"content": "..."}}]}
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict):
                c = msg.get("content")
                if isinstance(c, str) and c.strip():
                    return c[:_MAX_CONTENT_CHARS]

        return None
    except Exception:
        return None


def build_capture_payload(
    text: str,
    *,
    model: str = "",
    session_id: Optional[str] = None,
    platform: str = "proxy",
    captured_at_iso: Optional[str] = None,
) -> dict:
    """Build the daemon-shaped ``CaptureEvent`` JSON payload (no Pro import)."""
    if captured_at_iso is None:
        captured_at_iso = datetime.now(timezone.utc).isoformat()
    metadata = {"via": "proxy-capture-intake"}
    if model:
        metadata["model"] = model
    payload: dict = {
        "source": _SOURCE_LLM_RESPONSE,
        "content": text,
        "captured_at": captured_at_iso,
        "platform": platform or "proxy",
        "metadata": metadata,
    }
    if session_id:
        payload["session_id"] = session_id
    return payload


def forward_to_daemon(payload: Mapping[str, Any], *, timeout: float = _FORWARD_TIMEOUT_S) -> Optional[dict]:
    """POST ``payload`` to the loopback Pro daemon's ``/pak/v1/promote``.

    Returns ``{"status": int, "body": dict}`` on a completed round-trip, or
    ``None`` when no daemon is reachable / anything fails. Never raises.
    OSS stores nothing — this only forwards.
    """
    try:
        from tokenpak.licensing.daemon_probe import detect_daemon_state, sock_info_path
    except Exception:
        return None
    try:
        if detect_daemon_state() != "active":
            return None
        info = json.loads(sock_info_path().read_text(encoding="utf-8"))
        port = int(info["port"])
    except Exception:
        return None

    conn = None
    try:
        body = json.dumps(dict(payload)).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        # The daemon is loopback-only and requires no auth on /pak/v1/*; we do
        # NOT forward the caller's auth headers (mirrors the existing
        # _handle_pak_promote_forward defensive contract).
        conn.request(
            "POST",
            "/pak/v1/promote",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        resp = conn.getresponse()
        raw = resp.read()
        parsed: dict = {}
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except Exception:
                parsed = {}
        return {"status": resp.status, "body": parsed}
    except Exception:
        return None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _run_capture(
    headers: Any,
    response_body: Any,
    model: str = "",
    *,
    session_id: Optional[str] = None,
    platform: str = "proxy",
) -> Optional[dict]:
    """Synchronous worker: gate → extract → build → forward. Testable.

    Returns the daemon round-trip result, or ``None`` for any no-op/short
    circuit (disabled, no header, no text, no daemon). Never raises.
    """
    try:
        if not should_attempt(headers):
            return None
        text = extract_response_text(response_body, model)
        if not text:
            return None
        payload = build_capture_payload(
            text, model=model, session_id=session_id, platform=platform
        )
        return forward_to_daemon(payload)
    except Exception:
        return None


def maybe_forward_capture(
    headers: Any,
    response_body: Any,
    model: str = "",
    *,
    session_id: Optional[str] = None,
    platform: str = "proxy",
) -> None:
    """Proxy entrypoint — fire-and-forget, non-blocking, fail-silent.

    Call from the proxy's **post-response** point (after the response has been
    written to the client). Hands the work to a short-lived daemon thread and
    returns immediately so the worker thread is never blocked on the daemon.
    Does nothing (and never raises) unless capture intake is enabled AND the
    request carried the opt-in header.
    """
    try:
        if not should_attempt(headers):
            return
        t = threading.Thread(
            target=_run_capture,
            args=(headers, response_body, model),
            kwargs={"session_id": session_id, "platform": platform},
            name="tpk-capture-intake",
            daemon=True,
        )
        t.start()
    except Exception:
        # Capture intake must never break a request — not even thread spawn.
        try:
            sys.stderr.write("tokenpak: capture-intake spawn failed (ignored)\n")
        except Exception:
            pass
