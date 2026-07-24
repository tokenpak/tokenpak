"""Shared helpers for tests that drive a real ProxyServer subprocess.

Used by test_concurrent_data_path.py and test_crash_durability.py. Not a
test module (leading underscore keeps pytest from collecting it).

Design notes:
  - The per-test "home" lives on tmpfs (/dev/shm) when available. The
    monitor.db schema migration issues ~20 ALTER TABLE statements, each
    with journal fsyncs; on a loaded ext4 disk that alone can exceed the
    per-test timeout. tmpfs makes it deterministic.
  - monitor.db is pre-seeded with the product's own initializer
    (tokenpak.proxy.monitor.Monitor) in the *test* process so that
    (a) the TOKENPAK_DB env candidate is a valid DB the resolver accepts,
    and (b) the subprocess skips the ALTER storm at startup.
  - The subprocess env is built from scratch (not inherited) so host-level
    TOKENPAK_* / API-key variables cannot change proxy behavior.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = Path(__file__).with_name("_stub_proxy_launcher.py")

# Generous: covers cold-start lazy imports on a loaded/shared host. The
# stub upstream itself answers in microseconds.
FIRST_REQUEST_TIMEOUT = 60.0
STARTUP_TIMEOUT = 30.0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_test_home() -> Path:
    """Per-test throwaway home dir, on tmpfs when the platform has it."""
    shm = Path("/dev/shm")
    if shm.is_dir() and os.access(shm, os.W_OK):
        return Path(tempfile.mkdtemp(prefix="tokenpak-proxy-test-", dir=shm))
    return Path(tempfile.mkdtemp(prefix="tokenpak-proxy-test-"))


def seed_monitor_db(db_path: Path) -> None:
    """Create a fully-migrated monitor.db using the product initializer."""
    from tokenpak.proxy.monitor import Monitor

    Monitor(str(db_path))


class ProxyProc:
    """A real ProxyServer subprocess wired to a stub upstream."""

    def __init__(self, stub_url: str, *, extra_env: dict[str, str] | None = None):
        self.home = make_test_home()
        self.db_path = self.home / "monitor.db"
        seed_monitor_db(self.db_path)
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.stdout_path = self.home / "proxy-stdout.log"
        self.stderr_path = self.home / "proxy-stderr.log"
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "PYTHONPATH": str(REPO_ROOT),
            "TOKENPAK_TEST_STUB_UPSTREAM": stub_url,
            "TOKENPAK_PORT": str(self.port),
            "TOKENPAK_DB": str(self.db_path),
            # Keep the data path focused: no spend-guard veto, no capture.
            "TOKENPAK_SPEND_GUARD_ENABLED": "0",
        }
        if extra_env:
            env.update(extra_env)
        self._stdout_f = open(self.stdout_path, "wb")
        self._stderr_f = open(self.stderr_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(LAUNCHER)],
            env=env,
            stdout=self._stdout_f,
            stderr=self._stderr_f,
        )

    # -- lifecycle ---------------------------------------------------------

    def wait_ready(self, timeout: float = STARTUP_TIMEOUT) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"proxy subprocess exited rc={self.proc.returncode} during startup; "
                    f"stderr:\n{self.stderr()}"
                )
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=0.5)
                s.close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(
            f"proxy did not open port {self.port} within {timeout}s; stderr:\n{self.stderr()}"
        )

    def sigkill(self) -> None:
        self.proc.kill()
        self.proc.wait(timeout=10)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self._stdout_f.close()
        self._stderr_f.close()

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    # -- I/O -----------------------------------------------------------------

    def stderr(self) -> str:
        self._stderr_f.flush()
        return self.stderr_path.read_text(errors="replace")

    def post_message(
        self,
        content: str,
        *,
        request_id: str | None = None,
        request_id_header: str = "X-Request-ID",
        extra_headers: dict[str, str] | None = None,
        timeout: float = FIRST_REQUEST_TIMEOUT,
    ):
        """POST a small non-streaming /v1/messages request."""
        return self.post_messages(
            [{"role": "user", "content": content}],
            request_id=request_id,
            request_id_header=request_id_header,
            extra_headers=extra_headers,
            timeout=timeout,
        )

    def post_messages(
        self,
        messages: list[dict[str, str]],
        *,
        request_id: str | None = None,
        request_id_header: str = "X-Request-ID",
        extra_headers: dict[str, str] | None = None,
        timeout: float = FIRST_REQUEST_TIMEOUT,
    ):
        """POST a non-streaming /v1/messages conversation.

        Returns (status, headers, body). Uses http.client rather than
        urllib.request because urllib rewrites header casing
        (``key.capitalize()`` -> "X-request-id") and these tests need to
        control the exact bytes of the request-id header name.
        """
        body = json.dumps(
            {
                "model": "claude-sonnet-4-5",
                "max_tokens": 32,
                "messages": messages,
            }
        ).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.putrequest("POST", "/v1/messages")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("x-api-key", "test-key")
            conn.putheader("Content-Length", str(len(body)))
            if request_id is not None:
                conn.putheader(request_id_header, request_id)
            for name, value in (extra_headers or {}).items():
                conn.putheader(name, value)
            conn.endheaders(body)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    # -- monitor.db ----------------------------------------------------------

    def row_count(self) -> int:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)
        try:
            return conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        finally:
            conn.close()

    def wait_row_count(self, expected: int, timeout: float = 15.0) -> int:
        """Poll until the ledger holds >= expected rows (async write queue)."""
        deadline = time.time() + timeout
        n = self.row_count()
        while n < expected and time.time() < deadline:
            time.sleep(0.2)
            n = self.row_count()
        return n


def assert_no_exceptions_in_stderr(proxy: ProxyProc) -> None:
    """Fail if the proxy subprocess logged tracebacks or handler errors."""
    err = proxy.stderr()
    markers = (
        "Traceback (most recent call last)",
        "Proxy error",
        "DB write error",
        "DB write dropped",  # post-#470 monitor drop marker — keep deliberate drops visible
    )
    hits = [m for m in markers if m in err]
    assert not hits, f"proxy stderr contains error marker(s) {hits}:\n{err}"
