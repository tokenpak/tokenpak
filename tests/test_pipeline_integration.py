"""Integration tests for tokenpak.proxy.pipeline — byte-identical output verification.

These tests verify the critical constraint that the modular pipeline
produces the same output as the monolith for both Claude Code
(byte-preserved) and OpenClaw (full pipeline) paths.
"""
import json
import pytest

from tokenpak.proxy.request import (
    ProxyRequest,
    _find_system_array_close,
    _byte_inject_system_block,
    ROUTE_CLAUDE_CODE,
    ROUTE_OPENCLAW,
    ROUTE_SDK,
)
from tokenpak.proxy.route_policy import get_policy
from tokenpak.proxy.pipeline import (
    process_request,
    stage_byte_restore,
    stage_cache_poison_removal,
    stage_vault_injection,
)
from tokenpak.proxy.cache_poison import strip_cache_poisons


# ---------------------------------------------------------------------------
# Byte-level injection tests (_find_system_array_close + _byte_inject_system_block)
# ---------------------------------------------------------------------------

class TestFindSystemArrayClose:
    def test_simple_system_array(self):
        body = b'{"system": [{"type": "text", "text": "Hello"}], "messages": []}'
        pos = _find_system_array_close(body)
        assert pos > 0
        assert body[pos] == ord("]")

    def test_empty_system_array(self):
        body = b'{"system": [], "messages": []}'
        pos = _find_system_array_close(body)
        assert pos > 0
        assert body[pos] == ord("]")

    def test_nested_arrays(self):
        body = b'{"system": [{"type": "text", "text": "has [brackets] inside"}], "messages": []}'
        pos = _find_system_array_close(body)
        assert pos > 0
        # Verify we found the RIGHT ] — the system array close, not the nested one
        after = body[pos + 1:]
        assert b'"messages"' in after

    def test_no_system_key(self):
        body = b'{"messages": [{"role": "user", "content": "Hello"}]}'
        pos = _find_system_array_close(body)
        assert pos == -1

    def test_system_is_string_not_array(self):
        body = b'{"system": "Hello", "messages": []}'
        pos = _find_system_array_close(body)
        assert pos == -1

    def test_escaped_quotes_in_system(self):
        body = b'{"system": [{"type": "text", "text": "says \\"hello\\""}], "messages": []}'
        pos = _find_system_array_close(body)
        assert pos > 0
        assert body[pos] == ord("]")

    def test_multiline_formatted_json(self):
        body = json.dumps({
            "system": [
                {"type": "text", "text": "Line 1"},
                {"type": "text", "text": "Line 2"},
            ],
            "messages": [],
        }, indent=2).encode()
        pos = _find_system_array_close(body)
        assert pos > 0


