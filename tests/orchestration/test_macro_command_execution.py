"""Injection-safety coverage for premade macro command execution.

Premade macro steps historically ran through the host shell
(``subprocess.run(cmd, shell=True)``). These tests pin the governed execution
model for that surface: fixed ``tokenpak ...`` step invocations run as an argv
vector with ``shell=False`` and pass payload-like content literally (no shell
interpretation).

The general macro engine's step execution is a separate surface with its own
hardening track and is intentionally not covered here.
"""

from __future__ import annotations

import sys

import pytest

_ECHO_ARG = [sys.executable, "-c", "import sys; print(sys.argv[1])"]


def _payload(sentinel) -> str:
    return f"; touch {sentinel} ; echo owned & whoami | cat"


# ── premade macros: argv, shell=False ─────────────────────────────────────────


def test_premade_step_runs_as_argv(monkeypatch):
    from tokenpak.orchestration import commands as commands_mod
    from tokenpak.orchestration.macros.premade_macros import PremadeMacroRunner

    seen = {}
    real = commands_mod.run_command_action

    def spy(action, **kwargs):
        seen["use_shell"] = action.use_shell
        seen["argv"] = action.argv
        return real(action, **kwargs)

    monkeypatch.setattr(commands_mod, "run_command_action", spy)
    PremadeMacroRunner()._run_step({"name": "n", "label": "l", "cmd": "tokenpak status --json"})
    assert seen["use_shell"] is False
    assert seen["argv"] == ("tokenpak", "status", "--json")


def test_premade_step_payload_is_literal(tmp_path):
    from tokenpak.orchestration.macros.premade_macros import PremadeMacroRunner

    sentinel = tmp_path / "PWNED"
    payload = _payload(sentinel)
    # A crafted step proves the runner does not shell-interpret its command.
    import shlex

    cmd = " ".join(shlex.quote(p) for p in [*_ECHO_ARG, payload])
    out = PremadeMacroRunner()._run_step({"name": "n", "label": "l", "cmd": cmd})
    assert out["returncode"] == 0
    assert payload in out["output"]
    assert not sentinel.exists()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
