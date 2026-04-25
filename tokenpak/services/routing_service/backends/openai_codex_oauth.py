# SPDX-License-Identifier: Apache-2.0
"""OpenAI Codex OAuth backend — route requests through the ``codex`` CLI.

Companion subprocess bridge for the ``tokenpak-openai-codex`` provider,
analogous to :class:`AnthropicOAuthBackend` for Claude Code. Used when
the platform bridge resolves to ``tokenpak-openai-codex`` — typically
OpenClaw's Codex provider, but any caller that stamps
``X-TokenPak-Provider: tokenpak-openai-codex`` reaches this path.

Why not just inject credentials and forward bytes?
--------------------------------------------------

The byte-forward path (``services/routing_service/credential_injector``)
works for the Codex Responses API but loses the things that make the
Codex CLI useful as an *agent*:

- Tool use (file reads, shell commands, sandbox-bounded execution).
- ``codex exec resume <uuid>`` session continuity, which accumulates
  ChatGPT's prompt cache across turns of the same OpenClaw conversation.
- The CLI's own AGENTS.md / tools / MCP discovery from the workspace
  cwd (parity with what AnthropicOAuthBackend gives Claude Code).
- Subscription billing via OAuth without the proxy juggling JWTs.

The HTTP byte-forward path stays — both paths coexist. The selector
picks subprocess when the caller declares the Codex provider; everything
else falls through to credential injection or the api backend.

Contract:
    - **Subprocess only** — no fallback to network. If the CLI isn't
      installed / logged in we return 502 so the failure is visible
      (same posture AnthropicOAuthBackend takes).
    - **JSONL parsing** — ``codex exec --json`` emits one event per
      line: ``thread.started`` carries the session uuid,
      ``item.completed`` carries assistant text, ``turn.completed``
      carries usage. We accumulate text across all ``item.completed``
      ``agent_message`` items.
    - **Session resume** — second-turn requests in the same OpenClaw
      conversation hit the same fingerprint → same Codex thread via
      ``codex exec resume <uuid>``. ChatGPT's prompt cache accumulates
      across turns inside one thread.
    - **Streaming clients** — when ``"stream": true`` is set we
      synthesize the OpenAI Responses streaming event sequence so the
      client's parser sees a conformant wire. Token-by-token streaming
      via ``codex exec --json`` event-by-event flush is a later
      enhancement; one delta event suffices to unblock callers today.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from tokenpak.services.request import Request
from tokenpak.services.routing_service.backends.base import BackendResponse

logger = logging.getLogger(__name__)


class OpenAICodexOAuthBackend:
    """Dispatch requests through the local ``codex`` CLI."""

    name = "openai-codex-oauth"

    def __init__(self, codex_binary: Optional[str] = None) -> None:
        self._codex_binary = (
            codex_binary or shutil.which("codex") or self._discover_codex() or "codex"
        )

    @staticmethod
    def _discover_codex() -> Optional[str]:
        """Find ``codex`` in well-known node bin locations.

        Systemd user services don't inherit nvm's PATH, so ``shutil.which``
        misses nvm-installed binaries. We glob the canonical install
        roots (any version) and return the first executable found.

        No hardcoded version enumeration — the glob picks up whatever
        node version the user has installed.
        """
        import os

        candidates = []
        home = Path.home()
        # nvm — versioned tree, any version.
        candidates.extend(sorted(home.glob(".nvm/versions/node/*/bin/codex")))
        # bun, fnm, volta, asdf, system locations — each gets one shot.
        candidates.extend([
            home / ".bun/bin/codex",
            home / ".fnm/aliases/default/bin/codex",
            home / ".volta/bin/codex",
            home / ".asdf/shims/codex",
            home / ".local/bin/codex",
            Path("/usr/local/bin/codex"),
            Path("/opt/homebrew/bin/codex"),
        ])
        for c in candidates:
            try:
                if c.is_file() and os.access(c, os.X_OK):
                    return str(c)
            except OSError:
                continue
        return None

    def _is_available(self) -> bool:
        """True only when the configured binary resolves + is executable."""
        import os

        if "/" not in self._codex_binary:
            return bool(shutil.which(self._codex_binary))
        return os.path.isfile(self._codex_binary) and os.access(
            self._codex_binary, os.X_OK
        )

    def dispatch(self, request: Request) -> BackendResponse:
        """Invoke ``codex exec --json`` (or ``codex exec resume``) with the prompt.

        Session continuity: if the request carries a platform signal,
        consult the session mapper for the Codex thread uuid bound to
        this conversation. Pass it via ``codex exec resume <uuid>`` on
        subsequent turns. First turn runs without resume; we parse the
        ``thread.started`` event to learn the uuid and persist it.
        """
        if not self._is_available():
            return BackendResponse(
                status=502,
                headers={"content-type": "application/json"},
                body=json.dumps({
                    "error": {
                        "type": "backend_unavailable",
                        "message": (
                            "Codex CLI not found on PATH. Install it and run "
                            "`codex login` to authenticate via ChatGPT OAuth."
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

            origin, mapped_session_id = self._resolve_session(request)
            workspace = self._resolve_workspace(request)
            model_hint = self._extract_model(request.body or b"")

            argv = self._build_argv(
                prompt=prompt,
                model=model_hint,
                workspace=workspace,
                resume_thread_id=mapped_session_id,
            )

            import os as _os

            _env = _os.environ.copy()
            # Prevent the subprocess from looping back through us.
            _env.pop("OPENAI_BASE_URL", None)
            _env.pop("ANTHROPIC_BASE_URL", None)

            completed = subprocess.run(
                argv,
                input=prompt.encode("utf-8"),
                capture_output=True,
                timeout=300,
                check=False,
                env=_env,
                cwd=workspace,
            )
            if completed.returncode != 0:
                err = completed.stderr.decode("utf-8", errors="replace")[:500]
                return BackendResponse(
                    status=502,
                    headers={"content-type": "application/json"},
                    body=json.dumps({
                        "error": {
                            "type": "backend_failure",
                            "message": f"codex CLI exited {completed.returncode}: {err}",
                        }
                    }).encode(),
                )

            parsed = self._parse_cli_output(completed.stdout)

            # First-turn session capture: persist thread_id so turn 2 can
            # resume against ChatGPT's accumulated prompt cache.
            if mapped_session_id is None and parsed.get("session_id"):
                self._persist_session(
                    origin,
                    parsed["session_id"],
                    parsed.get("model") or model_hint,
                    request=request,
                )

            envelope = self._as_responses_envelope(parsed, model_hint)

            if self._stream_requested(request.body or b""):
                sse_body = self._as_sse_stream(envelope).encode("utf-8")
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
                body=json.dumps(envelope).encode(),
            )
        except subprocess.TimeoutExpired:
            return BackendResponse(
                status=504,
                headers={"content-type": "application/json"},
                body=json.dumps({
                    "error": {"type": "timeout", "message": "codex CLI timed out"}
                }).encode(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("openai-codex-oauth: dispatch failed: %s", exc)
            return BackendResponse(
                status=502,
                headers={"content-type": "application/json"},
                body=json.dumps({
                    "error": {"type": "backend_error", "message": str(exc)[:200]}
                }).encode(),
            )

    # ── argv builder ──────────────────────────────────────────────────

    def _build_argv(
        self,
        prompt: str,
        model: Optional[str],
        workspace: Optional[str],
        resume_thread_id: Optional[str],
    ) -> List[str]:
        """Build the codex subprocess command line.

        ``codex exec resume`` rejects ``--sandbox`` (the resumed session
        inherits the original sandbox policy), so the flag only goes on
        first-turn ``codex exec`` invocations. Workspace cwd is what
        codex reads AGENTS.md / tools from, same as Claude CLI.

        Prompt is passed via stdin (``-`` placeholder) to avoid argv
        length limits and shell-quoting hazards on long contexts.
        """
        argv: List[str] = [self._codex_binary, "exec"]
        if resume_thread_id:
            argv.extend(["resume", resume_thread_id])

        argv.append("--json")
        argv.append("--skip-git-repo-check")

        if not resume_thread_id:
            # First-turn-only flags. ``codex exec resume`` rejects
            # ``--sandbox`` and ``-C`` (the resumed thread inherits
            # both from the original session).
            argv.extend(["--sandbox", "read-only"])
            if workspace:
                argv.extend(["-C", workspace])

        if model:
            argv.extend(["--model", model])

        # Stdin sentinel: codex reads the prompt from stdin when "-" is
        # given as the prompt argument.
        argv.append("-")
        return argv

    # ── workspace + session resolution (Apr 15-18 parity) ─────────────

    @staticmethod
    def _resolve_workspace(request: Request) -> Optional[str]:
        """Return the cwd to pass to codex.

        Precedence:
          1. ``X-OpenClaw-Workspace`` request header.
          2. ``OPENCLAW_WORKSPACE`` env override.
          3. ``~/.openclaw/workspace`` default.
          4. ``None`` — inherit proxy's cwd.
        """
        import os as _os

        headers = request.headers or {}
        for k, v in headers.items():
            if k.lower() == "x-openclaw-workspace" and v:
                p = Path(v).expanduser()
                if p.is_dir():
                    return str(p)
        env_ws = _os.environ.get("OPENCLAW_WORKSPACE", "").strip()
        if env_ws:
            p = Path(env_ws).expanduser()
            if p.is_dir():
                return str(p)
        default = Path.home() / ".openclaw" / "workspace"
        if default.is_dir():
            return str(default)
        return None

    _BRIDGE_FP_SCOPE = "codex-bridge-fp"

    def _resolve_session(self, request: Request):
        """Return ``(PlatformOrigin | None, mapped_thread_id | None)``.

        Same two-tier resolution as the Anthropic backend:
          1. Explicit platform session id (``X-OpenClaw-Session``)
             scoped under the platform name.
          2. Conversation fingerprint scoped under
             ``codex-bridge-fp`` — keyed on (model, system,
             first user input). Same conversation replays the same
             prefix → same fingerprint → ``codex exec resume`` →
             ChatGPT's prompt cache accumulates.
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
    def _has_platform_headers(headers) -> bool:
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
    def _conversation_fingerprint(body: bytes) -> Optional[str]:
        """Stable id for the platform conversation.

        Keyed on (model + first user input text). Codex requests come
        in OpenAI Responses shape (``input`` list of role+content blocks)
        or Chat Completions shape (``messages`` array). Both replay the
        prior conversation per turn, so the first user input is stable
        across turns.
        """
        if not body:
            return None
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None

        first_user_text = (
            _first_user_text_from_input(data.get("input"))
            or _first_user_text_from_messages(data.get("messages"))
        )
        if not first_user_text:
            return None

        instructions = data.get("instructions")
        system_text = (
            instructions if isinstance(instructions, str) else ""
        )[:2000]

        import hashlib

        model = data.get("model") or ""
        key = (
            f"model={model}\nsystem={system_text}\n"
            f"first_user={first_user_text[:1000]}"
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def _persist_session(
        cls,
        origin,
        thread_id: str,
        model: Optional[str],
        request: Optional[Request] = None,
    ) -> None:
        """Persist the (platform_session, fingerprint) → thread_id mapping."""
        try:
            from tokenpak.services.routing_service.session_mapper import (
                get_session_mapper,
            )
        except Exception:
            return
        mapper = get_session_mapper()
        metadata = {"model": model} if model else {}
        provider = "tokenpak-openai-codex"
        if origin is not None:
            provider = origin.declared_provider or provider

        if origin is not None and origin.session_id:
            try:
                mapper.set(
                    scope=origin.platform_name,
                    external_id=origin.session_id,
                    provider=provider,
                    internal_id=thread_id,
                    metadata=metadata,
                )
            except Exception:
                pass

        if request is not None and cls._has_platform_headers(
            request.headers or {}
        ):
            fp = cls._conversation_fingerprint(request.body or b"")
            if fp:
                try:
                    mapper.set(
                        scope=cls._BRIDGE_FP_SCOPE,
                        external_id=fp,
                        provider=provider,
                        internal_id=thread_id,
                        metadata=metadata,
                    )
                except Exception:
                    pass

    # ── codex --json output parsing ───────────────────────────────────

    @staticmethod
    def _parse_cli_output(stdout: bytes) -> dict:
        """Decode codex JSONL stdout into a flat record.

        codex exec --json emits one JSON event per line. The events we
        care about:
          - ``thread.started`` → ``thread_id`` (session uuid)
          - ``item.completed`` with item.type=agent_message → ``text``
          - ``turn.completed`` → ``usage`` (input_tokens,
            cached_input_tokens, output_tokens)
          - ``error`` (any) → captured into the result string

        Multiple ``agent_message`` items in one turn (rare for exec
        mode) get concatenated. Anything we don't recognise is ignored.
        """
        text_chunks: list[str] = []
        thread_id: Optional[str] = None
        usage: dict = {}
        error_text: Optional[str] = None

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue

            etype = event.get("type")
            if etype == "thread.started":
                tid = event.get("thread_id")
                if isinstance(tid, str):
                    thread_id = tid
            elif etype == "item.completed":
                item = event.get("item") or {}
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    txt = item.get("text")
                    if isinstance(txt, str):
                        text_chunks.append(txt)
            elif etype == "turn.completed":
                u = event.get("usage")
                if isinstance(u, dict):
                    usage = u
            elif etype == "error":
                msg = event.get("message") or event.get("error")
                if isinstance(msg, str):
                    error_text = msg

        if not text_chunks and error_text:
            text_chunks.append(f"[codex error] {error_text}")

        return {
            "result": "\n".join(text_chunks),
            "session_id": thread_id,
            "model": None,  # codex --json doesn't echo the model id
            "usage": usage,
            "stop_reason": "stop",
        }

    # ── OpenAI Responses envelope shaping ─────────────────────────────

    @staticmethod
    def _as_responses_envelope(parsed: dict, model_hint: Optional[str]) -> dict:
        """Build a non-streaming OpenAI Responses API envelope."""
        usage = parsed.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cached_tokens = int(usage.get("cached_input_tokens") or 0)

        thread_id = parsed.get("session_id") or "cli"
        text = parsed.get("result", "")
        model = parsed.get("model") or model_hint or "codex-via-oauth"

        return {
            "id": f"resp_codex_{thread_id}",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": model,
            "output": [
                {
                    "id": f"msg_codex_{thread_id}",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": text,
                            "annotations": [],
                        }
                    ],
                }
            ],
            "usage": {
                "input_tokens": input_tokens,
                "input_tokens_details": {"cached_tokens": cached_tokens},
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        }

    @staticmethod
    def _stream_requested(body: bytes) -> bool:
        if not body:
            return False
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
        return bool(isinstance(data, dict) and data.get("stream") is True)

    @staticmethod
    def _as_sse_stream(envelope: dict) -> str:
        """Re-encode the Responses envelope as an SSE event stream.

        Synthesises the canonical OpenAI Responses streaming event
        sequence: ``response.created`` → ``response.output_item.added``
        → ``response.content_part.added`` → ``response.output_text.delta``
        (single chunk with the whole text) → ``response.output_text.done``
        → ``response.content_part.done`` → ``response.output_item.done``
        → ``response.completed``. Conformant with the streaming wire
        contract; not token-by-token.
        """
        output = envelope.get("output") or []
        item = output[0] if output else {}
        content_parts = item.get("content") or []
        part = content_parts[0] if content_parts else {}
        text = part.get("text", "") if isinstance(part, dict) else ""

        in_progress = {
            **{k: v for k, v in envelope.items() if k != "usage"},
            "status": "in_progress",
            "output": [],
        }

        events = [
            ("response.created", {"type": "response.created", "response": in_progress}),
            (
                "response.output_item.added",
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": item.get("id"),
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                    },
                },
            ),
            (
                "response.content_part.added",
                {
                    "type": "response.content_part.added",
                    "item_id": item.get("id"),
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
            ),
            (
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "item_id": item.get("id"),
                    "output_index": 0,
                    "content_index": 0,
                    "delta": text,
                },
            ),
            (
                "response.output_text.done",
                {
                    "type": "response.output_text.done",
                    "item_id": item.get("id"),
                    "output_index": 0,
                    "content_index": 0,
                    "text": text,
                },
            ),
            (
                "response.content_part.done",
                {
                    "type": "response.content_part.done",
                    "item_id": item.get("id"),
                    "output_index": 0,
                    "content_index": 0,
                    "part": {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    },
                },
            ),
            (
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": item,
                },
            ),
            ("response.completed", {"type": "response.completed", "response": envelope}),
        ]
        return "".join(
            f"event: {ev}\ndata: {json.dumps(data)}\n\n" for ev, data in events
        )

    # ── prompt + model extraction ─────────────────────────────────────

    @staticmethod
    def _extract_model(body: bytes) -> Optional[str]:
        if not body:
            return None
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        m = data.get("model") if isinstance(data, dict) else None
        return m if isinstance(m, str) and m.strip() else None

    @staticmethod
    def _extract_prompt(body: bytes) -> Optional[str]:
        """Pull the most recent user turn from a Codex-bound request body.

        Handles both shapes Codex callers use:
          - OpenAI Responses API (``input`` is a string OR a list of
            role+content blocks where content uses ``input_text`` /
            ``output_text`` parts).
          - OpenAI Chat Completions (``messages`` array with
            string-or-list content).

        Returns the last user message text, or ``None`` when no user
        turn is recoverable.
        """
        if not body:
            return None
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None

        input_value = data.get("input")
        if isinstance(input_value, str) and input_value.strip():
            return input_value
        if isinstance(input_value, list):
            text = _last_user_text_from_input(input_value)
            if text:
                return text

        messages = data.get("messages")
        if isinstance(messages, list):
            text = _last_user_text_from_messages(messages)
            if text:
                return text

        return None