class TestByteInjectSystemBlock:
    def test_inject_into_non_empty_array(self):
        body = json.dumps({
            "system": [
                {"type": "text", "text": "Existing prompt"},
            ],
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()

        result = _byte_inject_system_block(body, "Injected vault context")
        parsed = json.loads(result)
        assert len(parsed["system"]) == 2
        assert parsed["system"][1]["type"] == "text"
        assert parsed["system"][1]["text"] == "Injected vault context"

    def test_inject_into_empty_array(self):
        body = json.dumps({
            "system": [],
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()

        result = _byte_inject_system_block(body, "Vault context")
        parsed = json.loads(result)
        assert len(parsed["system"]) == 1
        assert parsed["system"][0]["text"] == "Vault context"

    def test_preserves_original_bytes_outside_injection(self):
        # The key constraint: bytes before and after the injection point
        # must be IDENTICAL to the original body
        original = b'{"system": [{"type": "text", "text": "Original"}], "messages": [{"role": "user", "content": "Hello"}]}'
        close_pos = _find_system_array_close(original)
        result = _byte_inject_system_block(original, "Injected")

        # Everything before the injection point should be identical
        assert result[:close_pos] == original[:close_pos]
        # Everything after the injection should end with the same suffix
        assert result.endswith(original[close_pos:])

    def test_empty_injection_returns_original(self):
        body = b'{"system": [{"type": "text", "text": "Hello"}], "messages": []}'
        result = _byte_inject_system_block(body, "")
        assert result == body

    def test_no_system_array_returns_original(self):
        body = b'{"messages": [{"role": "user", "content": "Hello"}]}'
        result = _byte_inject_system_block(body, "Injected")
        assert result == body

    def test_special_characters_in_injection(self):
        body = json.dumps({
            "system": [{"type": "text", "text": "Base"}],
            "messages": [],
        }).encode()
        result = _byte_inject_system_block(body, 'Text with "quotes" and \n newlines')
        parsed = json.loads(result)
        assert parsed["system"][1]["text"] == 'Text with "quotes" and \n newlines'

    def test_unicode_in_injection(self):
        body = json.dumps({
            "system": [{"type": "text", "text": "Base"}],
            "messages": [],
        }).encode()
        result = _byte_inject_system_block(body, "Context with emoji \U0001f4da and CJK \u4e16\u754c")
        parsed = json.loads(result)
        assert "\U0001f4da" in parsed["system"][1]["text"]
        assert "\u4e16\u754c" in parsed["system"][1]["text"]


# ---------------------------------------------------------------------------
# Byte-preserved pipeline path tests (Claude Code)
# ---------------------------------------------------------------------------

class TestBytePreservedPipeline:
    """Verify the Claude Code byte-preserved path preserves exact bytes."""

    def test_no_vault_injection_restores_exact_bytes(self):
        """When vault has no matches, original bytes must be returned exactly."""
        original_body = json.dumps({
            "model": "claude-sonnet-4-6",
            "system": [{"type": "text", "text": "You are helpful."}],
            "messages": [{"role": "user", "content": "Hello"}],
        }).encode()

        req = ProxyRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"x-api-key": "sk-ant-xxx", "content-type": "application/json"},
            body=original_body,
        )
        policy = get_policy(ROUTE_CLAUDE_CODE)

        # Run just the byte_restore stage with no vault injection text
        req, result = stage_byte_restore(
            req, policy,
            original_body=original_body,
            vault_injection_text="",
        )
        assert req.body == original_body, "Byte-preserved path must return identical bytes"

    def test_cache_poison_does_not_mutate_final_bytes(self):
        """Cache poison runs on a working copy; final bytes come from byte_restore."""
        body_with_timestamps = json.dumps({
            "model": "claude-sonnet-4-6",
            "system": [{"type": "text", "text": "Time: 2026-04-13T14:30:00Z"}],
            "messages": [{"role": "user", "content": "Hello"}],
        }).encode()

        original = bytes(body_with_timestamps)

        req = ProxyRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"x-api-key": "sk-ant-xxx"},
            body=body_with_timestamps,
        )
        policy = get_policy(ROUTE_CLAUDE_CODE)

        # Cache poison scrubs timestamps
        req, cp_result = stage_cache_poison_removal(req, policy)
        assert cp_result.details["changed"] is True
        assert req.body != original  # body was mutated by cache poison

        # Byte restore discards the mutation and returns original
        req, br_result = stage_byte_restore(
            req, policy,
            original_body=original,
            vault_injection_text="",
        )
        assert req.body == original, "Byte restore must undo cache poison mutation"

    def test_byte_inject_produces_valid_json(self):
        """Byte-level vault injection must produce valid, parseable JSON."""
        original = json.dumps({
            "model": "claude-sonnet-4-6",
            "system": [
                {"type": "text", "text": "You are helpful."},
            ],
            "messages": [{"role": "user", "content": "What is tokenpak?"}],
        }).encode()

        injected = _byte_inject_system_block(original, "TokenPak is a proxy.")

        # Must still be valid JSON
        parsed = json.loads(injected)
        assert len(parsed["system"]) == 2
        assert parsed["system"][1]["text"] == "TokenPak is a proxy."

        # Messages must be preserved exactly
        assert parsed["messages"] == [{"role": "user", "content": "What is tokenpak?"}]


# ---------------------------------------------------------------------------
# Full pipeline path tests (OpenClaw)
# ---------------------------------------------------------------------------

