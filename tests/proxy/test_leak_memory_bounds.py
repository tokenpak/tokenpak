"""Per-request memory work in the proxy is bounded and incremental.

- The cache-invalidator session cache keeps a compact signature of each
  session's last request under a byte cap, instead of up to 100 raw bodies
  of 1-3 MB each, and each body is parsed once rather than the previous body
  being parsed again on every POST.
- The session-economics calibration corpus folds in only rows appended since
  the last read instead of re-reading the whole ledger on every call, and
  always equals a fresh full parse.
"""

from __future__ import annotations

import http.client
import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tokenpak.proxy import session_forecast_calibration as cal

# ---------------------------------------------------------------------------
# Cache-invalidator session cache
# ---------------------------------------------------------------------------


def test_session_cache_holds_at_most_its_byte_cap(monkeypatch) -> None:
    from tokenpak.proxy.cache_invalidator import _SessionBodyCache

    monkeypatch.setenv("TOKENPAK_CACHE_INVALIDATOR_MAX_BYTES", str(4 << 20))
    cache = _SessionBodyCache(maxsize=100)
    for i in range(20):
        cache.put(f"session-{i}", b"x" * (1 << 20))
    retained = [cache.get(f"session-{i}") for i in range(20)]
    held = sum(len(v) for v in retained if isinstance(v, (bytes, bytearray)))
    assert held <= 4 << 20
    # The most recent sessions survive, the oldest are evicted first.
    assert retained[-1] is not None and retained[0] is None


def test_oversized_entry_is_not_kept(monkeypatch) -> None:
    from tokenpak.proxy.cache_invalidator import _SessionBodyCache

    monkeypatch.setenv("TOKENPAK_CACHE_INVALIDATOR_MAX_BYTES", "1024")
    cache = _SessionBodyCache(maxsize=100)
    cache.put("small", b"y" * 100)
    cache.put("huge", b"z" * 4096)
    assert cache.get("huge") is None
    assert cache.get("small") == b"y" * 100


def _body(tools: list[str], *, system: str = "sys", size: int = 0, images: bool = False) -> bytes:
    content: list[dict[str, object]] = [{"type": "text", "text": "q" * size}]
    if images:
        content.append({"type": "image", "source": {"type": "base64", "data": "AA=="}})
    return json.dumps(
        {
            "model": "claude-opus-4-8",
            "system": system,
            "tools": [{"name": t, "input_schema": {"type": "object"}} for t in tools],
            "messages": [{"role": "user", "content": content}],
        }
    ).encode()


@pytest.mark.parametrize(
    "prev,curr",
    [
        (_body(["a", "b"]), _body(["a", "b"], size=50)),
        (_body(["a", "b"]), _body(["b", "a"])),
        (_body(["a"]), _body(["a", "c"])),
        (_body(["a"], system="one"), _body(["a"], system="two")),
        (_body(["a"]), _body(["a"], images=True)),
        (b"not json", _body(["a"])),
        (_body(["a"]), b"[1, 2]"),
    ],
    ids=["same", "reordered-tools", "added-tool", "system", "images", "bad-prev", "non-object"],
)
def test_signature_diff_matches_the_body_detector(prev: bytes, curr: bytes) -> None:
    from tokenpak.proxy.cache_invalidator import (
        _body_signature,
        _detect_cache_invalidators,
        _diff_signatures,
    )

    p, c = _body_signature(prev), _body_signature(curr)
    via_signatures = _diff_signatures(p, c) if p is not None and c is not None else []
    assert via_signatures == _detect_cache_invalidators(prev, curr)


