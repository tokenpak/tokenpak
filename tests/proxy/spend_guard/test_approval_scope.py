# SPDX-License-Identifier: Apache-2.0
"""One typed approval releases exactly one held request.

An agent's tool loop keeps re-sending the operator's ``yes`` as the last
user-role message while tool calls and results pile up after it. Reading
that stale ``yes`` as a fresh answer approved every later held request in
the turn. These tests replay that turn shape with the real estimator and
policy (and, for the proxy test, a mock upstream) and check that one
``yes`` yields one replay and every later over-threshold request stays
held.
"""

from __future__ import annotations

import gzip
import http.client
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tokenpak.proxy.spend_guard import orchestrator
from tokenpak.proxy.spend_guard.intent import Intent, parse_intent
from tokenpak.proxy.spend_guard.pending import PendingStore
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig

# No registry context window, so the flat per-request block band applies.
MODEL = "gpt-6-astra"
SESSION = "codex-turn"
# Each tool output alone pushes a request over the test block band.
TOOL_OUTPUT = "x" * 40_000


def _user(text: str) -> dict:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
    }


def _tool_step(n: int) -> list[dict]:
    return [
        {"type": "reasoning", "summary": []},
        {"type": "function_call", "name": "shell", "call_id": f"c{n}", "arguments": "{}"},
        {"type": "function_call_output", "call_id": f"c{n}", "output": TOOL_OUTPUT},
    ]


def _body(items: list[dict]) -> bytes:
    return json.dumps({"model": MODEL, "input": items}, separators=(",", ":")).encode()


def _turn(further: int) -> tuple[bytes, bytes, list[bytes]]:
    """Return (held request, typed yes, later tool-loop requests)."""
    history = [_user("refactor the module"), *_tool_step(0)]
    held = _body(history)
    history.append(_user("yes"))
    approval = _body(history)
    later = []
    for n in range(1, further + 1):
        history.extend(_tool_step(n))
        later.append(_body(history))
    return held, approval, later


def _config(state_dir) -> SpendGuardConfig:
    cfg = SpendGuardConfig()
    cfg.enabled = True
    cfg.audit_db_path = str(state_dir / "spend_guard.db")
    cfg.warn_tokens = 5_000
    cfg.block_tokens = 10_000
    cfg.hard_block_tokens = 10_000_000
    cfg.session_block_cost_usd = 0.0
    cfg.rolling_caps_enabled = False
    return cfg


def _audit_counts(db_path: str) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT event_type, count(*) FROM spend_guard_audit GROUP BY event_type"
        ).fetchall()
    return dict(rows)


@pytest.fixture
def state_dir():
    """Guard state on tmpfs when available.

    Every guard decision commits to SQLite several times; on a loaded host
    disk those fsyncs alone can exhaust the per-test timeout. These tests
    check approval logic, not durability.
    """
    shm = Path("/dev/shm")
    base = str(shm) if shm.is_dir() and os.access(shm, os.W_OK) else None
    path = Path(tempfile.mkdtemp(prefix="tokenpak-approval-scope-", dir=base))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fresh_sweep(monkeypatch):
    monkeypatch.setattr(orchestrator, "_LAST_EXPIRE_SWEEP", 0.0)


def _evaluate(body: bytes, cfg: SpendGuardConfig, session: str = SESSION):
    return orchestrator.evaluate(body, MODEL, session, {}, config=cfg)


# ── intent: the reply must be the newest input item ────────────────────────


def test_yes_followed_by_tool_items_is_not_an_answer():
    _, approval, later = _turn(1)
    assert parse_intent(approval) is Intent.POSITIVE
    assert parse_intent(later[0]) is Intent.AMBIGUOUS


@pytest.mark.parametrize(
    "newest",
    [
        {"role": "assistant", "content": "yes"},
        {"role": "tool", "tool_call_id": "c1", "content": "yes"},
    ],
)
def test_chat_completions_yes_must_be_newest_message(newest):
    body = json.dumps(
        {"model": MODEL, "messages": [{"role": "user", "content": "yes"}, newest]}
    ).encode()
    assert parse_intent(body) is Intent.AMBIGUOUS


def test_anthropic_tool_result_turn_is_not_an_answer():
    # Nothing typed after the tool result; its output is not a reply. A
    # reply typed after it is (test_claude_code_merged_reply.py).
    body = json.dumps(
        {
            "model": "claude-opus-4-7",
            "messages": [
                {"role": "user", "content": "yes"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "t1"}]},
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "yes"}],
                },
            ],
        }
    ).encode()
    assert parse_intent(body) is Intent.AMBIGUOUS


