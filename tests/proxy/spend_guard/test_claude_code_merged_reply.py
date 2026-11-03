# SPDX-License-Identifier: Apache-2.0
"""A reply typed into the tool-result message answers exactly one hold.

After a 402 on a request whose newest message carries a tool result, Claude
Code appends the operator's typed reply to that same user message, so the
content reads ``[tool_result, text]``. Each further reply typed after a
re-prompt is appended to the same message again. The typed text after the
last tool result is the reply. Text inside a tool result is tool output and
never a reply, and the merged message stops counting once newer messages
follow it. These tests use the real estimator and policy and, for the proxy
tests, a loopback mock upstream.
"""

from __future__ import annotations

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
SESSION = "claude-code-turn"
# Each tool output alone pushes a request over the test block band.
TOOL_OUTPUT_CHARS = 60_000
RELEASED = ("forward", "forward_modified", "replay")


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def _reminder(text: str) -> dict:
    return _text(f"<system-reminder>\n{text}\n</system-reminder>")


def _tool_output(call: str) -> str:
    unit = f"[{call}] synthetic log line\n"
    return (unit * (TOOL_OUTPUT_CHARS // len(unit) + 1))[:TOOL_OUTPUT_CHARS]


class ClaudeCodeTurn:
    """One Claude Code turn: tool calls, and replies typed after a 402."""

    def __init__(self) -> None:
        self.messages: list[dict] = [
            {"role": "user", "content": [_reminder("context"), _text("refactor the module")]}
        ]
        self.calls = 0
        self.tool_step()

    def tool_step(self, output=None) -> "ClaudeCodeTurn":
        self.calls += 1
        call = f"toolu_{self.calls}"
        self.messages += [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": call, "name": "Read", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call,
                        "content": _tool_output(call) if output is None else output,
                    }
                ],
            },
        ]
        return self

    def reply(self, *blocks: dict) -> "ClaudeCodeTurn":
        """Append typed blocks to the newest user message, as Claude Code does."""
        self.messages[-1]["content"] = [*self.messages[-1]["content"], *blocks]
        return self

    def body(self) -> bytes:
        doc = {"model": MODEL, "max_tokens": 1024, "messages": self.messages}
        return json.dumps(doc, separators=(",", ":")).encode()


def _newest(*content: dict) -> bytes:
    """A body whose newest message is a user message with ``content``."""
    turn = ClaudeCodeTurn()
    turn.messages[-1]["content"] = list(content)
    return turn.body()


def _tool_result(content, call: str = "toolu_1") -> dict:
    return {"type": "tool_result", "tool_use_id": call, "content": content}


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


