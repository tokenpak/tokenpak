"""AnthropicOAuthBackend — 1.3.0-γ acceptance.

Doesn't invoke the real ``claude`` binary — that would require a logged-in
OAuth session and unpredictable network. Tests the contract + failure
modes (binary missing, malformed body).
"""

from __future__ import annotations

import json

from tokenpak.services.request import Request
from tokenpak.services.routing_service.backends.anthropic_oauth import (
    AnthropicOAuthBackend,
)


def test_missing_binary_returns_502():
    backend = AnthropicOAuthBackend(claude_binary="/definitely/not/a/real/path/claude-nope")
    r = Request(body=b'{"messages":[{"role":"user","content":"hi"}]}')
    resp = backend.dispatch(r)
    assert resp.status == 502
    body = json.loads(resp.body)
    assert body["error"]["type"] == "backend_unavailable"


def test_malformed_body_returns_400(monkeypatch, tmp_path):
    # Install a fake claude that should never actually be invoked.
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    backend = AnthropicOAuthBackend(claude_binary=str(fake))

    r = Request(body=b"not json at all")
    resp = backend.dispatch(r)
    assert resp.status == 400
    body = json.loads(resp.body)
    assert body["error"]["type"] == "invalid_request"


def test_extract_prompt_string_content():
    body = json.dumps({
        "messages": [
            {"role": "user", "content": "hello world"}
        ]
    }).encode()
    assert AnthropicOAuthBackend._extract_prompt(body) == "hello world"


def test_extract_prompt_list_content():
    body = json.dumps({
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "part one"},
                {"type": "text", "text": "part two"},
            ]}
        ]
    }).encode()
    result = AnthropicOAuthBackend._extract_prompt(body)
    assert "part one" in result and "part two" in result


def test_extract_prompt_picks_last_user_message():
    body = json.dumps({
        "messages": [
            {"role": "user", "content": "first user msg"},
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "LATEST user msg"},
        ]
    }).encode()
    assert AnthropicOAuthBackend._extract_prompt(body) == "LATEST user msg"


def test_extract_prompt_no_user_message_returns_none():
    body = json.dumps({"messages": [{"role": "assistant", "content": "hi"}]}).encode()
    assert AnthropicOAuthBackend._extract_prompt(body) is None
