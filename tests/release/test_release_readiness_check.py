"""Static contract checks for the offline release-readiness tool (no builds, no keys)."""

import importlib.util
from pathlib import Path

_p = Path(__file__).resolve().parents[2] / "scripts" / "release_readiness_check.py"
_s = importlib.util.spec_from_file_location("rrc", _p)
rrc = importlib.util.module_from_spec(_s)
_s.loader.exec_module(rrc)


def test_child_env_is_isolated(tmp_path):
    env = rrc.clean_env(tmp_path)
    assert env["HOME"] == str(tmp_path) and env["TMPDIR"].startswith(str(tmp_path))
    assert env["PIP_NO_INDEX"] == "1"
    assert not any("TOKEN" in k and k != "TOKENPAK_HOME" for k in env)


def test_pins_are_full_commit_shas():
    for spec in rrc.DEFAULTS.values():
        assert len(spec.rsplit("@", 1)[1]) == 40


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


def test_default_pins_are_portable_and_fail_closed_without_root(tmp_path):
    import pytest

    for spec in [*rrc.DEFAULTS.values(), rrc.SERVER]:
        assert not Path(spec.rsplit("@", 1)[0]).is_absolute()
    with pytest.raises(SystemExit):
        rrc.resolve_spec(rrc.DEFAULTS["oss"], None)
    repo, sha = rrc.DEFAULTS["oss"].rsplit("@", 1)
    assert rrc.resolve_spec(rrc.DEFAULTS["oss"], tmp_path) == f"{tmp_path / repo}@{sha}"
    assert rrc.resolve_spec(f"/abs/repo@{sha}", None) == f"/abs/repo@{sha}"


def test_no_hardcoded_home_paths():
    assert "/home/" not in _p.read_text()
