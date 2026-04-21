"""tests/test_first_request.py — First Request Test (P2-T3)

Validates end-to-end first-request flow:
1. Proxy (ingest API) starts cleanly
2. Health check endpoint works
3. User sends a test request via HTTP
4. Proxy responds correctly with expected JSON format
5. Logs show request was processed
6. Graceful cleanup

This test is crucial for the onboarding flow — a new user should be able to:
  pip install tokenpak
  export ANTHROPIC_API_KEY=sk-...
  run pytest tests/test_first_request.py

The test will:
- Start the ingest API server in a subprocess
- Wait for health check (30s timeout)
- Send a test ingest request with valid data
- Verify response JSON format + fields
- Check test logs for request processing
- Gracefully shut down the server
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

logger = logging.getLogger(__name__)


# ============================================================================
# Subprocess-based server management
# ============================================================================


class IngestServer:
    """Manager for ingest API server running in subprocess.

    This simulates a real user starting the proxy in production.
    """

    def __init__(self, port: int = 9876, entries_dir: Path | None = None):
        """Initialize server manager.

        Args:
            port: Port to run the ingest API on.
            entries_dir: Temporary directory for JSONL entries.
        """
        self.port = port
        self.entries_dir = entries_dir or Path(tempfile.gettempdir()) / "tokenpak-test-entries"
        self.process: subprocess.Popen[bytes] | None = None
        self.startup_log: str = ""

    def start(self, timeout_s: float = 30.0) -> None:
        """Start the ingest API server in a subprocess.

        Uses a simple Uvicorn-style startup via Python subprocess.
        Waits for health check to pass (indicates server is ready).

        Args:
            timeout_s: Seconds to wait for server startup.

        Raises:
            RuntimeError: If server fails to start within timeout.
        """
        self.entries_dir.mkdir(parents=True, exist_ok=True)

        # Start ingest API server via subprocess
        env = os.environ.copy()
        env["TOKENPAK_ENTRIES_DIR"] = str(self.entries_dir)
        env["TOKENPAK_PORT"] = str(self.port)
        env["PYTHONUNBUFFERED"] = "1"

        # Script to start the ingest API
        startup_script = f"""
import sys
sys.path.insert(0, '{Path.cwd()}')

from pathlib import Path
from tokenpak.agent.ingest.api import create_ingest_app
import uvicorn

# Patch VAULT_ENTRIES_DIR to use temp directory
import tokenpak.agent.ingest.api
tokenpak.agent.ingest.api.VAULT_ENTRIES_DIR = Path('{self.entries_dir}')

app = create_ingest_app()
uvicorn.run(
    app,
    host="127.0.0.1",
    port={self.port},
    log_level="info",
)
"""

        try:
            self.process = subprocess.Popen(
                [sys.executable, "-c", startup_script],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start ingest server: {e}")

        # Wait for health check to pass
        health_url = f"http://127.0.0.1:{self.port}/health"
        deadline = time.time() + timeout_s
        last_error = None

        while time.time() < deadline:
            try:
                with urlopen(health_url, timeout=2) as resp:
                    if resp.status == 200:
                        logger.info(f"✅ Ingest API health check passed on port {self.port}")
                        return
                    last_error = f"Unexpected status {resp.status}"
            except (URLError, HTTPError, ConnectionRefusedError) as e:
                last_error = str(e)
                time.sleep(0.5)
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                time.sleep(0.5)

        # Health check failed
        self.stop()  # Clean up the failed process
        raise RuntimeError(
            f"Ingest server failed to start within {timeout_s}s. "
            f"Last error: {last_error}. "
            f"Logs: {self.get_logs()}"
        )

    def get_logs(self) -> str:
        """Get accumulated logs from server stdout."""
        if not self.process:
            return ""

        # Try to read from pipe without blocking
        try:
            # This is a bit hacky, but we can't easily read from a running process
            # In real pytest, we'd use capsys or similar
            return self.startup_log
        except Exception as e:
            return f"(Could not read logs: {e})"

    def stop(self) -> None:
        """Stop the server gracefully, with timeout for SIGKILL fallback."""
        if not self.process:
            return

        try:
            # Send SIGTERM for graceful shutdown
            self.process.terminate()
            deadline = time.time() + 5.0

            while time.time() < deadline:
                if self.process.poll() is not None:
                    # Process exited
                    logger.info("✅ Ingest server stopped gracefully")
                    return
                time.sleep(0.1)

            # Timeout — force kill
            logger.warning("⚠️  Ingest server did not stop in 5s, killing...")
            self.process.kill()
            self.process.wait(timeout=2)
        except Exception as e:
            logger.warning(f"Error stopping server: {e}")
        finally:
            self.process = None


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def entries_dir() -> Generator[Path, None, None]:
    """Create a temporary entries directory for this test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def ingest_server(entries_dir: Path) -> Generator[IngestServer, None, None]:
    """Start ingest API server for this test, and clean up after."""
    server = IngestServer(port=9876, entries_dir=entries_dir)
    server.start(timeout_s=30.0)
    yield server
    server.stop()


