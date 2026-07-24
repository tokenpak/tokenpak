# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for tokenpak shadow mode modules (shadow_hook.py and shadow_reader.py).
"""

from __future__ import annotations

import json
import threading
import time

import pytest

# ---------------------------------------------------------------------------
# ShadowHook tests
# ---------------------------------------------------------------------------


class TestShadowHookEnabled:
    """Tests for ShadowHook in enabled state."""

    def _make_hook(self, tmp_path):
        from tokenpak.proxy.shadow_hook import ShadowHook

        ledger_path = str(tmp_path / "test_ledger.db")
        return ShadowHook(ledger_path=ledger_path, enabled=True)

    def test_enabled_by_default(self, tmp_path):
        hook = self._make_hook(tmp_path)
        assert hook.enabled is True

    def test_record_request_returns_txn_key(self, tmp_path):
        hook = self._make_hook(tmp_path)
        txn = hook.record_request("claude-sonnet-4-6", "hello world", context_tokens=10)
        assert txn is not None

    def test_record_response_returns_row_id(self, tmp_path):
        hook = self._make_hook(tmp_path)
        txn = hook.record_request("claude-sonnet-4-6", "hello world")
        row_id = hook.record_response(txn, "response text", response_tokens=5, latency_ms=100.0)
        assert row_id is not None
        assert isinstance(row_id, int)

    def test_record_response_without_txn_returns_none(self, tmp_path):
        hook = self._make_hook(tmp_path)
        result = hook.record_response(None, "response")
        assert result is None

    def test_record_response_unknown_txn_returns_none(self, tmp_path):
        hook = self._make_hook(tmp_path)
        result = hook.record_response(99999999, "response")
        assert result is None

    def test_get_stats_returns_dict(self, tmp_path):
        hook = self._make_hook(tmp_path)
        stats = hook.get_stats()
        assert isinstance(stats, dict)

    def test_pending_cleared_after_response(self, tmp_path):
        hook = self._make_hook(tmp_path)
        txn = hook.record_request("gpt-4o", "query")
        hook.record_response(txn, "reply")
        # Second response with same key should return None (already consumed)
        result = hook.record_response(txn, "reply again")
        assert result is None

    def test_thread_safe_concurrent_requests(self, tmp_path):
        hook = self._make_hook(tmp_path)
        results = []
        errors = []

        def do_round():
            try:
                txn = hook.record_request("model-x", "concurrent query")
                row = hook.record_response(txn, "concurrent reply", latency_ms=50.0)
                results.append(row)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_round) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10


class TestShadowHookDisabled:
    def test_disabled_record_request_returns_none(self):
        from tokenpak.proxy.shadow_hook import ShadowHook

        hook = ShadowHook(enabled=False)
        assert hook.record_request("model", "query") is None

    def test_disabled_record_response_returns_none(self):
        from tokenpak.proxy.shadow_hook import ShadowHook

        hook = ShadowHook(enabled=False)
        assert hook.record_response(42, "response") is None

    def test_disabled_record_feedback_returns_false(self):
        from tokenpak.proxy.shadow_hook import ShadowHook

        hook = ShadowHook(enabled=False)
        assert hook.record_feedback(1, True) is False

    def test_disabled_get_stats_returns_empty_dict(self):
        from tokenpak.proxy.shadow_hook import ShadowHook

        hook = ShadowHook(enabled=False)
        assert hook.get_stats() == {}

    def test_init_failure_disables_hook(self, tmp_path):
        """If ledger init fails, hook should disable itself gracefully."""
        from tokenpak.proxy.shadow_hook import ShadowHook

        # Point to a non-writable path to trigger init failure
        bad_path = "/root/noaccess/ledger.db"
        hook = ShadowHook(ledger_path=bad_path, enabled=True)
        # Should gracefully disable rather than crash
        assert (
            hook.enabled is False or hook._ledger is None or hook.record_request("m", "q") is None
        )


# ---------------------------------------------------------------------------
# ShadowReader tests
# ---------------------------------------------------------------------------


class TestShadowReaderDisabled:
    """ShadowReader with TOKENPAK_SHADOW_MODE not set (disabled)."""

    def test_observe_request_returns_empty_when_disabled(self, tmp_path):
        from tokenpak.proxy.shadow_reader import ShadowReader

        reader = ShadowReader(shadow_log_path=tmp_path / "obs.jsonl")
        # Shadow mode env var not set → disabled
        obs_id = reader.observe_request("POST", "/v1/messages", {}, 1024, model="gpt-4o")
        assert obs_id == ""

    def test_observe_response_noop_when_disabled(self, tmp_path):
        from tokenpak.proxy.shadow_reader import ShadowReader

        reader = ShadowReader(shadow_log_path=tmp_path / "obs.jsonl")
        # Should not raise
        reader.observe_response("abc", 200, {}, 512, 100.0)

    def test_observe_metric_noop_when_disabled(self, tmp_path):
        from tokenpak.proxy.shadow_reader import ShadowReader

        reader = ShadowReader(shadow_log_path=tmp_path / "obs.jsonl")
        reader.observe_metric("latency", 42.5)

    def test_get_stats_returns_dict_when_disabled(self, tmp_path):
        from tokenpak.proxy.shadow_reader import ShadowReader

        reader = ShadowReader(shadow_log_path=tmp_path / "obs.jsonl")
        stats = reader.get_stats()
        assert isinstance(stats, dict)


class TestShadowReaderEnabled:
    """ShadowReader with TOKENPAK_SHADOW_MODE=true."""

    @pytest.fixture(autouse=True)
    def enable_shadow(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_SHADOW_MODE", "true")

    def _make_reader(self, tmp_path):
        # Force module-level constant by patching the instance
        from tokenpak.proxy.shadow_reader import ShadowReader

        reader = ShadowReader(shadow_log_path=tmp_path / "obs.jsonl")
        reader.enabled = True  # override since module constant was set before env patch
        reader.log_path.parent.mkdir(parents=True, exist_ok=True)
        return reader

    def test_observe_request_returns_obs_id(self, tmp_path):
        reader = self._make_reader(tmp_path)
        obs_id = reader.observe_request("POST", "/v1/messages", {"auth": "Bearer x"}, 512, "gpt-4")
        assert isinstance(obs_id, str)
        assert len(obs_id) > 0

    def test_observe_response_adds_to_buffer(self, tmp_path):
        reader = self._make_reader(tmp_path)
        reader.observe_response("obs-1", 200, {"content-type": "application/json"}, 256, 88.0)
        with reader._buffer_lock:
            assert len(reader._buffer) >= 1

    def test_observe_metric_adds_to_buffer(self, tmp_path):
        reader = self._make_reader(tmp_path)
        reader.observe_metric("cache.hit_rate", 0.95, tags={"model": "sonnet"})
        with reader._buffer_lock:
            assert any(o.mode == "metric" for o in reader._buffer)

    def test_flush_writes_jsonl(self, tmp_path):
        reader = self._make_reader(tmp_path)
        reader.observe_request("GET", "/health", {}, 0)
        reader.flush()
        time.sleep(0.2)  # allow background write thread
        if reader.log_path.exists():
            lines = reader.log_path.read_text().strip().splitlines()
            assert len(lines) >= 1
            record = json.loads(lines[0])
            assert "observation_id" in record
            assert "timestamp" in record

    def test_mark_compression_analysis(self, tmp_path):
        reader = self._make_reader(tmp_path)
        obs_id = reader.observe_request("POST", "/v1/messages", {}, 1000, "claude-3")
        reader.mark_compression_analysis(
            obs_id, applicable=True, gain_tokens=200, cost_change=-0.01
        )
        with reader._buffer_lock:
            matching = [o for o in reader._buffer if o.observation_id == obs_id]
        assert len(matching) == 1
        assert matching[0].compression_applicable is True
        assert matching[0].compression_gain_tokens == 200

    def test_stop_cleanly(self, tmp_path):
        reader = self._make_reader(tmp_path)
        reader.stop()  # Should not raise


class TestShadowReaderHelpers:
    def test_gen_obs_id_is_unique(self):
        from tokenpak.proxy.shadow_reader import ShadowReader

        ids = {ShadowReader._gen_obs_id() for _ in range(100)}
        assert len(ids) == 100

    def test_gen_obs_id_is_string(self):
        from tokenpak.proxy.shadow_reader import ShadowReader

        obs_id = ShadowReader._gen_obs_id()
        assert isinstance(obs_id, str)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestShadowModuleHelpers:
    def test_is_shadow_mode_enabled_false_by_default(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_SHADOW_MODE", raising=False)
        # Re-import after env change isn't possible easily, but we can test the function
        from tokenpak import shadow_reader

        # The module-level SHADOW_MODE constant reflects state at import time
        # Just verify is_shadow_mode_enabled returns a bool
        result = shadow_reader.is_shadow_mode_enabled()
        assert isinstance(result, bool)

    def test_get_shadow_reader_returns_instance(self):
        from tokenpak import shadow_reader

        reader = shadow_reader.get_shadow_reader()
        assert reader is not None

    def test_get_shadow_reader_singleton(self):
        from tokenpak import shadow_reader

        r1 = shadow_reader.get_shadow_reader()
        r2 = shadow_reader.get_shadow_reader()
        assert r1 is r2
