"""Token mode cannot disappear at startup or on the legacy disabled path."""

import json
from types import SimpleNamespace

import pytest

from tokenpak.proxy import guard_snapshot_endpoint, server
from tokenpak.proxy.spend_guard.request_accounting import RequestAccounting, _request_config
from tokenpak.proxy.spend_guard.serving_basis import capture_serving_basis

SWITCHES = ("TOKENPAK_SPEND_GUARD_ENABLED", "TOKENPAK_SPEND_GUARD_RESERVATIONS_ENABLED")
BASIS_ENV = "TOKENPAK_SPEND_GUARD_ACCOUNTING_BASIS"


@pytest.fixture
def path(tmp_path, monkeypatch):
    for key in (*SWITCHES, BASIS_ENV):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENPAK_CONFIG", str(tmp_path / "config.yaml"))
    return tmp_path / "config.yaml"


def token_config(path, section="tip_spend_guard", **updates):
    policy = dict(
        enabled=True,
        reservations_enabled=True,
        accounting_basis="provider_tokens",
        rolling_caps_per_agent_max_cost_usd=0,
        rolling_caps_per_fleet_max_cost_usd=0,
        rolling_caps_per_fleet_max_tokens_total=100,
    )
    policy.update(updates)
    path.write_text(json.dumps({section: policy}))


@pytest.mark.parametrize("switch", SWITCHES)
@pytest.mark.parametrize("section", ["tip_spend_guard", "spend_guard"])
@pytest.mark.parametrize("location", ["explicit", "default"])
def test_actual_server_rejects_token_disable_before_pool_or_listener(
    path, monkeypatch, switch, section, location
):
    token_config(path, section)
    if location == "default":
        monkeypatch.delenv("TOKENPAK_CONFIG")
    monkeypatch.setenv(switch, "0")

    def forbidden(*args, **kwargs):
        pytest.fail("server reached a pool or listener before rejecting token intent")

    monkeypatch.setattr(server, "ConnectionPool", forbidden)
    with pytest.raises(ValueError, match="requires enabled"):
        server.ProxyServer(port=0)


@pytest.mark.parametrize("switch", SWITCHES)
def test_detached_explicit_token_env_cannot_be_substituted(path, monkeypatch, switch):
    monkeypatch.setenv(BASIS_ENV, "provider_tokens")
    monkeypatch.setenv(switch, "false")
    with pytest.raises(ValueError, match="requires enabled"):
        _request_config()


@pytest.mark.parametrize("value", [None, "", "tokens", True, [], {}])
def test_invalid_file_basis_rejected_even_when_disabled(path, monkeypatch, value):
    token_config(path, accounting_basis=value)
    monkeypatch.setenv(SWITCHES[0], "0")
    with pytest.raises(ValueError, match="unsupported accounting basis"):
        capture_serving_basis()


@pytest.mark.parametrize("contents", [None, "spend_guard: [\n", "{}"])
@pytest.mark.parametrize("switch", SWITCHES)
def test_legacy_disabled_request_never_reads_config(path, monkeypatch, contents, switch):
    if contents is not None:
        path.write_text(contents)
    monkeypatch.setenv(switch, "false")
    intent = capture_serving_basis()
    assert intent.accounting_basis == "priced_usage"

    def forbidden():
        pytest.fail("disabled legacy request read configuration")

    monkeypatch.setattr(guard_snapshot_endpoint, "_raw_config", forbidden)
    accounting = RequestAccounting(SimpleNamespace(_guard_serving_basis=intent), {})
    assert accounting.store is None
    accounting.before_send()
    assert (path.read_text() if path.exists() else None) == contents


def test_canonical_alias_and_environment_precedence(path, monkeypatch):
    path.write_text(
        json.dumps(
            {
                "spend_guard": {"accounting_basis": "provider_tokens"},
                "tip_spend_guard": {"accounting_basis": "priced_usage"},
            }
        )
    )
    assert capture_serving_basis().accounting_basis == "priced_usage"
    token_config(path)
    monkeypatch.setenv(BASIS_ENV, "priced_usage")
    monkeypatch.setenv(SWITCHES[0], "false")
    assert capture_serving_basis().accounting_basis == "priced_usage"


def test_parsed_token_policy_errors_never_become_legacy_defaults(path):
    token_config(path, rolling_caps_per_fleet_max_tokens_total=0)
    with pytest.raises(ValueError, match="total-token cap"):
        capture_serving_basis()


@pytest.mark.parametrize(
    "initial,next_basis", [("priced_usage", "provider_tokens"), ("provider_tokens", "priced_usage")]
)
def test_file_basis_change_requires_a_new_serving_generation(path, initial, next_basis):
    token_config(path, accounting_basis=initial)
    intent = capture_serving_basis()
    token_config(path, accounting_basis=next_basis)
    with pytest.raises(ValueError, match="new serving generation"):
        _request_config(intent)
    assert capture_serving_basis().accounting_basis == next_basis


def test_new_file_basis_does_not_activate_while_legacy_disabled(path, monkeypatch):
    path.write_text("{}")
    intent = capture_serving_basis()
    token_config(path)
    monkeypatch.setenv(SWITCHES[0], "false")
    assert _request_config(intent).accounting_basis == "priced_usage"
    monkeypatch.delenv(SWITCHES[0])
    with pytest.raises(ValueError, match="new serving generation"):
        _request_config(intent)


@pytest.mark.parametrize("switch", SWITCHES)
def test_post_start_token_disable_refuses_without_config_read(path, monkeypatch, switch):
    token_config(path)
    intent = capture_serving_basis()
    monkeypatch.setenv(switch, "false")
    monkeypatch.setattr(
        guard_snapshot_endpoint, "_raw_config", lambda: pytest.fail("request config I/O")
    )
    with pytest.raises(ValueError, match="requires enabled"):
        _request_config(intent)


def test_environment_basis_cannot_change_mid_generation(path, monkeypatch):
    token_config(path)
    intent = capture_serving_basis()
    monkeypatch.setenv(BASIS_ENV, "priced_usage")
    with pytest.raises(ValueError, match="new serving generation"):
        _request_config(intent)


def test_invalid_explicit_environment_basis_is_not_a_legacy_disable(path, monkeypatch):
    monkeypatch.setenv(BASIS_ENV, "")
    monkeypatch.setenv(SWITCHES[0], "false")
    with pytest.raises(ValueError, match="unsupported accounting basis"):
        capture_serving_basis()


def test_new_priced_generation_cannot_reinterpret_a_token_ledger(path, monkeypatch):
    from tokenpak.proxy.monitor import Monitor
    from tokenpak.proxy.spend_guard.reservation import ReservationStore, ReservationUnavailable

    token_config(path, audit_db_path=str(path.parent / "guard.db"))
    monitor = Monitor(path.parent / "monitor.db")
    store = ReservationStore(
        path.parent / "guard.db", monitor.db_path, accounting_basis="provider_tokens"
    )
    store.begin_request("session-a", "owner-a")
    monkeypatch.setenv(BASIS_ENV, "priced_usage")
    owner = SimpleNamespace(
        _guard_serving_basis=capture_serving_basis(),
        _guard_snapshot_owner_id="test",
        monitor=monitor,
    )
    try:
        with pytest.raises(ReservationUnavailable, match="basis"):
            RequestAccounting(owner, {})
    finally:
        monitor.stop(timeout=3)
