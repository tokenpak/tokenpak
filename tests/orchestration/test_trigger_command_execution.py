"""Injection-safety coverage for trigger command execution (CP-02).

Trigger automation historically ran config-provided command strings through the
host shell (``subprocess.run(cmd, shell=True)``), which is fragile on Windows and
a quoting/injection hazard. These tests pin the governed command-action model:

* normal TokenPak actions run as an argv vector with ``shell=False``;
* the only ``shell=True`` path is an explicit ``shell:``-prefixed action;
* shell metacharacters and event-payload data are passed **literally** and do not
  execute as shell syntax;
* the trigger daemon and the macro hook ``fire`` path both honor this and still
  record exit code + output.
"""

from __future__ import annotations

import shlex
import sys
import warnings

import pytest

from tokenpak.orchestration.commands import (
    SHELL_PREFIX,
    CommandAction,
    CommandResult,
    argv_action,
    parse_trigger_action,
    run_command_action,
)

# A program-name + script that echoes its first real argument back verbatim, used
# to prove a payload reached the process as one literal argv element.
_ECHO_ARG = [sys.executable, "-c", "import sys; print(sys.argv[1])"]


def _payload(sentinel: str) -> str:
    """POSIX shell-injection payload: if any stage shell-interprets it, the
    ``touch`` sub-command creates *sentinel*. ``shell=False`` keeps it literal."""
    return f"; touch {sentinel} ; echo owned & whoami | cat"


def _quote(parts) -> str:
    return " ".join(shlex.quote(p) for p in parts)


# ── parse_trigger_action ──────────────────────────────────────────────────────


def test_subcommand_is_argv_with_tokenpak_prefix():
    action = parse_trigger_action("status", warn=False)
    assert action.use_shell is False
    assert action.argv == ("tokenpak", "status")


def test_external_command_not_prefixed():
    action = parse_trigger_action("git status --short", warn=False)
    assert action.use_shell is False
    assert action.argv == ("git", "status", "--short")


def test_path_action_runs_as_argv_without_prefix():
    for raw in ("/usr/bin/backup.sh", "./run.sh --now", "~/bin/job"):
        action = parse_trigger_action(raw, warn=False)
        assert action.use_shell is False
        assert action.argv[0] == shlex.split(raw)[0]


def test_quoted_argument_stays_one_literal_token():
    action = parse_trigger_action('status --label "my report"', warn=False)
    assert action.argv == ("tokenpak", "status", "--label", "my report")


def test_metacharacters_are_literal_argv_not_shell():
    # The whole point: ';' / '&' / '$' do not chain commands — they are argv data.
    action = parse_trigger_action("status; rm -rf ~ && echo $HOME", warn=False)
    assert action.use_shell is False
    # First token 'status;' is not the bare subcommand 'status', so no prefix and
    # nothing is shell-interpreted.
    assert action.argv[0] == "status;"
    assert "&&" in action.argv


def test_metacharacters_emit_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        parse_trigger_action("status | tee log", warn=True)
    assert any("metacharacters" in str(w.message) for w in caught)


def test_shell_prefix_is_opt_in_shell_path():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        action = parse_trigger_action(f"{SHELL_PREFIX}tokenpak status | tee log", warn=True)
    assert action.use_shell is True
    assert action.shell_command == "tokenpak status | tee log"
    assert any("legacy shell" in str(w.message) for w in caught)


# ── run_command_action ────────────────────────────────────────────────────────


def test_argv_payload_is_passed_literally(tmp_path):
    sentinel = tmp_path / "PWNED"
    payload = _payload(str(sentinel))
    result = run_command_action(argv_action([*_ECHO_ARG, payload]), timeout=20)
    assert result.returncode == 0
    assert result.output == payload  # echoed back verbatim
    assert not sentinel.exists()  # the ';touch' never executed -> no shell


def test_dry_run_executes_nothing(tmp_path):
    sentinel = tmp_path / "created"
    action = argv_action([sys.executable, "-c", f"open({str(sentinel)!r}, 'w').close()"])
    result = run_command_action(action, dry_run=True)
    assert result.dry_run is True
    assert result.returncode == 0
    assert not sentinel.exists()


def test_missing_executable_is_structured_not_raised():
    result = run_command_action(argv_action(["tokenpak-no-such-binary-xyz"]), timeout=10)
    assert result.returncode == 127
    assert result.success is False


