# SPDX-License-Identifier: Apache-2.0
"""The license is found in whichever default home holds it.

``tokenpak._paths.home()`` picks one directory for the whole install by what
that directory holds. An install whose license sat in ``~/.tokenpak`` while
newer state (companion data, logs, a Pro daemon directory) had been created in
``~/.tpk`` therefore read as unlicensed although the file was on disk.
``tokenpak._paths.license_file`` resolves the license per file instead, and the
licensing module reads, writes, locks and removes through it.

What this file pins
-------------------
- The resolver table: no homes; canonical only; legacy only; split home (state
  in the canonical home, license only in the legacy one); a license in both
  homes; ``TOKENPAK_HOME`` scoped; ``TOKENPAK_LICENSE_FILE`` explicit.
- Reads and writes of a present license name the same file, so the write lock
  sits beside it.
- Activation over a signed license in the other home is refused and changes
  nothing; the same key is accepted as already active; a pending stub is
  replaced in place.
- ``home()`` and ``write_home()`` are unchanged for every other kind of state.

All state lives in ``tmp_path``; no daemon, network or signing material is
used. The "signed" license carries a dummy signature value: it is only a
payload the OSS side must not destroy, never something verified.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenpak import _paths, licensing
from tokenpak.licensing import (
    TIER_FREE,
    TIER_PRO,
    activate,
    daemon_probe,
    deactivate,
    load_license,
    summary_for_cli,
)

_KEY = "SAME-KEY-0123456789-ABCDEF"
_OTHER_KEY = "OTHER-KEY-0123456789-ABCDEF"


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """An isolated user home with neither TokenPak home created yet."""
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setattr(Path, "home", lambda: user)
    for name in (_paths.ENV_VAR, _paths.LICENSE_FILE_ENV, "TOKENPAK_LICENSE_DEV_SHIM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(daemon_probe, "detect_daemon_state", lambda **_: "unavailable")
    return SimpleNamespace(
        user=user,
        canonical=user / _paths.CANONICAL_DIRNAME,
        legacy=user / _paths.LEGACY_DIRNAME,
        tmp=tmp_path,
    )


def _put(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def _pending_stub(key: str = _KEY) -> dict:
    return {
        "tier": TIER_FREE,
        "key": key,
        "activated_at": "2026-10-01T00:00:00Z",
        "status": "pending_validation",
    }


def _signed_pro(**extra) -> dict:
    """A signed-shaped Pro license, as the Pro package installs one."""
    payload = {
        "plan": "pro",
        "tier": "pro",
        "features": ["model_routing_intelligent"],
        "issued_at": "2026-07-05T22:16:27.843Z",
        "expires_at": None,
        "signature": {
            "algorithm": "ed25519",
            "key_id": "tokenpak-license-v2",
            "value": "SYNTHETIC-NOT-A-REAL-SIGNATURE",
        },
    }
    payload.update(extra)
    return payload


def _hold_other_state(home: Path) -> None:
    """Make *home* hold state that is not a license (what the resolver counts)."""
    (home / "companion").mkdir(parents=True, exist_ok=True)
    (home / "watchdog.log").write_text("", encoding="utf-8")


# -- The resolver table -------------------------------------------------------


def test_fresh_install_has_no_license_and_resolves_canonical(homes):
    assert _paths.license_file() == homes.canonical / "license.json"
    assert _paths.license_file(for_write=True) == homes.canonical / "license.json"
    assert not homes.canonical.exists() and not homes.legacy.exists()  # read-only


def test_canonical_only(homes):
    lic = _put(homes.canonical / "license.json", _signed_pro())
    assert _paths.license_file() == lic
    assert _paths.license_file(for_write=True) == lic


def test_legacy_only(homes):
    lic = _put(homes.legacy / "license.json", _signed_pro())
    assert _paths.license_file() == lic
    assert _paths.license_file(for_write=True) == lic


def test_split_home_reads_and_writes_the_legacy_license(homes):
    """Split home: the canonical home holds state, the license is only in legacy."""
    _hold_other_state(homes.canonical)
    lic = _put(homes.legacy / "license.json", _signed_pro())

    assert _paths.license_file() == lic
    assert _paths.license_file(for_write=True) == lic
    # Everything else is untouched: other state still resolves to the canonical
    # home, for reads and for writes.
    assert _paths.home() == homes.canonical
    assert _paths.write_home() == homes.canonical
    assert _paths.under("companion") == homes.canonical / "companion"
    assert _paths.under("pro", "daemon.sock-info") == homes.canonical / "pro" / "daemon.sock-info"


def test_license_in_both_homes_resolves_canonical(homes):
    canonical = _put(homes.canonical / "license.json", _pending_stub("CANONICAL-KEY-0123456789"))
    _put(homes.legacy / "license.json", _signed_pro())
    assert _paths.license_file() == canonical
    assert _paths.license_file(for_write=True) == canonical


def test_tokenpak_home_is_a_closed_world(homes, monkeypatch):
    """A scoped home never falls through to a license in the default homes."""
    _put(homes.canonical / "license.json", _signed_pro())
    _put(homes.legacy / "license.json", _signed_pro())
    scoped = homes.tmp / "scoped"
    monkeypatch.setenv(_paths.ENV_VAR, str(scoped))

    assert _paths.license_file() == scoped / "license.json"
    assert _paths.license_file(for_write=True) == scoped / "license.json"
    assert load_license().tier == TIER_FREE  # the real licenses are not visible


def test_tokenpak_home_with_a_license_uses_it(homes, monkeypatch):
    scoped = homes.tmp / "scoped"
    lic = _put(scoped / "license.json", _signed_pro())
    monkeypatch.setenv(_paths.ENV_VAR, str(scoped))
    assert _paths.license_file() == lic
    assert _paths.license_file(for_write=True) == lic


def test_license_file_override_wins_over_everything(homes, monkeypatch):
    _put(homes.canonical / "license.json", _signed_pro())
    _put(homes.legacy / "license.json", _signed_pro())
    monkeypatch.setenv(_paths.ENV_VAR, str(homes.tmp / "scoped"))
    explicit = homes.tmp / "elsewhere" / "named.json"
    monkeypatch.setenv(_paths.LICENSE_FILE_ENV, str(explicit))

    assert _paths.license_file() == explicit
    assert _paths.license_file(for_write=True) == explicit
    assert licensing._license_path() == explicit
    assert licensing._license_write_path() == explicit


def test_empty_license_file_override_counts_as_unset(homes, monkeypatch):
    lic = _put(homes.legacy / "license.json", _signed_pro())
    monkeypatch.setenv(_paths.LICENSE_FILE_ENV, "")
    assert _paths.license_file() == lic


def test_no_license_anywhere_reads_follow_home_and_writes_follow_write_home(homes):
    """With no license to follow, a first license goes where a new install's state goes."""
    # An empty legacy directory is all there is: reads resolve to it, but a new
    # license is never started in the legacy home.
    homes.legacy.mkdir()
    assert _paths.license_file() == homes.legacy / "license.json"
    assert _paths.license_file(for_write=True) == homes.canonical / "license.json"

    # An install that lives in the legacy home (state, no license) keeps writing
    # there, exactly as before.
    (homes.legacy / "config.yaml").write_text("{}", encoding="utf-8")
    assert _paths.license_file() == homes.legacy / "license.json"
    assert _paths.license_file(for_write=True) == homes.legacy / "license.json"


