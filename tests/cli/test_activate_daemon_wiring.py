# SPDX-License-Identifier: Apache-2.0
"""OSS-side activate → Pro daemon /v1/features wiring tests (Cycle 5).

Covers every state the wiring must distinguish per the design at
``~/vault/01_PROJECTS/tokenpak/beta-polishing-2026-05-13/design-oss-activate-wiring-2026-05-15.md``:

- daemon unreachable (no sock-info file)
- daemon returns verified Pro envelope
- daemon returns signature_mismatch
- daemon returns placeholder-key advisory
- daemon returns malformed payload
- HTTP-level errors (timeout / connection refused)

Plus the inviolable: local file edits MUST NEVER unlock Pro features
even if they fake a higher tier.
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch

import pytest


def _stored_license_path(tmp_path):
    return tmp_path / "license.json"


@pytest.fixture
def isolated_license(tmp_path, monkeypatch):
    """Force the licensing module to read/write under tmp_path."""
    from tokenpak import licensing as _lic

    monkeypatch.setattr(_lic, "_license_path",
                        lambda: _stored_license_path(tmp_path))
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Unreachable daemon
# ---------------------------------------------------------------------------


def test_activate_no_daemon_keeps_pending(isolated_license, monkeypatch):
    from tokenpak import licensing as _lic

    # Force daemon probe to report unavailable.
    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.detect_daemon_state",
        lambda: "unavailable",
    )
    result = _lic.activate("PLAUSIBLE-LICENSE-KEY-0001")
    assert result.ok is True
    assert "daemon not running" in result.summary.lower()
    assert result.license.tier == _lic.TIER_FREE
    assert result.license.status == "pending_validation"


# ---------------------------------------------------------------------------
# Verified Pro envelope
# ---------------------------------------------------------------------------


def _fake_urlopen(payload: dict):
    """Return a callable suitable for monkeypatching urllib.request.urlopen."""

    class _Resp:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    body = json.dumps(payload).encode("utf-8")
    return lambda url, timeout=None: _Resp(body)


def test_activate_daemon_verified_pro_upgrades_tier(isolated_license, tmp_path, monkeypatch):
    from tokenpak import licensing as _lic

    # Daemon "active" + sock-info file present
    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.detect_daemon_state",
        lambda: "active",
    )
    sock = tmp_path / "sock_info.json"
    sock.write_text(json.dumps({"port": 12345}))
    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.sock_info_path",
        lambda: sock,
    )

    payload = {
        "tier": "pro",
        "features": ["A1_workflow_engine"],
        "is_valid": True,
        "in_grace": False,
        "expires_at": None,
        "degraded_reason": None,
        "signature": {
            "verified": True,
            "reason": "verified",
            "key_fingerprint": "abc123",
            "key_is_placeholder": False,
        },
    }
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(payload))

    result = _lic.activate("PLAUSIBLE-LICENSE-KEY-0002")
    assert result.ok is True
    assert "verified by the Pro daemon" in result.summary
    assert result.license.tier == "pro"
    assert result.license.status == "active"

    # On-disk license now reflects Pro
    stored = json.loads(_stored_license_path(tmp_path).read_text())
    assert stored["tier"] == "pro"
    assert stored["status"] == "active"


# ---------------------------------------------------------------------------
# Signature failures
# ---------------------------------------------------------------------------


def test_activate_signature_mismatch_keeps_pending(isolated_license, tmp_path, monkeypatch):
    from tokenpak import licensing as _lic

    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.detect_daemon_state",
        lambda: "active",
    )
    sock = tmp_path / "sock_info.json"
    sock.write_text(json.dumps({"port": 12345}))
    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.sock_info_path",
        lambda: sock,
    )

    payload = {
        "tier": "oss",
        "is_valid": False,
        "signature": {
            "verified": False,
            "reason": "signature_mismatch",
            "key_is_placeholder": False,
        },
    }
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(payload))

    result = _lic.activate("PLAUSIBLE-LICENSE-KEY-0003")
    assert result.ok is True
    assert "rejected verification" in result.summary
    assert result.license.tier == _lic.TIER_FREE
    assert result.license.status == "pending_validation"


# ---------------------------------------------------------------------------
# Placeholder-key advisory
# ---------------------------------------------------------------------------


def test_activate_placeholder_key_keeps_pending(isolated_license, tmp_path, monkeypatch):
    from tokenpak import licensing as _lic

    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.detect_daemon_state",
        lambda: "active",
    )
    sock = tmp_path / "sock_info.json"
    sock.write_text(json.dumps({"port": 12345}))
    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.sock_info_path",
        lambda: sock,
    )

    payload = {
        "tier": "oss",
        "is_valid": False,
        "signature": {
            "verified": False,
            "reason": "placeholder_key",
            "key_is_placeholder": True,
            "key_fingerprint": "0000000000000000",
        },
    }
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(payload))

    result = _lic.activate("PLAUSIBLE-LICENSE-KEY-0004")
    assert result.ok is True
    assert "placeholder" in result.summary.lower()
    assert result.license.tier == _lic.TIER_FREE


# ---------------------------------------------------------------------------
# HTTP timeout / connection refused / malformed payload
# ---------------------------------------------------------------------------


def test_activate_http_timeout_keeps_pending(isolated_license, tmp_path, monkeypatch):
    import urllib.error

    from tokenpak import licensing as _lic

    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.detect_daemon_state",
        lambda: "active",
    )
    sock = tmp_path / "sock_info.json"
    sock.write_text(json.dumps({"port": 12345}))
    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.sock_info_path",
        lambda: sock,
    )

    def _raises(url, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _raises)

    result = _lic.activate("PLAUSIBLE-LICENSE-KEY-0005")
    assert result.ok is True
    assert "daemon not running" in result.summary.lower()
    assert result.license.tier == _lic.TIER_FREE


def test_activate_malformed_payload_keeps_pending(isolated_license, tmp_path, monkeypatch):
    from tokenpak import licensing as _lic

    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.detect_daemon_state",
        lambda: "active",
    )
    sock = tmp_path / "sock_info.json"
    sock.write_text(json.dumps({"port": 12345}))
    monkeypatch.setattr(
        "tokenpak.licensing.daemon_probe.sock_info_path",
        lambda: sock,
    )
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(["not-a-dict"]))

    result = _lic.activate("PLAUSIBLE-LICENSE-KEY-0006")
    assert result.ok is True
    assert result.license.tier == _lic.TIER_FREE


# ---------------------------------------------------------------------------
# Local file edits MUST NOT unlock Pro
# ---------------------------------------------------------------------------


def test_manual_local_edit_does_not_unlock_pro(isolated_license, tmp_path):
    """If the user manually edits license.json to claim tier=pro, the gating
    must still reject Pro features because status != active was set via the
    daemon-mediated flow."""
    from tokenpak import licensing as _lic

    forged = {
        "tier": "pro",
        "key": "forged-key",
        "activated_at": "2026-05-15T00:00:00Z",
        "email": "",
        "status": "pending_validation",
        "features_override": [],
    }
    _stored_license_path(tmp_path).write_text(json.dumps(forged))

    # A Pro-gated feature must still report locked because status != active.
    enabled = _lic.is_feature_enabled("A1_workflow_engine")
    assert enabled is False, (
        "Pro feature unlocked from a forged local license — "
        "tamper-detection regression"
    )