def _audit_events(db_path: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT event_type FROM spend_guard_audit ORDER BY id").fetchall()
    return [row[0] for row in rows]


@pytest.fixture
def state_dir():
    """Guard state on tmpfs when available; these tests check logic, not fsync."""
    shm = Path("/dev/shm")
    base = str(shm) if shm.is_dir() and os.access(shm, os.W_OK) else None
    path = Path(tempfile.mkdtemp(prefix="tokenpak-merged-reply-", dir=base))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fresh_sweep(monkeypatch):
    monkeypatch.setattr(orchestrator, "_LAST_EXPIRE_SWEEP", 0.0)


def _evaluate(body: bytes, cfg: SpendGuardConfig):
    return orchestrator.evaluate(body, MODEL, SESSION, {}, config=cfg)


# ── intent: the typed text after the last tool result ──────────────────────


@pytest.mark.parametrize(("word", "expected"), [("yes", Intent.POSITIVE), ("no", Intent.NEGATIVE)])
def test_reply_typed_after_the_tool_result_is_the_answer(word, expected):
    assert parse_intent(ClaudeCodeTurn().reply(_text(word)).body()) is expected


@pytest.mark.parametrize(
    "typed",
    [
        [_reminder("hook context"), _text("yes")],
        [_text("yes"), _reminder("hook context")],
        [_reminder("a"), _text("yes"), _reminder("b")],
    ],
    ids=["reminder-before", "reminder-after", "reminders-around"],
)
def test_client_reminders_around_the_typed_reply_are_skipped(typed):
    assert parse_intent(ClaudeCodeTurn().reply(*typed).body()) is Intent.POSITIVE


@pytest.mark.parametrize(
    "output",
    [
        "yes",
        "no",
        [_text("yes")],
        [_text("no")],
        [_text("ok"), _text("yes")],
    ],
    ids=["str-yes", "str-no", "blocks-yes", "blocks-no", "blocks-ok-yes"],
)
def test_tool_output_is_never_a_reply(output):
    # Nothing typed after the tool result: the words are tool output.
    assert parse_intent(_newest(_tool_result(output))) is Intent.AMBIGUOUS
    # Client reminders after it are not typed text either.
    assert parse_intent(_newest(_tool_result(output), _reminder("yes"))) is Intent.AMBIGUOUS


@pytest.mark.parametrize(
    "content",
    [
        [_text("yes"), _tool_result("ok")],
        [_tool_result("ok", "toolu_a"), _text("yes"), _tool_result("ok", "toolu_b")],
        [_text("no"), _tool_result("ok")],
    ],
    ids=["yes-then-result", "yes-between-results", "no-then-result"],
)
def test_text_before_the_last_tool_result_is_not_a_reply(content):
    assert parse_intent(_newest(*content)) is Intent.AMBIGUOUS


@pytest.mark.parametrize(
    "typed",
    [
        [_text("yes, but only the tests")],
        [_text("no yes")],
        [_text("yes"), _text("actually, check with me first")],
        [_reminder("yes")],
    ],
    ids=["qualified", "mixed", "later-qualifier", "reminder-only"],
)
def test_ambiguous_typed_reply_is_not_an_answer(typed):
    assert parse_intent(ClaudeCodeTurn().reply(*typed).body()) is Intent.AMBIGUOUS


@pytest.mark.parametrize("word", ["yes", "no"])
def test_plain_text_tool_output_without_a_tool_result_is_not_a_reply(word):
    # Some clients send tool output as plain text blocks, with no
    # tool_result block to mark where typed text starts.
    content = [_text("[read_file for 'notes.txt'] Result:"), _text(word)]
    assert parse_intent(_newest(*content)) is Intent.AMBIGUOUS


def test_merged_reply_stops_counting_once_newer_messages_follow():
    turn = ClaudeCodeTurn().reply(_text("yes"))
    assert parse_intent(turn.body()) is Intent.POSITIVE
    turn.tool_step()
    assert parse_intent(turn.body()) is Intent.AMBIGUOUS


# ── orchestrator: one merged yes, one replay ───────────────────────────────


@pytest.mark.parametrize("further", [3, 5])
@pytest.mark.parametrize("client_retries", [False, True])
def test_merged_yes_releases_exactly_one_held_request(state_dir, further, client_retries):
    cfg = _config(state_dir)
    turn = ClaudeCodeTurn()
    held = turn.body()
    assert _evaluate(held, cfg).kind == "block"

    replay = _evaluate(turn.reply(_text("yes")).body(), cfg)
    assert replay.kind == "replay"
    assert replay.body == held

    released = [replay.body]
    refused = 0
    for _ in range(further):
        later = turn.tool_step().body()  # the agent continues; nothing typed
        for _attempt in range(2 if client_retries else 1):
            out = _evaluate(later, cfg)
            if out.kind in RELEASED:
                released.append(out.body)
            else:
                assert out.http_status == 402
                refused += 1

    n = further + 1  # over-threshold requests in the turn, including the first
    assert released == [held]
    assert refused == (n - 1) * (2 if client_retries else 1)
    events = _audit_events(cfg.audit_db_path)
    assert events.count("replay") == 1
    # The original hold, then the first later request; every other later
    # request is re-prompted against that second hold.
    assert events.count("block") == 2
    assert events.count("block") - 1 + events.count("reprompt") == refused
    assert "cancel" not in events


def test_merged_no_cancels_the_hold(state_dir):
    cfg = _config(state_dir)
    turn = ClaudeCodeTurn()
    first = _evaluate(turn.body(), cfg)
    assert first.kind == "block"

    out = _evaluate(turn.reply(_text("no")).body(), cfg)
    assert (out.kind, out.http_status) == ("cancel", 200)
    store = PendingStore(cfg.audit_db_path)
    assert store.get_by_session(SESSION) is None
    assert store.get_by_id(first.pending_id).status == "discarded"
    assert _audit_events(cfg.audit_db_path) == ["block", "cancel"]


def test_resent_merged_approval_does_not_release_a_later_hold(state_dir):
    cfg = _config(state_dir)
    turn = ClaudeCodeTurn()
    held = turn.body()
    assert _evaluate(held, cfg).kind == "block"
    approval = turn.reply(_text("yes")).body()
    assert _evaluate(approval, cfg).kind == "replay"
    later = turn.tool_step().body()
    second = _evaluate(later, cfg)
    assert second.kind == "block"

    # The client re-sends the approving request verbatim: the same merged
    # yes, already used once. It must not release the new hold.
    retry = _evaluate(approval, cfg)
    assert (retry.kind, retry.http_status, retry.audit_event) == ("reprompt", 402, "stale_reply")
    assert PendingStore(cfg.audit_db_path).get_by_session(SESSION).pending_id == second.pending_id

    # A yes typed for the new hold releases it, and only it.
    out = _evaluate(turn.reply(_text("yes")).body(), cfg)
    assert out.kind == "replay"
    assert out.body == later


def test_reply_typed_after_a_reprompt_answers_the_hold(state_dir):
    cfg = _config(state_dir)
    turn = ClaudeCodeTurn()
    held = turn.body()
    assert _evaluate(held, cfg).kind == "block"

    # An ambiguous reply is re-prompted and resolves nothing.
    out = _evaluate(turn.reply(_text("yes, but only the tests")).body(), cfg)
    assert (out.kind, out.http_status) == ("reprompt", 402)
    assert PendingStore(cfg.audit_db_path).get_by_session(SESSION) is not None

    # Claude Code appends the next reply to the same message.
    out = _evaluate(turn.reply(_text("yes")).body(), cfg)
    assert out.kind == "replay"
    assert out.body == held


@pytest.mark.parametrize("word", ["yes", "no"])
def test_tool_output_reply_neither_releases_nor_cancels(state_dir, word):
    cfg = _config(state_dir)
    turn = ClaudeCodeTurn()
    first = _evaluate(turn.body(), cfg)
    assert first.kind == "block"

    # The agent's next tool result is the bare word, with nothing typed.
    for output in (word, [_text(word)]):
        out = _evaluate(turn.tool_step(output=output).body(), cfg)
        assert (out.kind, out.http_status) == ("reprompt", 402)

    store = PendingStore(cfg.audit_db_path)
    assert store.get_by_session(SESSION).pending_id == first.pending_id
    assert store.get_by_id(first.pending_id).status == "pending"
    events = _audit_events(cfg.audit_db_path)
    assert "replay" not in events and "cancel" not in events


# ── proxy: the mock upstream sees exactly one call ─────────────────────────


@pytest.fixture
def anthropic_proxy(state_dir, monkeypatch):
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
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": MODEL,
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 8, "output_tokens": 2},
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
        custom_urls={"anthropic": upstream_base},
        custom_hosts={upstream_base: "anthropic"},
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
            f"http://127.0.0.1:{upstream.server_port}/v1/messages",
            body,
            {
                "Content-Type": "application/json",
                "x-api-key": "test-only",
                "anthropic-version": "2023-06-01",
                "X-Claude-Code-Session-Id": SESSION,
            },
        )
        response = conn.getresponse()
        response.read()
        return response.status, response.getheader("X-TokenPak-Spend-Guard") or ""
    finally:
        conn.close()


