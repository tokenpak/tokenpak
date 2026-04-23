"""Claude Code OAuth backend — route requests through the ``claude`` CLI.

Used when ``X-TokenPak-Backend: claude-code`` is set on the request OR
when policy explicitly directs traffic to the OAuth path. The CLI is
launched as a subprocess; stdout streaming is converted to the
response body. This uses the user's Claude Max subscription quota
(OAuth) instead of API-key billing.

Contract requirements (preserved from the 2026-04-13 byte-preserved
proxy architecture memory):

- **No JSON re-serialization** of the request body — hand the CLI the
  exact bytes the client sent.
- **Header pass-through** — the CLI reads its own auth from
  ``~/.claude/.credentials.json``; headers are forwarded only for
  anthropic-beta markers and session correlation.
- **Graceful failure** — if the CLI isn't installed or isn't logged
  in, return a 502 with a diagnostic; do NOT fall back to the
  api-key backend silently (that would break OAuth billing
  expectations).

This is a γ-phase skeleton: the streaming subprocess driver still
needs the live proxy pipeline rewrite before it becomes the hot path.
For now it validates the contract + provides a callable target that
the selector can dispatch to during integration tests.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Optional

from tokenpak.services.request import Request
from tokenpak.services.routing_service.backends.base import BackendResponse

logger = logging.getLogger(__name__)


class AnthropicOAuthBackend:
    """Dispatch requests through the local ``claude`` CLI."""

    name = "anthropic-oauth"

    def __init__(self, claude_binary: Optional[str] = None) -> None:
        self._claude_binary = claude_binary or shutil.which("claude") or "claude"

    def _is_available(self) -> bool:
        """True only when the configured binary resolves + is executable."""
        import os

        # Bare name: ask PATH.
        if "/" not in self._claude_binary:
            return bool(shutil.which(self._claude_binary))
        # Absolute / relative path: must exist and be executable.
        return os.path.isfile(self._claude_binary) and os.access(
            self._claude_binary, os.X_OK
        )

    def dispatch(self, request: Request) -> BackendResponse:
        """Invoke ``claude --print`` with the request body on stdin.

        Current state: executes the CLI synchronously + captures
        stdout. Streaming mode lands alongside the proxy pipeline
        rewrite. Returns a JSON-shaped Anthropic Messages response.
        """
        if not self._is_available():
            return BackendResponse(
                status=502,
                headers={"content-type": "application/json"},
                body=json.dumps({
                    "error": {
                        "type": "backend_unavailable",
                        "message": (
                            "Claude Code CLI not found on PATH. Install it "
                            "and run `claude auth login` for OAuth billing."
                        ),
                    }
                }).encode(),
            )

        try:
            # Extract the user message from the request body — the CLI
            # expects a prompt, not a wire-format JSON payload.
            prompt = self._extract_prompt(request.body or b"")
            if prompt is None:
                return BackendResponse(
                    status=400,
                    headers={"content-type": "application/json"},
                    body=json.dumps({
                        "error": {
                            "type": "invalid_request",
                            "message": "Could not extract prompt from request body.",
                        }
                    }).encode(),
                )

            # Synchronous invocation for γ; streaming driver lands later.
            completed = subprocess.run(
                [self._claude_binary, "--print", prompt],
                capture_output=True,
                timeout=120,
                check=False,
            )
            if completed.returncode != 0:
                err = completed.stderr.decode("utf-8", errors="replace")[:500]
                return BackendResponse(
                    status=502,
                    headers={"content-type": "application/json"},
                    body=json.dumps({
                        "error": {
                            "type": "backend_failure",
                            "message": f"claude CLI exited {completed.returncode}: {err}",
                        }
                    }).encode(),
                )

            # Wrap the CLI's text output in Anthropic Messages response shape
            # so clients can consume it uniformly.
            assistant_text = completed.stdout.decode("utf-8", errors="replace")
            return BackendResponse(
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps({
                    "id": "msg_claude_cli",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": assistant_text}],
                    "model": "claude-via-oauth",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                }).encode(),
            )
        except subprocess.TimeoutExpired:
            return BackendResponse(
                status=504,
                headers={"content-type": "application/json"},
                body=json.dumps({
                    "error": {"type": "timeout", "message": "claude CLI timed out"}
                }).encode(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("anthropic-oauth backend: dispatch failed: %s", exc)
            return BackendResponse(
                status=502,
                headers={"content-type": "application/json"},
                body=json.dumps({
                    "error": {"type": "backend_error", "message": str(exc)[:200]}
                }).encode(),
            )

    @staticmethod
    def _extract_prompt(body: bytes) -> Optional[str]:
        """Pull the last user turn out of an Anthropic Messages body."""
        if not body:
            return None
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        messages = data.get("messages") or []
        for msg in reversed(messages):
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        t = blk.get("text")
                        if isinstance(t, str):
                            parts.append(t)
                if parts:
                    return "\n".join(parts)
        return None


__all__ = ["AnthropicOAuthBackend"]