# ── orchestrator: one yes, one replay ──────────────────────────────────────


@pytest.mark.parametrize("further", [3, 5])
@pytest.mark.parametrize("client_retries", [False, True])
def test_one_yes_releases_exactly_one_held_request(state_dir, further, client_retries):
    cfg = _config(state_dir)
    held, approval, later = _turn(further)

    first = _evaluate(held, cfg)
    assert first.kind == "block"

    replay = _evaluate(approval, cfg)
    assert replay.kind == "replay"
    assert replay.body == held

    forwarded = 1  # the replay above
    refused = []
    for body in later:
        attempts = [body, body] if client_retries else [body]
        for attempt in attempts:
            out = _evaluate(attempt, cfg)
            if out.kind in ("forward", "forward_modified", "replay"):
                forwarded += 1
            else:
                assert out.http_status == 402
                refused.append(out.kind)

    n = further + 1  # over-threshold requests in the turn, including the first
    assert forwarded == 1
    assert len(refused) == (n - 1) * (2 if client_retries else 1)
    counts = _audit_counts(cfg.audit_db_path)
    assert counts.get("replay", 0) == 1
    # Two holds: the original request, then the first later one. Every
    # other later request is re-prompted against that second hold.
    assert counts.get("block", 0) == 2
    later_refusals = (counts["block"] - 1) + counts.get("reprompt", 0)
    assert later_refusals == len(refused)


def test_retried_approval_does_not_release_a_second_hold(state_dir):
    cfg = _config(state_dir)
    held, approval, later = _turn(1)

    assert _evaluate(held, cfg).kind == "block"
    assert _evaluate(approval, cfg).kind == "replay"
    # A concurrent request in the same session is held next.
    second = _evaluate(later[0], cfg)
    assert second.kind == "block"

    # The client retries the approving request verbatim: same trailing yes,
    # already used once. It must not release the new hold.
    retry = _evaluate(approval, cfg)
    assert retry.kind == "reprompt"
    assert retry.http_status == 402
    assert retry.audit_event == "stale_reply"
    pending = PendingStore(cfg.audit_db_path).get_by_session(SESSION)
    assert pending is not None and pending.pending_id == second.pending_id


def test_held_request_ending_in_approval_word_cannot_approve_itself(state_dir):
    cfg = _config(state_dir)
    held = _body([_user("start"), *_tool_step(0), _user("continue")])

    first = _evaluate(held, cfg)
    assert first.kind == "block"
    # A client retry re-sends the held request; its newest item is
    # "continue", but that text was typed before the hold existed.
    retry = _evaluate(held, cfg)
    assert retry.kind == "reprompt"
    assert retry.audit_event == "stale_reply"
    assert PendingStore(cfg.audit_db_path).get_by_session(SESSION) is not None


def test_a_new_yes_releases_the_next_hold(state_dir):
    cfg = _config(state_dir)
    history = [_user("refactor the module"), *_tool_step(0)]
    assert _evaluate(_body(history), cfg).kind == "block"
    history.append(_user("yes"))
    assert _evaluate(_body(history), cfg).kind == "replay"

    history.extend(_tool_step(1))
    second_held = _body(history)
    assert _evaluate(second_held, cfg).kind == "block"
    # The operator answers the second hold with another typed yes.
    history.append(_user("yes"))
    out = _evaluate(_body(history), cfg)
    assert out.kind == "replay"
    assert out.body == second_held


def test_no_still_discards_the_hold(state_dir):
    cfg = _config(state_dir)
    history = [_user("refactor the module"), *_tool_step(0)]
    assert _evaluate(_body(history), cfg).kind == "block"
    history.append(_user("no"))
    out = _evaluate(_body(history), cfg)
    assert out.kind == "cancel"
    assert PendingStore(cfg.audit_db_path).get_by_session(SESSION) is None


def test_stale_reply_does_not_cross_sessions(state_dir):
    cfg = _config(state_dir)
    held, approval, _ = _turn(0)
    assert _evaluate(held, cfg, "sess-a").kind == "block"
    assert _evaluate(approval, cfg, "sess-a").kind == "replay"
    # The same bytes in another session answer that session's own hold.
    assert _evaluate(held, cfg, "sess-b").kind == "block"
    assert _evaluate(approval, cfg, "sess-b").kind == "replay"


