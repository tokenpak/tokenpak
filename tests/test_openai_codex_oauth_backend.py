# SPDX-License-Identifier: Apache-2.0
"""Tests for OpenAICodexOAuthBackend (no network — codex CLI is mocked)."""

from __future__ import annotations

import json
from unittest.mock import patch

from tokenpak.services.request import Request
from tokenpak.services.routing_service.backends.openai_codex_oauth import (
    OpenAICodexOAuthBackend,
)

# ── Sample codex --json output (real shape captured 2026-04-24) ──────

THREAD_ID = "019dc2d8-aaaa-bbbb-cccc-dddddddddddd"

OK_STDOUT = (
    f'{{"type":"thread.started","thread_id":"{THREAD_ID}"}}\n'
    '{"type":"turn.started"}\n'
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message",'
    '"text":"PONG"}}\n'
    '{"type":"turn.completed","usage":{"input_tokens":12345,'
    '"cached_input_tokens":3456,"output_tokens":7}}\n'
).encode()


# ── Argv construction ────────────────────────────────────────────────


class TestArgvBuilder:
    def test_first_turn_includes_sandbox_and_workspace(self):
        be = OpenAICodexOAuthBackend(codex_binary="codex")
        argv = be._build_argv(
            prompt="hi", model="gpt-5.3-codex", workspace="/ws", resume_thread_id=None
        )
        assert "exec" in argv
        assert "resume" not in argv
        assert "--json" in argv
        assert "--skip-git-repo-check" in argv
        assert "--sandbox" in argv
        assert "read-only" in argv
        assert "-C" in argv and "/ws" in argv
        assert "--model" in argv and "gpt-5.3-codex" in argv
        assert argv[-1] == "-"

    def test_resume_omits_sandbox_and_workspace(self):
        be = OpenAICodexOAuthBackend(codex_binary="codex")
        argv = be._build_argv(
            prompt="hi", model="gpt-5.3-codex", workspace="/ws", resume_thread_id=THREAD_ID
        )
        assert "exec" in argv
        # resume subcommand follows exec
        assert argv[argv.index("exec") + 1] == "resume"
        assert THREAD_ID in argv
        # Resume rejects these flags — the bridge must not set them.
        assert "--sandbox" not in argv
        assert "-C" not in argv
        # Model is still allowed on resume.
        assert "--model" in argv

    def test_first_turn_no_workspace_no_C(self):
        be = OpenAICodexOAuthBackend(codex_binary="codex")
        argv = be._build_argv(
            prompt="hi", model=None, workspace=None, resume_thread_id=None
        )
        assert "-C" not in argv
        assert "--model" not in argv


# ── JSONL output parsing ─────────────────────────────────────────────


class TestParseCliOutput:
    def test_parses_thread_text_and_usage(self):
        parsed = OpenAICodexOAuthBackend._parse_cli_output(OK_STDOUT)
        assert parsed["session_id"] == THREAD_ID
        assert parsed["result"] == "PONG"
        assert parsed["usage"]["input_tokens"] == 12345
        assert parsed["usage"]["cached_input_tokens"] == 3456
        assert parsed["usage"]["output_tokens"] == 7

    def test_concatenates_multiple_agent_messages(self):
        stdout = (
            '{"type":"thread.started","thread_id":"x"}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"A"}}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"B"}}\n'
            '{"type":"turn.completed","usage":{}}\n'
        ).encode()
        parsed = OpenAICodexOAuthBackend._parse_cli_output(stdout)
        assert parsed["result"] == "A\nB"

    def test_ignores_non_agent_message_items(self):
        stdout = (
            '{"type":"thread.started","thread_id":"x"}\n'
            '{"type":"item.completed","item":{"type":"reasoning","text":"hidden"}}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"shown"}}\n'
            '{"type":"turn.completed","usage":{}}\n'
        ).encode()
        parsed = OpenAICodexOAuthBackend._parse_cli_output(stdout)
        assert parsed["result"] == "shown"

    def test_captures_error_when_no_agent_message(self):
        stdout = (
            '{"type":"thread.started","thread_id":"x"}\n'
            '{"type":"error","message":"model not supported"}\n'
            '{"type":"turn.failed"}\n'
        ).encode()
        parsed = OpenAICodexOAuthBackend._parse_cli_output(stdout)
        assert "[codex error]" in parsed["result"]
        assert "model not supported" in parsed["result"]

    def test_ignores_blank_and_malformed_lines(self):
        stdout = (
            '\n'
            'not json\n'
            '{"type":"thread.started","thread_id":"x"}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}\n'
            '{"type":"turn.completed","usage":{}}\n'
        ).encode()
        parsed = OpenAICodexOAuthBackend._parse_cli_output(stdout)
        assert parsed["session_id"] == "x"
        assert parsed["result"] == "hi"


