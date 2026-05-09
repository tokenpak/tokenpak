"""
Tests for F.1 (provider detection), F.2 (translation), F.3 (failover config).

Covers:
  - detect_provider: path, headers, body, host fingerprinting
  - translate_request: anthropic↔openai, anthropic↔google, roundtrip
  - translate_response: anthropic↔openai
  - FailoverConfig: parsing, credential skipping, model mapping, iteration
  - write_default_config: creates valid parseable YAML
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

from tokenpak.proxy.failover import (
    FailoverManager,
    load_failover_config,
    write_default_config,
)
from tokenpak.proxy.providers.detector import detect_provider
from tokenpak.proxy.providers.translator import (
    translate_request,
    translate_response,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _body(d: Dict[str, Any]) -> bytes:
    return json.dumps(d).encode()


# ---------------------------------------------------------------------------
# F.1 — Provider Detection
# ---------------------------------------------------------------------------

class TestDetectProvider:
    """detect_provider() — all detection layers."""

    # ── Path-based ────────────────────────────────────────────────────────

    def test_path_anthropic_messages(self):
        assert detect_provider(path="/v1/messages") == "anthropic"

    def test_path_openai_chat(self):
        assert detect_provider(path="/v1/chat/completions") == "openai"

    def test_path_openai_completions(self):
        assert detect_provider(path="/v1/completions") == "openai"

    def test_path_google_generate(self):
        assert detect_provider(path="/v1beta/models/gemini-pro:generateContent") == "google"

    def test_path_ollama_chat(self):
        assert detect_provider(path="/api/chat") == "ollama"

    def test_path_ollama_generate(self):
        assert detect_provider(path="/api/generate") == "ollama"

    # ── Host-based ─────────────────────────────────────────────────────────

    def test_host_anthropic(self):
        assert detect_provider(host="api.anthropic.com") == "anthropic"

    def test_host_openai(self):
        assert detect_provider(host="api.openai.com") == "openai"

    def test_host_google(self):
        assert detect_provider(host="generativelanguage.googleapis.com") == "google"

    def test_host_ollama_localhost(self):
        assert detect_provider(host="localhost") == "ollama"

    # ── Header-based ──────────────────────────────────────────────────────

    def test_header_anthropic_version(self):
        assert detect_provider(headers={"anthropic-version": "2023-06-01"}) == "anthropic"

    def test_header_x_api_key(self):
        assert detect_provider(headers={"x-api-key": "sk-ant-abc123"}) == "anthropic"

    def test_header_bearer_ant(self):
        assert detect_provider(headers={"Authorization": "Bearer sk-ant-api03-xxx"}) == "anthropic"

    def test_header_bearer_openai(self):
        assert detect_provider(headers={"Authorization": "Bearer sk-proj-xxx"}) == "openai"

    def test_header_bearer_google(self):
        assert detect_provider(headers={"Authorization": "Bearer AIzaSyXXX"}) == "google"

    # ── Body-based ────────────────────────────────────────────────────────

    def test_body_model_claude(self):
        body = _body({"model": "claude-sonnet-4-5", "messages": []})
        assert detect_provider(body=body) == "anthropic"

    def test_body_model_gpt(self):
        body = _body({"model": "gpt-4o", "messages": []})
        assert detect_provider(body=body) == "openai"

    def test_body_model_gemini(self):
        body = _body({"model": "gemini-1.5-pro", "contents": []})
        assert detect_provider(body=body) == "google"

    def test_body_field_contents(self):
        body = _body({"contents": [{"role": "user", "parts": [{"text": "hi"}]}]})
        assert detect_provider(body=body) == "google"

    def test_body_system_messages(self):
        body = _body({"system": "You are helpful.", "messages": [{"role": "user", "content": "hi"}]})
        assert detect_provider(body=body) == "anthropic"

    def test_body_system_message_in_messages(self):
        body = _body({
            "model": "gpt-4o",
            "messages": [{"role": "system", "content": "Be helpful"}, {"role": "user", "content": "hi"}]
        })
        assert detect_provider(body=body) == "openai"

    # ── Dict-style input ──────────────────────────────────────────────────

    def test_dict_input(self):
        req = {"path": "/v1/messages", "headers": {}}
        assert detect_provider(req) == "anthropic"

    # ── Fallback ──────────────────────────────────────────────────────────

    def test_unknown_fallback(self):
        assert detect_provider(path="/unknown") == "unknown"

    def test_empty_body_no_crash(self):
        assert detect_provider(body=b"not-json") in ("unknown", "anthropic", "openai", "google", "ollama")


# ---------------------------------------------------------------------------
# F.2 — Translation: Anthropic ↔ OpenAI
# ---------------------------------------------------------------------------

class TestTranslateAnthropicOpenAI:
    """translate_request / translate_response: anthropic ↔ openai."""

    _ant_req = {
        "model": "claude-sonnet-4-5",
        "system": "Be helpful.",
        "messages": [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "Explain AI."},
        ],
        "max_tokens": 512,
        "stream": False,
        "temperature": 0.7,
        "stop_sequences": ["DONE"],
    }

    def test_anthropic_to_openai_system_becomes_first_message(self):
        out = translate_request(self._ant_req, "anthropic", "openai")
        assert out["messages"][0]["role"] == "system"
        assert "Be helpful." in out["messages"][0]["content"]

    def test_anthropic_to_openai_message_count(self):
        out = translate_request(self._ant_req, "anthropic", "openai")
        # system + 3 original messages = 4
        assert len(out["messages"]) == 4

    def test_anthropic_to_openai_stop_sequences(self):
        out = translate_request(self._ant_req, "anthropic", "openai")
        assert out.get("stop") == ["DONE"]

    def test_anthropic_to_openai_temperature(self):
        out = translate_request(self._ant_req, "anthropic", "openai")
        assert out["temperature"] == 0.7

    def test_openai_to_anthropic_system_extracted(self):
        oai_req = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a coder."},
                {"role": "user", "content": "Write hello world."},
            ],
            "max_tokens": 256,
        }
        out = translate_request(oai_req, "openai", "anthropic")
        assert out.get("system") == "You are a coder."
        # No system messages in body
        assert all(m["role"] != "system" for m in out["messages"])

    def test_openai_to_anthropic_stop_becomes_stop_sequences(self):
        oai_req = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "stop": ["END"],
        }
        out = translate_request(oai_req, "openai", "anthropic")
        assert out.get("stop_sequences") == ["END"]

    def test_roundtrip_anthropic_openai_anthropic_preserves_content(self):
        original = {
            "model": "claude-sonnet-4-5",
            "system": "Be a helper.",
            "messages": [{"role": "user", "content": "What is 2+2?"}],
            "max_tokens": 128,
        }
        mid = translate_request(original, "anthropic", "openai")
        restored = translate_request(mid, "openai", "anthropic")
        # System should be preserved
        assert "Be a helper." in restored.get("system", "")
        # User message should survive
        assert any("2+2" in str(m.get("content", "")) for m in restored["messages"])

    def test_tool_translation_anthropic_to_openai(self):
        ant_req = {
            "model": "claude-sonnet-4-5",
            "messages": [{"role": "user", "content": "What's the weather?"}],
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Get weather info",
                    "input_schema": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                    },
                }
            ],
        }
        out = translate_request(ant_req, "anthropic", "openai")
        assert "tools" in out
        assert out["tools"][0]["type"] == "function"
        assert out["tools"][0]["function"]["name"] == "get_weather"

    def test_tool_translation_openai_to_anthropic(self):
        oai_req = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "description": "Search the web",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        }
        out = translate_request(oai_req, "openai", "anthropic")
        assert "tools" in out
        assert out["tools"][0]["name"] == "search"
        assert "input_schema" in out["tools"][0]

    def test_no_op_same_provider(self):
        req = {"model": "gpt-4o", "messages": []}
        out = translate_request(req, "openai", "openai")
        assert out == req

    def test_unsupported_pair_raises(self):
        with pytest.raises(ValueError, match="No request translator"):
            translate_request({}, "anthropic", "ollama")

    def test_response_anthropic_to_openai(self):
        ant_resp = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": "Hello world"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        out = translate_response(ant_resp, "anthropic", "openai")
        assert out["choices"][0]["message"]["content"] == "Hello world"
        assert out["choices"][0]["finish_reason"] == "stop"
        assert out["usage"]["prompt_tokens"] == 10
        assert out["usage"]["completion_tokens"] == 5

    def test_response_openai_to_anthropic(self):
        oai_resp = {
            "id": "chatcmpl-abc",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hi there"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
        }
        out = translate_response(oai_resp, "openai", "anthropic")
        assert out["content"][0]["text"] == "Hi there"
        assert out["stop_reason"] == "end_turn"
        assert out["usage"]["input_tokens"] == 8


# ---------------------------------------------------------------------------
# F.2 — Translation: Anthropic ↔ Google
# ---------------------------------------------------------------------------

class TestTranslateAnthropicGoogle:
    def test_anthropic_to_google_contents(self):
        ant = {
            "model": "claude-sonnet-4-5",
            "system": "Be helpful.",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
        }
        out = translate_request(ant, "anthropic", "google")
        assert "contents" in out
        assert out["contents"][0]["role"] == "user"
        assert out["contents"][1]["role"] == "model"

    def test_anthropic_to_google_system_instruction(self):
        ant = {
            "model": "claude-sonnet-4-5",
            "system": "You are a teacher.",
            "messages": [{"role": "user", "content": "Teach me."}],
        }
        out = translate_request(ant, "anthropic", "google")
        assert out["systemInstruction"]["parts"][0]["text"] == "You are a teacher."

    def test_google_to_anthropic_role_model(self):
        google = {
            "model": "gemini-pro",
            "contents": [
                {"role": "user", "parts": [{"text": "Hi"}]},
                {"role": "model", "parts": [{"text": "Hello!"}]},
            ],
        }
        out = translate_request(google, "google", "anthropic")
        assert out["messages"][0]["role"] == "user"
        assert out["messages"][1]["role"] == "assistant"

    def test_google_to_anthropic_system_instruction(self):
        google = {
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "systemInstruction": {"parts": [{"text": "Be concise."}]},
        }
        out = translate_request(google, "google", "anthropic")
        assert out.get("system") == "Be concise."

    def test_openai_to_google_via_chained_translation(self):
        oai = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "Be a poet."},
                {"role": "user", "content": "Write a haiku."},
            ],
        }
        out = translate_request(oai, "openai", "google")
        assert "contents" in out
        assert out["systemInstruction"]["parts"][0]["text"] == "Be a poet."


# ---------------------------------------------------------------------------
# F.3 — Failover Config
# ---------------------------------------------------------------------------

class TestFailoverConfig:
    def _make_yaml(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "config.yaml"
        p.write_text(textwrap.dedent(content))
        return p

    def test_missing_file_returns_disabled(self, tmp_path):
        cfg = load_failover_config(tmp_path / "nonexistent.yaml")
        assert cfg.enabled is False

    def test_disabled_config(self, tmp_path):
        p = self._make_yaml(tmp_path, """
            failover:
              enabled: false
              chain: []
        """)
        cfg = load_failover_config(p)
        assert cfg.enabled is False

    def test_enabled_chain_parsed(self, tmp_path):
        p = self._make_yaml(tmp_path, """
            failover:
              enabled: true
              chain:
                - provider: anthropic
                  model_map: {}
                  credential_env: ANTHROPIC_API_KEY
                - provider: openai
                  model_map:
                    claude-sonnet-4-5: gpt-4o
                  credential_env: OPENAI_API_KEY
        """)
        cfg = load_failover_config(p)
        assert cfg.enabled is True
        assert len(cfg.chain) == 2
        assert cfg.chain[0].provider == "anthropic"
        assert cfg.chain[1].model_map.get("claude-sonnet-4-5") == "gpt-4o"

    def test_credential_skipped_when_env_missing(self, tmp_path):
        p = self._make_yaml(tmp_path, """
            failover:
              enabled: true
              chain:
                - provider: anthropic
                  model_map: {}
                  credential_env: _TRIX_NO_SUCH_ENV_VAR_XYZ
        """)
        cfg = load_failover_config(p)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_TRIX_NO_SUCH_ENV_VAR_XYZ", None)
            assert cfg.available_chain() == []

    def test_credential_available_when_env_set(self, tmp_path):
        p = self._make_yaml(tmp_path, """
            failover:
              enabled: true
              chain:
                - provider: anthropic
                  model_map: {}
                  credential_env: _TRIX_TEST_CRED_ENV
        """)
        cfg = load_failover_config(p)
        with patch.dict(os.environ, {"_TRIX_TEST_CRED_ENV": "fake-key"}):
            available = cfg.available_chain()
            assert len(available) == 1
            assert available[0].provider == "anthropic"

    def test_failover_manager_disabled_yields_nothing(self, tmp_path):
        p = self._make_yaml(tmp_path, """
            failover:
              enabled: false
              chain:
                - provider: anthropic
                  model_map: {}
                  credential_env: ANTHROPIC_API_KEY
        """)
        cfg = load_failover_config(p)
        mgr = FailoverManager(cfg)
        results = list(mgr.iter_providers("claude-sonnet-4-5"))
        assert results == []

    def test_failover_manager_preferred_comes_first(self, tmp_path):
        p = self._make_yaml(tmp_path, """
            failover:
              enabled: true
              chain:
                - provider: anthropic
                  model_map: {}
                  credential_env: _TRIX_ANT_KEY
                - provider: openai
                  model_map: {}
                  credential_env: _TRIX_OAI_KEY
        """)
        cfg = load_failover_config(p)
        with patch.dict(os.environ, {"_TRIX_ANT_KEY": "k1", "_TRIX_OAI_KEY": "k2"}):
            mgr = FailoverManager(cfg)
            results = list(mgr.iter_providers("gpt-4o", preferred="openai"))
            assert results[0].provider == "openai"
            assert results[1].provider == "anthropic"

    def test_model_mapping_applied(self, tmp_path):
        p = self._make_yaml(tmp_path, """
            failover:
              enabled: true
              chain:
                - provider: openai
                  model_map:
                    claude-sonnet-4-5: gpt-4o-mini
                  credential_env: _TRIX_OAI_KEY
        """)
        cfg = load_failover_config(p)
        mgr = FailoverManager(cfg)
        assert mgr.map_model("claude-sonnet-4-5", "openai") == "gpt-4o-mini"

    def test_write_default_config_creates_valid_yaml(self, tmp_path):
        p = tmp_path / "config.yaml"
        write_default_config(path=p, overwrite=True)
        assert p.exists()
        # Should be parseable
        import yaml
        data = yaml.safe_load(p.read_text())
        assert "failover" in data

    def test_write_default_config_no_overwrite(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("existing: true")
        write_default_config(path=p, overwrite=False)
        # Original content preserved
        assert "existing: true" in p.read_text()
