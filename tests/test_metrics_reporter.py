"""Tests for anonymous metrics reporter.

Coverage:
- MetricsRecord schema validation (no PII fields)
- Record / store operations
- Batch sync (mocked HTTP)
- Opt-in / opt-out gate
- to_upload_dict strips local-only fields
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path):
    """Return a MetricsStore backed by a temp file."""
    from tokenpak.telemetry.anon_metrics import MetricsStore
    return MetricsStore(db_path=tmp_path / "metrics.db")


def _make_record(**kwargs):
    from tokenpak.telemetry.anon_metrics import MetricsRecord
    defaults = dict(
        input_tokens=1000,
        output_tokens=200,
        tokens_saved=300,
        compression_ratio=0.30,
        latency_ms=123.4,
        model="anthropic/claude-sonnet-4-5",
    )
    defaults.update(kwargs)
    return MetricsRecord(**defaults)


# ---------------------------------------------------------------------------
# Schema / PII tests
# ---------------------------------------------------------------------------

class TestMetricsRecordSchema:

    def test_no_content_fields(self):
        """Verify MetricsRecord has no prompt/response content fields."""
        from tokenpak.telemetry.anon_metrics import MetricsRecord
        r = MetricsRecord()
        fields = set(vars(r).keys())
        forbidden = {"prompt", "content", "message", "response", "text", "user", "system"}
        assert not fields & forbidden, f"PII fields detected: {fields & forbidden}"

    def test_allowed_fields_only(self):
        """to_upload_dict contains only the allowed set of fields."""
        r = _make_record()
        upload = r.to_upload_dict()
        allowed = {
            "date_utc", "input_tokens", "output_tokens", "tokens_saved",
            "compression_ratio", "latency_ms", "model", "schema_version",
        }
        assert set(upload.keys()) == allowed

    def test_upload_dict_strips_local_id_and_synced(self):
        r = _make_record()
        upload = r.to_upload_dict()
        assert "local_id" not in upload
        assert "synced" not in upload

    def test_schema_version_present(self):
        r = _make_record()
        assert r.schema_version == "1.0"
        assert r.to_upload_dict()["schema_version"] == "1.0"

    def test_no_pii_in_serialised_json(self):
        """JSON serialisation must not leak content."""
        r = _make_record(model="openai/gpt-4o")
        payload_json = json.dumps(r.to_upload_dict())
        for banned in ["prompt", "content", "message", "response"]:
            assert banned not in payload_json, f"Banned field '{banned}' in JSON"

    def test_disallowed_fields_raise(self):
        """Adding a disallowed field should raise ValueError."""
        from tokenpak.telemetry.anon_metrics import MetricsRecord
        with pytest.raises((ValueError, TypeError)):
            MetricsRecord(prompt="secret content")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------

class TestMetricsStore:

    def test_record_and_retrieve_pending(self, tmp_path):
        store = _make_store(tmp_path)
        r = _make_record()
        store.record(r)
        pending = store.get_pending()
        assert len(pending) == 1
        assert pending[0].local_id == r.local_id
        assert not pending[0].synced

    def test_mark_synced(self, tmp_path):
        store = _make_store(tmp_path)
        r1 = _make_record()
        r2 = _make_record()
        store.record(r1)
        store.record(r2)
        store.mark_synced([r1.local_id])
        pending = store.get_pending()
        ids = {p.local_id for p in pending}
        assert r1.local_id not in ids
        assert r2.local_id in ids

    def test_no_duplicate_records(self, tmp_path):
        store = _make_store(tmp_path)
        r = _make_record()
        store.record(r)
        store.record(r)  # duplicate insert
        assert len(store.get_pending()) == 1

    def test_history_returns_records(self, tmp_path):
        store = _make_store(tmp_path)
        store.record(_make_record())
        store.record(_make_record())
        hist = store.history(days=7)
        assert len(hist) == 2

    def test_daily_summary(self, tmp_path):
        store = _make_store(tmp_path)
        for _ in range(3):
            store.record(_make_record(input_tokens=1000, tokens_saved=300))
        summary = store.daily_summary(days=7)
        assert len(summary) >= 1
        today = summary[0]
        assert today["requests"] == 3
        assert today["input_tokens"] == 3000
        assert today["tokens_saved"] == 900


# ---------------------------------------------------------------------------
# Batch sync tests
# ---------------------------------------------------------------------------

class TestSyncBatch:

    def test_dry_run_returns_all_records(self, tmp_path):
        from tokenpak.telemetry.reporter import sync_batch
        store = _make_store(tmp_path)
        r1 = _make_record()
        r2 = _make_record()
        store.record(r1)
        store.record(r2)

        with mock.patch("tokenpak.telemetry.anon_metrics._store", store):
            result = sync_batch(dry_run=True)

        assert result["uploaded"] == 2
        assert len(result["synced_ids"]) == 2
        assert not result["errors"]

    def test_sync_success_marks_records_synced(self, tmp_path):
        from tokenpak.telemetry.reporter import sync_batch
        store = _make_store(tmp_path)
        records = [_make_record() for _ in range(5)]
        for r in records:
            store.record(r)

        def _fake_post(url, payload, timeout=15):
            return 200

        with mock.patch("tokenpak.telemetry.reporter._post", _fake_post), \
             mock.patch("tokenpak.telemetry.anon_metrics._store", store):
            result = sync_batch()

        assert result["uploaded"] == 5
        assert not result["errors"]
        assert len(store.get_pending()) == 0

    def test_sync_4xx_marks_synced_to_avoid_infinite_retry(self, tmp_path):
        from tokenpak.telemetry.reporter import sync_batch
        store = _make_store(tmp_path)
        store.record(_make_record())

        def _fake_post(url, payload, timeout=15):
            return 422

        with mock.patch("tokenpak.telemetry.reporter._post", _fake_post), \
             mock.patch("tokenpak.telemetry.anon_metrics._store", store):
            result = sync_batch()

        assert result["uploaded"] == 1
        assert any("422" in e for e in result["errors"])
        assert len(store.get_pending()) == 0  # marked synced to stop loop

    def test_sync_retries_on_5xx(self, tmp_path):
        from tokenpak.telemetry.reporter import sync_batch, MAX_RETRIES
        store = _make_store(tmp_path)
        store.record(_make_record())

        call_count = {"n": 0}

        def _fake_post(url, payload, timeout=15):
            call_count["n"] += 1
            return 500

        with mock.patch("tokenpak.telemetry.reporter._post", _fake_post), \
             mock.patch("tokenpak.telemetry.reporter.time") as mock_time, \
             mock.patch("tokenpak.telemetry.anon_metrics._store", store):
            mock_time.sleep = mock.MagicMock()
            result = sync_batch()

        assert call_count["n"] == MAX_RETRIES
        assert result["skipped"] == 1
        assert result["errors"]

    def test_sync_network_error_retries(self, tmp_path):
        import urllib.error
        from tokenpak.telemetry.reporter import sync_batch, MAX_RETRIES
        store = _make_store(tmp_path)
        store.record(_make_record())

        call_count = {"n": 0}

        def _fake_post(url, payload, timeout=15):
            call_count["n"] += 1
            raise urllib.error.URLError("network down")

        with mock.patch("tokenpak.telemetry.reporter._post", _fake_post), \
             mock.patch("tokenpak.telemetry.reporter.time") as mock_time, \
             mock.patch("tokenpak.telemetry.anon_metrics._store", store):
            mock_time.sleep = mock.MagicMock()
            result = sync_batch()

        assert call_count["n"] == MAX_RETRIES
        assert result["skipped"] == 1

    def test_empty_pending_returns_zero(self, tmp_path):
        from tokenpak.telemetry.reporter import sync_batch
        store = _make_store(tmp_path)
        with mock.patch("tokenpak.telemetry.anon_metrics._store", store):
            result = sync_batch()
        assert result["uploaded"] == 0
        assert result["skipped"] == 0
        assert not result["errors"]


# ---------------------------------------------------------------------------
# Opt-in / opt-out gate tests
# ---------------------------------------------------------------------------

class TestOptInGate:

    def test_disabled_by_default(self):
        """Metrics should be off unless explicitly enabled."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENPAK_METRICS_ENABLED", None)
            # Patch config file to empty
            with mock.patch("tokenpak.agent.config._load", return_value={}):
                from tokenpak.agent.config import get_metrics_enabled
                assert not get_metrics_enabled()

    def test_enabled_via_env_var(self):
        with mock.patch.dict(os.environ, {"TOKENPAK_METRICS_ENABLED": "true"}):
            from tokenpak.agent.config import get_metrics_enabled
            assert get_metrics_enabled()

    def test_disabled_via_env_var(self):
        with mock.patch.dict(os.environ, {"TOKENPAK_METRICS_ENABLED": "false"}):
            from tokenpak.agent.config import get_metrics_enabled
            assert not get_metrics_enabled()

    def test_enabled_via_config_file(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENPAK_METRICS_ENABLED", None)
            with mock.patch("tokenpak.agent.config._load", return_value={"metrics.enabled": True}):
                from tokenpak.agent.config import get_metrics_enabled
                assert get_metrics_enabled()

    def test_record_request_no_op_when_disabled(self, tmp_path):
        """record_request must not write anything when metrics are disabled."""
        store = _make_store(tmp_path)
        with mock.patch("tokenpak.agent.config.get_metrics_enabled", return_value=False), \
             mock.patch("tokenpak.telemetry.anon_metrics._store", store):
            from tokenpak.telemetry.anon_metrics import record_request
            record_request(
                input_tokens=500, output_tokens=100, tokens_saved=100,
                latency_ms=50.0, model="gpt-4o",
            )
        assert len(store.get_pending()) == 0

    def test_record_request_writes_when_enabled(self, tmp_path):
        store = _make_store(tmp_path)
        with mock.patch("tokenpak.agent.config.get_metrics_enabled", return_value=True), \
             mock.patch("tokenpak.telemetry.anon_metrics._store", store):
            from tokenpak.telemetry.anon_metrics import record_request
            record_request(
                input_tokens=1000, output_tokens=200, tokens_saved=300,
                latency_ms=88.0, model="claude-sonnet",
            )
        pending = store.get_pending()
        assert len(pending) == 1
        assert pending[0].model == "claude-sonnet"
        assert pending[0].tokens_saved == 300
        assert pending[0].compression_ratio == pytest.approx(0.30, abs=0.001)

    def test_record_request_never_raises(self):
        """record_request must silently swallow all exceptions."""
        with mock.patch("tokenpak.agent.config.get_metrics_enabled", side_effect=RuntimeError("boom")):
            from tokenpak.telemetry.anon_metrics import record_request
            record_request(
                input_tokens=100, output_tokens=20, tokens_saved=10,
                latency_ms=10.0, model="test",
            )  # must not raise


# ---------------------------------------------------------------------------
# Payload structure test
# ---------------------------------------------------------------------------

class TestPayloadStructure:

    def test_batch_payload_structure(self, tmp_path):
        """Verify upload payload has required top-level keys."""
        from tokenpak.telemetry.reporter import sync_batch
        store = _make_store(tmp_path)
        records = [_make_record() for _ in range(3)]
        for r in records:
            store.record(r)

        captured = {}

        def _fake_post(url, payload, timeout=15):
            captured["payload"] = payload
            return 200

        with mock.patch("tokenpak.telemetry.reporter._post", _fake_post), \
             mock.patch("tokenpak.telemetry.anon_metrics._store", store):
            sync_batch()

        p = captured["payload"]
        assert "schema_version" in p
        assert "sent_at" in p
        assert "record_count" in p
        assert "records" in p
        assert p["record_count"] == 3
        assert len(p["records"]) == 3

    def test_records_in_payload_have_no_local_id(self, tmp_path):
        from tokenpak.telemetry.reporter import sync_batch
        store = _make_store(tmp_path)
        store.record(_make_record())

        captured = {}

        def _fake_post(url, payload, timeout=15):
            captured["payload"] = payload
            return 200

        with mock.patch("tokenpak.telemetry.reporter._post", _fake_post), \
             mock.patch("tokenpak.telemetry.anon_metrics._store", store):
            sync_batch()

        for rec in captured["payload"]["records"]:
            assert "local_id" not in rec
            assert "synced" not in rec
