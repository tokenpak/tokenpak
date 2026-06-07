"""
Unit tests for ``tokenpak.licensing.usage_meter``.

Covers acceptance criteria 4, 6, 7, 10:
  - Local buffering of events to a JSONL spool
  - Flush posts to the license server
  - Graceful degradation when server is unreachable (events stay buffered)
  - 24h heartbeat thread starts/stops cleanly
  - Server-rejected events (4xx) are dropped, not retried forever

Tests use only stdlib + a small in-process HTTP server stub. No live network.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
from pathlib import Path

import pytest

from tokenpak.licensing.usage_meter import (
    UsageMeter,
    _reset_default_meter_for_testing,
    flush_default,
    get_default_meter,
    record_usage,
)

# ---------------------------------------------------------------------------
# In-process HTTP stub
# ---------------------------------------------------------------------------


class _UsageServerStub:
    """Tiny HTTP server that records POSTs to /usage and lets tests configure
    the response status. Runs in a background thread on a random port."""

    def __init__(self, status: int = 201, body: dict | None = None):
        self.events: list[dict] = []
        self.status = status
        self.body = body or {"ok": True}
        self._server: socketserver.TCPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_UsageServerStub":
        events = self.events
        get_status = lambda: self.status  # noqa: E731
        get_body = lambda: self.body  # noqa: E731

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                if self.path != "/usage":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8") if length else ""
                try:
                    events.append(json.loads(body))
                except Exception:
                    events.append({"_raw": body})
                self.send_response(get_status())
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(get_body()).encode("utf-8"))

            def log_message(self, *_):  # noqa: D401
                """Silence stdlib's per-request stderr logging in tests."""

        self._server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_default_meter():
    _reset_default_meter_for_testing()
    yield
    _reset_default_meter_for_testing()


@pytest.fixture
def spool(tmp_path: Path) -> Path:
    return tmp_path / "spool"


# ---------------------------------------------------------------------------
# Acceptance criterion 4 — meter exists and records to spool
# ---------------------------------------------------------------------------


def test_record_appends_to_spool(spool: Path):
    meter = UsageMeter(license_id="TPAK-TEST-AAAA", spool_dir=spool)
    meter.record(tokens_in=100, tokens_out=20, model="claude-sonnet-4-6")
    meter.record(tokens_in=50, tokens_out=10, model="claude-haiku-4-5")

    spool_file = spool / "buffer.jsonl"
    assert spool_file.exists()
    lines = [json.loads(line) for line in spool_file.read_text().splitlines() if line]
    assert len(lines) == 2
    assert lines[0]["tokens_in"] == 100
    assert lines[1]["model"] == "claude-haiku-4-5"
    # license_id and timestamp should always be present
    assert lines[0]["license_id"] == "TPAK-TEST-AAAA"
    assert lines[0]["ts"]


def test_record_drops_when_no_license(spool: Path, caplog):
    meter = UsageMeter(license_id=None, spool_dir=spool)
    meter.record(tokens_in=100, tokens_out=20, model="m")
    spool_file = spool / "buffer.jsonl"
    assert not spool_file.exists()


def test_record_explicit_license_id_overrides_default(spool: Path):
    meter = UsageMeter(license_id="default-lic", spool_dir=spool)
    meter.record(tokens_in=1, tokens_out=2, model="m", license_id="override-lic")
    rows = [
        json.loads(line)
        for line in (spool / "buffer.jsonl").read_text().splitlines()
        if line
    ]
    assert rows[0]["license_id"] == "override-lic"


# ---------------------------------------------------------------------------
# Acceptance criterion 6 — heartbeat lifecycle
# ---------------------------------------------------------------------------


def test_heartbeat_starts_and_stops(spool: Path):
    meter = UsageMeter(
        license_id="TPAK-X",
        spool_dir=spool,
        heartbeat_seconds=3600,
    )
    meter.start_heartbeat()
    # Idempotent — second start does not error and does not spawn a 2nd thread
    meter.start_heartbeat()
    assert meter._heartbeat_thread is not None
    assert meter._heartbeat_thread.is_alive()

    meter.stop_heartbeat()
    assert meter._heartbeat_thread is None


# ---------------------------------------------------------------------------
# Acceptance criterion 7 — flush + graceful degradation
# ---------------------------------------------------------------------------


def test_flush_posts_buffered_events_to_server(spool: Path):
    with _UsageServerStub(status=201) as srv:
        meter = UsageMeter(
            license_id="TPAK-X",
            spool_dir=spool,
            server_url=srv.url,
            http_timeout=2.0,
        )
        meter.record(tokens_in=1, tokens_out=2, model="m")
        meter.record(tokens_in=3, tokens_out=4, model="m")

        result = meter.flush()
        assert result["posted"] == 2
        assert result["remaining"] == 0
        assert result["errors"] == 0

        # All events arrived at the stub
        assert len(srv.events) == 2
        # Spool emptied and removed
        assert not (spool / "buffer.jsonl").exists()


def test_flush_buffers_when_server_unreachable(spool: Path):
    # Pick a port we know is not listening
    bogus_url = "http://127.0.0.1:1"
    meter = UsageMeter(
        license_id="TPAK-X",
        spool_dir=spool,
        server_url=bogus_url,
        http_timeout=0.5,
    )
    meter.record(tokens_in=1, tokens_out=2, model="m")
    meter.record(tokens_in=3, tokens_out=4, model="m")

    result = meter.flush()
    assert result["posted"] == 0
    assert result["remaining"] == 2
    assert result["errors"] == 1  # bails after first failure

    # Spool still on disk for replay
    spool_file = spool / "buffer.jsonl"
    assert spool_file.exists()
    rows = [json.loads(line) for line in spool_file.read_text().splitlines() if line]
    assert len(rows) == 2