# ── module-level helpers (shared by extractor + fingerprint) ──────────


def _content_text_responses(content) -> str:
    """Concatenate text out of an OpenAI Responses content array."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") in ("input_text", "output_text", "text"):
                t = blk.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _content_text_messages(content) -> str:
    """Concatenate text out of a Chat Completions content array."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "text":
                t = blk.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _first_user_text_from_input(input_value) -> Optional[str]:
    if isinstance(input_value, str):
        return input_value if input_value.strip() else None
    if not isinstance(input_value, list):
        return None
    for item in input_value:
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        text = _content_text_responses(item.get("content"))
        if text.strip():
            return text
    return None


def _first_user_text_from_messages(messages) -> Optional[str]:
    if not isinstance(messages, list):
        return None
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        text = _content_text_messages(msg.get("content"))
        if text.strip():
            return text
    return None


def _last_user_text_from_input(input_value) -> Optional[str]:
    if not isinstance(input_value, list):
        return None
    for item in reversed(input_value):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        text = _content_text_responses(item.get("content"))
        if text.strip():
            return text
    return None


def _last_user_text_from_messages(messages) -> Optional[str]:
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        text = _content_text_messages(msg.get("content"))
        if text.strip():
            return text
    return None


__all__ = ["OpenAICodexOAuthBackend"]
