# SPDX-License-Identifier: Apache-2.0
"""Entitlement lifecycle on the OSS side — expiry honoured, failure recoverable.

The Pro package issues a signed license file and the OSS package stores,
reports and (for its own ``features`` surface) gates on the same file. Two
states of that file were handled wrongly here:

1. **Expired.** A signed Pro license carries ``expires_at`` and no ``status``,
   so it loaded as ``active`` forever. ``tokenpak license`` and
   ``tokenpak features`` kept reporting Pro after the Pro gate had already
   refused the license for having lapsed.
2. **Re-activation.** ``activate`` wrote a pending stub over ``license.json``
   before anything verified the new key. Over an installed, current license
   that discarded the entitlement and its signature — and the stub's schema
   cannot hold a signature, so nothing could restore it. One mistyped key
   took Pro away with a success message.

What this file pins
-------------------
- A license past ``expires_at`` (plus any issuer ``grace_days``) unlocks no
  gated feature through its tier; a current or open-ended one still does
  (the guard does not over-correct). An explicit ``features_override`` is a
  separate, pre-existing grant that still applies — see the characterization
  test below; this change does not alter that policy.
- An unreadable ``expires_at`` fails closed, and a ``grace_days`` too large to
  add (past a timedelta, or past the last representable date) is ignored
  rather than raised out of the gate, the CLI or ``activate``.
- ``activate`` over a current paid license refuses, leaves the file
  byte-identical, and names the way to replace it; the same key is idempotent.
- ``activate`` over an expired or pending license still proceeds, so the
  refusal never strands a user who really needs to replace it.
- ``deactivate`` then ``activate`` is the working replacement path.
- ``license`` / ``features`` output says ``expired`` and does not say Pro is
  active.

All state lives in ``tmp_path``; no daemon, network or signing material is
used. The "signed" license below carries a dummy signature value — it is only
a payload the OSS side must not destroy, never something verified.
"""

from __future__ import annotations

import argparse
import datetime
import json

import pytest

from tokenpak import licensing
from tokenpak.cli.commands import features as features_cmd
from tokenpak.cli.commands import license_cmd
from tokenpak.licensing import (
    TIER_PRO,
    License,
    _license_is_expired,
    _license_status,
    activate,
    daemon_probe,
    deactivate,
    is_feature_enabled,
    load_license,
    summary_for_cli,
)

_PRO_FEATURES = sorted(f for f, t in licensing._GATES.items() if t == TIER_PRO)
_NOW = datetime.datetime(2026, 10, 3, 12, 0, tzinfo=datetime.timezone.utc)
_FUTURE = "2027-10-03T00:00:00Z"
_PAST = "2026-09-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(tmp_path / "license.json"))
    monkeypatch.delenv("TOKENPAK_LICENSE_DEV_SHIM", raising=False)
    monkeypatch.setattr(daemon_probe, "detect_daemon_state", lambda **_: "unavailable")
    yield


