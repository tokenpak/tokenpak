"""PolicyResolver — 1.3.0-α acceptance."""

from __future__ import annotations

import pytest

from tokenpak.core.routing.policy import DEFAULT_POLICY
from tokenpak.core.routing.route_class import RouteClass
from tokenpak.services.policy_service.resolver import (
    PolicyResolver,
    get_resolver,
)


@pytest.fixture
def resolver() -> PolicyResolver:
    return PolicyResolver()


def test_claude_code_tui_is_byte_preserve_client_cache(resolver):
    p = resolver.resolve(RouteClass.CLAUDE_CODE_TUI)
    assert p.body_handling == "byte_preserve"
    assert p.cache_ownership == "client"
    assert p.ttl_ordering_enforcement is True
    assert p.compression_eligible is False
    assert p.capture_session_id_header == "x-claude-code-session-id"


def test_every_claude_code_mode_is_byte_preserve(resolver):
    for rc in RouteClass:
        if not rc.is_claude_code:
            continue
        p = resolver.resolve(rc)
        assert p.body_handling == "byte_preserve", f"{rc} should be byte_preserve"
        assert p.cache_ownership == "client", f"{rc} should have client cache"


def test_claude_code_cron_blocks_dlp(resolver):
    p = resolver.resolve(RouteClass.CLAUDE_CODE_CRON)
    assert p.dlp_mode == "block"  # cron has no human in the loop


def test_anthropic_sdk_is_mutate_proxy_cache(resolver):
    p = resolver.resolve(RouteClass.ANTHROPIC_SDK)
    assert p.body_handling == "mutate"
    assert p.cache_ownership == "proxy"
    assert p.compression_eligible is True
    assert p.injection_enabled is True


def test_openai_sdk_has_no_cache(resolver):
    p = resolver.resolve(RouteClass.OPENAI_SDK)
    assert p.cache_ownership == "none"
    assert p.ttl_ordering_enforcement is False


def test_generic_falls_back_to_defaults(resolver):
    p = resolver.resolve(RouteClass.GENERIC)
    # generic.yaml mirrors DEFAULT_POLICY intentionally.
    assert p.body_handling == DEFAULT_POLICY.body_handling
    assert p.compression_eligible == DEFAULT_POLICY.compression_eligible


def test_env_override_coerces_types(monkeypatch, resolver):
    monkeypatch.setenv("TOKENPAK_POLICY_INJECTION_ENABLED", "true")
    monkeypatch.setenv("TOKENPAK_POLICY_INJECTION_BUDGET_CHARS", "5000")
    p = resolver.resolve(RouteClass.GENERIC)
    assert p.injection_enabled is True
    assert p.injection_budget_chars == 5000


def test_env_override_ignores_unknown_fields(monkeypatch, resolver):
    """Typos in env vars don't create new Policy fields — canonical
    field list is the dataclass."""
    monkeypatch.setenv("TOKENPAK_POLICY_INJECTED_ENABLED", "true")  # typo
    p = resolver.resolve(RouteClass.GENERIC)
    assert not hasattr(p, "injected_enabled")


def test_module_level_resolver_is_shared():
    assert get_resolver() is get_resolver()