@pytest.mark.timeout(180)
def test_proxy_caches_a_signature_not_the_request_body(
    tmp_path, monkeypatch, stub_upstream
) -> None:
    from tests.proxy._proxy_subprocess import free_port
    from tokenpak.proxy import cache_invalidator, router
    from tokenpak.proxy import server as server_module
    from tokenpak.proxy.monitor import Monitor
    from tokenpak.proxy.server import ProxyServer

    db = tmp_path / "monitor.db"
    Monitor(str(db))
    monkeypatch.setenv("TOKENPAK_DB", str(db))
    monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "0")
    monkeypatch.setenv("TOKENPAK_SPEND_GUARD_ENABLED", "0")
    monkeypatch.setenv("TOKENPAK_DLP_ENABLED", "0")
    monkeypatch.setitem(
        router.PROVIDER_URLS, "anthropic", f"http://127.0.0.1:{stub_upstream.server_port}"
    )
    monkeypatch.setattr(cache_invalidator, "_SESSION_CACHE", None)
    http_server_type = server_module._ThreadedHTTPServer
    monkeypatch.setattr(
        server_module,
        "_ThreadedHTTPServer",
        lambda address, handler: http_server_type((address[0], 0), handler),
    )
    proxy = ProxyServer(host="127.0.0.1", port=free_port())
    # Log the stub upstream exactly like a provider host.
    proxy._intercept_hosts = set(router.INTERCEPT_HOSTS) | {"127.0.0.1"}
    proxy.start(blocking=False)
    try:
        assert proxy._server is not None
        port = int(proxy._server.server_address[1])
        session = "signature-session"
        for _ in range(3):
            body = _body(["read", "write"], size=256 << 10)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            try:
                conn.request(
                    "POST",
                    "/v1/messages",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": "test-key",
                        "anthropic-version": "2023-06-01",
                        "X-Claude-Code-Session-Id": session,
                    },
                )
                resp = conn.getresponse()
                resp.read()
                assert resp.status == 200
            finally:
                conn.close()
    finally:
        proxy.stop()
    entry = cache_invalidator._get_session_cache().get(session)
    assert entry is not None
    assert not isinstance(entry, (bytes, bytearray)), "the raw request body is cached"


# ---------------------------------------------------------------------------
# Incremental calibration corpus
# ---------------------------------------------------------------------------

_COLUMNS = (
    "session_id, model, reasoning_effort, timestamp, status_code, input_tokens, "
    "output_tokens, cache_read_tokens, cache_creation_tokens"
)


def _ledger(path: Path) -> None:
    from tokenpak.proxy.monitor import Monitor

    Monitor(str(path))


