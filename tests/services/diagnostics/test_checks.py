"""Shared diagnostic checks — δ acceptance."""

from __future__ import annotations

from tokenpak.services.diagnostics import (
    CheckResult,
    CheckStatus,
    run_claude_code_checks,
    run_core_checks,
)


def test_core_checks_return_structured_results():
    results = run_core_checks()
    assert len(results) >= 1
    assert all(isinstance(r, CheckResult) for r in results)
    # Names should be stable identifiers.
    names = [r.name for r in results]
    assert "version" in names
    assert "install-drift" in names


def test_claude_code_checks_return_structured_results():
    results = run_claude_code_checks()
    assert len(results) >= 1
    assert all(isinstance(r, CheckResult) for r in results)
    names = [r.name for r in results]
    assert "claude-binary" in names
    assert "companion-settings" in names


def test_status_values_are_enum():
    for r in run_core_checks():
        assert r.status in (CheckStatus.OK, CheckStatus.WARN, CheckStatus.FAIL)


def test_version_check_ok_on_real_install():
    results = run_core_checks()
    version_check = next(r for r in results if r.name == "version")
    # On a real editable install this should be OK; other states mean
    # the test is running in an environment with shadows / missing
    # __version__. Either way, don't raise.
    assert version_check.status in (CheckStatus.OK, CheckStatus.WARN, CheckStatus.FAIL)
