# SPDX-License-Identifier: Apache-2.0
"""Unattended blocks stop, report, and require one local operator approval."""

from __future__ import annotations

import http.client
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tokenpak.proxy.spend_guard import orchestrator, unattended
from tokenpak.proxy.spend_guard.pending import PendingStore, hash_request
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig

MODEL = "gpt-6-astra"
SESSION = "unattended-test"
HEADERS = {unattended.HEADER: "1"}


def body(text="x" * 40_000):
    return json.dumps({"model": MODEL, "input": [{"role": "user", "content": text}]}).encode()


@pytest.fixture
def cfg(state_dir):
    return SpendGuardConfig(
        enabled=True,
        audit_db_path=str(state_dir / "guard.db"),
        block_tokens=10_000,
        hard_block_tokens=1_000_000,
        rolling_caps_enabled=False,
        session_block_cost_usd=0,
    )


def evaluate(cfg, data=None, headers=None, session=SESSION, target="/v1/responses"):
    return orchestrator.evaluate(
        body() if data is None else data,
        MODEL,
        session,
        HEADERS if headers is None else headers,
        config=cfg,
        target_url=target,
    )


def error(outcome):
    assert outcome.kind in ("block", "hard_block")
    assert outcome.http_status == 402
    result = json.loads(outcome.response_body)["error"]
    assert result["retryable"] is False
    assert result["in_band_approval_allowed"] is False
    assert result["approval_prompt"] is None
    assert "Reply" not in result["message"]
    return result


def test_block_and_content_free_event_preserve_original_reason(cfg):
    first = error(evaluate(cfg))
    assert first["reason"] == "projected_tokens_exceeded"
    assert first["projected_tokens"] > 10_000
    assert first["projected_cost_usd"] > 0
    assert first["pending_id"].startswith("tpg_")
    assert first["operator_approval_available"] is True
    rows = unattended.events(cfg.audit_db_path)
    assert len(rows) == 1
    event = rows[0]
    assert event["event_id"] == first["escalation_event_id"]
    assert event["schema_version"] == 1
    assert event["reason"] == first["reason"]
    assert event["projected_tokens"] == first["projected_tokens"]
    assert event["session_id"] == SESSION
    assert not any(key in json.dumps(rows) for key in ("raw_request", "Authorization", "xxxx"))
    for _ in range(3):
        repeated = error(evaluate(cfg))
        assert repeated["reason"] == first["reason"]
        assert repeated["escalation_event_id"] == first["escalation_event_id"]
    assert len(unattended.events(cfg.audit_db_path)) == 1
    assert unattended.events(cfg.audit_db_path, after=event["event_id"]) == []


@pytest.mark.parametrize(
    "reply",
    [
        "yes",
        "no",
        "[TIP: allow=once] yes",
        "[TIP: bypass=on] yes",
        "[TIP: cancel]",
        "[TIP: allow=session] yes",
    ],
)
@pytest.mark.parametrize("headers", [HEADERS, {}])
def test_conversation_cannot_resolve_unattended_hold(cfg, reply, headers):
    first = evaluate(cfg)
    second = error(evaluate(cfg, body(reply), headers))
    assert second["pending_id"] == first.pending_id
    assert PendingStore(cfg.audit_db_path).get_by_id(first.pending_id).status == "pending"


def test_operator_approval_releases_only_the_identical_request_once(cfg):
    first = evaluate(cfg)
    store = PendingStore(cfg.audit_db_path)
    assert store.approve_unattended(first.pending_id)
    assert not store.approve_unattended(first.pending_id)
    assert error(evaluate(cfg, body("yes")))["pending_id"] == first.pending_id
    assert error(evaluate(cfg, body("y" * 40_000)))["pending_id"] == first.pending_id
    assert evaluate(cfg, target="/v1/chat/completions").kind == "block"
    assert evaluate(cfg, session="other").kind == "block"
    released = evaluate(cfg)
    assert released.kind == "replay"
    assert released.body == body()
    assert released.pending_id == first.pending_id
    assert not store.approve_unattended(first.pending_id)
    assert evaluate(cfg).kind == "block"


