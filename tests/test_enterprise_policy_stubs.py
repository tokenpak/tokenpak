"""
Tests for Enterprise policy engine stubs.

Verifies that:
1. Stub modules are importable and have the right interfaces
2. Non-Enterprise tier receives graceful upgrade messages
3. CLI command modules are importable and callable
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# 1. Enterprise module interfaces are importable
# ---------------------------------------------------------------------------


def test_policy_engine_imports():
    """PolicyEngine and related classes can be imported."""
    from tokenpak.enterprise.policy import (
        PolicyEngine,
        PolicyEngineBase,
    )
    assert issubclass(PolicyEngine, PolicyEngineBase)


def test_sla_router_imports():
    """SLARouter and related classes can be imported."""
    from tokenpak.enterprise.sla import (
        SLARouter,
        SLARouterBase,
    )
    assert issubclass(SLARouter, SLARouterBase)


def test_governance_engine_imports():
    """GovernanceEngine and related classes can be imported."""
    from tokenpak.enterprise.governance import (
        GovernanceEngine,
        GovernanceEngineBase,
    )
    assert issubclass(GovernanceEngine, GovernanceEngineBase)


# ---------------------------------------------------------------------------
# 2. Non-Enterprise tier gets upgrade messages + graceful defaults
# ---------------------------------------------------------------------------


def _mock_non_enterprise():
    """Context manager: patch is_enterprise() to return False."""
    return patch(
        "tokenpak.infrastructure.license_activation.is_enterprise",
        return_value=False,
    )


def _mock_tier(tier: str = "OSS"):
    """Patch get_plan() to return a minimal result with given tier name."""
    from unittest.mock import MagicMock
    mock_result = MagicMock()
    mock_result.tier.value = tier.lower()
    return patch(
        "tokenpak.infrastructure.license_activation.get_plan",
        return_value=mock_result,
    )


def test_policy_engine_oss_tier_list_policies(capsys):
    """list_policies on OSS tier prints upgrade message and returns []."""
    with _mock_non_enterprise(), _mock_tier("oss"):
        from tokenpak.enterprise.policy import PolicyEngine
        engine = PolicyEngine()
        engine._delegate = None  # force stub path

        result = engine.list_policies()

    captured = capsys.readouterr()
    assert result == []
    assert "Enterprise" in captured.out
    assert "tokenpak.dev/enterprise" in captured.out


def test_policy_engine_oss_tier_enforce_allows(capsys):
    """enforce() on OSS tier allows everything (no policy engine active)."""
    with _mock_non_enterprise(), _mock_tier("oss"):
        from tokenpak.enterprise.policy import PolicyEngine
        engine = PolicyEngine()
        engine._delegate = None

        result = engine.enforce("openai/gpt-4o")

    assert result.allowed is True
    assert "non-Enterprise" in result.reason


def test_sla_router_oss_tier_list_profiles(capsys):
    """list_profiles on OSS tier prints upgrade message and returns []."""
    with _mock_non_enterprise(), _mock_tier("oss"):
        from tokenpak.enterprise.sla import SLARouter
        router = SLARouter()
        router._delegate = None

        result = router.list_profiles()

    captured = capsys.readouterr()
    assert result == []
    assert "Enterprise" in captured.out


def test_sla_router_oss_tier_resolve_passthrough():
    """resolve() on OSS tier passes model through unchanged."""
    with _mock_non_enterprise(), _mock_tier("oss"):
        from tokenpak.enterprise.sla import SLARouter
        router = SLARouter()
        router._delegate = None

        decision = router.resolve("openai/gpt-4o")

    assert decision.original_model == "openai/gpt-4o"
    assert decision.resolved_model == "openai/gpt-4o"
    assert "non-Enterprise" in decision.reason


def test_governance_engine_oss_tier_classify(capsys):
    """classify() on OSS tier prints upgrade message and returns default."""
    with _mock_non_enterprise(), _mock_tier("oss"):
        from tokenpak.enterprise.governance import DataClass, GovernanceEngine
        engine = GovernanceEngine()
        engine._delegate = None

        result = engine.classify("some text")

    captured = capsys.readouterr()
    assert result.data_class == DataClass.INTERNAL
    assert "Enterprise" in captured.out


# ---------------------------------------------------------------------------
# 3. CLI command modules are importable and run() is callable
# ---------------------------------------------------------------------------


def test_cli_policy_module_importable():
    from tokenpak.cli.commands import policy
    assert callable(getattr(policy, "run", None))


def test_cli_sla_module_importable():
    from tokenpak.cli.commands import sla
    assert callable(getattr(sla, "run", None))


def test_cli_compliance_module_importable():
    from tokenpak.cli.commands import compliance
    assert callable(getattr(compliance, "run", None))


def test_cli_policy_show_no_license(capsys):
    """tokenpak policy show on OSS prints upgrade and exits."""
    with _mock_non_enterprise(), _mock_tier("oss"):
        from tokenpak.cli.commands.policy import run
        with pytest.raises(SystemExit) as exc_info:
            run(["show"])
        assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert "Enterprise" in captured.out


def test_cli_sla_status_no_license(capsys):
    """tokenpak sla status on OSS prints upgrade and exits."""
    with _mock_non_enterprise(), _mock_tier("oss"):
        from tokenpak.cli.commands.sla import run
        with pytest.raises(SystemExit) as exc_info:
            run(["status"])
        assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert "Enterprise" in captured.out


def test_cli_compliance_report_no_license(capsys):
    """tokenpak compliance report soc2 on OSS prints upgrade and exits."""
    with _mock_non_enterprise(), _mock_tier("oss"):
        from tokenpak.cli.commands.compliance import run
        with pytest.raises(SystemExit) as exc_info:
            run(["report", "soc2"])
        assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert "Enterprise" in captured.out