def test_a_directory_named_license_json_is_not_a_license(homes):
    (homes.canonical / "license.json").mkdir(parents=True)
    lic = _put(homes.legacy / "license.json", _signed_pro())
    assert _paths.license_file() == lic


def test_a_dangling_link_is_not_a_license(homes):
    homes.canonical.mkdir()
    try:
        (homes.canonical / "license.json").symlink_to(homes.tmp / "missing-target.json")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    lic = _put(homes.legacy / "license.json", _signed_pro())
    assert _paths.license_file() == lic


def test_a_license_that_cannot_be_inspected_still_counts_as_present(homes, monkeypatch):
    """Unreadable is not absent: the install must not be handed to another license."""
    canonical = _put(homes.canonical / "license.json", _signed_pro())
    _put(homes.legacy / "license.json", _signed_pro())
    original = Path.is_file

    def is_file(self, *args, **kwargs):
        if self == canonical:
            raise PermissionError(13, "Permission denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", is_file)
    assert _paths.license_file() == canonical


# -- Reads, writes and locks through the licensing module ---------------------


def test_load_license_and_status_read_the_legacy_license(homes):
    _hold_other_state(homes.canonical)
    lic = _put(
        homes.legacy / "license.json",
        {"tier": TIER_PRO, "status": "active", "key": _KEY, "activated_at": "2026-10-01T00:00:00Z"},
    )
    assert load_license().tier == TIER_PRO
    assert summary_for_cli()["license_path"] == str(lic)


def test_activation_over_a_signed_license_in_the_other_home_is_refused(homes):
    _hold_other_state(homes.canonical)
    lic = _put(homes.legacy / "license.json", _signed_pro())
    before = lic.read_bytes()

    result = activate(_OTHER_KEY)

    assert result.ok is False and result.error == "license_already_installed"
    assert "tokenpak deactivate" in result.summary
    assert lic.read_bytes() == before
    # Nothing was started in the canonical home, and no lock was taken.
    assert not (homes.canonical / "license.json").exists()
    assert not (homes.canonical / "license.json.lock").exists()