def _install_signed(tmp_path, *, expires_at: str | None = _FUTURE) -> bytes:
    """Write a signed-shaped Pro license the way the Pro package installs one."""
    payload = {
        "plan": "pro",
        "tier": "pro",
        "features": ["model_routing_intelligent"],
        "grace_days": 3,
        "last_revalidated_at": "2026-10-02T00:00:00+00:00",
        "signature": {
            "algorithm": "ed25519",
            "key_id": "tokenpak-license-v2",
            "value": "SYNTHETIC-NOT-A-REAL-SIGNATURE",
        },
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at
    path = tmp_path / "license.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path.read_bytes()


# ── Expiry ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("feature", _PRO_FEATURES)
def test_expired_license_unlocks_no_pro_feature_through_its_tier(feature):
    lic = License(tier=TIER_PRO, status="active", expires_at=_PAST)
    assert is_feature_enabled(feature, lic=lic) is False


@pytest.mark.parametrize("expires_at", [_FUTURE, None, ""])
def test_current_or_open_ended_license_still_unlocks_pro(expires_at):
    lic = License(tier=TIER_PRO, status="active", expires_at=expires_at)
    assert all(is_feature_enabled(f, lic=lic) for f in _PRO_FEATURES)


@pytest.mark.parametrize(
    "expires_at, expected",
    [
        ("2026-10-03T11:59:59Z", True),  # one second past
        ("2026-10-03T12:00:01Z", False),  # one second left
        ("2026-10-03T11:00:00+00:00", True),
        ("2026-10-03T13:00:00+02:00", True),  # offset honoured: 11:00Z is past
        ("2026-10-03T15:00:00+02:00", False),  # offset honoured: 13:00Z is ahead
        ("2026-10-02", True),  # bare date = midnight UTC at its start
        ("2026-10-04", False),
        ("2026-10-03T11:59:59", True),  # naive reads as UTC
        ("not-a-date", True),  # unreadable fails closed
    ],
)
def test_license_is_expired_reading(expires_at, expected):
    lic = License(tier=TIER_PRO, status="active", expires_at=expires_at)
    assert _license_is_expired(lic, now=_NOW) is expected


@pytest.mark.parametrize(
    "grace, expected",
    [
        (0, True),
        (3, False),  # 2026-10-01 + 3d = 2026-10-04, still ahead of the clock
        (1, True),
        (-5, True),  # negative cushion is ignored, never shortens or extends
        ("7", True),  # not an int: ignored
        (True, True),  # bool is not a cushion
    ],
)
def test_issuer_grace_extends_expiry_only_when_well_formed(grace, expected):
    lic = License.from_dict(
        {"tier": "pro", "expires_at": "2026-10-01T00:00:00Z", "grace_days": grace}
    )
    assert _license_is_expired(lic, now=_NOW) is expected


def test_grace_only_serialised_when_set():
    assert "grace_days" not in License().to_dict()
    assert License.from_dict({"tier": "pro", "grace_days": 5}).to_dict()["grace_days"] == 5


def test_activate_does_not_replace_a_license_that_is_in_its_grace_window(tmp_path):
    payload = {"plan": "pro", "tier": "pro", "grace_days": 3650, "expires_at": _PAST}
    (tmp_path / "license.json").write_text(json.dumps(payload), encoding="utf-8")
    before = (tmp_path / "license.json").read_bytes()
    assert is_feature_enabled(_PRO_FEATURES[0], lic=load_license()) is True
    assert activate("MISTYPED-KEY-0123456789").ok is False
    assert (tmp_path / "license.json").read_bytes() == before


_CORRUPT_GRACE = [
    pytest.param(10**100, id="beyond-a-c-int"),
    pytest.param(1_000_000_000, id="beyond-timedelta-max"),
    pytest.param(999_999_999, id="timedelta-max-carries-end-past-9999"),
    pytest.param(3_000_000, id="in-range-but-carries-end-past-9999"),
]


@pytest.mark.parametrize("grace", _CORRUPT_GRACE)
def test_unrepresentable_grace_is_ignored_and_fails_closed(grace):
    lic = License.from_dict(
        {
            "tier": "pro",
            "status": "active",
            "expires_at": "2026-09-01T00:00:00Z",
            "grace_days": grace,
        }
    )
    # The declared end stands: expired, locked, and nothing raises.
    assert _license_is_expired(lic, now=_NOW) is True
    assert _license_status(lic) == "expired"
    assert not any(is_feature_enabled(f, lic=lic) for f in _PRO_FEATURES)


def test_unrepresentable_grace_never_extends_a_license_near_the_last_date():
    # End is already at the last representable second: the cushion cannot be
    # added, so the declared end stands — current at that instant, expired after.
    lic = License.from_dict({"tier": "pro", "expires_at": "9999-12-31T23:59:59Z", "grace_days": 1})
    at_end = datetime.datetime(9999, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc)
    assert _license_is_expired(lic, now=_NOW) is False
    assert _license_is_expired(lic, now=at_end) is False
    assert _license_is_expired(lic, now=at_end + datetime.timedelta(microseconds=1)) is True


@pytest.mark.parametrize("grace", _CORRUPT_GRACE)
def test_corrupt_grace_on_disk_does_not_crash_activate_or_the_cli(tmp_path, capsys, grace):
    payload = {
        "tier": "pro",
        "status": "active",
        "expires_at": "2026-09-01T00:00:00Z",
        "grace_days": grace,
    }
    (tmp_path / "license.json").write_text(json.dumps(payload), encoding="utf-8")
    assert license_cmd.run_license(argparse.Namespace(as_json=False)) == 0
    assert "Status    expired" in capsys.readouterr().out
    assert features_cmd.cmd_features_list(argparse.Namespace(as_json=True, tier=None)) == 0
    assert json.loads(capsys.readouterr().out)["license_status"] == "expired"
    # Expired, so replaceable — and it does not raise on the way.
    assert activate("RENEWED-KEY-0123456789").ok is True


def test_expiry_does_not_remove_an_explicit_features_override():
    """Characterization, not endorsement: an override grant predates this change.

    ``is_feature_enabled`` has always let an explicit per-license
    ``features_override`` win over status, so an expired license that carries one
    still reports that feature enabled on the OSS ``features`` display. Expiry
    removes the tier grant only. Whether an override should survive expiry is a
    policy question this change deliberately leaves open.
    """
    feature = _PRO_FEATURES[0]
    lic = License(tier=TIER_PRO, status="active", expires_at=_PAST, features_override=[feature])
    assert _license_status(lic) == "expired"
    assert is_feature_enabled(feature, lic=lic) is True
    assert not any(is_feature_enabled(f, lic=lic) for f in _PRO_FEATURES if f != feature)


def test_status_reports_expired_for_a_lapsed_active_license():
    assert _license_status(License(tier=TIER_PRO, status="active", expires_at=_PAST)) == "expired"
    # Recorded states are not rewritten.
    assert _license_status(License(tier=TIER_PRO, status="revoked", expires_at=_PAST)) == "revoked"
    assert _license_status(License(status="pending_validation")) == "pending_validation"
    assert _license_status(License(tier=TIER_PRO, status="active", expires_at=_FUTURE)) == "active"


def test_summary_for_an_installed_expired_signed_license(tmp_path):
    _install_signed(tmp_path, expires_at=_PAST)
    s = summary_for_cli()
    assert s["status"] == "expired"
    assert s["enabled_gated_count"] == 0


def test_missing_license_is_free_and_not_expired():
    lic = load_license()
    assert _license_is_expired(lic) is False
    assert _license_status(lic) == "active"  # the Free default; nothing to lapse
    assert not any(is_feature_enabled(f, lic=lic) for f in _PRO_FEATURES)


# ── Re-activation over an installed license ─────────────────────────────


def test_activate_over_current_pro_refuses_and_preserves_the_file(tmp_path):
    before = _install_signed(tmp_path)
    r = activate("MISTYPED-KEY-0123456789")
    assert r.ok is False
    assert r.error == "license_already_installed"
    assert "tokenpak deactivate" in r.summary
    assert (tmp_path / "license.json").read_bytes() == before  # signature intact
    assert is_feature_enabled(_PRO_FEATURES[0], lic=load_license()) is True


def test_activate_over_signed_plan_only_license_preserves_the_file(tmp_path):
    """A signed license may carry ``plan`` and no ``tier``; the Pro gate honours it."""
    payload = json.loads(_install_signed(tmp_path))
    del payload["tier"]
    path = tmp_path / "license.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    before = path.read_bytes()
    r = activate("MISTYPED-KEY-0123456789")
    assert r.ok is False
    assert r.error == "license_already_installed"
    assert path.read_bytes() == before


def test_activate_over_unsigned_plan_only_file_still_stages(tmp_path):
    path = tmp_path / "license.json"
    path.write_text(json.dumps({"plan": "pro", "status": "active"}), encoding="utf-8")
    r = activate("MISTYPED-KEY-0123456789")
    assert r.ok is True


def test_activate_same_key_over_current_license_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_LICENSE_DEV_SHIM", "1")
    assert activate("TPK-DEVSHIM-PRO-LOCAL-0001").ok is True
    before = (tmp_path / "license.json").read_bytes()
    monkeypatch.delenv("TOKENPAK_LICENSE_DEV_SHIM")
    r = activate("TPK-DEVSHIM-PRO-LOCAL-0001")
    assert r.ok is True
    assert "already active" in r.summary
    assert (tmp_path / "license.json").read_bytes() == before


def test_activate_over_expired_license_may_replace_it(tmp_path):
    _install_signed(tmp_path, expires_at=_PAST)
    r = activate("RENEWED-KEY-0123456789")
    assert r.ok is True
    assert load_license().status == "pending_validation"


def test_activate_over_a_pending_stub_is_unchanged(tmp_path):
    assert activate("FIRST-KEY-0123456789ab").ok is True
    r = activate("SECOND-KEY-0123456789a")
    assert r.ok is True
    assert load_license().key == "SECOND-KEY-0123456789a"


def test_deactivate_then_activate_is_the_replacement_path(tmp_path):
    _install_signed(tmp_path)
    assert activate("REPLACEMENT-KEY-0123456").ok is False
    assert deactivate() is True
    r = activate("REPLACEMENT-KEY-0123456")
    assert r.ok is True
    assert load_license().key == "REPLACEMENT-KEY-0123456"


def test_rejected_key_shapes_still_fail_before_touching_an_installed_license(tmp_path):
    before = _install_signed(tmp_path)
    for bad in ("", "short", "has spaces in it 0123456", "placeholder"):
        assert activate(bad).ok is False
    assert (tmp_path / "license.json").read_bytes() == before


def test_activate_reports_replacement_during_daemon_consult_truthfully(tmp_path, monkeypatch):
    """A signed license that wins the race during the daemon consult is kept, and
    ``activate`` must not claim a stored key or hand back an unpersisted Pro license."""
    key = "RACE-KEY-0123456789abcd"
    path = tmp_path / "license.json"
    signed = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"signature": {"verified": True}, "is_valid": True, "tier": "pro"}
            ).encode()

    real_save = licensing.save_license
    calls = []

    def racing_save(lic):
        calls.append(lic.status)
        if len(calls) == 2:  # the save after the daemon verified the key
            _install_signed(tmp_path)
            signed["bytes"] = path.read_bytes()
            raise licensing.LicenseInstalledError("license_already_installed")
        real_save(lic)

    sock = tmp_path / "sock.json"
    sock.write_text('{"port": 1}')
    monkeypatch.setattr(daemon_probe, "detect_daemon_state", lambda **_: "active")
    monkeypatch.setattr(daemon_probe, "sock_info_path", lambda: sock)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    monkeypatch.setattr(licensing, "save_license", racing_save)

    r = activate(key)

    assert len(calls) == 2
    assert r.ok is False
    assert r.error == "license_replaced_during_consult"
    assert r.license is None
    assert "key stored" not in r.summary.lower()
    assert "remain locked" not in r.summary
    assert "left unchanged" in r.summary
    assert path.read_bytes() == signed["bytes"]


