"""Opt-in accounting must preserve disabled forwarding and strict observations."""

from types import SimpleNamespace

import pytest

from tokenpak.proxy.guard_snapshot_endpoint import _effective_config
from tokenpak.proxy.spend_guard.request_accounting import RequestAccounting


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setenv("TOKENPAK_CONFIG", str(path))
    monkeypatch.delenv("TOKENPAK_SPEND_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("TOKENPAK_SPEND_GUARD_RESERVATIONS_ENABLED", raising=False)
    return path


@pytest.mark.parametrize(
    "switch", ["TOKENPAK_SPEND_GUARD_ENABLED", "TOKENPAK_SPEND_GUARD_RESERVATIONS_ENABLED"]
)
@pytest.mark.parametrize("contents", [None, "spend_guard: [\n"])
def test_explicit_disable_preserves_forwarding_without_config_io(
    config_path, monkeypatch, switch, contents
):
    if contents is not None:
        config_path.write_text(contents)
    monkeypatch.setenv(switch, "false")
    accounting = RequestAccounting(object(), {})
    assert accounting.store is None
    assert accounting.admit(b"{}", "unknown", "request", {}) is None
    accounting.before_send()
    accounting.finish()
    if contents is None:
        assert not config_path.exists()
    else:
        assert config_path.read_text() == contents


def test_missing_optional_config_keeps_default_accounting_off(config_path):
    assert RequestAccounting(object(), {}).store is None
    assert not config_path.exists()
    # Observation never treats a missing explicitly configured policy as known.
    with pytest.raises(FileNotFoundError):
        _effective_config()


def test_durable_opt_in_refuses_missing_config(config_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_SPEND_GUARD_RESERVATIONS_ENABLED", "true")
    with pytest.raises(FileNotFoundError):
        RequestAccounting(object(), {})
    assert not config_path.exists()


def test_file_opt_in_cannot_silently_drop_missing_monitor(config_path):
    config_path.write_text("spend_guard:\n  enabled: true\n  reservations_enabled: true\n")
    from tokenpak.proxy.spend_guard.reservation import ReservationUnavailable

    with pytest.raises(ReservationUnavailable, match="native monitor unavailable"):
        RequestAccounting(SimpleNamespace(_guard_snapshot_owner_id="test", monitor=None), {})
