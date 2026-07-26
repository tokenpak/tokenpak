# SPDX-License-Identifier: Apache-2.0
"""Persisted entitlement state must be readable only by its owner.

`save_license` wrote `license.json` at the process umask — 0664 on a default
Linux setup — into a home created by a bare `mkdir`, which left the directory
0775. Activating on a fresh install therefore produced a world-readable
license inside a group-writable home. The license carries the entitlement
grant and its signature.

The same class applied to `budget.db`, which is a record of what the user
spent.

These assert the actual mode bits on disk, in an isolated HOME.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_WRITE_LICENSE = """
from tokenpak import licensing as L
L.save_license(L.License(tier=L.TIER_PRO, status="active", key="TESTKEY-0000"))
print(L._license_path())
"""


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _run(home: Path, code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        timeout=180,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return result.stdout.strip().splitlines()[-1]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_license_file_is_owner_only(tmp_path: Path) -> None:
    written = Path(_run(tmp_path, _WRITE_LICENSE))

    assert written.exists(), f"license was not written to {written}"
    mode = _mode(written)
    assert mode == 0o600, f"license.json is {oct(mode)}, expected 0o600"
    assert not mode & stat.S_IRGRP, "license is group-readable"
    assert not mode & stat.S_IROTH, "license is world-readable"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_the_home_holding_the_license_is_owner_only(tmp_path: Path) -> None:
    """A 0600 file inside a 0777 directory is not protected state."""
    written = Path(_run(tmp_path, _WRITE_LICENSE))

    mode = _mode(written.parent)
    assert mode == 0o700, f"{written.parent} is {oct(mode)}, expected 0o700"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_no_world_readable_temp_file_is_left_behind(tmp_path: Path) -> None:
    """The atomic write must not leave a readable .tmp on failure paths."""
    written = Path(_run(tmp_path, _WRITE_LICENSE))

    leftovers = list(written.parent.glob("license.json*.tmp"))
    assert not leftovers, f"temp files left behind: {leftovers}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_budget_store_is_owner_only(tmp_path: Path) -> None:
    tpk = tmp_path / ".tpk"
    tpk.mkdir(parents=True)
    (tpk / ".seen_intro").touch()

    subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", "cost"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={
            "HOME": str(tmp_path),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "NO_COLOR": "1",
            "TERM": "dumb",
            "TOKENPAK_PORT": "8899",
        },
        timeout=180,
    )

    db = tpk / "budget.db"
    if not db.exists():  # nothing created it; nothing to protect
        pytest.skip("budget store was not created by this path")
    mode = _mode(db)
    assert not mode & stat.S_IROTH, f"budget.db is world-readable ({oct(mode)})"


# ---------------------------------------------------------------------------
# Upgrading an existing install must not lose the entitlement.
#
# `save_license` resolves the destination before calling `ensure_home()`.
# While `ensure_home()` created the canonical directory unconditionally, that
# ordering wrote the license to the legacy home and then moved resolution to
# the canonical one — so the very next read found nothing and a just-activated
# Pro license reported Free. The mode-bit tests above all run against a clean
# HOME, so none of them could see it.
# ---------------------------------------------------------------------------

_ROUNDTRIP = """
from tokenpak import licensing as L
L.save_license(L.License(tier=L.TIER_PRO, status="active", key="TESTKEY-0000"))
got = L.load_license()
print("%s|%s" % (getattr(got, "tier", None), getattr(got, "key", None)))
"""


def _legacy_install(tmp_path: Path) -> Path:
    """A HOME whose state already lives in the pre-canonical directory."""
    legacy = tmp_path / ".tokenpak"
    legacy.mkdir(parents=True)
    (legacy / "config.yaml").write_text("mode: hybrid\n")
    return legacy


def test_activation_survives_on_an_upgraded_install(tmp_path: Path) -> None:
    """Activate on a legacy tree, then read it back in the same process."""
    _legacy_install(tmp_path)

    assert _run(tmp_path, _ROUNDTRIP) == "pro|TESTKEY-0000"


def test_activation_survives_across_processes_on_an_upgraded_install(
    tmp_path: Path,
) -> None:
    """The failure was cross-invocation: activate, exit, then read again."""
    _legacy_install(tmp_path)
    _run(tmp_path, _ROUNDTRIP)

    read_back = _run(
        tmp_path,
        "from tokenpak import licensing as L\n"
        "got = L.load_license()\n"
        'print("%s|%s" % (getattr(got, "tier", None), getattr(got, "key", None)))\n',
    )
    assert read_back == "pro|TESTKEY-0000"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_the_home_that_holds_the_license_is_hardened_on_an_upgraded_install(
    tmp_path: Path,
) -> None:
    """Hardening the canonical home is no use when the license is elsewhere."""
    legacy = _legacy_install(tmp_path)
    legacy.chmod(0o775)

    _run(tmp_path, _WRITE_LICENSE)

    assert (legacy / "license.json").exists(), "license did not land in the active home"
    assert _mode(legacy) == 0o700, f"{legacy} is {oct(_mode(legacy))}, expected 0o700"
