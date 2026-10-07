"""Static contract checks for the offline release-readiness tool (no builds, no keys)."""

import argparse
import importlib.util
import re
from pathlib import Path

import pytest

_p = Path(__file__).resolve().parents[2] / "scripts" / "release_readiness_check.py"
_s = importlib.util.spec_from_file_location("rrc", _p)
rrc = importlib.util.module_from_spec(_s)
_s.loader.exec_module(rrc)

SHA = "0123456789abcdef0123456789abcdef01234567"
ARGS = [
    "--out",
    "out",
    "--oss",
    f"repo-a@{SHA}",
    "--paid",
    f"repo-b@{SHA}",
    "--prior-oss",
    f"repo-c@{SHA}",
    "--server",
    f"repo-d@{SHA}",
]


def test_child_env_is_isolated(tmp_path):
    env = rrc.clean_env(tmp_path)
    assert env["HOME"] == str(tmp_path) and env["TMPDIR"].startswith(str(tmp_path))
    assert env["PIP_NO_INDEX"] == "1"
    assert not any("TOKEN" in k and k != "TOKENPAK_HOME" for k in env)


def test_pins_must_name_a_full_commit_id():
    assert rrc.pin(f"repo@{SHA}") == f"repo@{SHA}"
    assert rrc.pin(f"/abs/repo@{SHA.upper()}") == f"/abs/repo@{SHA.upper()}"
    for bad in ("repo", f"@{SHA}", "repo@main", "repo@abc123", f"repo@{SHA[:-1]}", f"repo@{SHA}0"):
        with pytest.raises(argparse.ArgumentTypeError):
            rrc.pin(bad)


@pytest.mark.parametrize("flag", ["--out", "--oss", "--paid", "--prior-oss", "--server"])
def test_every_input_is_a_required_argument(flag):
    args = list(ARGS)
    i = args.index(flag)
    del args[i : i + 2]
    with pytest.raises(SystemExit) as exc:
        rrc.build_parser().parse_args(args)
    assert exc.value.code == 2


def test_all_inputs_given_parses_and_nothing_is_defaulted():
    ns = rrc.build_parser().parse_args(ARGS)
    assert (ns.oss, ns.paid, ns.prior_oss, ns.server) == (
        f"repo-a@{SHA}",
        f"repo-b@{SHA}",
        f"repo-c@{SHA}",
        f"repo-d@{SHA}",
    )
    assert ns.worktree_root is None
    assert not hasattr(rrc, "DEFAULTS") and not hasattr(rrc, "SERVER")


def test_a_malformed_pin_is_a_usage_error():
    args = list(ARGS)
    args[args.index("--oss") + 1] = "repo@main"
    with pytest.raises(SystemExit) as exc:
        rrc.build_parser().parse_args(args)
    assert exc.value.code == 2


def test_tool_never_touches_key_apis():
    src = _p.read_text()
    for banned in ("create_app", "CryptoManager"):
        assert banned not in src
    # generate_private_key may appear only as a guard override, never as a call.
    assert "generate_private_key(" not in src


def test_completion_is_not_release_authorization():
    r = rrc.release_semantics(
        {"dependencies_resolvable_offline": True, "pip_check_clean": True, "cli_ok": True}
    )
    assert r["release_ready"] is False and "deployment_gates_unverified" in r["release_blockers"]
    r = rrc.release_semantics({"cli_ok": False})
    assert "cli_ok" in r["release_blockers"] and r["packaging_check_completed"] is True


def test_relative_pins_fail_closed_without_a_root(tmp_path):
    with pytest.raises(SystemExit):
        rrc.resolve_spec(f"repo@{SHA}", None)
    assert rrc.resolve_spec(f"repo@{SHA}", tmp_path) == f"{tmp_path / 'repo'}@{SHA}"
    assert rrc.resolve_spec(f"/abs/repo@{SHA}", None) == f"/abs/repo@{SHA}"


def test_no_hardcoded_home_paths_or_pinned_commits():
    src = _p.read_text()
    assert "/home/" not in src
    assert not re.search(r"\b[0-9a-fA-F]{40}\b", src)