def test_concurrent_redemption_has_exactly_one_winner(cfg):
    first = evaluate(cfg)
    store = PendingStore(cfg.audit_db_path)
    assert store.approve_unattended(first.pending_id)

    def redeem(_):
        return store.consume_unattended(
            first.pending_id,
            session_id=SESSION,
            request_hash=hash_request(body(), MODEL),
            target_url="/v1/responses",
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        winners = list(pool.map(redeem, range(12)))
    assert sum(result is not None for result in winners) == 1


@pytest.mark.parametrize("approve_first", [False, True])
def test_expiry_cannot_be_approved_or_redeemed(cfg, approve_first):
    first = evaluate(cfg)
    store = PendingStore(cfg.audit_db_path)
    if approve_first:
        assert store.approve_unattended(first.pending_id)
    with sqlite3.connect(cfg.audit_db_path) as conn:
        conn.execute("UPDATE pending_requests SET expires_at=0")
    assert not store.approve_unattended(first.pending_id)
    assert (
        store.consume_unattended(
            first.pending_id,
            session_id=SESSION,
            request_hash=hash_request(body(), MODEL),
            target_url="/v1/responses",
        )
        is None
    )
    assert error(evaluate(cfg))["operator_approval_available"] is False


def test_hard_block_cannot_be_approved(cfg):
    cfg.hard_block_tokens = 12_000
    denied = error(evaluate(cfg))
    assert denied["original_error_type"] == "tokenpak_spend_guard_hard_blocked"
    assert denied["pending_id"] is None
    assert denied["operator_approval_available"] is False
    assert len(unattended.events(cfg.audit_db_path)) == 1


def test_tightened_hard_ceiling_survives_operator_approval(cfg):
    first = evaluate(cfg)
    assert PendingStore(cfg.audit_db_path).approve_unattended(first.pending_id)
    cfg.hard_block_tokens = 12_000
    assert evaluate(cfg).kind == "hard_block"


def test_interactive_flow_unchanged_and_can_opt_into_unattended(cfg):
    first = evaluate(cfg, headers={})
    assert json.loads(first.response_body)["error"]["approval_prompt"] == "Proceed? Yes / No"
    assert not PendingStore(cfg.audit_db_path).approve_unattended(first.pending_id)
    assert evaluate(cfg, body("yes"), headers={}).kind == "replay"
    second = evaluate(cfg, headers={})
    error(evaluate(cfg, body("yes")))
    assert evaluate(cfg, body("yes"), headers={}).kind == "block"
    assert PendingStore(cfg.audit_db_path).approve_unattended(second.pending_id)
    assert evaluate(cfg).kind == "replay"


def test_missing_session_remains_reportable_without_approval(cfg):
    denied = error(evaluate(cfg, session=""))
    assert denied["pending_id"] is None
    assert denied["operator_approval_available"] is False
    assert denied["reason"] == "projected_tokens_exceeded"


def test_event_write_failure_is_explicit_and_never_forwards(cfg, monkeypatch):
    def broken(*_):
        raise sqlite3.OperationalError("fixture")

    monkeypatch.setattr(unattended, "_record_event", broken)
    denied = error(evaluate(cfg))
    assert denied["escalation_recorded"] is False
    assert denied["escalation_error"] == "audit_store_unavailable"


def test_rolling_cap_block_has_one_operator_approvable_hold(cfg, monkeypatch):
    from tokenpak.proxy.spend_guard import rolling_caps

    cfg.rolling_caps_enabled = True
    breach = rolling_caps.CapBreach("per_agent_cost_usd", "fixture", 3600, 5, 5, 1, 30)
    monkeypatch.setattr(rolling_caps, "check_rolling_caps_and_admit", lambda **_: (breach, None))
    headers = {**HEADERS, "X-Tokenpak-Agent": "fixture"}
    first = error(evaluate(cfg, headers=headers))
    assert first["reason"] == "rolling_cap_breach:per_agent_cost_usd"
    assert first["projected_tokens"] > 10_000
    assert PendingStore(cfg.audit_db_path).approve_unattended(first["pending_id"])
    assert evaluate(cfg, headers=headers).kind == "replay"
    cfg.hard_block_tokens = 12_000
    assert evaluate(cfg, headers=headers).kind == "hard_block"


def test_store_failure_reports_original_reason_without_approval(cfg, monkeypatch):
    def broken(*_, **__):
        raise sqlite3.OperationalError("fixture")

    monkeypatch.setattr(PendingStore, "store", broken)
    denied = error(evaluate(cfg))
    assert denied["reason"] == "projected_tokens_exceeded"
    assert denied["pending_id"] is None
    assert denied["operator_approval_available"] is False
    assert denied["escalation_recorded"] is True


def test_public_wrapper_state_failure_is_terminal_in_unattended_mode(cfg, monkeypatch):
    from tokenpak.proxy.spend_guard import evaluate as public_evaluate

    def broken(*_, **__):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(orchestrator, "_evaluate", broken)
    monkeypatch.setattr("tokenpak.proxy.spend_guard.time.sleep", lambda _: None)
    denied = error(public_evaluate(body(), MODEL, SESSION, HEADERS, config=cfg))
    assert denied["reason"] == "guard_state_busy"
    assert denied["pending_id"] is None
    assert denied["escalation_recorded"] is True


def test_error_reporting_failure_cannot_escape_the_public_guard(cfg, monkeypatch):
    from tokenpak.proxy.spend_guard import evaluate as public_evaluate

    def broken(*_, **__):
        raise RuntimeError("fixture")

    monkeypatch.setattr(orchestrator, "_evaluate", broken)
    monkeypatch.setattr(unattended, "finalize_block", broken)
    denied = public_evaluate(body(), MODEL, SESSION, HEADERS, config=cfg)
    assert denied.kind == "block"
    assert denied.http_status == 402
    result = json.loads(denied.response_body)["error"]
    assert result["retryable"] is False
    assert result["escalation_recorded"] is False


@pytest.mark.parametrize("directive", ["[TIP: allow=once]", "[TIP: bypass=on]"])
def test_tip_cannot_preapprove_unattended_request(cfg, directive):
    error(evaluate(cfg, body(directive + " " + "x" * 40_000)))


def test_unattended_header_is_never_forwarded():
    from tokenpak.proxy.headers import forward_headers

    for route in ("claude_code", "sdk", "unknown"):
        result = forward_headers({**HEADERS, "Content-Type": "application/json"}, route, True)
        assert unattended.HEADER.lower() not in {key.lower() for key in result}


def test_events_on_new_home_do_not_create_state(tmp_path):
    path = tmp_path / "absent" / "guard.db"
    assert unattended.events(str(path)) == []
    assert not path.parent.exists()


def test_cli_approval_and_event_cursor(cfg, monkeypatch, capsys):
    from tokenpak._cli_core import build_parser

    monkeypatch.setattr("tokenpak.proxy.spend_guard.policy.load_config", lambda: cfg)
    first = evaluate(cfg)
    parser = build_parser()
    args = parser.parse_args(["spend-guard", "events", "--json"])
    args.func(args)
    result = json.loads(capsys.readouterr().out)
    assert result["next_cursor"] == result["events"][0]["event_id"]
    args = parser.parse_args(["spend-guard", "approve", first.pending_id, "--json"])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["provider_called"] is False
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert "error" in json.loads(capsys.readouterr().out)
    assert evaluate(cfg).kind == "replay"


def test_opt_in_is_per_request_not_proxy_environment(cfg, monkeypatch):
    monkeypatch.setenv(unattended.ENV, "1")
    result = evaluate(cfg, headers={})
    assert json.loads(result.response_body)["error"]["approval_prompt"] == "Proceed? Yes / No"


def test_launchers_inject_opt_in_only_for_proxy_route(monkeypatch):
    from tokenpak.companion.codex import launcher

    monkeypatch.setattr(launcher, "_local_proxy_is_healthy", lambda: True)
    monkeypatch.setenv(unattended.ENV, "1")
    args, routed = launcher._with_tokenpak_proxy_route([])
    assert routed
    assert f'model_providers.tokenpak.http_headers."{unattended.HEADER}"="1"' in args
    command_args, _ = launcher._with_tokenpak_proxy_route(["exec", "--", "fixture prompt"])
    assert command_args.index(
        f'model_providers.tokenpak.http_headers."{unattended.HEADER}"="1"'
    ) < command_args.index("--")
    overridden, routed = launcher._with_tokenpak_proxy_route(["-c", 'model_provider="other"'])
    assert not routed
    assert unattended.HEADER not in str(overridden)
    env = {unattended.ENV: "1", "ANTHROPIC_CUSTOM_HEADERS": "X-Custom: retained"}
    unattended.claude_headers(env)
    unattended.claude_headers(env)
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == f"X-Custom: retained\n{unattended.HEADER}: 1"
    monkeypatch.delenv(unattended.ENV)
    assert unattended.HEADER not in str(launcher._with_tokenpak_proxy_route([]))


# Reuse the existing mock-only HTTP domain, including its destination allowlist.
from tests.proxy.spend_guard import test_approval_scope as approval_fixtures  # noqa: E402

proxy_domain = approval_fixtures.proxy_domain
state_dir = approval_fixtures.state_dir


@pytest.mark.needs_proxy
@pytest.mark.timeout(120)
def test_proxy_blocks_then_releases_one_mock_upstream_call(proxy_domain):
    proxy, upstream, received, db = proxy_domain

    def post(data, path="/v1/responses"):
        conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=30)
        try:
            conn.request(
                "POST",
                f"http://127.0.0.1:{upstream.server_port}{path}",
                data,
                {
                    **HEADERS,
                    "session-id": SESSION,
                    "Content-Type": "application/json",
                    "Authorization": "Bearer fixture-only",
                },
            )
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    status, data = post(body())
    assert status == 402
    first = json.loads(data)["error"]
    assert post(body("yes"))[0] == 402
    assert received == []
    assert PendingStore(db).approve_unattended(first["pending_id"])
    assert post(body(), "/v1/chat/completions")[0] == 402
    assert post(body())[0] == 200
    for _ in range(3):
        assert post(body())[0] == 402
    assert received == [body()]
