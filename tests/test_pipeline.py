"""Tests for tokenpak.proxy.pipeline — modular request pipeline stages.

Covers each extracted stage function and both pipeline paths
(byte_preserved + full_pipeline).
"""
import json
import pytest

from tokenpak.proxy.request import ProxyRequest, ROUTE_CLAUDE_CODE, ROUTE_OPENCLAW, ROUTE_SDK
from tokenpak.proxy.route_policy import get_policy, ROUTE_POLICIES
from tokenpak.proxy.headers import (
    forward_headers,
    sanitize_headers,
    OPENCLAW_HEADER_ALLOWLIST,
    CLAUDE_CODE_HEADER_ALLOWLIST,
)
from tokenpak.proxy.pipeline import (
    process_request,
    stage_cache_poison_removal,
    stage_vault_injection,
    stage_header_forwarding,
    stage_auth_injection,
    stage_cache_control,
    stage_compaction,
    stage_ttl_hotfix,
    stage_byte_restore,
    PipelineResult,
    StageResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_request(body_dict=None, headers=None, **kwargs):
    """Build a ProxyRequest with a JSON body."""
    body = b""
    if body_dict is not None:
        body = json.dumps(body_dict).encode("utf-8")
    return ProxyRequest(
        method="POST",
        url="https://api.anthropic.com/v1/messages",
        headers=headers or {"Content-Type": "application/json"},
        body=body,
        **kwargs,
    )


def _sample_body():
    """A minimal Anthropic /v1/messages body."""
    return {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "system": [
            {"type": "text", "text": "You are a helpful assistant."},
        ],
        "messages": [
            {"role": "user", "content": "Hello!"},
        ],
    }


def _body_with_timestamps():
    """Body with dynamic content that should be scrubbed."""
    return {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "system": [
            {"type": "text", "text": "Current time: 2026-04-13T14:30:00Z. Request a1b2c3d4-e5f6-7890-abcd-ef1234567890."},
        ],
        "messages": [
            {"role": "user", "content": "What time is it?"},
        ],
    }


def _body_with_cache_control():
    """Body with cache_control blocks for TTL hotfix testing."""
    return {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "system": [
            {"type": "text", "text": "System prompt", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "More context", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Important", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        ],
        "messages": [
            {"role": "user", "content": "Hello!"},
        ],
    }


# ---------------------------------------------------------------------------
# headers.py tests
# ---------------------------------------------------------------------------

class TestForwardHeaders:
    def test_claude_code_client_auth_forwards_all(self):
        headers = {
            "X-Api-Key": "sk-ant-xxx",
            "Content-Type": "application/json",
            "X-Custom": "value",
            "User-Agent": "claude-code/1.0",
        }
        result = forward_headers(headers, ROUTE_CLAUDE_CODE, client_has_auth=True)
        # Should forward everything except hop-by-hop
        assert "X-Api-Key" in result
        assert "Content-Type" in result
        assert "X-Custom" in result
        assert "User-Agent" in result

    def test_claude_code_no_auth_uses_allowlist(self):
        headers = {
            "x-api-key": "placeholder",
            "content-type": "application/json",
            "x-custom-header": "value",
            "x-stainless-lang": "python",
            "anthropic-version": "2023-06-01",
        }
        result = forward_headers(headers, ROUTE_CLAUDE_CODE, client_has_auth=False)
        assert "x-api-key" in result
        assert "content-type" in result
        assert "x-stainless-lang" in result
        assert "anthropic-version" in result
        assert "x-custom-header" not in result

    def test_openclaw_strict_allowlist(self):
        headers = {
            "x-api-key": "sk-ant-xxx",
            "authorization": "Bearer token",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "messages-2024-12-19-latest",
            "content-type": "application/json",
            "user-agent": "openclaw/1.0",
        }
        result = forward_headers(headers, ROUTE_OPENCLAW)
        assert "x-api-key" in result
        assert "authorization" in result
        assert "anthropic-version" in result
        assert "anthropic-beta" in result
        assert "content-type" not in result
        assert "user-agent" not in result

    def test_sdk_sanitizes(self):
        headers = {
            "Content-Type": "application/json",
            "Host": "localhost:8766",
            "Connection": "keep-alive",
            "X-Custom": "value",
        }
        result = forward_headers(headers, ROUTE_SDK)
        assert "Content-Type" in result
        assert "X-Custom" in result
        # hop-by-hop stripped
        assert "Host" not in result
        assert "Connection" not in result

    def test_hop_by_hop_never_forwarded_claude_code(self):
        headers = {
            "Host": "localhost",
            "Connection": "keep-alive",
            "Transfer-Encoding": "chunked",
            "x-api-key": "sk-ant-xxx",
        }
        result = forward_headers(headers, ROUTE_CLAUDE_CODE, client_has_auth=True)
        assert "Host" not in result
        assert "Connection" not in result
        assert "Transfer-Encoding" not in result


# ---------------------------------------------------------------------------
# stage_cache_poison_removal tests
# ---------------------------------------------------------------------------

class TestStageCachePoisonRemoval:
    def test_scrubs_timestamps_and_uuids(self):
        req = _make_request(_body_with_timestamps())
        policy = get_policy(ROUTE_OPENCLAW)
        req, result = stage_cache_poison_removal(req, policy)
        assert not result.skipped
        assert result.details["changed"] is True
        body = json.loads(req.body)
        assert "2026-04-13T14:30:00Z" not in body["system"][0]["text"]
        assert "a1b2c3d4" not in body["system"][0]["text"]

    def test_skips_when_disabled(self):
        req = _make_request(_body_with_timestamps())
        policy = {"cache_poison_removal": "disabled"}
        req, result = stage_cache_poison_removal(req, policy)
        assert result.skipped
        assert result.skip_reason == "disabled_by_policy"

    def test_empty_body_skips(self):
        req = _make_request()
        policy = get_policy(ROUTE_OPENCLAW)
        req, result = stage_cache_poison_removal(req, policy)
        assert result.skipped

    def test_clean_body_unchanged(self):
        req = _make_request(_sample_body())
        policy = get_policy(ROUTE_OPENCLAW)
        original = req.body
        req, result = stage_cache_poison_removal(req, policy)
        assert result.details["changed"] is False
        assert req.body == original


# ---------------------------------------------------------------------------
# stage_header_forwarding tests
# ---------------------------------------------------------------------------

class TestStageHeaderForwarding:
    def test_openclaw_filters_to_allowlist(self):
        headers = {
            "x-api-key": "key",
            "anthropic-version": "v1",
            "user-agent": "test",
        }
        req = _make_request(headers=headers)
        policy = get_policy(ROUTE_OPENCLAW)
        req, result = stage_header_forwarding(
            req, policy, route=ROUTE_OPENCLAW
        )
        assert "x-api-key" in req.headers
        assert "anthropic-version" in req.headers
        assert "user-agent" not in req.headers
        assert result.details["headers_before"] == 3
        assert result.details["headers_after"] == 2

    def test_claude_code_client_auth_forwards_all(self):
        headers = {
            "x-api-key": "sk-ant-xxx",
            "x-custom": "val",
            "anthropic-version": "v1",
        }
        req = _make_request(headers=headers)
        policy = get_policy(ROUTE_CLAUDE_CODE)
        req, result = stage_header_forwarding(
            req, policy, route=ROUTE_CLAUDE_CODE, client_has_auth=True
        )
        assert "x-api-key" in req.headers
        assert "x-custom" in req.headers


# ---------------------------------------------------------------------------
# stage_auth_injection tests
# ---------------------------------------------------------------------------

class TestStageAuthInjection:
    def test_passthrough_skips(self):
        req = _make_request(headers={"x-api-key": "sk-ant-xxx"})
        policy = get_policy(ROUTE_CLAUDE_CODE)
        req, result = stage_auth_injection(req, policy, client_has_auth=True)
        assert result.skipped
        assert "sk-ant-xxx" in req.headers.values()

    def test_inject_from_pool(self):
        req = _make_request(headers={})
        policy = get_policy(ROUTE_OPENCLAW)

        def mock_pool():
            return ("sk-pool-key-123", 0)

        req, result = stage_auth_injection(
            req, policy, client_has_auth=False, get_pool_key=mock_pool
        )
        assert not result.skipped
        assert req.headers["x-api-key"] == "sk-pool-key-123"
        assert result.details["injected"] is True

    def test_inject_from_cli_token_fallback(self):
        req = _make_request(headers={})
        policy = get_policy(ROUTE_OPENCLAW)

        def mock_cli():
            return "sk-cli-token-456"

        req, result = stage_auth_injection(
            req, policy, client_has_auth=False, get_cli_token=mock_cli
        )
        assert req.headers["x-api-key"] == "sk-cli-token-456"

    def test_no_credentials_available(self):
        req = _make_request(headers={})
        policy = get_policy(ROUTE_OPENCLAW)
        req, result = stage_auth_injection(req, policy, client_has_auth=False)
        assert result.skip_reason == "no_credentials_available"


# ---------------------------------------------------------------------------
# stage_cache_control tests
# ---------------------------------------------------------------------------

class TestStageCacheControl:
    def test_skipped_for_client_managed(self):
        req = _make_request(_sample_body())
        policy = get_policy(ROUTE_CLAUDE_CODE)
        req, result = stage_cache_control(req, policy)
        assert result.skipped
        assert result.skip_reason == "client_managed"

    def test_skipped_when_stamps_disabled(self):
        req = _make_request(_sample_body())
        policy = {"cache_control": "proxy_managed", "stable_cache_stamps": "disabled"}
        req, result = stage_cache_control(req, policy)
        assert result.skipped
        assert result.skip_reason == "stamps_disabled"


# ---------------------------------------------------------------------------
# stage_compaction tests
# ---------------------------------------------------------------------------

class TestStageCompaction:
    def test_disabled_for_byte_preserved(self):
        req = _make_request(_sample_body())
        policy = get_policy(ROUTE_CLAUDE_CODE)
        req, result = stage_compaction(req, policy)
        assert result.skipped
        assert result.skip_reason == "disabled_by_policy"

    def test_runs_with_compactor(self):
        req = _make_request(_sample_body())
        policy = get_policy(ROUTE_OPENCLAW)

        def mock_compact(body, adapter=None):
            return body, 100, 200, 10  # sent, original, protected

        req, result = stage_compaction(req, policy, compact_fn=mock_compact)
        assert not result.skipped
        assert result.tokens_delta == -100  # -(200-100)
        assert result.details["original_tokens"] == 200
        assert result.details["sent_tokens"] == 100


# ---------------------------------------------------------------------------
# stage_ttl_hotfix tests
# ---------------------------------------------------------------------------

class TestStageTtlHotfix:
    def test_strips_default_ttl_before_explicit(self):
        req = _make_request(_body_with_cache_control())
        policy = get_policy(ROUTE_OPENCLAW)
        req, result = stage_ttl_hotfix(req, policy)
        assert not result.skipped
        assert result.details["stripped_count"] == 2
        body = json.loads(req.body)
        # First two system blocks should have cache_control removed
        assert "cache_control" not in body["system"][0]
        assert "cache_control" not in body["system"][1]
        # Third (explicit TTL) should remain
        assert "cache_control" in body["system"][2]
        assert body["system"][2]["cache_control"]["ttl"] == "1h"

    def test_skipped_for_client_managed(self):
        req = _make_request(_body_with_cache_control())
        policy = get_policy(ROUTE_CLAUDE_CODE)
        req, result = stage_ttl_hotfix(req, policy)
        assert result.skipped

    def test_no_explicit_ttl_skips(self):
        body = {
            "model": "claude-sonnet-4-6",
            "system": [
                {"type": "text", "text": "Hello", "cache_control": {"type": "ephemeral"}},
            ],
            "messages": [{"role": "user", "content": "Hi"}],
        }
        req = _make_request(body)
        policy = get_policy(ROUTE_OPENCLAW)
        req, result = stage_ttl_hotfix(req, policy)
        assert result.skipped
        assert result.skip_reason == "no_explicit_ttl"


# ---------------------------------------------------------------------------
# stage_byte_restore tests
# ---------------------------------------------------------------------------

class TestStageByteRestore:
    def test_not_byte_preserved_skips(self):
        req = _make_request(_sample_body())
        policy = get_policy(ROUTE_OPENCLAW)
        req, result = stage_byte_restore(req, policy, original_body=req.body)
        assert result.skipped
        assert result.skip_reason == "not_byte_preserved"

    def test_restores_original_when_no_injection(self):
        original = json.dumps(_sample_body()).encode()
        mutated = json.dumps({"mutated": True}).encode()
        req = ProxyRequest(method="POST", url="https://api.anthropic.com/v1/messages",
                           body=mutated)
        policy = get_policy(ROUTE_CLAUDE_CODE)
        req, result = stage_byte_restore(
            req, policy, original_body=original, vault_injection_text=""
        )
        assert req.body == original
        assert result.details["action"] == "restored_original"


# ---------------------------------------------------------------------------
# process_request (orchestrator) tests
# ---------------------------------------------------------------------------

class TestProcessRequest:
    def test_full_pipeline_route(self):
        req = _make_request(_body_with_timestamps())
        policy = get_policy(ROUTE_OPENCLAW)
        result = process_request(
            req, policy, route=ROUTE_OPENCLAW, client_has_auth=False
        )
        assert isinstance(result, PipelineResult)
        stage_names = [s.name for s in result.stages]
        assert "cache_poison_removal" in stage_names
        assert "vault_injection" in stage_names
        assert "cache_control" in stage_names
        assert "compaction" in stage_names
        assert "ttl_hotfix" in stage_names
        assert "header_forwarding" in stage_names
        assert "auth_injection" in stage_names

    def test_passthrough_pipeline_route(self):
        req = _make_request(_sample_body())
        policy = get_policy(ROUTE_CLAUDE_CODE)
        result = process_request(
            req, policy, route=ROUTE_CLAUDE_CODE, client_has_auth=True
        )
        stage_names = [s.name for s in result.stages]
        assert "cache_poison_removal" in stage_names
        assert "vault_injection" in stage_names
        assert "header_forwarding" in stage_names
        assert "auth_injection" in stage_names
        assert "byte_restore" in stage_names
        # Full-pipeline-only stages should NOT appear
        assert "compaction" not in stage_names
        assert "cache_control" not in stage_names
        assert "ttl_hotfix" not in stage_names

    def test_sdk_uses_full_pipeline(self):
        req = _make_request(_sample_body())
        policy = get_policy(ROUTE_SDK)
        result = process_request(
            req, policy, route=ROUTE_SDK, client_has_auth=False
        )
        stage_names = [s.name for s in result.stages]
        assert "compaction" in stage_names

    def test_policy_drives_stage_skipping(self):
        req = _make_request(_sample_body())
        policy = get_policy(ROUTE_CLAUDE_CODE)
        result = process_request(
            req, policy, route=ROUTE_CLAUDE_CODE, client_has_auth=True
        )
        # Auth should be skipped for passthrough + client_has_auth
        auth_stage = next(s for s in result.stages if s.name == "auth_injection")
        assert auth_stage.skipped

    def test_compaction_skipped_without_compactor(self):
        req = _make_request(_sample_body())
        policy = get_policy(ROUTE_OPENCLAW)
        result = process_request(
            req, policy, route=ROUTE_OPENCLAW, compact_fn=None
        )
        compaction = next(s for s in result.stages if s.name == "compaction")
        assert compaction.skipped
        assert compaction.skip_reason == "no_body_or_no_compactor"


# ---------------------------------------------------------------------------
# Route policy integration tests
# ---------------------------------------------------------------------------

class TestRoutePolicyIntegration:
    def test_all_routes_have_required_keys(self):
        required = {
            "auth", "body", "vault_injection", "compaction",
            "cache_control", "headers", "platform_tag",
            "cache_poison_removal", "stable_cache_stamps", "cache_cap",
        }
        for route_name, policy in ROUTE_POLICIES.items():
            missing = required - set(policy.keys())
            assert not missing, f"Route {route_name} missing keys: {missing}"

    def test_claude_code_is_byte_preserved(self):
        policy = get_policy(ROUTE_CLAUDE_CODE)
        assert policy["body"] == "byte_preserved"
        assert policy["compaction"] == "disabled"
        assert policy["cache_control"] == "client_managed"

    def test_openclaw_is_full_pipeline(self):
        policy = get_policy(ROUTE_OPENCLAW)
        assert policy["body"] == "full_pipeline"
        assert policy["compaction"] == "enabled"
        assert policy["auth"] == "inject"