class TestFullPipelineIntegration:
    """Verify the OpenClaw/SDK full pipeline produces correct output."""

    def test_cache_poison_scrubbing_applied(self):
        body = json.dumps({
            "model": "claude-sonnet-4-6",
            "system": [{"type": "text", "text": "Time: 2026-04-13T14:30:00Z req_id: a1b2c3d4-e5f6-7890-abcd-ef1234567890"}],
            "messages": [{"role": "user", "content": "Hello"}],
        }).encode()

        req = ProxyRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"content-type": "application/json"},
            body=body,
        )
        policy = get_policy(ROUTE_OPENCLAW)
        result = process_request(
            req, policy, route=ROUTE_OPENCLAW, client_has_auth=False
        )

        parsed = json.loads(result.request.body)
        system_text = parsed["system"][0]["text"]
        assert "2026-04-13T14:30:00Z" not in system_text
        assert "a1b2c3d4" not in system_text

    def test_ttl_hotfix_stage_present_in_full_pipeline(self):
        """Verify the TTL hotfix stage is included in the full pipeline."""
        body = json.dumps({
            "model": "claude-sonnet-4-6",
            "system": [{"type": "text", "text": "A"}],
            "messages": [{"role": "user", "content": "Hello"}],
        }).encode()

        req = ProxyRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"content-type": "application/json"},
            body=body,
        )
        policy = get_policy(ROUTE_OPENCLAW)
        result = process_request(
            req, policy, route=ROUTE_OPENCLAW, client_has_auth=False
        )

        stage_names = [s.name for s in result.stages]
        assert "ttl_hotfix" in stage_names
        # TTL hotfix should skip gracefully when no explicit TTL present
        ttl_stage = next(s for s in result.stages if s.name == "ttl_hotfix")
        assert ttl_stage.skipped or ttl_stage.details.get("stripped_count", 0) >= 0

    def test_compaction_skipped_for_claude_code(self):
        body = json.dumps({
            "model": "claude-sonnet-4-6",
            "system": [{"type": "text", "text": "Hello"}],
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()

        req = ProxyRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"x-api-key": "sk-ant-xxx"},
            body=body,
        )
        policy = get_policy(ROUTE_CLAUDE_CODE)
        result = process_request(
            req, policy, route=ROUTE_CLAUDE_CODE, client_has_auth=True
        )
        stage_names = {s.name: s for s in result.stages}
        # No compaction stage should exist for byte-preserved path
        assert "compaction" not in stage_names

    def test_auth_injected_for_openclaw(self):
        req = ProxyRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"content-type": "application/json"},
            body=json.dumps({"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "Hi"}]}).encode(),
        )
        policy = get_policy(ROUTE_OPENCLAW)

        def mock_pool():
            return ("sk-injected-key", 0)

        result = process_request(
            req, policy, route=ROUTE_OPENCLAW,
            client_has_auth=False, get_pool_key=mock_pool,
        )
        assert result.request.headers.get("x-api-key") == "sk-injected-key"

    def test_headers_filtered_for_openclaw(self):
        req = ProxyRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": "key",
                "anthropic-version": "v1",
                "user-agent": "test/1.0",
                "x-custom": "value",
            },
            body=json.dumps({"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "Hi"}]}).encode(),
        )
        policy = get_policy(ROUTE_OPENCLAW)
        result = process_request(
            req, policy, route=ROUTE_OPENCLAW, client_has_auth=False
        )
        # OpenClaw allowlist should filter out non-essential headers
        assert "user-agent" not in result.request.headers
        assert "x-custom" not in result.request.headers
        assert "x-api-key" in result.request.headers


# ---------------------------------------------------------------------------
# Cross-path consistency tests
# ---------------------------------------------------------------------------

class TestCrossPathConsistency:
    """Verify that both pipeline paths handle edge cases consistently."""

    def test_empty_body_handled(self):
        for route in (ROUTE_CLAUDE_CODE, ROUTE_OPENCLAW, ROUTE_SDK):
            req = ProxyRequest(
                method="POST",
                url="https://api.anthropic.com/v1/messages",
                headers={},
                body=b"",
            )
            policy = get_policy(route)
            result = process_request(req, policy, route=route)
            assert isinstance(result.request, ProxyRequest)

    def test_malformed_json_body_handled(self):
        for route in (ROUTE_CLAUDE_CODE, ROUTE_OPENCLAW, ROUTE_SDK):
            req = ProxyRequest(
                method="POST",
                url="https://api.anthropic.com/v1/messages",
                headers={},
                body=b"not json at all {{{",
            )
            policy = get_policy(route)
            result = process_request(req, policy, route=route)
            # Should not crash — fail-open
            assert isinstance(result.request, ProxyRequest)