@pytest.mark.needs_proxy
@pytest.mark.timeout(120)
def test_proxy_forwards_one_replay_for_one_merged_yes(anthropic_proxy):
    _, _, received, audit_db = anthropic_proxy
    turn = ClaudeCodeTurn()
    held = turn.body()

    assert _post(anthropic_proxy, held) == (402, "block")
    assert _post(anthropic_proxy, turn.reply(_text("yes")).body())[0] == 200

    statuses = []
    for _ in range(3):
        later = turn.tool_step().body()
        statuses.append(_post(anthropic_proxy, later))
        statuses.append(_post(anthropic_proxy, later))  # client retry

    assert [status for status, _ in statuses] == [402] * len(statuses)
    assert received == [held]
    events = _audit_events(audit_db)
    assert events.count("replay") == 1
    assert events.count("block") == 2


@pytest.mark.needs_proxy
@pytest.mark.timeout(120)
def test_proxy_merged_no_cancels_without_an_upstream_call(anthropic_proxy):
    _, _, received, audit_db = anthropic_proxy
    turn = ClaudeCodeTurn()

    assert _post(anthropic_proxy, turn.body()) == (402, "block")
    assert _post(anthropic_proxy, turn.reply(_text("no")).body())[0] == 200
    assert received == []
    assert _audit_events(audit_db) == ["block", "cancel"]
