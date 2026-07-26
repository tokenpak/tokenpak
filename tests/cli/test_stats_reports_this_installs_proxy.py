# SPDX-License-Identifier: Apache-2.0
"""`tokenpak stats` must report this install's proxy, not whatever is on 8766.

Found by running the built wheel in an isolated no-data environment: with
TOKENPAK_PORT set to a free port and an empty HOME, `stats` reported
"Requests: 2756 total, 3 errors" and "Uptime: 25h 45m" — the numbers of a
proxy running on the host's default port, belonging to a different install.

Two call sites resolved the proxy URL as
``os.environ.get("TOKENPAK_PROXY_URL", "http://127.0.0.1:8766")``, consulting
TOKENPAK_PROXY_URL but never TOKENPAK_PORT. Anyone running on a non-default
port was shown someone else's traffic as their own measured result — the
exact failure the measured-data work exists to prevent.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _FakeProxy(http.server.BaseHTTPRequestHandler):
    """Answers /health with a recognisable request count."""

    payload = {"status": "ok", "uptime_seconds": 4242, "requests_total": 999_111}

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def decoy_proxy():
    """A proxy on some port that is NOT the one the CLI is told to use."""
    port = _free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), _FakeProxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


def _run_stats(home: Path, **env_extra: str) -> str:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "NO_COLOR": "1",
        "TERM": "dumb",
    }
    env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", "stats"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=180,
    )
    return result.stdout + result.stderr


@pytest.fixture()
def clean_home(tmp_path: Path) -> Path:
    tpk = tmp_path / ".tpk"
    tpk.mkdir(parents=True)
    (tpk / ".seen_intro").touch()
    return tmp_path


def test_stats_does_not_read_a_proxy_on_a_different_port(clean_home, decoy_proxy) -> None:
    """The decoy stands in for the host's own proxy on the default port."""
    quiet_port = _free_port()  # nothing listens here
    out = _run_stats(clean_home, TOKENPAK_PORT=str(quiet_port))

    assert "999,111" not in out and "999111" not in out, (
        f"stats reported another proxy's request count:\n{out}"
    )
    assert "4242" not in out, f"stats reported another proxy's uptime:\n{out}"


def test_stats_reads_the_proxy_on_the_configured_port(clean_home, decoy_proxy) -> None:
    """The other half: pointed at a proxy, it must actually read it.

    Without this, "reports nothing" would pass the test above trivially.
    """
    out = _run_stats(clean_home, TOKENPAK_PORT=str(decoy_proxy))

    assert "999,111" in out or "999111" in out, (
        f"stats did not read the proxy on TOKENPAK_PORT={decoy_proxy}:\n{out}"
    )


def test_proxy_url_still_wins_over_port(clean_home, decoy_proxy) -> None:
    """TOKENPAK_PROXY_URL is the explicit override and keeps precedence."""
    quiet_port = _free_port()
    out = _run_stats(
        clean_home,
        TOKENPAK_PORT=str(quiet_port),
        TOKENPAK_PROXY_URL=f"http://127.0.0.1:{decoy_proxy}",
    )

    assert "999,111" in out or "999111" in out, f"explicit TOKENPAK_PROXY_URL was ignored:\n{out}"
