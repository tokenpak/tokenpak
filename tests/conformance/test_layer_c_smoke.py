"""Layer C — narrow HTTP-path smoke.

Per DECISION-SC-06-C: the legacy proxy/server.py HTTP request path does
not currently route through ``services.routing_service.BackendSelector``
— the SC-03 ``TOKENPAK_PROVIDER_STUB=loopback`` selector override does
not reach the httpx pool. Wiring that would require a proxy refactor
explicitly ruled out of SC scope ("no loopback redesign unless an
unavoidable blocker appears"). Layer C is therefore narrowed to the
truthful coverage available today:

1. ``ProxyServer`` boot triggers ``notify_capability_published(
   'tip-proxy', SELF_CAPABILITIES_PROXY)`` via SC-02 wiring.
2. ``Monitor.log`` disk artifacts round-trip cleanly — rows written
   to monitor.db AND the observer fires the same schema-valid row.
3. ``JournalStore.write_entry`` disk artifacts round-trip — rows
   persist + observer fires the schema-valid row.

Full HTTP-request-through-proxy-to-loopback smoke is carried forward
as a follow-up (see SC-07/SC+1 open item in the phase closeout).
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from tokenpak_tip_validator import validate_against

from tokenpak.companion.journal.store import JournalStore
from tokenpak.core.contracts.capabilities import SELF_CAPABILITIES_PROXY
from tokenpak.proxy.monitor import Monitor

from .conftest import installed_validator_knows_schema


pytestmark = [pytest.mark.conformance, pytest.mark.smoke]


def _free_port() -> int:
    """Find a currently-unused localhost port.

    Classic pattern: bind to port 0, read the OS-assigned port, close.
    Small TOCTOU window is acceptable for single-test smoke coverage.
    """
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def test_proxy_startup_publishes_capabilities(conformance_observer):
    """ProxyServer.start emits the tip-proxy capability observer event.

    Does NOT call serve_forever — just enters .start(blocking=False)
    briefly and stops. The capability publication happens at the top
    of start() (SC-02), before the listening socket even opens. We
    pick a free ephemeral port so this does not conflict with a
    locally-running TokenPak proxy on the default 8766.
    """
    # Import here to keep Layer A/B tests import-cheap.
    from tokenpak.proxy.server import ProxyServer

    port = _free_port()
    ps = ProxyServer(host="127.0.0.1", port=port)
    try:
        ps.start(blocking=False)
    finally:
        try:
            ps.stop()
        except Exception:
            pass

    assert conformance_observer["capabilities"], (
        "ProxyServer.start should have published tip-proxy capabilities"
    )
    profile, caps = conformance_observer["capabilities"][-1]
    assert profile == "tip-proxy"
    assert frozenset(caps) == SELF_CAPABILITIES_PROXY


def test_monitor_log_disk_artifact_and_observer_agree(conformance_observer):
    """Monitor.log writes the requests row to SQLite AND the observer
    sees the same schema-valid TIP row.

    Drains the queue synchronously by calling stop_write_queue if it
    exists; otherwise sleeps briefly for the background thread.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "monitor.db"
        m = Monitor(db_path=str(db_path))
        m.log(
            model="claude-opus-4-7",
            input_tokens=100,
            output_tokens=25,
            cost=0.01,
            latency_ms=123,
            status_code=200,
            endpoint="https://api.anthropic.com/v1/messages",
            cache_read_tokens=50,
            cache_creation_tokens=0,
            cache_origin="proxy",
            request_id="smoke-monitor-001",
        )

        # Drain the async write queue. Write worker is daemon+1s poll,
        # so a short sleep is bounded.
        import time as _t
        for _ in range(20):
            _t.sleep(0.1)
            try:
                conn = sqlite3.connect(str(db_path))
                row_count = conn.execute(
                    "SELECT COUNT(*) FROM requests"
                ).fetchone()[0]
                conn.close()
                if row_count >= 1:
                    break
            except sqlite3.OperationalError:
                continue

        # Disk artifact: row landed.
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT cache_origin, model, input_tokens FROM requests"
        ).fetchall()
        conn.close()
        assert rows, "no row written to monitor.db"
        disk_origin, disk_model, disk_in = rows[-1]
        assert disk_origin == "proxy"
        assert disk_model == "claude-opus-4-7"
        assert disk_in == 100

    # Observer: same call fired a TIP-shaped row.
    assert conformance_observer["telemetry"], "observer saw no row"
    tip_row = conformance_observer["telemetry"][-1]
    assert tip_row["request_id"] == "smoke-monitor-001"
    assert tip_row["cache_origin"] == "proxy"
    assert tip_row["model"] == "claude-opus-4-7"
    assert validate_against("telemetry-event", tip_row).ok


@pytest.mark.skipif(
    not installed_validator_knows_schema("companion-journal-row"),
    reason=(
        "installed tokenpak-tip-validator predates companion-journal-row "
        "schema (SC-01). Install validator from a registry checkout to run: "
        "pip install -e ./registry."
    ),
)
def test_journal_store_disk_artifact_and_observer_agree(conformance_observer):
    """JournalStore.write_entry persists to SQLite AND the observer sees
    the same schema-valid companion-journal-row.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "journal.db"
        js = JournalStore(db_path=db_path)
        js.write_entry("smoke-session", "test content", entry_type="auto")

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT session_id, entry_type, content FROM entries"
        ).fetchall()
        conn.close()
        assert rows, "no row written to journal.db"
        sid, etype, content = rows[-1]
        assert sid == "smoke-session"
        assert etype == "auto"
        assert content == "test content"

    # Observer captured a TIP-shaped row.
    assert conformance_observer["journal"], "observer saw no journal row"
    row = conformance_observer["journal"][-1]
    assert row["session_id"] == "smoke-session"
    assert row["entry_type"] == "auto"
    assert validate_against("companion-journal-row", row).ok