# ── Prompt extraction (Responses + Chat Completions shapes) ──────────


class TestExtractPrompt:
    def test_responses_string_input(self):
        body = json.dumps({"model": "x", "input": "hello"}).encode()
        assert OpenAICodexOAuthBackend._extract_prompt(body) == "hello"

    def test_responses_message_array_uses_last_user(self):
        body = json.dumps({
            "model": "x",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "first"}]},
                {"role": "assistant", "content": [{"type": "output_text", "text": "ack"}]},
                {"role": "user", "content": [{"type": "input_text", "text": "second"}]},
            ],
        }).encode()
        assert OpenAICodexOAuthBackend._extract_prompt(body) == "second"

    def test_chat_completions_messages_uses_last_user(self):
        body = json.dumps({
            "model": "x",
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ack"},
                {"role": "user", "content": "second"},
            ],
        }).encode()
        assert OpenAICodexOAuthBackend._extract_prompt(body) == "second"

    def test_no_user_turn_returns_none(self):
        body = json.dumps({"model": "x", "input": []}).encode()
        assert OpenAICodexOAuthBackend._extract_prompt(body) is None

    def test_malformed_body_returns_none(self):
        assert OpenAICodexOAuthBackend._extract_prompt(b"not json") is None


# ── Conversation fingerprint ─────────────────────────────────────────


class TestFingerprint:
    def test_same_first_user_yields_same_fp(self):
        a = json.dumps({
            "model": "gpt-5.3-codex",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        }).encode()
        b = json.dumps({
            "model": "gpt-5.3-codex",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
                {"role": "assistant", "content": [{"type": "output_text", "text": "ack"}]},
                {"role": "user", "content": [{"type": "input_text", "text": "follow-up"}]},
            ],
        }).encode()
        assert (
            OpenAICodexOAuthBackend._conversation_fingerprint(a)
            == OpenAICodexOAuthBackend._conversation_fingerprint(b)
        )

    def test_different_first_user_yields_different_fp(self):
        a = json.dumps({
            "model": "gpt-5.3-codex",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        }).encode()
        b = json.dumps({
            "model": "gpt-5.3-codex",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "world"}]}],
        }).encode()
        assert (
            OpenAICodexOAuthBackend._conversation_fingerprint(a)
            != OpenAICodexOAuthBackend._conversation_fingerprint(b)
        )

    def test_different_model_yields_different_fp(self):
        a = json.dumps({
            "model": "gpt-5.3-codex",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        }).encode()
        b = json.dumps({
            "model": "gpt-5.2-codex",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        }).encode()
        assert (
            OpenAICodexOAuthBackend._conversation_fingerprint(a)
            != OpenAICodexOAuthBackend._conversation_fingerprint(b)
        )


# ── Envelope shaping ─────────────────────────────────────────────────


class TestResponsesEnvelope:
    def test_non_streaming_envelope_shape(self):
        parsed = OpenAICodexOAuthBackend._parse_cli_output(OK_STDOUT)
        env = OpenAICodexOAuthBackend._as_responses_envelope(parsed, "gpt-5.3-codex")
        assert env["object"] == "response"
        assert env["status"] == "completed"
        assert env["model"] == "gpt-5.3-codex"
        assert env["output"][0]["content"][0]["text"] == "PONG"
        assert env["usage"]["input_tokens"] == 12345
        assert env["usage"]["input_tokens_details"]["cached_tokens"] == 3456
        assert env["usage"]["output_tokens"] == 7

    def test_sse_stream_emits_canonical_event_sequence(self):
        parsed = OpenAICodexOAuthBackend._parse_cli_output(OK_STDOUT)
        env = OpenAICodexOAuthBackend._as_responses_envelope(parsed, "gpt-5.3-codex")
        sse = OpenAICodexOAuthBackend._as_sse_stream(env)
        for ev in [
            "response.created",
            "response.output_item.added",
            "response.content_part.added",
            "response.output_text.delta",
            "response.output_text.done",
            "response.content_part.done",
            "response.output_item.done",
            "response.completed",
        ]:
            assert f"event: {ev}\n" in sse
        # Delta carries the text.
        assert '"delta": "PONG"' in sse


