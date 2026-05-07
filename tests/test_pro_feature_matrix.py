"""Tests for Pro feature matrix and audit log (20+ tests)."""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.pro.feature_matrix", reason="module not available in current build")
import json
from io import StringIO
from unittest.mock import patch

import pytest

from tokenpak.pro.audit_log import AuditLog
from tokenpak.pro.feature_matrix import ADAPTERS, FEATURES, FeatureMatrix

# ── Feature Matrix ───────────────────────────────────────────────────────────


class TestFeatureMatrix:
    def setup_method(self):
        self.fm = FeatureMatrix()

    # Known supported features
    def test_anthropic_function_calling(self):
        assert self.fm.is_supported("anthropic", "function_calling") is True

    def test_anthropic_streaming(self):
        assert self.fm.is_supported("anthropic", "streaming") is True

    def test_anthropic_structured_output(self):
        assert self.fm.is_supported("anthropic", "structured_output") is True

    def test_anthropic_deterministic(self):
        assert self.fm.is_supported("anthropic", "deterministic") is True

    def test_anthropic_agentic(self):
        assert self.fm.is_supported("anthropic", "agentic") is True

    def test_anthropic_workflow_false(self):
        assert self.fm.is_supported("anthropic", "workflow") is False

    def test_openai_function_calling(self):
        assert self.fm.is_supported("openai", "function_calling") is True

    def test_openai_streaming(self):
        assert self.fm.is_supported("openai", "streaming") is True

    def test_google_streaming(self):
        assert self.fm.is_supported("google", "streaming") is True

    def test_google_function_calling_false(self):
        assert self.fm.is_supported("google", "function_calling") is False

    def test_google_structured_output_false(self):
        assert self.fm.is_supported("google", "structured_output") is False

    def test_google_deterministic_false(self):
        assert self.fm.is_supported("google", "deterministic") is False

    def test_google_agentic_false(self):
        assert self.fm.is_supported("google", "agentic") is False

    # Unknown adapter/feature should return False (not crash)
    def test_unknown_adapter_returns_false(self):
        assert self.fm.is_supported("unknown-provider", "streaming") is False

    def test_unknown_feature_returns_false(self):
        assert self.fm.is_supported("anthropic", "nonexistent_feature") is False

    def test_unknown_adapter_and_feature_returns_false(self):
        assert self.fm.is_supported("bogus", "bogus") is False

    # tokenpak-* variants inherit from base
    def test_tokenpak_anthropic_inherits_streaming(self):
        assert self.fm.is_supported("tokenpak-anthropic", "streaming") is True

    def test_tokenpak_anthropic_inherits_function_calling(self):
        assert self.fm.is_supported("tokenpak-anthropic", "function_calling") is True

    def test_tokenpak_openai_inherits_structured_output(self):
        assert self.fm.is_supported("tokenpak-openai", "structured_output") is True

    def test_tokenpak_google_inherits_streaming(self):
        assert self.fm.is_supported("tokenpak-google", "streaming") is True

    def test_tokenpak_anthropic_workflow_enabled(self):
        # tokenpak-* extras enable workflow
        assert self.fm.is_supported("tokenpak-anthropic", "workflow") is True

    def test_tokenpak_openai_workflow_enabled(self):
        assert self.fm.is_supported("tokenpak-openai", "workflow") is True

    def test_tokenpak_google_workflow_enabled(self):
        assert self.fm.is_supported("tokenpak-google", "workflow") is True

    # get_matrix returns full dict
    def test_get_matrix_contains_all_adapters(self):
        matrix = self.fm.get_matrix()
        for adapter in ADAPTERS:
            assert adapter in matrix

    def test_get_matrix_contains_all_features(self):
        matrix = self.fm.get_matrix()
        for adapter in ADAPTERS:
            for feature in FEATURES:
                assert feature in matrix[adapter]

    def test_get_matrix_values_are_bool(self):
        matrix = self.fm.get_matrix()
        for adapter, features in matrix.items():
            for feature, val in features.items():
                assert isinstance(val, bool), f"{adapter}.{feature} should be bool"

    # get_fallback returns string
    def test_get_fallback_returns_string(self):
        result = self.fm.get_fallback("google", "function_calling")
        assert isinstance(result, str) and len(result) > 0

    def test_get_fallback_workflow(self):
        result = self.fm.get_fallback("anthropic", "workflow")
        assert isinstance(result, str) and len(result) > 0

    def test_get_fallback_unknown_feature(self):
        result = self.fm.get_fallback("anthropic", "unknown_feature")
        assert isinstance(result, str) and len(result) > 0

    # model parameter accepted without crash
    def test_is_supported_with_model_param(self):
        result = self.fm.is_supported("anthropic", "streaming", model="claude-3-5-sonnet-20241022")
        assert result is True


# ── AuditLog ─────────────────────────────────────────────────────────────────


