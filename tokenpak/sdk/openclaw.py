"""tokenpak.sdk.openclaw — OpenClaw gateway adapter.

Supports two execution backends:

  **api** (default): OpenClaw → tokenpak proxy → Anthropic API
      Standard HTTP forwarding with full pipeline (compression, caching, dedup).

  **claude_code**: OpenClaw → tokenpak proxy → claude -p --resume
      Routes through Claude Code for tool use, CLAUDE.md context, subscription
      billing, and persistent multi-turn sessions via --resume.

The backend is selected by the ``X-TokenPak-Backend: claude-code`` header
on the incoming request. OpenClaw configures this per-provider in its
config (e.g. ``tokenpak-claude-code`` provider vs ``tokenpak-anthropic``).

Session mapping:
  Each OpenClaw session ID (from ``X-OpenClaw-Session`` header or message
  metadata) maps to a Claude Code session UUID. The mapping persists in
  ``~/.tokenpak/openclaw_sessions.json`` so conversations survive restarts.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from tokenpak.sdk.base import TokenPakAdapter


_SESSION_MAP_PATH = Path.home() / ".tokenpak" / "openclaw_sessions.json"
_SESSION_MAP: Optional[dict] = None


def _load_session_map() -> dict:
    global _SESSION_MAP
    if _SESSION_MAP is not None:
        return _SESSION_MAP
    if _SESSION_MAP_PATH.exists():
        try:
            _SESSION_MAP = json.loads(_SESSION_MAP_PATH.read_text())
        except Exception:
            _SESSION_MAP = {}
    else:
        _SESSION_MAP = {}
    return _SESSION_MAP


def _save_session_map() -> None:
    if _SESSION_MAP is None:
        return
    _SESSION_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SESSION_MAP_PATH.write_text(json.dumps(_SESSION_MAP, indent=2))


def _get_claude_session(openclaw_session: str) -> tuple[str, bool]:
    """Map an OpenClaw session ID to a Claude Code session UUID.

    Returns (claude_session_uuid, is_new).
    """
    smap = _load_session_map()
    if openclaw_session in smap:
        return smap[openclaw_session], False
    # New session
    claude_id = str(uuid.uuid4())
    smap[openclaw_session] = claude_id
    _save_session_map()
    return claude_id, True


def execute_via_claude_code(
    openclaw_session: str,
    messages: list[dict[str, Any]],
    model: str = "claude-sonnet-4-6",
    system: str = "",
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Execute a request through Claude Code via ``tokenpak claude -p --resume``.

    This is called by the proxy when ``X-TokenPak-Backend: claude-code`` is set.

    Args:
        openclaw_session: OpenClaw's session/conversation ID.
        messages: Anthropic-format messages array.
        model: Model to use.
        system: System prompt (if any).
        max_tokens: Max output tokens.

    Returns:
        Anthropic-format response dict with usage metrics.
    """
    claude_session, is_new = _get_claude_session(openclaw_session)

    # Extract the latest user message (Claude Code maintains history via --resume)
    latest_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                latest_msg = " ".join(
                    b.get("text", "") for b in content if b.get("type") == "text"
                )
            else:
                latest_msg = str(content)
            break

    if not latest_msg:
        return _error_response("No user message found in request")

    # Build the command
    cmd = ["tokenpak", "claude"]
    cmd.extend(["--model", model])

    if is_new:
        cmd.extend(["--session-id", claude_session])
    else:
        cmd.extend(["--resume", claude_session])

    cmd.extend(["--output-format", "json"])
    cmd.extend(["-p", latest_msg])

    # Execute
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return _error_response("Claude Code session timed out (300s)")
    except FileNotFoundError:
        return _error_response("tokenpak or claude command not found")

    elapsed = time.monotonic() - t0

    if proc.returncode != 0 and not proc.stdout.strip():
        error_msg = proc.stderr.strip()[:300] or f"Exit code {proc.returncode}"
        return _error_response(error_msg)

    # Parse Claude Code JSON output
    output = proc.stdout.strip()
    if output.startswith("{"):
        try:
            data = json.loads(output)
            # Convert Claude Code JSON to Anthropic API response format
            return _format_anthropic_response(data, model, elapsed)
        except json.JSONDecodeError:
            pass

    # Plain text fallback
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": output}],
        "model": model,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": len(latest_msg) // 4,
            "output_tokens": len(output) // 4,
        },
    }


def _format_anthropic_response(data: dict, model: str, elapsed: float) -> dict:
    """Convert Claude Code --output-format json to Anthropic API format."""
    result_text = data.get("result", "")
    usage = data.get("usage", {}) or {}
    cost = data.get("cost_usd", 0)

    # If the JSON already has Anthropic-format fields, pass through
    if data.get("type") == "message":
        return data

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": result_text}],
        "model": model,
        "stop_reason": data.get("stop_reason", "end_turn"),
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        },
    }


def _error_response(message: str) -> dict:
    """Build an Anthropic-format error response."""
    return {
        "type": "error",
        "error": {
            "type": "api_error",
            "message": message,
        },
    }


class OpenClawAdapter(TokenPakAdapter):
    """Adapter for OpenClaw gateway environments.

    Supports ``backend="api"`` (default HTTP forwarding) and
    ``backend="claude_code"`` (route through Claude Code CLI).
    """

    provider_name = "openclaw"

    def __init__(self, base_url: str = "", api_key: str = "openclaw") -> None:
        url = base_url or os.environ.get("OPENCLAW_GATEWAY_URL", "http://localhost:18789")
        super().__init__(base_url=url, api_key=api_key)

    def prepare_request(self, request: dict) -> dict:
        return request

    def parse_response(self, response: dict) -> dict:
        return response

    def extract_tokens(self, response: dict) -> dict:
        usage = response.get("usage", {})
        return {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "cache_write": usage.get("cache_creation_input_tokens", 0),
            "total": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        }

    def send(self, prepared_request: dict) -> dict:
        """Send via HTTP (standard path). For claude_code backend,
        the proxy calls execute_via_claude_code() directly."""
        import httpx
        headers = {"content-type": "application/json"}
        resp = httpx.post(
            f"{self.base_url}/v1/messages",
            json=prepared_request,
            headers=headers,
            timeout=120.0,
        )
        return resp.json()


__all__ = ["OpenClawAdapter", "execute_via_claude_code"]
