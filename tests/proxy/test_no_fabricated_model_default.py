# SPDX-License-Identifier: Apache-2.0
"""A missing model id must never be replaced with a real model name.

Logging, forecast, streaming, and spend-guard estimation paths used to
default a missing model to a specific real model id. That fabricated an
execution fact: receipt/log rows and cost attribution ended up keyed to a
model that was never actually requested. These tests pin the corrected
behavior — a missing model is recorded as empty ("unknown"), cost is
estimated against default-class *rates* without naming a model, and the
Claude Code CLI backend is left to its own configured default.
"""

import io
import json

import pytest

# ---------------------------------------------------------------------------
# Claude Code CLI backend (SDK executor)
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, stdout: str):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def _run_executor(monkeypatch, tmp_path, model_kwargs):
    """Invoke execute_via_claude_code with a captured fake subprocess."""
    from tokenpak.sdk import openclaw

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc(json.dumps({
            "result": "hello",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }))

    monkeypatch.setattr(openclaw, "_get_claude_session",
                        lambda _s: ("00000000-0000-0000-0000-000000000001", True))
    monkeypatch.setattr(openclaw, "_find_claude_binary", lambda: "/fake/claude")
    monkeypatch.setattr(openclaw.subprocess, "run", fake_run)

    result = openclaw.execute_via_claude_code(
        openclaw_session="oc_test",
        messages=[{"role": "user", "content": "hi"}],
        **model_kwargs,
    )
    return captured["cmd"], result


class TestClaudeCodeBackendModelHandling:
    def test_missing_model_omits_flag_and_reports_empty(self, monkeypatch, tmp_path):
        cmd, result = _run_executor(monkeypatch, tmp_path, {})
        assert "--model" not in cmd
        assert result["model"] == ""

    def test_explicit_model_still_passed_and_reported(self, monkeypatch, tmp_path):
        cmd, result = _run_executor(
            monkeypatch, tmp_path, {"model": "claude-opus-4-6"}
        )
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-opus-4-6"
        assert result["model"] == "claude-opus-4-6"

    def test_format_response_does_not_invent_model(self):
        from tokenpak.sdk.openclaw import _format_anthropic_response

        out = _format_anthropic_response({"result": "x", "usage": {}}, "", 0.1)
        assert out["model"] == ""


# ---------------------------------------------------------------------------
# SSE conversion (streaming response shell)
# ---------------------------------------------------------------------------


class _CaptureBuffer(io.BytesIO):
    """BytesIO that survives close() so the test can read what was written."""

    def close(self):  # noqa: D102 — the handler closes wfile when done
        pass


class _FakeSSEHandler:
    """Duck-typed stand-in for the HTTP handler: records what is sent."""

    def __init__(self):
        self.wfile = _CaptureBuffer()
        self.headers_sent = []
        self.status = None

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers_sent.append((key, value))

    def end_headers(self):
        pass


class TestSSEModelEcho:
    def _stream(self, result):
        from tokenpak.proxy.server import _ProxyHandler

        fake = _FakeSSEHandler()
        _ProxyHandler._send_claude_code_sse(fake, result)
        return fake.wfile.getvalue().decode()

    def test_missing_model_streams_empty_model(self):
        raw = self._stream({
            "id": "msg_t1",
            "content": [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
        start_data = None
        for line in raw.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[len("data: "):])
                if payload.get("type") == "message_start":
                    start_data = payload
                    break
        assert start_data is not None
        assert start_data["message"]["model"] == ""

    def test_known_model_passes_through(self):
        raw = self._stream({
            "id": "msg_t2",
            "model": "claude-haiku-4-5",
            "content": [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
        assert '"model": "claude-haiku-4-5"' in raw


# ---------------------------------------------------------------------------
# Spend-guard estimation with an unresolved model
# ---------------------------------------------------------------------------


class TestSpendGuardEmptyModel:
    def test_estimator_prices_with_default_rates_without_model_id(self):
        from tokenpak.proxy.spend_guard.estimator import estimate

        body = json.dumps({
            "messages": [{"role": "user", "content": "hello there"}],
            "max_tokens": 100,
        }).encode()
        est = estimate(body, "")
        # Cost is still projected (fail-safe: never unpriced) ...
        assert est.projected_cost_usd > 0
        # ... but the estimate carries no fabricated model id.
        assert est.model == ""

    def test_default_rates_match_rate_registry_default(self):
        from tokenpak.models import get_rates

        assert get_rates("") == get_rates(None)
        assert get_rates(None)["input"] > 0