class TestAuditLog:
    """Tests use in-memory SQLite."""

    def _make_log(self) -> AuditLog:
        return AuditLog(db_path=":memory:")

    # DB auto-created / empty db graceful
    def test_db_auto_created_in_memory(self):
        log = self._make_log()
        assert log is not None
        log.close()

    def test_get_feature_usage_empty_db(self):
        with self._make_log() as log:
            rows = log.get_feature_usage()
        assert rows == []

    def test_get_stats_empty_db(self):
        with self._make_log() as log:
            stats = log.get_stats()
        assert stats["total"] == 0
        assert stats["by_feature"] == {}
        assert stats["by_adapter"] == {}

    def test_export_json_empty_db(self):
        with self._make_log() as log:
            result = log.export_json()
        parsed = json.loads(result)
        assert parsed == []

    # log_usage writes record
    def test_log_usage_writes_record(self):
        with self._make_log() as log:
            log.log_usage("anthropic", "claude-3-5-sonnet", "streaming")
            rows = log.get_feature_usage()
        assert len(rows) == 1
        assert rows[0]["adapter"] == "anthropic"
        assert rows[0]["feature"] == "streaming"

    # get_feature_usage returns list
    def test_get_feature_usage_returns_list(self):
        with self._make_log() as log:
            log.log_usage("openai", "gpt-4o", "function_calling")
            result = log.get_feature_usage()
        assert isinstance(result, list)

    # filtering by feature
    def test_filter_by_feature(self):
        with self._make_log() as log:
            log.log_usage("anthropic", "claude-3", "streaming")
            log.log_usage("anthropic", "claude-3", "function_calling")
            rows = log.get_feature_usage(feature="streaming")
        assert len(rows) == 1
        assert rows[0]["feature"] == "streaming"

    # filtering by adapter
    def test_filter_by_adapter(self):
        with self._make_log() as log:
            log.log_usage("anthropic", "claude-3", "streaming")
            log.log_usage("openai", "gpt-4o", "streaming")
            rows = log.get_feature_usage(adapter="openai")
        assert len(rows) == 1
        assert rows[0]["adapter"] == "openai"

    # days filter
    def test_days_filter_recent_records(self):
        with self._make_log() as log:
            log.log_usage("anthropic", "claude-3", "agentic")
            rows = log.get_feature_usage(days=7)
        assert len(rows) == 1

    def test_days_filter_zero_excludes_all(self):
        with self._make_log() as log:
            log.log_usage("anthropic", "claude-3", "agentic")
            rows = log.get_feature_usage(days=0)
        assert len(rows) == 0

    # get_stats aggregates correctly
    def test_get_stats_returns_dict(self):
        with self._make_log() as log:
            log.log_usage("anthropic", "claude-3", "streaming")
            stats = log.get_stats()
        assert isinstance(stats, dict)
        assert "total" in stats

    def test_get_stats_counts_multiple_entries(self):
        with self._make_log() as log:
            log.log_usage("anthropic", "claude-3", "streaming")
            log.log_usage("anthropic", "claude-3", "streaming")
            log.log_usage("openai", "gpt-4o", "function_calling")
            stats = log.get_stats()
        assert stats["total"] == 3
        assert stats["by_feature"]["streaming"] == 2
        assert stats["by_feature"]["function_calling"] == 1
        assert stats["by_adapter"]["anthropic"] == 2
        assert stats["by_adapter"]["openai"] == 1

    def test_get_stats_filter_by_feature(self):
        with self._make_log() as log:
            log.log_usage("anthropic", "claude-3", "streaming")
            log.log_usage("openai", "gpt-4o", "function_calling")
            stats = log.get_stats(feature="streaming")
        assert stats["total"] == 1
        assert "streaming" in stats["by_feature"]

    def test_get_stats_filter_by_adapter(self):
        with self._make_log() as log:
            log.log_usage("anthropic", "claude-3", "streaming")
            log.log_usage("openai", "gpt-4o", "function_calling")
            stats = log.get_stats(adapter="anthropic")
        assert stats["total"] == 1
        assert "anthropic" in stats["by_adapter"]

    # export_json returns valid JSON
    def test_export_json_valid_json(self):
        with self._make_log() as log:
            log.log_usage("anthropic", "claude-3", "streaming")
            result = log.export_json()
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1

    # metadata stored and retrievable
    def test_log_usage_with_metadata(self):
        meta = {"tokens": 100, "latency_ms": 42}
        with self._make_log() as log:
            log.log_usage("anthropic", "claude-3", "streaming", metadata=meta)
            rows = log.get_feature_usage()
        assert rows[0]["metadata"] == meta

    # db auto-created if path missing handled in open
    def test_context_manager_closes_connection(self):
        log = self._make_log()
        with log:
            log.log_usage("anthropic", "claude-3", "streaming")
        # After __exit__, conn should be None
        assert log._conn is None


# ── CLI integration ───────────────────────────────────────────────────────────


class TestAuditLogCLI:
    """Smoke tests for the CLI commands."""

    def _run(self, argv):
        from tokenpak.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(argv)
        captured = StringIO()
        with patch("sys.stdout", captured):
            args.func(args)
        return captured.getvalue()

    def test_audit_log_show_runs(self):
        output = self._run(["audit-log", "show"])
        # Should either show records or "No usage records found."
        assert output is not None

    def test_audit_log_stats_runs(self):
        output = self._run(["audit-log", "stats"])
        assert "Total events:" in output

    def test_audit_log_export_produces_valid_json(self):
        output = self._run(["audit-log", "export"])
        parsed = json.loads(output)
        assert isinstance(parsed, list)

    def test_audit_log_show_with_days_arg(self):
        output = self._run(["audit-log", "show", "--days", "30"])
        assert output is not None

    def test_audit_log_stats_with_feature_arg(self):
        output = self._run(["audit-log", "stats", "--feature", "streaming"])
        assert "Total events:" in output

    def test_audit_log_stats_with_adapter_arg(self):
        output = self._run(["audit-log", "stats", "--adapter", "anthropic"])
        assert "Total events:" in output
