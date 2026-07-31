# SPDX-License-Identifier: Apache-2.0
"""TOKENPAK_HOME must isolate writes, and preview --json must stay JSON.

Both defects were found by an adversarial review on 2026-07-26. The first
caused real data loss: `TOKENPAK_HOME=/tmp/x tokenpak setup --yes` wrote
nothing to /tmp/x and overwrote the operator's live ~/.tokenpak/config.yaml
with no backup. The file-boundary standard names TOKENPAK_HOME an operator
override for sandboxes, so honouring a config found outside it is a
conformance violation, not a design choice.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(args, home, tp_home=None):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TERM": "dumb",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
    }
    # Pin a port nothing listens on. Without this, `setup` reaches its
    # start-the-proxy branch and spawns a detached process on the default 8766;
    # whether it does depends on whether something already holds that port, so
    # the test's behaviour would vary with ambient machine state.
    env.setdefault("TOKENPAK_PORT", "8899")
    if tp_home is not None:
        env["TOKENPAK_HOME"] = str(tp_home)
    return subprocess.run(
        [sys.executable, "-m", "tokenpak", *args], capture_output=True, text=True, env=env
    )


class TestHomeOverrideIsolatesWrites:
    def test_setup_writes_inside_the_override_only(self):
        """setup must not retarget its write to a config found outside the override."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            legacy = home / ".tokenpak"
            legacy.mkdir(parents=True)
            decoy = legacy / "config.yaml"
            decoy.write_text("proxy:\n  port: 9999\n")
            before = decoy.read_text()

            sandbox = Path(td) / "sandbox"
            _run(["setup", "--yes", "--profile", "balanced"], home, tp_home=sandbox)

            # The live/legacy config is untouched...
            assert decoy.read_text() == before, (
                "setup escaped TOKENPAK_HOME and rewrote real config"
            )
            # ...and the sandbox actually received one.
            assert list(sandbox.glob("config*")), "setup wrote nothing into TOKENPAK_HOME"


class TestPreviewJsonContract:
    def test_input_guard_still_emits_json(self):
        """--json emits JSON on every path, including refusals."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            target = Path(td) / "conv.json"
            target.write_text(json.dumps([{"role": "user", "content": "hi"}]))

            # A path passed positionally is refused — but must refuse in JSON.
            r = _run(["preview", "--json", str(target)], home)
            assert r.returncode != 0
            payload = json.loads(r.stdout)  # must not be prose
            assert payload["state"] == "error"
            assert payload["reason"]


class TestCorruptIsNotMissing:
    def test_unparseable_meta_config_is_corrupt_not_absent(self):
        """A config that exists but will not parse must not report success.

        The first fix here probed with `config_read_path()`, which tests
        existence only and whose candidates include config.json — so it handed
        back the very file that had just failed to parse and announced "no
        legacy meta config", exiting 0. That converted a noisy wrong exit code
        into a silent false pass for `set -e` wrappers.
        """
        from tokenpak.cli.exit_codes import EXIT_CORRUPT_STATE

        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            (home / ".tokenpak").mkdir(parents=True)
            (home / ".tokenpak" / "config.json").write_text('{"broken": [')
            r = _run(["config", "validate"], home)
            assert r.returncode == EXIT_CORRUPT_STATE
            assert "corrupt config, not a missing one" in r.stdout


class TestOverrideResistsSymlinkEscape:
    def test_symlinked_write_target_is_refused(self):
        """resolve() the write target, not just the read candidate.

        Detecting the escape on the config we *find* while writing through a
        target we never resolved left the hole open: open(path, "w") follows
        symlinks, so a link planted inside the override still wrote through to
        the real file.
        """
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            real = home / "real-config.yaml"
            real.write_text("proxy:\n  port: 9999\n")
            before = real.read_text()

            sandbox = Path(td) / "sb"
            sandbox.mkdir()
            (sandbox / "config.yaml").symlink_to(real)

            r = _run(["setup", "--yes", "--profile", "balanced"], home, tp_home=sandbox)
            assert r.returncode != 0
            assert "Refusing to write outside TOKENPAK_HOME" in r.stdout
            assert real.read_text() == before, "symlink write-through still clobbered the target"


class TestClearPidRespectsOverride:
    def test_override_does_not_sweep_real_home_pid(self):
        """A sandboxed run must not orphan the operator's running proxy.

        clear_pid() swept every candidate home unconditionally, and setup
        reaches it on the "proxy did not start" branch — the branch a sandbox is
        most likely to take.
        """
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            (home / ".tokenpak").mkdir(parents=True)
            live_pid = home / ".tokenpak" / "proxy.pid"
            live_pid.write_text("99999")

            sandbox = Path(td) / "sb"
            sandbox.mkdir()
            code = "from tokenpak.core.runtime.lifecycle import clear_pid; clear_pid()"
            subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                env={
                    "HOME": str(home),
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                    "TOKENPAK_HOME": str(sandbox),
                },
            )
            assert live_pid.exists(), "clear_pid deleted a PID file outside TOKENPAK_HOME"