def test_same_key_reactivation_is_still_a_no_write_result(homes):
    _hold_other_state(homes.canonical)
    lic = _put(
        homes.legacy / "license.json",
        {"tier": TIER_PRO, "status": "active", "key": _KEY, "activated_at": "2026-10-01T00:00:00Z"},
    )
    before = lic.read_bytes()

    result = activate(_KEY)

    assert result.ok is True and "already active" in result.summary
    assert lic.read_bytes() == before
    assert not (homes.canonical / "license.json").exists()
    assert not (homes.canonical / "license.json.lock").exists()


def test_a_pending_stub_is_replaced_in_place_with_its_lock_beside_it(homes):
    _hold_other_state(homes.canonical)
    lic = _put(homes.legacy / "license.json", _pending_stub(_KEY))

    result = activate(_OTHER_KEY)

    assert result.ok is True
    assert json.loads(lic.read_text(encoding="utf-8"))["key"] == _OTHER_KEY
    assert (homes.legacy / "license.json.lock").exists()  # beside the license in effect
    assert not (homes.canonical / "license.json").exists()
    assert not (homes.canonical / "license.json.lock").exists()
    assert load_license().key == _OTHER_KEY


def test_the_write_lock_sits_beside_the_resolved_license(homes):
    _hold_other_state(homes.canonical)
    _put(homes.legacy / "license.json", _signed_pro())
    path = licensing._license_write_path()
    with licensing._license_write_lock(path):
        assert (homes.legacy / "license.json.lock").exists()
    assert not (homes.canonical / "license.json.lock").exists()


def test_a_first_activation_starts_in_the_write_home(homes):
    result = activate(_KEY)
    assert result.ok is True
    assert (homes.canonical / "license.json").is_file()
    assert (homes.canonical / "license.json.lock").is_file()
    assert not homes.legacy.exists()
    assert load_license().key == _KEY


def test_a_first_activation_in_a_legacy_install_stays_in_legacy(homes):
    """An install that lives in the legacy home (state, no license yet) keeps writing there."""
    (homes.legacy / "config.yaml").parent.mkdir(parents=True)
    (homes.legacy / "config.yaml").write_text("{}", encoding="utf-8")
    result = activate(_KEY)
    assert result.ok is True
    assert (homes.legacy / "license.json").is_file()
    assert not homes.canonical.exists()


def test_deactivate_removes_the_license_in_effect(homes):
    _hold_other_state(homes.canonical)
    lic = _put(homes.legacy / "license.json", _signed_pro())
    assert deactivate() is True
    assert not lic.exists()
    assert load_license().tier == TIER_FREE
    assert deactivate() is False  # nothing left to remove


def test_deactivate_uncovers_the_license_in_the_other_home(homes):
    """A pending stub in the canonical home shadows a license in the legacy home.

    This is the state a 1.30.2 ``activate`` left behind on a split-home install.
    ``deactivate`` removes the file in use; the license underneath then applies.
    """
    _hold_other_state(homes.canonical)
    stub = _put(homes.canonical / "license.json", _pending_stub(_OTHER_KEY))
    legacy = _put(homes.legacy / "license.json", _signed_pro())
    before = legacy.read_bytes()

    assert _paths.license_file() == stub
    assert deactivate() is True

    assert not stub.exists()
    assert _paths.license_file() == legacy
    assert legacy.read_bytes() == before


def test_save_license_replaces_a_stub_in_the_other_home_atomically(homes):
    _hold_other_state(homes.canonical)
    lic = _put(homes.legacy / "license.json", _pending_stub(_KEY))
    licensing.save_license(
        licensing.License(tier=TIER_FREE, key=_OTHER_KEY, status="pending_validation")
    )
    assert json.loads(lic.read_text(encoding="utf-8"))["key"] == _OTHER_KEY
    assert not list(homes.legacy.glob("license.json.tmp*"))


def test_save_license_never_replaces_a_signed_license_in_the_other_home(homes):
    _hold_other_state(homes.canonical)
    lic = _put(homes.legacy / "license.json", _signed_pro())
    before = lic.read_bytes()
    with pytest.raises(licensing.LicenseInstalledError):
        licensing.save_license(
            licensing.License(tier=TIER_FREE, key=_OTHER_KEY, status="pending_validation")
        )
    assert lic.read_bytes() == before


# -- Other state is not moved -------------------------------------------------


def test_the_daemon_connection_file_still_follows_the_selected_home(homes):
    """Only the license is found per file; the connection file follows ``home()``."""
    _hold_other_state(homes.canonical)
    _put(homes.legacy / "license.json", _signed_pro())
    assert daemon_probe.sock_info_path() == homes.canonical / "pro" / "daemon.sock-info"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_a_replaced_license_in_the_other_home_stays_owner_only(homes):
    _hold_other_state(homes.canonical)
    lic = _put(homes.legacy / "license.json", _pending_stub(_KEY))
    lic.chmod(0o644)
    activate(_OTHER_KEY)
    assert (lic.stat().st_mode & 0o777) == 0o600
