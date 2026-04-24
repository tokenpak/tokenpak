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
from pathlib import Path
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
            # Pass the model from the request body so Claude CLI doesn't
            # pick its own default (Apr 15-18 parity — the bridge
            # respected OpenClaw's /model selection).
            model_hint = self._extract_model(request.body or b"")
            if model_hint:
                cmd.extend(["--model", model_hint])
            # Session mode decision:
            #   1. Explicit X-OpenClaw-Session (or equivalent) that
            #      maps to a stored Claude session → --resume <uuid>
            #      (multi-turn via CLI state, v1.3.14 semantic)
            #   2. Platform signal (UA=openclaw, X-TokenPak-Backend)
            #      BUT no session header → START FRESH every call.
            #      The platform replays full messages[] per turn so
            #      we don't need CLI-side continuity; resuming
            #      instead accumulates irrelevant prior state +
            #      triggers Claude Code auto-compaction. v1.3.21 fix.
            #   3. No platform signal at all → --continue fallback
            #      (direct CLI callers that don't replay history).
            # Session mode decision:
            #   1. Mapped session (explicit platform session id OR
            #      conversation fingerprint match) → ``--resume <uuid>``.
            #      Claude CLI has the prior-turn state locally; Anthropic
            #      cache accumulates across turns within the same
            #      OpenClaw conversation. This is Kevin's 2026-04-24
            #      ratified target behavior.
            #   2. Platform-bridged FIRST turn (no mapping yet) → run
            #      fresh, let Claude CLI pick a UUID. Session IS
            #      persisted locally so turn 2 can resume via the
            #      fingerprint we write in _persist_session.
            #   3. Direct CLI caller with no platform signal → the
            #      legacy ``--continue`` fallback (resumes whatever
            #      last session on this machine — only appropriate
            #      when the caller is the local claude CLI itself).
            is_platform_bridged = (
                origin is not None
                or self._has_platform_headers(request.headers or {})
            )
            if mapped_session_id:
                cmd.extend(["--resume", mapped_session_id])
            elif is_platform_bridged:
                # First turn — don't --continue. Default session
                # persistence is fine; we'll persist the UUID via
                # _persist_session after parsing the response, so
                # turn 2 can resume by fingerprint.
                pass
            elif _os.environ.get("TOKENPAK_OAUTH_NO_CONTINUE", "").strip() != "1":
                # Direct CLI caller fallback.
                cmd.append("--continue")
            cmd.extend(["--print", "--output-format", "json", prompt])

            # Clean env (Apr 15-18 pattern, with cache re-enabled
            # 2026-04-24 after empirical verification):
            #   - Strip ANTHROPIC_BASE_URL / OPENAI_BASE_URL so the
            #     subprocess doesn't loop back through this proxy
            #     (infinite recursion + throughput tank).
            #   - TOKENPAK_COMPANION_BARE=1 hints the companion hook
            #     to skip injecting the CLI's native CLAUDE.md /
            #     system context — the caller (OpenClaw etc.) is
            #     already carrying its own context in the messages[].
            #
            # We deliberately DO NOT set DISABLE_PROMPT_CACHING. The
            # Apr 15-18 monolith had it because the in-proxy
            # compression pipeline competed with the CLI's
            # cache_control markers; in the v1.3.20+ architecture
            # the subprocess hits api.anthropic.com directly (no
            # ANTHROPIC_BASE_URL loop), so Anthropic's server-side
            # prompt cache is free to fire. Cache hits across turns
            # depend on a stable workspace-prompt prefix, which our
            # content-hashed tempfile preserves. Set
            # TOKENPAK_BRIDGE_DISABLE_PROMPT_CACHE=1 to opt out
            # (debugging only).
            _env = _os.environ.copy()
            _env.pop("ANTHROPIC_BASE_URL", None)
            _env.pop("OPENAI_BASE_URL", None)
            _env["TOKENPAK_COMPANION_BARE"] = "1"
            if _os.environ.get(
                "TOKENPAK_BRIDGE_DISABLE_PROMPT_CACHE", ""
            ).strip() == "1":
                _env["DISABLE_PROMPT_CACHING"] = "1"

            # Workspace resolution (Apr 15-18 parity): caller's
            # X-OpenClaw-Workspace header wins; otherwise default to
            # ~/.openclaw/workspace which is where the gateway's agent
            # state lives. cwd matters because Claude CLI reads
            # CLAUDE.md / settings from cwd and its parent tree.
            _workspace = self._resolve_workspace(request)

            # Platform-slim context by default: concatenate the
            # workspace's *.md files into one appended system prompt
            # file and skip the tokenpak-companion MCP + system
            # prompt layer unless TOKENPAK_BRIDGE_COMPANION=1.
            # See ``_build_context_flags`` below.
            cmd = cmd[:1] + self._build_context_flags(_workspace) + cmd[1:]

            completed = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300,
                check=False,
                env=_env,
                cwd=_workspace,
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
            # Persist session mapping on first turn when we learned a
            # new Claude session id. Covers both explicit-session and
            # fingerprint-based scopes inside _persist_session.
            if mapped_session_id is None and parsed.get("session_id"):
                self._persist_session(
                    origin, parsed["session_id"], parsed.get("model"), request=request
                )

            # Caller asked for streaming? OpenClaw's Anthropic JS SDK
            # does — it sets ``"stream": true`` in the body. When it
            # gets a non-streaming response the parser fails with
            # "request ended without sending any chunks". Re-shape
            # our single JSON result into Anthropic's SSE event
            # sequence so any streaming client sees it as progress.
            if self._stream_requested(request.body or b""):
                messages_envelope = self._as_messages_response(parsed)
                sse_body = self._as_sse_stream(messages_envelope).encode("utf-8")
                return BackendResponse(
                    status=200,
                    headers={
                        "content-type": "text/event-stream; charset=utf-8",
                        "cache-control": "no-cache",
                    },
                    body=sse_body,
                )

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

    # ── Subprocess flag builder — platform-slim by default ──────────
    #
    # Kevin 2026-04-24 ratification: the platform-bridge subprocess
    # path should take ONLY the platform's own .md context by default.
    # The full tokenpak-companion profile (MCP schemas, companion
    # system prompt, settings) layers on top of whatever system
    # prompt the platform carries in ``messages[]`` — which pushes
    # context over Claude Code's auto-compaction threshold and kills
    # Anthropic's prompt cache across turns.
    #
    # Default: concatenate the platform workspace's ``*.md`` files at
    # the workspace root (MEMORY.md, IDENTITY.md, AGENTS.md, etc.)
    # into a single ``--append-system-prompt-file``. Claude CLI's
    # native CLAUDE.md auto-discovery still runs (tiny overhead) but
    # tokenpak's companion layer is skipped.
    #
    # Opt-in: ``TOKENPAK_BRIDGE_COMPANION=1`` additionally loads the
    # tokenpak-companion MCP + settings + companion-prompt files
    # from ``~/.tokenpak/companion/run/`` for callers that want the
    # full interactive ``tokenpak claude`` experience.

    _COMPANION_RUN_DIR = Path.home() / ".tokenpak" / "companion" / "run"
    _PLATFORM_PROMPT_CACHE_DIR = Path("/tmp/tokenpak-bridge-prompts")

    @classmethod
    def _companion_flags(cls) -> list[str]:
        """Full tokenpak-companion flags (opt-in).

        Only loaded when ``TOKENPAK_BRIDGE_COMPANION=1`` — otherwise
        the bridge stays platform-slim so platform callers' context
        stays well under the auto-compaction threshold.
        """
        flags: list[str] = []
        if not cls._COMPANION_RUN_DIR.is_dir():
            return flags
        mcp_path = cls._COMPANION_RUN_DIR / "mcp.json"
        settings_path = cls._COMPANION_RUN_DIR / "settings.json"
        prompt_candidates = [
            cls._COMPANION_RUN_DIR / "companion-prompt.md",
            cls._COMPANION_RUN_DIR / "system_prompt.md",
        ]
        if mcp_path.is_file():
            flags.extend(["--mcp-config", str(mcp_path)])
        if settings_path.is_file():
            flags.extend(["--settings", str(settings_path)])
        for p in prompt_candidates:
            if p.is_file():
                flags.extend(["--append-system-prompt-file", str(p)])
                break
        return flags

    @classmethod
    def _platform_prompt_flags(cls, workspace: Optional[str]) -> list[str]:
        """Build ``--append-system-prompt-file`` from the platform's .md files.

        Concatenates every ``*.md`` at the workspace root (non-
        recursive) into a single tempfile cached by content hash.
        Re-generates when workspace files change; stays stable
        otherwise so Claude CLI's cache_control tracking sees a
        stable prefix across turns.

        Workspace conventions observed 2026-04-24 (OpenClaw):
        ``MEMORY.md``, ``IDENTITY.md``, ``AGENTS.md``, ``TOOLS.md``,
        ``SOUL.md``, ``HEARTBEAT.md``, ``PAUSED_PROJECTS.md``,
        ``REFERENCE_INDEX.md``, ``EMERGENCY_RESTORE.md``. Any other
        platform adapter following the same convention (.md at
        workspace root) gets the same treatment.
        """
        if not workspace:
            return []
        import hashlib

        ws = Path(workspace)
        if not ws.is_dir():
            return []
        md_files = sorted(p for p in ws.glob("*.md") if p.is_file())
        if not md_files:
            return []
        # Cache key: file list + mtimes. Regenerate on ANY change.
        fingerprint = "\n".join(
            f"{p}:{int(p.stat().st_mtime)}" for p in md_files
        ).encode("utf-8")
        digest = hashlib.sha256(fingerprint).hexdigest()[:16]
        cls._PLATFORM_PROMPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached = cls._PLATFORM_PROMPT_CACHE_DIR / f"{digest}.md"
        if not cached.is_file():
            chunks = []
            for p in md_files:
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                chunks.append(f"# --- {p.name} ---\n\n{content}\n")
            cached.write_text("\n".join(chunks), encoding="utf-8")
        return ["--append-system-prompt-file", str(cached)]

    @classmethod
    def _build_context_flags(cls, workspace: Optional[str]) -> list[str]:
        """Assemble the full flag list for this subprocess dispatch.

        Default path: platform-slim (workspace .md only). Opt-in via
        ``TOKENPAK_BRIDGE_COMPANION=1`` adds the full
        tokenpak-companion profile on top.
        """
        import os as _os

        flags = cls._platform_prompt_flags(workspace)
        if _os.environ.get("TOKENPAK_BRIDGE_COMPANION", "0").strip() == "1":
            flags.extend(cls._companion_flags())
        return flags

    # ── Request shape extractors (Apr 15-18 parity) ───────────────────

    @staticmethod
    def _extract_model(body: bytes) -> Optional[str]:
        """Pull the ``model`` field out of an Anthropic Messages body.

        The Apr 15-18 bridge passed the OpenClaw-selected model through
        to the CLI via ``--model``. Without this, Claude CLI picks its
        configured default — which can diverge from what the caller
        asked for (e.g. OpenClaw says Haiku, CLI picks Opus).
        """
        if not body:
            return None
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        m = data.get("model") if isinstance(data, dict) else None
        return m if isinstance(m, str) and m.strip() else None

    @staticmethod
    def _has_platform_headers(headers) -> bool:
        """True when the caller identified as a platform bridge.

        Looks for the standard platform markers tokenpak's bridge
        uses — X-TokenPak-Backend (installed by tokenpak-inject.sh
        into OpenClaw's provider config), X-TokenPak-Provider (the
        generic explicit declaration), or X-OpenClaw-* / X-Codex-*
        identifiers any adapter might stamp. Any of them mean
        'caller is replaying its own conversation per turn; don't
        resume a stale CLI session'.
        """
        if not headers:
            return False
        for k in headers:
            kl = k.lower()
            if kl in ("x-tokenpak-backend", "x-tokenpak-provider"):
                return True
            if kl.startswith("x-openclaw-") or kl.startswith("x-codex-"):
                return True
        return False

    @staticmethod
    def _resolve_workspace(request: Request) -> Optional[str]:
        """Return the ``cwd`` to pass to the subprocess.

        Precedence:
          1. ``X-OpenClaw-Workspace`` request header (if valid dir).
          2. ``OPENCLAW_WORKSPACE`` env override.
          3. ``~/.openclaw/workspace`` default (where the gateway keeps
             agent state).
          4. ``None`` — inherit proxy's cwd as last resort.

        cwd matters because Claude CLI reads CLAUDE.md + settings from
        the cwd tree, and tool_use operations (file reads, shell
        commands) are relative to it. Apr 15-18 bridge always set this
        explicitly; v1.3.13-19 lost the behavior.
        """
        import os as _os
        from pathlib import Path as _Path

        headers = request.headers or {}
        # Case-insensitive lookup
        for k, v in headers.items():
            if k.lower() == "x-openclaw-workspace" and v:
                p = _Path(v).expanduser()
                if p.is_dir():
                    return str(p)
        env_ws = _os.environ.get("OPENCLAW_WORKSPACE", "").strip()
        if env_ws:
            p = _Path(env_ws).expanduser()
            if p.is_dir():
                return str(p)
        default = _Path.home() / ".openclaw" / "workspace"
        if default.is_dir():
            return str(default)
        return None

    # ── Session mapper integration (v1.3.14 + v1.3.21 fingerprint) ───

    _BRIDGE_FP_SCOPE = "bridge-fp"

    def _resolve_session(self, request: Request):
        """Return ``(PlatformOrigin | None, mapped_session_id | None)``.

        Resolution priority:
          1. Explicit platform session id (``X-OpenClaw-Session``)
             keyed under ``scope=<platform_name>``. Strongest signal.
          2. Conversation fingerprint derived from the first user
             message + model in ``messages[]``, keyed under
             ``scope=bridge-fp``. This is what allows multi-turn
             OpenClaw conversations to reuse a single Claude CLI
             session (and thus accumulate Anthropic's prompt cache
             across turns). Same OpenClaw conversation replays the
             same first user message on every turn → same fingerprint
             → same Claude session via ``--resume``. A new OpenClaw
             /new session starts with a different first message →
             different fingerprint → fresh Claude session → cache
             cleanly breaks (Kevin's 2026-04-24 ratification).
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

        provider = None
        if origin is not None:
            provider = origin.declared_provider or resolve_provider(
                request.headers or {}
            )
        else:
            provider = resolve_provider(request.headers or {})

        try:
            from tokenpak.services.routing_service.session_mapper import (
                get_session_mapper,
            )
        except Exception:
            return origin, None
        mapper = get_session_mapper()

        # Path 1: explicit platform session id (strongest).
        if origin is not None and origin.session_id and provider is not None:
            try:
                record = mapper.get(
                    scope=origin.platform_name,
                    external_id=origin.session_id,
                    provider=provider,
                )
                if record is not None:
                    return origin, record.internal_id
            except Exception:
                pass

        # Path 2: conversation fingerprint (bridge-fp scope). Only
        # fires when the caller is actually platform-bridged —
        # direct CLI callers never hit this path.
        if provider is not None and self._has_platform_headers(
            request.headers or {}
        ):
            fp = self._conversation_fingerprint(request.body or b"")
            if fp:
                try:
                    record = mapper.get(
                        scope=self._BRIDGE_FP_SCOPE,
                        external_id=fp,
                        provider=provider,
                    )
                    if record is not None:
                        return origin, record.internal_id
                except Exception:
                    pass

        return origin, None

    @staticmethod
    def _conversation_fingerprint(body: bytes) -> Optional[str]:
        """Stable ID from (model, first user message text).

        Same OpenClaw conversation replays the same first user message
        on every turn — so the fingerprint is stable across turns in
        one conversation, and unique per ``/new`` session.
        """
        if not body:
            return None
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        messages = data.get("messages") or []
        first_user_text: Optional[str] = None
        for m in messages:
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            content = m.get("content")
            if isinstance(content, str):
                first_user_text = content
            elif isinstance(content, list):
                parts: list[str] = []
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        t = blk.get("text")
                        if isinstance(t, str):
                            parts.append(t)
                if parts:
                    first_user_text = "\n".join(parts)
            if first_user_text is not None:
                break
        if not first_user_text or not first_user_text.strip():
            return None
        model = data.get("model") or ""
        import hashlib

        key = f"{model}\n{first_user_text[:1000]}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def _persist_session(
        cls,
        origin,
        claude_session_id: str,
        model: Optional[str],
        request: Optional[Request] = None,
    ) -> None:
        """Persist session mapping(s) on the first turn.

        Two scopes are written when applicable:

          - ``scope=<platform_name>`` keyed on the explicit platform
            session id (when the caller sent one). Used by
            X-OpenClaw-Session-style callers.
          - ``scope=bridge-fp`` keyed on the conversation fingerprint
            (first user message hash). Used for real OpenClaw traffic
            that doesn't carry an explicit session id. Second-turn
            requests in the same conversation replay the same first
            user message → same fingerprint → ``--resume`` hits the
            Claude CLI session already populated by turn 1 →
            Anthropic's prompt cache accumulates across turns.
        """
        try:
            from tokenpak.services.routing_service.session_mapper import (
                get_session_mapper,
            )
        except Exception:
            return
        mapper = get_session_mapper()
        metadata = {"model": model} if model else {}
        provider = "tokenpak-claude-code"
        if origin is not None:
            provider = origin.declared_provider or provider

        # Write path 1: explicit platform session id, when present.
        if origin is not None and origin.session_id:
            try:
                mapper.set(
                    scope=origin.platform_name,
                    external_id=origin.session_id,
                    provider=provider,
                    internal_id=claude_session_id,
                    metadata=metadata,
                )
            except Exception:
                pass

        # Write path 2: conversation fingerprint (OpenClaw default).
        if request is not None and cls._has_platform_headers(request.headers or {}):
            fp = cls._conversation_fingerprint(request.body or b"")
            if fp:
                try:
                    mapper.set(
                        scope=cls._BRIDGE_FP_SCOPE,
                        external_id=fp,
                        provider=provider,
                        internal_id=claude_session_id,
                        metadata=metadata,
                    )
                except Exception:
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
    def _stream_requested(body: bytes) -> bool:
        """True when the caller set ``"stream": true`` in the JSON body.

        Anthropic's SDKs stream by default (OpenClaw's JS SDK always
        asks for SSE). When streaming is requested, the response must
        be ``text/event-stream`` with the Messages event sequence;
        a flat JSON body causes the client parser to fail with
        "request ended without sending any chunks".
        """
        if not body:
            return False
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
        return bool(isinstance(data, dict) and data.get("stream") is True)

    @staticmethod
    def _as_sse_stream(envelope: dict) -> str:
        """Re-encode a Messages response as an SSE event stream.

        Synthesizes the Anthropic Messages streaming event sequence
        from our single non-streaming subprocess result — one
        ``message_start`` → ``content_block_start`` →
        ``content_block_delta`` (with the whole text) →
        ``content_block_stop`` → ``message_delta`` (with usage) →
        ``message_stop``. Clients get the full response in one SSE
        flush; not token-by-token but conformant with the streaming
        wire contract so their parser doesn't bail.

        Real token-by-token streaming via ``claude --output-format
        stream-json`` is a later enhancement; synthetic events are
        enough to unblock OpenClaw today.
        """
        content_blocks = envelope.get("content") or []
        text = ""
        for blk in content_blocks:
            if isinstance(blk, dict) and blk.get("type") == "text":
                text = blk.get("text") or ""
                break

        # message_start carries the metadata envelope minus the text.
        msg_start_payload = {
            "type": "message_start",
            "message": {
                "id": envelope.get("id", "msg_claude_cli"),
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": envelope.get("model") or "claude-via-oauth",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": envelope.get("usage", {}).get("input_tokens", 0),
                    "output_tokens": 0,
                    "cache_creation_input_tokens": envelope.get("usage", {}).get(
                        "cache_creation_input_tokens", 0
                    ),
                    "cache_read_input_tokens": envelope.get("usage", {}).get(
                        "cache_read_input_tokens", 0
                    ),
                },
            },
        }
        content_start = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
        content_delta = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        }
        content_stop = {"type": "content_block_stop", "index": 0}
        message_delta = {
            "type": "message_delta",
            "delta": {
                "stop_reason": envelope.get("stop_reason", "end_turn"),
                "stop_sequence": None,
            },
            "usage": {
                "output_tokens": envelope.get("usage", {}).get("output_tokens", 0),
            },
        }
        message_stop = {"type": "message_stop"}

        events = [
            ("message_start", msg_start_payload),
            ("content_block_start", content_start),
            ("content_block_delta", content_delta),
            ("content_block_stop", content_stop),
            ("message_delta", message_delta),
            ("message_stop", message_stop),
        ]
        return "".join(
            f"event: {ev}\ndata: {json.dumps(data)}\n\n" for ev, data in events
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
