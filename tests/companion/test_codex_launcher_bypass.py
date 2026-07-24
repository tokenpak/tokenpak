# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the Codex launcher bypass-flag opt-in.

Covers env-var opt-in for ``--dangerously-bypass-approvals-and-sandbox``.
Default `tokenpak codex` MUST remain vanilla/pass-through; the env var
is the only opt-in surface in this pass (future UX = permission tiers).
"""

from __future__ import annotations

from tokenpak.companion.codex import launcher

BYPASS = "--dangerously-bypass-approvals-and-sandbox"
ENV_VAR = "TOKENPAK_CODEX_BYPASS_APPROVALS_AND_SANDBOX"


def test_default_invocation_does_not_inject_bypass_flag():
    result = launcher._maybe_inject_bypass_flag(["--foo", "bar"], env={})
    assert BYPASS not in result
    assert result == ["--foo", "bar"]


def test_env_var_unset_does_not_inject_bypass_flag():
    result = launcher._maybe_inject_bypass_flag([], env={"OTHER": "1"})
    assert result == []


def test_env_var_falsy_does_not_inject_bypass_flag():
    for falsy in ["", "0", "false", "no", "off", "FALSE"]:
        result = launcher._maybe_inject_bypass_flag(["-x"], env={ENV_VAR: falsy})
        assert BYPASS not in result, f"unexpected injection for falsy={falsy!r}"


def test_env_var_truthy_injects_bypass_flag():
    for truthy in ["1", "true", "yes", "TRUE", "Yes", " 1 "]:
        result = launcher._maybe_inject_bypass_flag(["-x"], env={ENV_VAR: truthy})
        assert result.count(BYPASS) == 1, f"expected one injection for {truthy!r}"
        assert "-x" in result


def test_user_provided_flag_preserved_without_duplication():
    result = launcher._maybe_inject_bypass_flag([BYPASS, "--foo"], env={ENV_VAR: "1"})
    assert result.count(BYPASS) == 1
    assert "--foo" in result


def test_helper_does_not_mutate_input_list():
    original = ["-x", "--y"]
    launcher._maybe_inject_bypass_flag(original, env={ENV_VAR: "1"})
    assert original == ["-x", "--y"]