# ── CLI surfaces ────────────────────────────────────────────────────────


def test_cli_activate_refusal_exits_nonzero_with_detail(tmp_path, capsys):
    before = _install_signed(tmp_path)
    with pytest.raises(SystemExit) as exc:
        license_cmd.run_activate(argparse.Namespace(key="MISTYPED-KEY-0123456789", email=""))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "activate failed" in err
    assert "detail: license_already_installed" in err
    assert (tmp_path / "license.json").read_bytes() == before


def test_cli_license_reports_expired_not_active(tmp_path, capsys):
    _install_signed(tmp_path, expires_at=_PAST)
    assert license_cmd.run_license(argparse.Namespace(as_json=False)) == 0
    out = capsys.readouterr().out
    assert "Status    expired" in out
    assert "License expired" in out
    assert "Status    active" not in out


def test_cli_features_json_reports_expired_and_locks_pro(tmp_path, capsys):
    _install_signed(tmp_path, expires_at=_PAST)
    assert features_cmd.cmd_features_list(argparse.Namespace(as_json=True, tier=None)) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["license_status"] == "expired"
    pro_rows = [r for r in data["features"] if r["required_tier"] == TIER_PRO]
    assert pro_rows and all(r["state"] == "locked" for r in pro_rows)
    assert all("expired" in r["reason"] for r in pro_rows)


def test_cli_features_explain_names_no_date_when_a_stored_expired_status_has_none(tmp_path, capsys):
    (tmp_path / "license.json").write_text(
        json.dumps({"tier": "pro", "status": "expired"}), encoding="utf-8"
    )
    args = argparse.Namespace(feature=_PRO_FEATURES[0], as_json=True)
    assert features_cmd.cmd_features_explain(args) == 0
    reason = json.loads(capsys.readouterr().out)["reason"]
    assert reason.startswith("License expired;") and "None" not in reason


def test_cli_features_explain_for_current_license_is_active(tmp_path, capsys):
    _install_signed(tmp_path, expires_at=_FUTURE)
    args = argparse.Namespace(feature=_PRO_FEATURES[0], as_json=True)
    assert features_cmd.cmd_features_explain(args) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "active"
