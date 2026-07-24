"""Unit tests for tokenpak.proxy.route_policy (SUE-MTC-03)."""

from tokenpak.proxy.request import ROUTE_CLAUDE_CODE, ROUTE_OPENCLAW, ROUTE_SDK
from tokenpak.proxy.route_policy import (
    ROUTE_POLICIES,
    get_policy,
    is_auth_passthrough,
    is_byte_preserved,
    is_compaction_enabled,
    platform_tag,
)


class TestGetPolicy:
    def test_claude_code_returns_policy(self):
        policy = get_policy(ROUTE_CLAUDE_CODE)
        assert isinstance(policy, dict)
        assert policy["auth"] == "passthrough"

    def test_openclaw_returns_policy(self):
        policy = get_policy(ROUTE_OPENCLAW)
        assert isinstance(policy, dict)
        assert policy["auth"] == "inject"

    def test_sdk_returns_policy(self):
        policy = get_policy(ROUTE_SDK)
        assert isinstance(policy, dict)
        assert policy["auth"] == "passthrough"

    def test_unknown_route_falls_back_to_default(self):
        policy = get_policy("some-unknown-route")
        assert isinstance(policy, dict)
        assert policy["platform_tag"] == "unknown"

    def test_all_routes_have_required_keys(self):
        required_keys = {
            "auth",
            "body",
            "vault_injection",
            "compaction",
            "cache_control",
            "headers",
            "platform_tag",
            "cache_poison_removal",
            "stable_cache_stamps",
            "cache_cap",
        }
        for route, policy in ROUTE_POLICIES.items():
            missing = required_keys - set(policy.keys())
            assert not missing, f"Route {route} missing keys: {missing}"


class TestClaudeCodePolicy:
    """Verify the Claude Code route matches the byte-preservation architecture."""

    def test_body_byte_preserved(self):
        policy = get_policy(ROUTE_CLAUDE_CODE)
        assert policy["body"] == "byte_preserved"

    def test_auth_passthrough(self):
        policy = get_policy(ROUTE_CLAUDE_CODE)
        assert policy["auth"] == "passthrough"

    def test_compaction_disabled(self):
        policy = get_policy(ROUTE_CLAUDE_CODE)
        assert policy["compaction"] == "disabled"

    def test_vault_injection_byte_splice(self):
        policy = get_policy(ROUTE_CLAUDE_CODE)
        assert policy["vault_injection"] == "byte_splice"

    def test_cache_control_client_managed(self):
        policy = get_policy(ROUTE_CLAUDE_CODE)
        assert policy["cache_control"] == "client_managed"

    def test_headers_forward_all(self):
        policy = get_policy(ROUTE_CLAUDE_CODE)
        assert policy["headers"] == "forward_all"

    def test_stable_cache_stamps_disabled(self):
        policy = get_policy(ROUTE_CLAUDE_CODE)
        assert policy["stable_cache_stamps"] == "disabled"

    def test_cache_cap_disabled(self):
        policy = get_policy(ROUTE_CLAUDE_CODE)
        assert policy["cache_cap"] == "disabled"

    def test_platform_tag(self):
        policy = get_policy(ROUTE_CLAUDE_CODE)
        assert policy["platform_tag"] == "claude-code"


class TestOpenClawPolicy:
    """Verify the OpenClaw route uses the full pipeline."""

    def test_body_full_pipeline(self):
        policy = get_policy(ROUTE_OPENCLAW)
        assert policy["body"] == "full_pipeline"

    def test_auth_inject(self):
        policy = get_policy(ROUTE_OPENCLAW)
        assert policy["auth"] == "inject"

    def test_compaction_enabled(self):
        policy = get_policy(ROUTE_OPENCLAW)
        assert policy["compaction"] == "enabled"

    def test_vault_injection_json(self):
        policy = get_policy(ROUTE_OPENCLAW)
        assert policy["vault_injection"] == "json_inject"

    def test_headers_allowlist(self):
        policy = get_policy(ROUTE_OPENCLAW)
        assert policy["headers"] == "allowlist"

    def test_platform_tag(self):
        policy = get_policy(ROUTE_OPENCLAW)
        assert policy["platform_tag"] == "openclaw"


class TestConvenienceFunctions:
    def test_is_byte_preserved_claude_code(self):
        assert is_byte_preserved(ROUTE_CLAUDE_CODE) is True

    def test_is_byte_preserved_openclaw(self):
        assert is_byte_preserved(ROUTE_OPENCLAW) is False

    def test_is_auth_passthrough_claude_code(self):
        assert is_auth_passthrough(ROUTE_CLAUDE_CODE) is True

    def test_is_auth_passthrough_openclaw(self):
        assert is_auth_passthrough(ROUTE_OPENCLAW) is False

    def test_is_compaction_enabled_claude_code(self):
        assert is_compaction_enabled(ROUTE_CLAUDE_CODE) is False

    def test_is_compaction_enabled_openclaw(self):
        assert is_compaction_enabled(ROUTE_OPENCLAW) is True

    def test_platform_tag_values(self):
        assert platform_tag(ROUTE_CLAUDE_CODE) == "claude-code"
        assert platform_tag(ROUTE_OPENCLAW) == "openclaw"
        assert platform_tag(ROUTE_SDK) == "sdk"
        assert platform_tag("anything-else") == "unknown"
