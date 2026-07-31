"""Regression tests for the three HIGH findings on the path-isolation change.

All three were proven by execution in review, not by inspection, so they are
pinned the same way.

- HIGH-1: guarding only `config_file` still destroyed the live config through
  `config_dir`, whose `proxy-startup.log` is opened "w".
- HIGH-2: `cmd_init` had no guard at all and printed a success banner naming the
  sandbox while overwriting the real config.
- HIGH-3: `config validate` exited 0 with a "✓" on a config that `start` and
  `status` both refuse to parse — fail-open against merge-base.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tokenpak._cli_core import _home_override_escape

# Import the real constant — an earlier revision hand-copied it as 3, which is
# EXIT_NOT_CONFIGURED. The test then failed CI against a CORRECT implementation
# (exit 8) while its message blamed the code. A hand-written copy of a constant
# is a fixture, and a test against a hand-written fixture verifies the fixture.
from tokenpak.cli.exit_codes import EXIT_CORRUPT_STATE


class TestHomeOverrideGuard:
    """The guard must cover every write target, not just the config file."""

    def test_path_inside_override_is_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
        assert _home_override_escape(tmp_path / "config.yaml") is None

    def test_unset_override_guards_nothing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TOKENPAK_HOME", raising=False)
        assert _home_override_escape(Path("/etc/passwd")) is None

    def test_symlinked_file_escape_is_refused(self, tmp_path, monkeypatch):
        """HIGH-1/2 root cause: resolve() must be what decides, not the text."""
        sandbox, real = tmp_path / "sandbox", tmp_path / "real"
        sandbox.mkdir()
        real.mkdir()
        live = real / "config.yaml"
        live.write_text("sentinel: true\n")
        (sandbox / "config.yaml").symlink_to(live)

        monkeypatch.setenv("TOKENPAK_HOME", str(sandbox))
        msg = _home_override_escape(sandbox / "config.yaml")
        assert msg is not None and "Refusing to write outside" in msg

    def test_symlinked_DIRECTORY_escape_is_refused(self, tmp_path, monkeypatch):
        """HIGH-1 exactly: the escape was a directory, not the config file.

        `startup_log = config_dir / "proxy-startup.log"` is opened "w", so a
        guard that inspected only `config_file` let the startup banner land in
        the live config (51 -> 1041 bytes, no backup).
        """
        sandbox, real = tmp_path / "sandbox", tmp_path / "real"
        sandbox.mkdir()
        real.mkdir()
        (sandbox / "home").symlink_to(real, target_is_directory=True)

        monkeypatch.setenv("TOKENPAK_HOME", str(sandbox))
        assert _home_override_escape(sandbox / "home") is not None

    def test_one_bad_path_among_good_ones_is_refused(self, tmp_path, monkeypatch):
        """A command passes all of its write targets; any one escaping refuses."""
        sandbox, real = tmp_path / "sandbox", tmp_path / "real"
        sandbox.mkdir()
        real.mkdir()
        (sandbox / ".env").symlink_to(real / ".env")

        monkeypatch.setenv("TOKENPAK_HOME", str(sandbox))
        assert (
            _home_override_escape(
                sandbox,
                sandbox / "config.yaml",
                sandbox / ".env",
            )
            is not None
        )

    def test_symlink_loop_refuses_instead_of_raising(self, tmp_path, monkeypatch):
        """LOW-4: resolve() raises RuntimeError on a loop; it was uncaught."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        a, b = sandbox / "a", sandbox / "b"
        a.symlink_to(b)
        b.symlink_to(a)

        monkeypatch.setenv("TOKENPAK_HOME", str(sandbox))
        assert _home_override_escape(a) is not None  # refusal, not a traceback


class TestConfigValidateIsNotFailOpen:
    """HIGH-3 — the true regression against merge-base."""

    @pytest.mark.skipif(sys.platform == "win32", reason="pty/exec shape differs")
    def test_corrupt_config_does_not_exit_zero(self, tmp_path):
        home = tmp_path / "home"
        (home / ".tokenpak").mkdir(parents=True)
        # Unparseable YAML: an unclosed flow mapping.
        (home / ".tokenpak" / "config.yaml").write_text("proxy: {unclosed\n")

        env = dict(os.environ, HOME=str(home), TOKENPAK_HOME=str(home / ".tokenpak"))
        env.pop("TOKENPAK_CONFIG", None)
        proc = subprocess.run(
            [sys.executable, "-m", "tokenpak", "config", "validate"],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        combined = proc.stdout + proc.stderr

        # Assert the EXACT code, never merely "non-zero". An earlier draft of
        # this test asserted `returncode != 0` and passed in an environment
        # missing an unrelated dependency, where the CLI exits 120 on an import
        # error before reaching the config at all — green for a reason that had
        # nothing to do with the defect. A crash is not a refusal.
        assert "ModuleNotFoundError" not in combined, (
            "the CLI could not start, so this test proves nothing about "
            f"config validate. Fix the environment, do not relax the assert.\n{combined}"
        )
        assert proc.returncode == EXIT_CORRUPT_STATE, (
            f"expected EXIT_CORRUPT_STATE ({EXIT_CORRUPT_STATE}) on a corrupt "
            f"config, got {proc.returncode}.\n{combined}"
        )
        assert "✓" not in proc.stdout.split("\n")[0], (
            f"claimed success on a corrupt config:\n{combined}"
        )