def _append(path: Path, rows: list[tuple[object, ...]]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executemany(f"INSERT INTO requests ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()


def _rows(
    rnd: random.Random, base: datetime, sessions: int, n: int, *, ordered: bool = False
) -> list[tuple[object, ...]]:
    out = []
    for i in range(n):
        s = rnd.randrange(sessions)
        model = "model-b" if rnd.random() < 0.03 else "model-a"
        # Live rows are stamped at completion and written in order; the
        # unordered mix exercises the re-parse fallback.
        minutes = i if ordered else rnd.randrange(0, 600)
        ts = base + timedelta(minutes=minutes)
        out.append(
            (
                f" hist-{s} " if rnd.random() < 0.05 else f"hist-{s}",
                model,
                "unknown",
                ts.isoformat(),
                200 if rnd.random() < 0.95 else 999,
                rnd.randrange(0, 4000),
                rnd.randrange(0, 900),
                0,
                0,
            )
        )
    return out


def _fresh(path: Path) -> tuple[cal._CorpusEntry, ...]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        return tuple(cal._parse_corpus(conn))
    finally:
        conn.close()


def test_incremental_corpus_always_equals_a_full_parse(tmp_path, monkeypatch) -> None:
    db = tmp_path / "monitor.db"
    _ledger(db)
    monkeypatch.setattr(cal, "_CORPUS_CACHE", {})
    rnd = random.Random(20261018)
    base = datetime(2026, 9, 1)
    for step in range(12):
        _append(db, _rows(rnd, base + timedelta(hours=step), 40, rnd.randrange(1, 60)))
        if step == 6:  # retention pruning: a deletion forces a re-parse
            conn = sqlite3.connect(str(db))
            conn.execute("DELETE FROM requests WHERE id % 7 = 0")
            conn.commit()
            conn.close()
        assert cal._corpus_for(str(db)) == _fresh(db), f"diverged at step {step}"


def test_appending_rows_does_not_reparse_the_ledger(tmp_path, monkeypatch) -> None:
    db = tmp_path / "monitor.db"
    _ledger(db)
    rnd = random.Random(7)
    base = datetime(2026, 9, 1)
    _append(db, _rows(rnd, base, 30, 600, ordered=True))
    monkeypatch.setattr(cal, "_CORPUS_CACHE", {})
    real_parse = cal._parse_corpus
    full_parses = []

    def counting_parse(conn):
        full_parses.append(1)
        return real_parse(conn)

    monkeypatch.setattr(cal, "_parse_corpus", counting_parse)
    cal._corpus_for(str(db))
    assert len(full_parses) == 1
    for i in range(5):
        later = base + timedelta(days=1, minutes=10 * i)
        _append(db, _rows(random.Random(i), later, 30, 3, ordered=True))
        cal._corpus_for(str(db))
    assert len(full_parses) == 1, "every append re-read the whole ledger"
    monkeypatch.setattr(cal, "_parse_corpus", real_parse)
    assert cal._corpus_for(str(db)) == _fresh(db)


def test_corpus_state_is_bounded_and_stays_exact(tmp_path, monkeypatch) -> None:
    db = tmp_path / "monitor.db"
    _ledger(db)
    monkeypatch.setattr(cal, "_CORPUS_CACHE", {})
    monkeypatch.setattr(cal, "MAX_SESSIONS", 2)  # window of 6 sessions
    monkeypatch.setattr(cal, "_CORPUS_MAX_TRACKED_TURNS", 60)
    base = datetime(2026, 9, 1)
    rows = []
    for s in range(30):
        for t in range(6):
            ts = base + timedelta(hours=s, minutes=t)
            rows.append((f"s{s:02d}", "model-a", "unknown", ts.isoformat(), 200, 100 + t, 10, 0, 0))
    _append(db, rows)
    assert cal._corpus_for(str(db)) == _fresh(db)
    state = cal._CORPUS_CACHE[str(db)]
    tracked = sum(len(a.costs) for a in state.sessions.values() if a.costs is not None)
    assert tracked <= 60
    # An evicted (old) session resumes: the corpus must still match a full parse.
    resumed = base + timedelta(days=3)
    _append(db, [("s00", "model-a", "unknown", resumed.isoformat(), 200, 500, 50, 0, 0)])
    assert cal._corpus_for(str(db)) == _fresh(db)
    # A window session turns mixed and drops out: an evicted one must take its place.
    _append(
        db,
        [
            (
                "s29",
                "model-b",
                "unknown",
                (resumed + timedelta(minutes=1)).isoformat(),
                200,
                1,
                1,
                0,
                0,
            )
        ],
    )
    assert cal._corpus_for(str(db)) == _fresh(db)


def test_refresh_rebuilds_only_the_sessions_that_changed(tmp_path, monkeypatch) -> None:
    db = tmp_path / "monitor.db"
    _ledger(db)
    base = datetime(2026, 9, 1)
    _append(db, _rows(random.Random(11), base, 40, 800, ordered=True))
    monkeypatch.setattr(cal, "_CORPUS_CACHE", {})
    cal._corpus_for(str(db))
    parsed: list[object] = []
    real_parse_ts = cal._parse_ts_utc

    def counting_parse_ts(value: object):
        parsed.append(value)
        return real_parse_ts(value)

    monkeypatch.setattr(cal, "_parse_ts_utc", counting_parse_ts)
    later = (base + timedelta(days=2)).isoformat()
    _append(db, [("hist-3", "model-a", "unknown", later, 200, 500, 50, 0, 0)])
    corpus = cal._corpus_for(str(db))
    assert len(parsed) <= 1, f"a one-row append re-derived {len(parsed)} sessions"
    assert corpus == _fresh(db)