def test_flush_replays_buffered_events_on_recovery(spool: Path):
    # First flush fails → events stay buffered
    bogus_url = "http://127.0.0.1:1"
    meter = UsageMeter(
        license_id="TPAK-X",
        spool_dir=spool,
        server_url=bogus_url,
        http_timeout=0.5,
    )
    meter.record(tokens_in=10, tokens_out=20, model="m")
    meter.record(tokens_in=30, tokens_out=40, model="m")
    res1 = meter.flush()
    assert res1["remaining"] == 2

    # Server comes back — change server_url and flush again
    with _UsageServerStub(status=201) as srv:
        meter.server_url = srv.url.rstrip("/")
        res2 = meter.flush()
        assert res2["posted"] == 2
        assert res2["remaining"] == 0
        assert len(srv.events) == 2
        assert {e["tokens_in"] for e in srv.events} == {10, 30}


def test_flush_empty_buffer_is_noop(spool: Path):
    meter = UsageMeter(license_id="TPAK-X", spool_dir=spool)
    result = meter.flush()
    assert result == {"posted": 0, "remaining": 0, "errors": 0}


def test_4xx_event_is_dropped_not_retried(spool: Path):
    with _UsageServerStub(status=404, body={"detail": "license_not_found"}) as srv:
        meter = UsageMeter(
            license_id="TPAK-DEAD",
            spool_dir=spool,
            server_url=srv.url,
            http_timeout=2.0,
        )
        meter.record(tokens_in=1, tokens_out=2, model="m")
        result = meter.flush()
        # 4xx → treated as posted (event dropped); buffer cleared.
        assert result["posted"] == 1
        assert result["remaining"] == 0


# ---------------------------------------------------------------------------
# Module-level singleton behaviour
# ---------------------------------------------------------------------------


def test_record_usage_uses_singleton(spool: Path, monkeypatch: pytest.MonkeyPatch):
    # Force the default meter to use our temp spool
    monkeypatch.setattr(
        "tokenpak.licensing.usage_meter.DEFAULT_SPOOL_DIR", spool
    )
    record_usage(
        tokens_in=5, tokens_out=6, model="claude-sonnet-4-6", license_id="TPAK-X"
    )
    record_usage(
        tokens_in=7, tokens_out=8, model="claude-sonnet-4-6", license_id="TPAK-X"
    )
    spool_file = spool / "buffer.jsonl"
    assert spool_file.exists()
    rows = [json.loads(line) for line in spool_file.read_text().splitlines() if line]
    assert len(rows) == 2

    # And flush_default delegates to the singleton
    with _UsageServerStub(status=201) as srv:
        meter = get_default_meter()
        meter.server_url = srv.url.rstrip("/")
        result = flush_default()
        assert result["posted"] == 2


# ---------------------------------------------------------------------------
# Spool durability — malformed lines are skipped, valid ones replay
# ---------------------------------------------------------------------------


def test_spool_skips_malformed_lines(spool: Path):
    spool.mkdir(parents=True, exist_ok=True)
    spool_file = spool / "buffer.jsonl"
    spool_file.write_text(
        json.dumps(
            {
                "license_id": "TPAK-X",
                "tokens_in": 1,
                "tokens_out": 2,
                "model": "m",
                "ts": "2026-04-28T00:00:00Z",
            }
        )
        + "\n"
        + "not json at all\n"
        + json.dumps(
            {
                "license_id": "TPAK-X",
                "tokens_in": 3,
                "tokens_out": 4,
                "model": "m",
                "ts": "2026-04-28T00:00:01Z",
            }
        )
        + "\n"
    )

    with _UsageServerStub(status=201) as srv:
        meter = UsageMeter(
            license_id="TPAK-X",
            spool_dir=spool,
            server_url=srv.url,
            http_timeout=2.0,
        )
        result = meter.flush()
        assert result["posted"] == 2
        assert {e["tokens_in"] for e in srv.events} == {1, 3}


# ---------------------------------------------------------------------------
# Bridge to telemetry/metering — forwarding does not crash on import errors
# ---------------------------------------------------------------------------


def test_telemetry_metering_bridge_forwards(spool: Path, monkeypatch: pytest.MonkeyPatch):
    """telemetry.metering.UsageMeter.record() forwards into the licensing meter."""
    monkeypatch.setattr(
        "tokenpak.licensing.usage_meter.DEFAULT_SPOOL_DIR", spool
    )

    from tokenpak.telemetry.metering import UsageMeter as TelemetryMeter

    tm = TelemetryMeter(key_id="TPAK-FORWARD-Z")
    tm.record(
        model="claude-sonnet-4-6",
        input_tokens=11,
        output_tokens=22,
        saved_tokens=0,
        request_type="chat",
    )
    tm.flush()  # waits for the local sqlite write thread

    # The licensing meter spool should have received the forward
    spool_file = spool / "buffer.jsonl"
    assert spool_file.exists()
    rows = [json.loads(line) for line in spool_file.read_text().splitlines() if line]
    assert any(
        r["license_id"] == "TPAK-FORWARD-Z"
        and r["tokens_in"] == 11
        and r["tokens_out"] == 22
        for r in rows
    )
