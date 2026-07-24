# SPDX-License-Identifier: Apache-2.0
"""Tests for the SAFE centralized-env CLI surface.

Covers ``config doctor`` (read-only diagnostics), the ``config env`` provenance
view (masked by default), and the ``.env.example`` scaffold stub. All tests use
a temp TokenPak home via ``TOKENPAK_HOME``; none touch the real home, none use
the network, and no fixture contains a real secret value (placeholders only).

Read-only invariant tests snapshot the fixture home before/after a doctor run
and assert nothing changed.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path

import pytest

from tokenpak.cli.commands import config_env


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """A temp TokenPak home wired via TOKENPAK_HOME; clears TOKENPAK_* env."""
    home = tmp_path / "tpk-home"
    monkeypatch.setenv("TOKENPAK_HOME", str(home))
    # Scrub any inherited TokenPak/provider env so tests are hermetic.
    for key in list(os.environ):
        if key.startswith("TOKENPAK_") and key != "TOKENPAK_HOME":
            monkeypatch.delenv(key, raising=False)
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    return home


def _snapshot(root: Path):
    """Snapshot {relpath: (mode, mtime_ns)} for every entry under root."""
    snap = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        st = p.stat()
        snap[str(p.relative_to(root))] = (st.st_mode, st.st_mtime_ns)
    return snap


# --- secret classification + masking ----------------------------------------


def test_secret_class_pattern_based():
    assert config_env.secret_class("ANTHROPIC_API_KEY") == "high"
    assert config_env.secret_class("TOKENPAK_TELEGRAM_BOT_TOKEN") == "high"
    assert config_env.secret_class("SMTP_PASS") == "high"
    assert config_env.secret_class("TOKENPAK_TELEGRAM_CHAT_ID") == "medium"
    assert config_env.secret_class("TOKENPAK_PORT") == "low"
    assert config_env.secret_class("TOKENPAK_LOG_LEVEL") == "low"


def test_mask_value_redacts_secrets_keeps_low():
    assert config_env.mask_value("ANTHROPIC_API_KEY", "sk-secretvalue") == "set"
    assert config_env.mask_value("TOKENPAK_PORT", "8766") == "8766"


# --- config doctor (D1..D8) --------------------------------------------------


def test_doctor_env_rule_ok_empty_home(fake_home):
    # TC-D-01: TOKENPAK_HOME override -> D1 rule=env, exit 0, no files created.
    before = _snapshot(fake_home)
    checks, code = config_env.run_doctor(environ={"TOKENPAK_HOME": str(fake_home)})
    d1 = next(c for c in checks if c.id == "D1")
    assert d1.status == "ok"
    assert "env" in d1.message
    assert code == 0
    assert _snapshot(fake_home) == before  # nothing created


def test_doctor_valid_config_yaml_ok(fake_home):
    # TC-D-02: valid config.yaml -> D2 ok.
    fake_home.mkdir(parents=True)
    (fake_home / "config.yaml").write_text("port: 8766\nmode: hybrid\n", encoding="utf-8")
    checks, code = config_env.run_doctor(environ={"TOKENPAK_HOME": str(fake_home)})
    d2 = next(c for c in checks if c.id == "D2")
    assert d2.status == "ok"
    assert code == 0


def test_doctor_malformed_config_fails_with_exit4(fake_home):
    # TC-D-03: malformed config.yaml -> D2 fail, exit 4.
    pytest.importorskip("yaml")
    fake_home.mkdir(parents=True)
    (fake_home / "config.yaml").write_text("port: : : [unbalanced\n", encoding="utf-8")
    checks, code = config_env.run_doctor(environ={"TOKENPAK_HOME": str(fake_home)})
    d2 = next(c for c in checks if c.id == "D2")
    assert d2.status == "fail"
    assert code == 4


def test_doctor_env_vars_listed_and_secret_not_leaked(fake_home, capsys):
    # TC-D-05: env seeds a port + a secret -> D4 lists both; value never leaks.
    env = {
        "TOKENPAK_HOME": str(fake_home),
        "TOKENPAK_PORT": "9999",
        "ANTHROPIC_API_KEY": "sk-ant-PLACEHOLDER-not-real",
    }
    checks, code = config_env.run_doctor(environ=env)
    d4 = next(c for c in checks if c.id == "D4" and c.check == "env_vars")
    assert "TOKENPAK_PORT" in d4.detail
    assert "ANTHROPIC_API_KEY" in d4.detail
    # render and assert the secret value never reaches stdout
    home_path, rule = config_env._home_rule()
    config_env.render_doctor(
        checks, as_json=False, quiet=False, verbose=True, home=home_path, rule=rule, exit_code=code
    )
    out = capsys.readouterr().out
    assert "PLACEHOLDER-not-real" not in out


def test_doctor_unknown_var_warns_no_fail(fake_home):
    # TC-D-06: unknown TOKENPAK_* -> warn, exit 0.
    env = {"TOKENPAK_HOME": str(fake_home), "TOKENPAK_BOGUS_THING": "1"}
    checks, code = config_env.run_doctor(environ=env)
    warns = [c for c in checks if c.check == "env_var_unknown"]
    assert any("TOKENPAK_BOGUS_THING" in c.message for c in warns)
    assert code == 0


def test_doctor_loose_dotenv_mode_warns_but_does_not_chmod(fake_home):
    # TC-D-09: <home>/.env mode 0644 -> D6 warn; mode unchanged.
    fake_home.mkdir(parents=True)
    env_file = fake_home / ".env"
    env_file.write_text("# placeholder\n", encoding="utf-8")
    env_file.chmod(0o644)
    before_mode = env_file.stat().st_mode & 0o777
    checks, code = config_env.run_doctor(environ={"TOKENPAK_HOME": str(fake_home)})
    d6 = next(c for c in checks if c.id == "D6")
    assert d6.status == "warn"
    assert env_file.stat().st_mode & 0o777 == before_mode  # not chmod'd
    assert code == 0


def test_doctor_json_output_parses(fake_home):
    # TC-D-12: --json output parses + has stable keys.
    fake_home.mkdir(parents=True)
    (fake_home / "config.yaml").write_text("port: 8766\n", encoding="utf-8")
    checks, code = config_env.run_doctor(environ={"TOKENPAK_HOME": str(fake_home)})
    home_path, rule = config_env._home_rule()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        config_env.render_doctor(
            checks,
            as_json=True,
            quiet=False,
            verbose=False,
            home=home_path,
            rule=rule,
            exit_code=code,
        )
    parsed = json.loads(buf.getvalue())
    assert set(parsed) == {"home", "checks", "summary", "exit_code"}
    assert parsed["exit_code"] == code


def test_doctor_read_only_invariant_across_runs(fake_home):
    # TC-D-14: snapshot home before/after; assert identical (load-bearing).
    fake_home.mkdir(parents=True)
    (fake_home / "config.yaml").write_text("port: 8766\n", encoding="utf-8")
    before = _snapshot(fake_home)
    for _ in range(3):
        config_env.run_doctor(environ={"TOKENPAK_HOME": str(fake_home)})
    assert _snapshot(fake_home) == before


# --- config env (provenance + masking) --------------------------------------


def test_env_show_masks_secret_by_default(capsys):
    env = {"TOKENPAK_PORT": "8766", "ANTHROPIC_API_KEY": "sk-PLACEHOLDER"}
    code = config_env.run_env_show(mask=True, as_json=False, environ=env)
    out = capsys.readouterr().out
    assert code == 0
    assert "8766" in out
    assert "sk-PLACEHOLDER" not in out  # secret value masked


def test_env_show_json_provenance(capsys):
    env = {"TOKENPAK_PORT": "8766"}
    config_env.run_env_show(mask=True, as_json=True, environ=env)
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["masked"] is True
    names = {v["name"]: v for v in parsed["vars"]}
    assert names["TOKENPAK_PORT"]["provenance"] == "process_env"
    assert names["TOKENPAK_PORT"]["value"] == "8766"


def test_env_show_empty(capsys):
    code = config_env.run_env_show(mask=True, as_json=False, environ={})
    out = capsys.readouterr().out
    assert code == 0
    assert "No TokenPak/provider env vars" in out


# --- .env.example scaffold stub ----------------------------------------------


def test_env_stub_text_placeholders_only():
    text = config_env.env_stub_text()
    # No line assigns a non-placeholder value to a secret-class key.
    for line in text.splitlines():
        stripped = line.lstrip("# ").strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            val = stripped.split("=", 1)[1].strip()
            if config_env.secret_class(key) == "high":
                assert val.startswith("<") or val == "", f"secret leaked: {line!r}"


def test_write_env_stub_creates_template_only(tmp_path):
    target_dir = tmp_path / "home"
    created, path = config_env.write_env_stub(target_dir)
    assert created is True
    assert path.name == ".env.example"
    assert path.exists()
    # Never writes a real .env.
    assert not (target_dir / ".env").exists()


def test_write_env_stub_idempotent(tmp_path):
    target_dir = tmp_path / "home"
    config_env.write_env_stub(target_dir)
    before = (target_dir / ".env.example").stat().st_mtime_ns
    created, _ = config_env.write_env_stub(target_dir)  # second run
    assert created is False  # no-op without force
    assert (target_dir / ".env.example").stat().st_mtime_ns == before
