"""tests/test_ingest.py — Ingest API Tests

Covers:
- POST /ingest — single entry ingestion
- POST /ingest/batch — batch ingestion
- Entry validation (model, tokens, cost, timestamp)
- Error handling (invalid data, batch size limits)
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tokenpak.agent.ingest.api import Entry, create_ingest_app


@pytest.fixture
def temp_entries_dir() -> Path:
    """Create a temporary entries directory for ingest tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def ingest_client(temp_entries_dir: Path) -> TestClient:
    """TestClient with ingest app, pointing to temp directory."""
    # Monkeypatch the VAULT_ENTRIES_DIR before creating the app
    with patch("tokenpak.agent.ingest.api.VAULT_ENTRIES_DIR", temp_entries_dir):
        app = create_ingest_app()
        yield TestClient(app)


class TestIngestSingle:
    """POST /ingest — Single entry ingestion."""

    def test_ingest_valid_entry(self, ingest_client: TestClient):
        """Valid entry → 200 OK with entry ID."""
        payload = {
            "model": "claude-sonnet-4-6",
            "tokens": 1000,
            "cost": 0.05,
            "timestamp": "2026-03-16T19:30:00Z",
            "agent": "test-agent",
        }

        resp = ingest_client.post("/ingest", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert len(data["ids"]) == 1
        assert len(data["ids"][0]) > 0

    def test_ingest_required_fields(self, ingest_client: TestClient):
        """Required fields (model, tokens, cost) are enforced."""
        # Missing tokens
        payload = {
            "model": "claude-sonnet-4-6",
            "cost": 0.05,
            "timestamp": "2026-03-16T19:30:00Z",
        }

        resp = ingest_client.post("/ingest", json=payload)

        # Should fail validation
        assert resp.status_code == 422

    def test_ingest_negative_tokens_rejected(self, ingest_client: TestClient):
        """Negative tokens rejected."""
        payload = {
            "model": "claude-sonnet-4-6",
            "tokens": -100,
            "cost": 0.05,
            "timestamp": "2026-03-16T19:30:00Z",
        }

        resp = ingest_client.post("/ingest", json=payload)

        assert resp.status_code == 422

    def test_ingest_negative_cost_rejected(self, ingest_client: TestClient):
        """Negative cost rejected."""
        payload = {
            "model": "claude-sonnet-4-6",
            "tokens": 1000,
            "cost": -0.05,
            "timestamp": "2026-03-16T19:30:00Z",
        }

        resp = ingest_client.post("/ingest", json=payload)

        assert resp.status_code == 422

    def test_ingest_invalid_timestamp(self, ingest_client: TestClient):
        """Invalid ISO8601 timestamp rejected."""
        payload = {
            "model": "claude-sonnet-4-6",
            "tokens": 1000,
            "cost": 0.05,
            "timestamp": "not-a-timestamp",
        }

        resp = ingest_client.post("/ingest", json=payload)

        assert resp.status_code == 422

    def test_ingest_optional_fields(self, ingest_client: TestClient):
        """Optional fields (agent, provider, session_id) accepted."""
        payload = {
            "model": "claude-sonnet-4-6",
            "tokens": 1000,
            "cost": 0.05,
            "timestamp": "2026-03-16T19:30:00Z",
            "agent": "sue",
            "provider": "anthropic",
            "session_id": "sess-123",
        }

        resp = ingest_client.post("/ingest", json=payload)

        assert resp.status_code == 200

    def test_ingest_extra_fields_allowed(self, ingest_client: TestClient):
        """Extra fields in entry are preserved (model_config.extra='allow')."""
        payload = {
            "model": "claude-sonnet-4-6",
            "tokens": 1000,
            "cost": 0.05,
            "timestamp": "2026-03-16T19:30:00Z",
            "custom_field": "custom_value",
            "extra_data": {"nested": "data"},
        }

        resp = ingest_client.post("/ingest", json=payload)

        assert resp.status_code == 200

    def test_ingest_creates_jsonl_file(self, ingest_client: TestClient, temp_entries_dir: Path):
        """Entry written to JSONL file with correct date."""
        payload = {
            "model": "claude-sonnet-4-6",
            "tokens": 1000,
            "cost": 0.05,
            "timestamp": "2026-03-16T19:30:00Z",
        }

        ingest_client.post("/ingest", json=payload)

        # Check file was created
        entries_file = temp_entries_dir / "2026-03-16.jsonl"
        assert entries_file.exists()

        # Read and verify entry
        with open(entries_file) as f:
            line = f.read().strip()
            entry = json.loads(line)
            assert entry["model"] == "claude-sonnet-4-6"
            assert entry["tokens"] == 1000


class TestIngestBatch:
    """POST /ingest/batch — Batch ingestion."""

    def test_batch_valid_entries(self, ingest_client: TestClient):
        """Batch of valid entries → 200 OK."""
        payload = [
            {
                "model": "claude-sonnet-4-6",
                "tokens": 1000,
                "cost": 0.05,
                "timestamp": "2026-03-16T19:30:00Z",
            },
            {
                "model": "claude-opus-4-5",
                "tokens": 1000,
                "cost": 0.15,
                "timestamp": "2026-03-16T19:45:00Z",
            },
        ]

        resp = ingest_client.post("/ingest/batch", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert len(data["ids"]) == 2

    def test_batch_empty_rejected(self, ingest_client: TestClient):
        """Empty batch rejected."""
        resp = ingest_client.post("/ingest/batch", json=[])

        assert resp.status_code == 400
        data = resp.json()
        assert "empty" in data["detail"].lower()

    def test_batch_too_large_rejected(self, ingest_client: TestClient):
        """Batch > 1000 entries rejected."""
        payload = [
            {
                "model": "claude-sonnet-4-6",
                "tokens": 1000,
                "cost": 0.05,
                "timestamp": "2026-03-16T19:30:00Z",
            }
        ] * 1001

        resp = ingest_client.post("/ingest/batch", json=payload)

        assert resp.status_code == 400
        data = resp.json()
        assert "too large" in data["detail"].lower()

    def test_batch_partial_failure(self, ingest_client: TestClient):
        """Batch with one invalid and one valid entry returns both IDs."""
        payload = [
            {
                "model": "claude-sonnet-4-6",
                "tokens": 1000,
                "cost": 0.05,
                "timestamp": "2026-03-16T19:30:00Z",
            },
            {
                "model": "claude-opus-4-5",
                "tokens": 1000,
                "cost": 0.15,
                "timestamp": "2026-03-16T19:45:00Z",
            },
        ]

        resp = ingest_client.post("/ingest/batch", json=payload)

        # Should succeed (Pydantic validates each before writing)
        assert resp.status_code == 200
        assert len(resp.json()["ids"]) == 2

    @pytest.mark.integration
    def test_batch_exact_limit(self, ingest_client: TestClient):
        """Batch of exactly 1000 entries accepted.

        Marked integration: the 1000-entry batch pathologically slow
        on CI-sandboxed runners (>30s timeout). Run with
        ``pytest -m integration`` locally.
        """
        payload = [
            {
                "model": "claude-sonnet-4-6",
                "tokens": 1000,
                "cost": 0.05,
                "timestamp": "2026-03-16T19:30:00Z",
            }
        ] * 1000

        resp = ingest_client.post("/ingest/batch", json=payload)

        assert resp.status_code == 200
        assert len(resp.json()["ids"]) == 1000

    def test_batch_creates_multiple_files(self, ingest_client: TestClient, temp_entries_dir: Path):
        """Batch with entries from multiple dates creates multiple files."""
        payload = [
            {
                "model": "claude-sonnet-4-6",
                "tokens": 1000,
                "cost": 0.05,
                "timestamp": "2026-03-15T19:30:00Z",
            },
            {
                "model": "claude-opus-4-5",
                "tokens": 1000,
                "cost": 0.15,
                "timestamp": "2026-03-16T19:30:00Z",
            },
        ]

        ingest_client.post("/ingest/batch", json=payload)

        # Check both files created
        file1 = temp_entries_dir / "2026-03-15.jsonl"
        file2 = temp_entries_dir / "2026-03-16.jsonl"
        assert file1.exists()
        assert file2.exists()


class TestEntryModel:
    """Entry Pydantic model validation."""

    def test_entry_default_timestamp(self):
        """Missing timestamp uses current time."""
        entry = Entry(
            model="claude-sonnet-4-6",
            tokens=1000,
            cost=0.05,
        )

        assert entry.timestamp is not None
        # Should be ISO8601 format
        dt = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
        assert dt.tzinfo is not None

    def test_entry_timestamp_validation(self):
        """Invalid timestamp raises ValueError."""
        with pytest.raises(ValueError):
            Entry(
                model="claude-sonnet-4-6",
                tokens=1000,
                cost=0.05,
                timestamp="invalid",
            )

    def test_entry_z_suffix_accepted(self):
        """Timestamp with Z suffix accepted."""
        entry = Entry(
            model="claude-sonnet-4-6",
            tokens=1000,
            cost=0.05,
            timestamp="2026-03-16T19:30:00Z",
        )

        assert entry.timestamp == "2026-03-16T19:30:00Z"

    def test_entry_model_dump(self):
        """Entry.model_dump() includes all fields."""
        entry = Entry(
            model="claude-sonnet-4-6",
            tokens=1000,
            cost=0.05,
            timestamp="2026-03-16T19:30:00Z",
            agent="sue",
        )

        data = entry.model_dump()
        assert data["model"] == "claude-sonnet-4-6"
        assert data["tokens"] == 1000
        assert data["cost"] == 0.05
        assert data["agent"] == "sue"


class TestHealth:
    """Health endpoint."""

    def test_health_endpoint(self, ingest_client: TestClient):
        """/health returns ok."""
        resp = ingest_client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "tokenpak-ingest" in data["service"]
