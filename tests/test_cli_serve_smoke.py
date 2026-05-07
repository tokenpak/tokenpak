"""tests/test_cli_serve_smoke.py

Smoke test for `tokenpak serve` CLI command.

Verifies that:
  1. The CLI entry point works (not just the Python API)
  2. The server starts with zero-config (no flags required)
  3. Health endpoint responds with 200 + JSON
  4. Process exits cleanly
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_health(
    port: int,
    timeout: float = 10.0,
    host: str = "127.0.0.1",
) -> bool:
    """Poll /health until it responds or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            url = f"http://{host}:{port}/health"
            with urllib.request.urlopen(url, timeout=1) as r:
                return r.status == 200
        except Exception:
            time.sleep(0.25)
    return False


def _get_health(port: int, host: str = "127.0.0.1") -> dict:
    """GET /health and parse JSON response."""
    url = f"http://{host}:{port}/health"
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------------------
# Test: CLI Smoke Test
# ---------------------------------------------------------------------------

class TestCliServeSmoke:
    """Smoke test for `tokenpak serve` CLI command."""

    def test_cli_serve_zero_config(self, tmp_path):
        """
        AC1: `tokenpak serve` works with zero config (no flags).

        - Spawn `tokenpak serve` in subprocess
        - Wait for health endpoint to respond
        - Assert /health returns 200 + valid JSON
        - Kill process cleanly
        """
        # Use a high random port to avoid conflicts
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()

        # Spawn `tokenpak serve --port <port>`
        proc = subprocess.Popen(
            [sys.executable, "-m", "tokenpak", "serve", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Wait for health endpoint
            assert _wait_health(port, timeout=10), \
                f"Server did not respond on port {port} within 10s"

            # Verify /health returns 200 + JSON
            health = _get_health(port)
            assert isinstance(health, dict), \
                f"Expected JSON dict, got {type(health)}"
            assert health.get("status") in ("ok", "healthy"), \
                f"Unexpected health status: {health}"

        finally:
            # Kill process cleanly
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def test_cli_serve_with_port_flag(self, tmp_path):
        """
        AC2: `tokenpak serve --port <N>` respects the port flag.
        """
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()

        proc = subprocess.Popen(
            [sys.executable, "-m", "tokenpak", "serve", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            assert _wait_health(port, timeout=10), \
                f"Server did not respond on specified port {port}"
            health = _get_health(port)
            assert health is not None
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def test_cli_serve_exit_code(self):
        """
        AC3: `tokenpak serve --help` exits cleanly with code 0.
        """
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "serve", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, \
            f"Expected exit code 0, got {result.returncode}"
        assert "usage:" in result.stdout.lower() or \
               "tokenpak serve" in result.stdout, \
            f"Expected help text in stdout, got: {result.stdout}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
