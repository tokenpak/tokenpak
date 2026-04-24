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
        """Invoke ``claude --print --output-format json`` with the request prompt.

        Session continuity (v1.3.14, 2026-04-24): if the request carries
        a platform signal (``X-OpenClaw-Session`` or similar), consult
        the session mapper to find the Claude CLI session UUID for this
        ``(platform, external_id, provider)`` triple. Pass ``--resume
        <uuid>`` on subsequent turns so multi-turn conversations stay
        coherent. First turn runs without ``--resume``; we parse the
        UUID out of the CLI's JSON output and persist it.

        When the mapper is disabled or there's no platform signal, fall
        back to the v1.3.13 ``--continue`` (resume-last-session)
        behavior so direct callers keep working.
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

            # Resolve platform origin + session mapping for this request.
            origin, mapped_session_id = self._resolve_session(request)

            # Build argv. --output-format json makes the CLI emit a
            # parseable record with session_id + usage + result; we
            # always ask for it so telemetry forwarding is accurate.
            import os as _os

            cmd = [self._claude_binary]
            if mapped_session_id:
                # Subsequent turn for a known platform session.
                cmd.extend(["--resume", mapped_session_id])
            elif origin is None and (
                _os.environ.get("TOKENPAK_OAUTH_NO_CONTINUE", "").strip() != "1"
            ):
                # No platform context at all — preserve v1.3.13 default.
                cmd.append("--continue")
            # If origin is present but there's no mapping yet, this is
            # the first turn — run fresh so the CLI picks its own UUID,
            # which we'll capture + persist below.
            cmd.extend(["--print", "--output-format", "json", prompt])

            completed = subprocess.run(
                cmd,
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

            # Parse the CLI's JSON output — session_id + usage + result text.
            parsed = self._parse_cli_output(completed.stdout)

            # Persist the session mapping on the first turn (when
            # we had an origin but no prior mapping). If the parse
            # failed we still return a valid response; the next
            # request for the same (platform, external_id) just
            # starts another fresh session — worst case is lost
            # continuity, never a user-visible failure.
            if origin is not None and mapped_session_id is None and parsed.get("session_id"):
                self._persist_session(origin, parsed["session_id"], parsed.get("model"))

            return BackendResponse(
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(self._as_messages_response(parsed)).encode(),
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

    # ── Session mapper integration (v1.3.14) ──────────────────────────

    def _resolve_session(self, request: Request):
        """Return ``(PlatformOrigin | None, mapped_session_id | None)``.

        Consults the platform bridge for the origin and the session
        mapper for a prior mapping. Never raises — a registry miss
        simply means "first turn for this platform session".
        """
        try:
            from tokenpak.services.routing_service.platform_bridge import (
                detect_origin,
                resolve_provider,
            )
        except Exception:
            return None, None
        try:
            origin = detect_origin(request.headers or {})
        except Exception:
            origin = None
        if origin is None or not origin.session_id:
            return origin, None
        provider = origin.declared_provider or resolve_provider(request.headers or {})
        if provider is None:
            return origin, None
        try:
            from tokenpak.services.routing_service.session_mapper import (
                get_session_mapper,
            )
        except Exception:
            return origin, None
        try:
            record = get_session_mapper().get(
                scope=origin.platform_name,
                external_id=origin.session_id,
                provider=provider,
            )
        except Exception:
            return origin, None
        if record is None:
            return origin, None
        return origin, record.internal_id

    @staticmethod
    def _persist_session(origin, claude_session_id: str, model: Optional[str]) -> None:
        """Store ``(scope=platform, external_id=session, provider) → claude uuid``."""
        try:
            from tokenpak.services.routing_service.platform_bridge import (
                resolve_provider,
            )
            from tokenpak.services.routing_service.session_mapper import (
                get_session_mapper,
            )
        except Exception:
            return
        provider = origin.declared_provider or "tokenpak-claude-code"
        # resolve_provider is headers-based; we already have the origin
        # so prefer its declared_provider, falling back to the default
        # for known platforms. Unknown platform → still persist under
        # whatever provider the request declared.
        _ = resolve_provider  # keep import present for future overrides
        metadata = {"model": model} if model else {}
        try:
            get_session_mapper().set(
                scope=origin.platform_name,
                external_id=origin.session_id,
                provider=provider,
                internal_id=claude_session_id,
                metadata=metadata,
            )
        except Exception:
            # Session mapping is a best-effort optimisation — never let
            # persistence failure break the live dispatch.
            pass

    # ── Claude CLI --output-format=json parsing ───────────────────────

    @staticmethod
    def _parse_cli_output(stdout: bytes) -> dict:
        """Decode ``claude --output-format json`` stdout.

        Expected schema (claude-cli 2.1.x):
          {"type":"result","session_id":"<uuid>","result":"<text>",
           "usage":{"input_tokens":N,"output_tokens":N,...},
           "modelUsage":{...},"total_cost_usd":F}

        Falls back to a best-effort text extraction when the CLI didn't
        emit JSON (e.g. old CLI version or an unexpected error path).
        """
        try:
            data = json.loads(stdout.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {
                "result": stdout.decode("utf-8", errors="replace"),
                "session_id": None,
                "model": None,
                "usage": {},
            }
        if not isinstance(data, dict):
            return {
                "result": str(data),
                "session_id": None,
                "model": None,
                "usage": {},
            }
        model = None
        model_usage = data.get("modelUsage")
        if isinstance(model_usage, dict) and model_usage:
            model = next(iter(model_usage.keys()), None)
        return {
            "result": data.get("result", ""),
            "session_id": data.get("session_id"),
            "model": model,
            "usage": data.get("usage") or {},
            "total_cost_usd": data.get("total_cost_usd"),
            "stop_reason": data.get("stop_reason") or "end_turn",
        }

    @staticmethod
    def _as_messages_response(parsed: dict) -> dict:
        """Re-shape the CLI's parsed output into an Anthropic Messages envelope."""
        usage = parsed.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        return {
            "id": f"msg_claude_{parsed.get('session_id') or 'cli'}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": parsed.get("result", "")}],
            "model": parsed.get("model") or "claude-via-oauth",
            "stop_reason": parsed.get("stop_reason", "end_turn"),
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": int(
                    usage.get("cache_creation_input_tokens") or 0
                ),
                "cache_read_input_tokens": int(
                    usage.get("cache_read_input_tokens") or 0
                ),
            },
        }

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