def test_existing_store_gains_answered_by_column(state_dir):
    db = state_dir / "spend_guard.db"
    with sqlite3.connect(db) as conn:
        # The table as earlier releases created it, with one live hold.
        conn.execute(
            """CREATE TABLE pending_requests (
                pending_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                request_hash TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                projected_tokens INTEGER NOT NULL DEFAULT 0,
                projected_cost_usd REAL NOT NULL DEFAULT 0.0,
                raw_request_blob BLOB NOT NULL,
                raw_request_headers TEXT NOT NULL DEFAULT '{}',
                target_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
            )"""
        )
        conn.execute(
            "INSERT INTO pending_requests (pending_id, session_id, created_at, expires_at,"
            " request_hash, raw_request_blob) VALUES ('tpg_old', ?, 0, 9e12, 'h-old', ?)",
            (SESSION, gzip.compress(b"held")),
        )

    store = PendingStore(str(db))
    assert store.get_by_session(SESSION).pending_id == "tpg_old"
    assert store.reply_seen(SESSION, "h-old")
    assert not store.reply_seen(SESSION, "h-new")
    assert store.consume("tpg_old", answered_by="h-new") is not None
    assert store.reply_seen(SESSION, "h-new")


# ── proxy: the mock upstream sees exactly one call ─────────────────────────


@pytest.fixture
def proxy_domain(state_dir, monkeypatch):
    from tokenpak.proxy import monitor as monitor_module
    from tokenpak.proxy import server as server_module

    assert monitor_module._stop_db_write_queue(timeout=3)
    cfg = _config(state_dir)
    # The proxy hook evaluates without an explicit config; hand it ours.
    monkeypatch.setattr(orchestrator, "load_config", lambda: cfg)
    monkeypatch.setenv("TOKENPAK_PASSTHROUGH", "1")
    monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "0")
    monkeypatch.setattr("tokenpak.proxy.config.MONITOR_DB", str(state_dir / "monitor.db"))
    monkeypatch.setattr(server_module, "INTERCEPT_HOSTS", {"127.0.0.1"})
    received: list[bytes] = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            reply = json.dumps(
                {
                    "id": "resp_test",
                    "object": "response",
                    "model": MODEL,
                    "output": [],
                    "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    proxy = server_module.ProxyServer(host="127.0.0.1", port=0)
    allowed_ports = {upstream.server_port}
    connect = socket.socket.connect

    def confined_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            assert address[0] in ("127.0.0.1", "::1") and address[1] in allowed_ports, (
                "unexpected network destination"
            )
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", confined_connect)
    upstream_base = f"http://127.0.0.1:{upstream.server_port}"
    proxy.router = server_module.ProviderRouter(
        custom_urls={"openai": upstream_base},
        custom_hosts={upstream_base: "openai"},
    )
    proxy.start(blocking=False)
    allowed_ports.add(proxy.port)
    yield proxy, upstream, received, cfg.audit_db_path
    proxy.stop()
    upstream.shutdown()
    upstream.server_close()
    worker.join(timeout=3)
    assert monitor_module._stop_db_write_queue(timeout=3)
    with monitor_module._DB_LOCK:
        if monitor_module._DB_CONNECTION is not None:
            monitor_module._DB_CONNECTION.close()
        monitor_module._DB_CONNECTION = None
        monitor_module._DB_CONNECTION_PATH = None


def _post(domain, body: bytes) -> tuple[int, str]:
    proxy, upstream, *_ = domain
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=30)
    try:
        conn.request(
            "POST",
            f"http://127.0.0.1:{upstream.server_port}/v1/responses",
            body,
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer test-only",
                "session-id": SESSION,
            },
        )
        response = conn.getresponse()
        response.read()
        return response.status, response.getheader("X-TokenPak-Spend-Guard") or ""
    finally:
        conn.close()


@pytest.mark.needs_proxy
@pytest.mark.timeout(120)
def test_proxy_forwards_one_replay_for_one_yes(proxy_domain):
    _, _, received, audit_db = proxy_domain
    held, approval, later = _turn(3)

    assert _post(proxy_domain, held) == (402, "block")
    assert _post(proxy_domain, approval)[0] == 200

    statuses = []
    for body in later:
        statuses.append(_post(proxy_domain, body))
        statuses.append(_post(proxy_domain, body))  # client retry

    assert [status for status, _ in statuses] == [402] * len(statuses)
    assert received == [held]
    counts = _audit_counts(audit_db)
    assert counts.get("replay", 0) == 1
    assert counts.get("block", 0) == 2
