"""An unverified staging write must not replace a signed paid license."""

import json

import pytest

from tokenpak import licensing


def _signed_current(path):
    data = {
        "key_id": "SIGNED-B",
        "plan": "pro",
        "status": "active",
        "expires_at": "2999-01-01T00:00:00+00:00",
        "signature": {"algorithm": "ed25519", "value": "x"},
    }
    path.write_text(json.dumps(data))
    return path.read_bytes()


def test_staging_save_refused_over_signed_install_and_bytes_preserved(tmp_path, monkeypatch):
    lic_file = tmp_path / "license.json"
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(lic_file))
    raw = _signed_current(lic_file)
    stub = licensing.License(tier="free", key="PENDING-KEY-0123456789", status="pending_validation")
    with pytest.raises(licensing.LicenseInstalledError):
        licensing.save_license(stub)
    assert lic_file.read_bytes() == raw


def test_activate_reports_already_installed_when_install_lands_before_save(tmp_path, monkeypatch):
    lic_file = tmp_path / "license.json"
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(lic_file))
    lic_file.write_text(json.dumps({"key": "OLD-PENDING-KEY-0123", "status": "pending_validation"}))
    real_save = licensing.save_license
    box = {}

    def install_then_save(lic):
        box["raw"] = _signed_current(lic_file)  # the paid CLI commits first
        return real_save(lic)

    monkeypatch.setattr(licensing, "save_license", install_then_save)
    result = licensing.activate("NEW-PENDING-KEY-0123456789")
    assert not result.ok and result.error == "license_already_installed"
    assert lic_file.read_bytes() == box["raw"]


def test_same_key_signed_install_not_stripped_by_stale_staging_or_daemon_writeback(
    tmp_path, monkeypatch
):
    lic_file = tmp_path / "license.json"
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(lic_file))
    key = "SAME-KEY-0123456789ABC"
    staged = licensing.License(tier="free", key=key, status="pending_validation")
    licensing.save_license(staged)
    signed = {
        "key": key,
        "key_id": key,
        "plan": "pro",
        "status": "active",
        "expires_at": "2999-01-01T00:00:00+00:00",
        "signature": {"algorithm": "ed25519", "value": "x"},
    }
    lic_file.write_text(json.dumps(signed))  # verified same-key install lands first
    raw = lic_file.read_bytes()
    # stale staging write, and the delayed daemon-consult writeback (tier/status upgrade)
    with pytest.raises(licensing.LicenseInstalledError):
        licensing.save_license(staged)
    staged.tier, staged.status = "pro", "active"
    with pytest.raises(licensing.LicenseInstalledError):
        licensing.save_license(staged)
    assert lic_file.read_bytes() == raw
    assert json.loads(lic_file.read_text())["signature"]["value"] == "x"
    # same-key re-activation is an idempotent no-write result
    result = licensing.activate(key)
    assert result.ok and lic_file.read_bytes() == raw