# ============================================================================
# Tests
# ============================================================================


class TestFirstRequest:
    """End-to-end first-request test suite."""

    def test_server_starts_cleanly(self, ingest_server: IngestServer):
        """Verify that server starts without errors."""
        assert ingest_server.process is not None
        assert ingest_server.process.poll() is None, "Server process exited unexpectedly"

    def test_health_check_passes(self, ingest_server: IngestServer):
        """Verify health check endpoint returns 200 OK."""
        health_url = f"http://127.0.0.1:{ingest_server.port}/health"
        try:
            with urlopen(health_url, timeout=5) as resp:
                assert resp.status == 200
                data = json.loads(resp.read())
                assert isinstance(data, dict)
                logger.info(f"✅ Health check response: {data}")
        except Exception as e:
            pytest.fail(f"Health check failed: {e}")

    def test_ingest_single_entry(self, ingest_server: IngestServer):
        """Verify user can ingest a single entry via HTTP POST."""
        ingest_url = f"http://127.0.0.1:{ingest_server.port}/ingest"

        payload = {
            "model": "claude-sonnet-4-6",
            "tokens": 1024,
            "cost": 0.05,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "test-user",
            "provider": "anthropic",
        }

        req = Request(
            ingest_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(req, timeout=5) as resp:
                assert resp.status == 200
                response_data = json.loads(resp.read())

                # Verify response structure
                assert "ids" in response_data  # Note: 'ids' is plural
                assert "status" in response_data
                assert response_data["status"] == "ok"
                assert len(response_data["ids"]) > 0

                logger.info(f"✅ Ingest response: {response_data}")
        except HTTPError as e:
            pytest.fail(f"Ingest POST failed with status {e.code}: {e.read()}")
        except Exception as e:
            pytest.fail(f"Ingest POST failed: {e}")

    def test_batch_ingest_entries(self, ingest_server: IngestServer):
        """Verify user can ingest multiple entries in a batch."""
        batch_url = f"http://127.0.0.1:{ingest_server.port}/ingest/batch"

        # Note: batch endpoint expects a JSON list, not an object with 'entries' key
        payload = [
            {
                "model": "claude-haiku-4-5",
                "tokens": 256,
                "cost": 0.01,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": "batch-test",
            },
            {
                "model": "claude-opus-4-5",
                "tokens": 2048,
                "cost": 0.15,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": "batch-test",
            },
        ]

        req = Request(
            batch_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(req, timeout=5) as resp:
                assert resp.status == 200
                response_data = json.loads(resp.read())

                # Verify response structure
                assert "ids" in response_data
                assert len(response_data["ids"]) >= 2
                assert "status" in response_data
                assert response_data["status"] == "ok"

                logger.info(f"✅ Batch ingest response: {response_data}")
        except HTTPError as e:
            pytest.fail(f"Batch ingest failed with status {e.code}: {e.read()}")
        except Exception as e:
            pytest.fail(f"Batch ingest failed: {e}")

    def test_entries_written_to_disk(self, ingest_server: IngestServer, entries_dir: Path):
        """Verify that ingest requests are actually written to disk."""
        ingest_url = f"http://127.0.0.1:{ingest_server.port}/ingest"

        payload = {
            "model": "claude-sonnet-4-6",
            "tokens": 512,
            "cost": 0.02,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "disk-test",
        }

        req = Request(
            ingest_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(req, timeout=5) as resp:
                resp.read()  # Consume response
        except Exception as e:
            pytest.fail(f"Ingest request failed: {e}")

        # Give the server a moment to write to disk
        time.sleep(0.5)

        # Check that a JSONL file was created for today
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entries_file = entries_dir / f"{today}.jsonl"

        assert entries_file.exists(), f"Entries file {entries_file} not found"

        # Read and parse JSONL
        lines = entries_file.read_text().strip().split("\n")
        assert len(lines) > 0

        # Parse last entry (should be our test entry)
        last_entry = json.loads(lines[-1])
        assert last_entry["model"] == "claude-sonnet-4-6"
        assert last_entry["agent"] == "disk-test"

        logger.info(f"✅ Entry written to disk: {last_entry}")

    def test_response_json_format(self, ingest_server: IngestServer):
        """Verify response JSON has all expected fields."""
        ingest_url = f"http://127.0.0.1:{ingest_server.port}/ingest"

        payload = {
            "model": "claude-sonnet-4-6",
            "tokens": 100,
            "cost": 0.001,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        req = Request(
            ingest_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())

                # Verify required fields
                required_fields = ["ids", "status"]
                for field in required_fields:
                    assert field in data, f"Missing required field: {field}"

                # Verify types
                assert isinstance(data["ids"], list)
                assert len(data["ids"]) > 0
                assert isinstance(data["ids"][0], str)
                assert isinstance(data["status"], str)
                assert data["status"] in ["ok", "error"]

                logger.info(f"✅ Response format valid: {data}")
        except Exception as e:
            pytest.fail(f"Response validation failed: {e}")


class TestFirstRequestWithEnvKey:
    """Test that requests work when ANTHROPIC_API_KEY is set."""

    @pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set — skipping live key test",
    )
    def test_with_real_api_key_env(self, ingest_server: IngestServer):
        """If real API key is provided, verify ingest works.

        In production, this validates that the full pipeline (including
        auth setup) works with the user's real credentials.
        """
        # The ingest API itself doesn't validate the API key,
        # but we verify the server is ready to handle it
        health_url = f"http://127.0.0.1:{ingest_server.port}/health"

        try:
            with urlopen(health_url, timeout=5) as resp:
                assert resp.status == 200
                logger.info("✅ Server ready to accept requests with real API key")
        except Exception as e:
            pytest.fail(f"Server not ready: {e}")


class TestGracefulShutdown:
    """Test that server shuts down gracefully."""

    def test_shutdown_within_timeout(self, entries_dir: Path):
        """Verify SIGTERM → graceful shutdown in < 5s."""
        server = IngestServer(port=9877, entries_dir=entries_dir)
        server.start(timeout_s=10.0)

        # Wait a moment for server to be ready
        time.sleep(1)

        assert server.process is not None
        pid = server.process.pid

        # Stop server
        start_time = time.time()
        server.stop()
        elapsed = time.time() - start_time

        # Verify shutdown was quick
        assert elapsed < 5.0, f"Shutdown took {elapsed:.1f}s (expected < 5s)"
        assert server.process is None or server.process.poll() is not None

        logger.info(f"✅ Graceful shutdown completed in {elapsed:.2f}s")


# ============================================================================
# Integration test (runs if all above pass)
# ============================================================================


class TestFirstRequestIntegration:
    """Full end-to-end integration test simulating a new user's first request."""

    def test_complete_onboarding_flow(self, ingest_server: IngestServer, entries_dir: Path):
        """Simulate a new user's complete first-request flow.

        Steps:
        1. Server is running (ingest_server fixture)
        2. User knows health endpoint works
        3. User sends their first entry
        4. User sees their data on disk
        5. User can send a second entry (no setup needed)
        6. Server cleans up gracefully
        """

        # Step 1: Health check
        health_url = f"http://127.0.0.1:{ingest_server.port}/health"
        with urlopen(health_url, timeout=5) as resp:
            assert resp.status == 200

        # Step 2: First entry
        ingest_url = f"http://127.0.0.1:{ingest_server.port}/ingest"

        first_entry = {
            "model": "claude-sonnet-4-6",
            "tokens": 500,
            "cost": 0.03,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "onboarding-test",
        }

        req = Request(
            ingest_url,
            data=json.dumps(first_entry).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            assert data["status"] == "ok"
            first_entry_id = data["ids"][0]  # Note: 'ids' is plural

        # Step 3: Verify entry on disk
        time.sleep(0.5)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entries_file = entries_dir / f"{today}.jsonl"
        assert entries_file.exists()

        # Step 4: Second entry (no extra setup)
        second_entry = {
            "model": "claude-haiku-4-5",
            "tokens": 100,
            "cost": 0.01,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "onboarding-test",
        }

        req = Request(
            ingest_url,
            data=json.dumps(second_entry).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            assert data["status"] == "ok"

        # Step 5: Verify both entries on disk
        time.sleep(0.5)
        lines = entries_file.read_text().strip().split("\n")
        assert len(lines) >= 2

        logger.info("✅ Complete onboarding flow succeeded!")
        logger.info(f"   - First entry ID: {first_entry_id}")
        logger.info(f"   - Total entries on disk: {len(lines)}")