def test_shell_optin_actually_uses_shell():
    # 'echo' as a shell builtin proves shell=True is honored for opt-in actions.
    result = run_command_action(parse_trigger_action("shell:echo alpha", warn=False), timeout=10)
    assert result.returncode == 0
    assert "alpha" in result.output


def test_env_is_passed_through():
    action = argv_action([sys.executable, "-c", "import os; print(os.environ['TP_TEST_VAR'])"])
    result = run_command_action(action, env={"TP_TEST_VAR": "xyz"}, timeout=10)
    assert result.output == "xyz"


def test_command_action_display_round_trips():
    assert CommandAction(argv=("tokenpak", "status")).display == "tokenpak status"
    assert CommandAction(shell_command="echo hi", use_shell=True).display == "echo hi"


# ── trigger daemon ────────────────────────────────────────────────────────────


class _RecordingStore:
    def __init__(self):
        self.fired = []

    def log_fire(self, trigger, exit_code, output):
        self.fired.append((exit_code, output))


def test_daemon_run_action_routes_through_governed_model(monkeypatch):
    from tokenpak.orchestration import commands as commands_mod
    from tokenpak.orchestration.triggers import daemon as daemon_mod
    from tokenpak.orchestration.triggers.store import Trigger

    seen = {}

    def recorder(action, **kwargs):
        seen["action"] = action
        seen["use_shell"] = parse_trigger_action(action, warn=False).use_shell
        return CommandResult(returncode=0, output="ok")

    # daemon imports run_trigger_action from the commands module at call time,
    # so patch it at the source.
    monkeypatch.setattr(commands_mod, "run_trigger_action", recorder)
    store = _RecordingStore()
    daemon_mod._run_action(Trigger(id="t1", event="timer:1s", action="version"), store)

    assert seen["action"] == "version"
    assert seen["use_shell"] is False  # default path is shell-free
    assert store.fired == [(0, "ok")]


def test_daemon_injection_action_does_not_shell_execute(tmp_path):
    from tokenpak.orchestration.triggers import daemon as daemon_mod
    from tokenpak.orchestration.triggers.store import Trigger

    sentinel = tmp_path / "PWNED"
    payload = _payload(str(sentinel))
    action = _quote([*_ECHO_ARG, payload])
    store = _RecordingStore()
    daemon_mod._run_action(Trigger(id="t2", event="timer:1s", action=action), store)

    assert store.fired and store.fired[0][0] == 0
    assert payload in store.fired[0][1]
    assert not sentinel.exists()


# ── macro hook fire (event-data substitution) ─────────────────────────────────


def _registry(tmp_path):
    from tokenpak.orchestration.macros.hooks import TriggerRegistry

    return TriggerRegistry(
        triggers_path=tmp_path / "triggers.json",
        log_path=tmp_path / "log.json",
    )


def test_hook_fire_passes_event_data_literally(tmp_path):
    """$EVENT_DATA substitution must land in a single argv slot, never the shell."""
    reg = _registry(tmp_path)
    sentinel = tmp_path / "PWNED"
    action = _quote(_ECHO_ARG) + " $EVENT_DATA"
    reg.add(event_type="git:push", pattern="*", action=action)

    hostile = f"; touch {sentinel} ; echo owned & whoami"
    entries = reg.fire(event_type="git:push", event_data=hostile, dry_run=False)

    assert len(entries) == 1
    assert entries[0].success is True
    assert entries[0].output.strip() == hostile  # passed through literally
    assert not sentinel.exists()  # no shell interpretation happened


def test_hook_fire_dry_run_does_not_execute(tmp_path):
    reg = _registry(tmp_path)
    sentinel = tmp_path / "created"
    action = f"{sys.executable} -c " + shlex.quote(f"open({str(sentinel)!r},'w').close()")
    reg.add(event_type="git:push", pattern="*", action=action)
    entries = reg.fire(event_type="git:push", event_data="x", dry_run=True)
    assert entries[0].dry_run is True
    assert not sentinel.exists()


def test_hook_fire_exposes_event_vars_in_env(tmp_path):
    reg = _registry(tmp_path)
    action = f"{sys.executable} -c " + shlex.quote(
        "import os; print(os.environ['TOKENPAK_EVENT_TYPE'], os.environ['TOKENPAK_EVENT_DATA'])"
    )
    reg.add(event_type="git:push", pattern="*", action=action)
    entries = reg.fire(event_type="git:push", event_data="payload-123", dry_run=False)
    assert entries[0].output.strip() == "git:push payload-123"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