# ── Dispatch (mocked subprocess) ─────────────────────────────────────


class TestDispatch:
    def _body(self, **overrides):
        defaults = {
            "model": "gpt-5.3-codex",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "ping"}]}],
            "stream": False,
        }
        defaults.update(overrides)
        return json.dumps(defaults).encode()

    def test_unavailable_returns_502(self):
        be = OpenAICodexOAuthBackend(codex_binary="/nonexistent/codex-binary")
        resp = be.dispatch(Request(body=self._body(), headers={}))
        assert resp.status == 502
        assert b"backend_unavailable" in resp.body

    def test_invalid_body_returns_400(self):
        be = OpenAICodexOAuthBackend(codex_binary="codex")
        with patch.object(be, "_is_available", return_value=True):
            resp = be.dispatch(Request(body=b"not json", headers={}))
        assert resp.status == 400
        assert b"invalid_request" in resp.body

    def test_successful_subprocess_yields_responses_envelope(self):
        be = OpenAICodexOAuthBackend(codex_binary="codex")
        completed = type("R", (), {"returncode": 0, "stdout": OK_STDOUT, "stderr": b""})
        with patch.object(be, "_is_available", return_value=True), \
             patch("subprocess.run", return_value=completed) as run_mock:
            resp = be.dispatch(Request(body=self._body(), headers={}))
        assert resp.status == 200
        env = json.loads(resp.body)
        assert env["output"][0]["content"][0]["text"] == "PONG"
        # Subprocess was called with the codex binary.
        argv = run_mock.call_args.args[0]
        assert argv[0].endswith("codex")
        assert "exec" in argv

    def test_subprocess_failure_returns_502(self):
        be = OpenAICodexOAuthBackend(codex_binary="codex")
        completed = type("R", (), {"returncode": 1, "stdout": b"", "stderr": b"boom"})
        with patch.object(be, "_is_available", return_value=True), \
             patch("subprocess.run", return_value=completed):
            resp = be.dispatch(Request(body=self._body(), headers={}))
        assert resp.status == 502
        assert b"backend_failure" in resp.body
        assert b"boom" in resp.body

    def test_streaming_request_yields_sse(self):
        be = OpenAICodexOAuthBackend(codex_binary="codex")
        completed = type("R", (), {"returncode": 0, "stdout": OK_STDOUT, "stderr": b""})
        with patch.object(be, "_is_available", return_value=True), \
             patch("subprocess.run", return_value=completed):
            resp = be.dispatch(Request(body=self._body(stream=True), headers={}))
        assert resp.status == 200
        assert resp.headers.get("content-type", "").startswith("text/event-stream")
        assert b"event: response.completed" in resp.body


# ── Selector integration ─────────────────────────────────────────────


class TestSelectorRouting:
    def test_codex_provider_selects_codex_backend(self):
        from tokenpak.core.routing.route_class import RouteClass
        from tokenpak.services.routing_service.backend_selector import BackendSelector

        sel = BackendSelector()
        req = Request(
            body=b"",
            headers={"X-TokenPak-Provider": "tokenpak-openai-codex"},
        )
        backend = sel.select(req, RouteClass.GENERIC)
        assert isinstance(backend, OpenAICodexOAuthBackend)

    def test_claude_code_provider_does_not_select_codex(self):
        from tokenpak.core.routing.route_class import RouteClass
        from tokenpak.services.routing_service.backend_selector import BackendSelector

        sel = BackendSelector()
        req = Request(
            body=b"",
            headers={"X-TokenPak-Provider": "tokenpak-claude-code"},
        )
        backend = sel.select(req, RouteClass.GENERIC)
        assert not isinstance(backend, OpenAICodexOAuthBackend)
