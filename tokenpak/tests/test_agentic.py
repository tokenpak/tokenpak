"""Unit tests for tokenpak.orchestration module.

Covers:
  - Package import (agentic/__init__.py)
  - ErrorNormalizer and FailureSignatureDB (error_normalizer.py)
  - _extract_http_status, RetryEngine, RetryExhaustedError, ImmediateAlertError (retry.py)
  - FileLockManager, LockConflictError, LockExpiredError (locks.py)
  - FileStateValidator, SchemaValidator, ValidationOrchestrator (validation_framework.py)
  - WorkflowBudget (workflow_budget.py)
  - AgentCapabilities, TaskRequirements (capabilities.py)

All external I/O is either done in temp dirs or mocked.
No live API calls.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Package-level imports
# ---------------------------------------------------------------------------


def test_agentic_package_importable():
    import tokenpak.orchestration  # noqa: F401 — just verify the import works


def test_agentic_exports_error_normalizer():
    from tokenpak.orchestration import ErrorNormalizer

    assert callable(ErrorNormalizer)


def test_agentic_exports_retry_engine():
    from tokenpak.orchestration import RetryEngine

    assert callable(RetryEngine)


# ---------------------------------------------------------------------------
# error_normalizer.py — ErrorNormalizer
# ---------------------------------------------------------------------------


class TestErrorNormalizerDefaults:
    """Tests using only the built-in default patterns."""

    def setup_method(self):
        from tokenpak.orchestration.error_normalizer import ErrorNormalizer

        # Use a non-existent path so no external file is loaded
        self.norm = ErrorNormalizer(extra_pattern_path=Path("/nonexistent/path.json"))

    def test_eaddrinuse(self):
        assert self.norm.normalize("EADDRINUSE: address already in use") == "PORT_BIND_FAILURE"

    def test_address_already_in_use(self):
        assert self.norm.normalize("Address already in use on 0.0.0.0:8080") == "PORT_BIND_FAILURE"

    def test_connection_refused(self):
        assert self.norm.normalize("connection refused: 127.0.0.1:5432") == "CONNECTION_REFUSED"

    def test_timeout(self):
        assert self.norm.normalize("request timed out after 30s") == "TIMEOUT"

    def test_timeout_variant(self):
        assert self.norm.normalize("timeout connecting to server") == "TIMEOUT"

    def test_rate_limit(self):
        assert self.norm.normalize("HTTP 429 Too Many Requests") == "RATE_LIMIT"

    def test_rate_limit_text(self):
        assert self.norm.normalize("rate limit exceeded") == "RATE_LIMIT"

    def test_auth_failure_401(self):
        assert self.norm.normalize("HTTP 401 Unauthorized") == "AUTH_FAILURE"

    def test_auth_failure_403(self):
        assert self.norm.normalize("HTTP 403 Forbidden") == "AUTH_FAILURE"

    def test_auth_failure_unauthorized(self):
        assert self.norm.normalize("Unauthorized: invalid token") == "AUTH_FAILURE"

    def test_empty_string(self):
        assert self.norm.normalize("") == "UNKNOWN_ERROR"

    def test_whitespace_only(self):
        assert self.norm.normalize("   ") == "UNKNOWN_ERROR"

    def test_fallback_signature(self):
        sig = self.norm.normalize("Something weird happened at line 42!")
        # fallback normalizes to uppercase words joined by _
        assert sig == sig.upper()
        assert " " not in sig

    def test_case_insensitivity(self):
        assert self.norm.normalize("rate LIMIT exceeded") == "RATE_LIMIT"


class TestErrorNormalizerFallback:
    def test_fallback_signature_static(self):
        from tokenpak.orchestration.error_normalizer import ErrorNormalizer

        sig = ErrorNormalizer._fallback_signature("hello world 42")
        assert sig == "HELLO_WORLD_42"

    def test_fallback_truncates_at_80(self):
        from tokenpak.orchestration.error_normalizer import ErrorNormalizer

        long_msg = "x" * 200
        sig = ErrorNormalizer._fallback_signature(long_msg)
        assert len(sig) <= 80

    def test_fallback_collapses_separators(self):
        from tokenpak.orchestration.error_normalizer import ErrorNormalizer

        sig = ErrorNormalizer._fallback_signature("a--b__c  d")
        assert "__" not in sig


class TestErrorNormalizerExternalPatterns:
    def test_loads_external_patterns(self, tmp_path):
        from tokenpak.orchestration.error_normalizer import ErrorNormalizer

        pattern_file = tmp_path / "patterns.json"
        pattern_file.write_text(
            json.dumps(
                [
                    {"regex": r"disk full", "normalized_signature": "DISK_FULL"},
                ]
            )
        )
        norm = ErrorNormalizer(extra_pattern_path=pattern_file)
        assert norm.normalize("No space left: disk full") == "DISK_FULL"

    def test_ignores_invalid_json(self, tmp_path):
        from tokenpak.orchestration.error_normalizer import ErrorNormalizer

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not-json{{{")
        # Should not raise; falls back to defaults only
        norm = ErrorNormalizer(extra_pattern_path=bad_file)
        assert norm.normalize("connection refused") == "CONNECTION_REFUSED"

    def test_ignores_non_list_json(self, tmp_path):
        from tokenpak.orchestration.error_normalizer import ErrorNormalizer

        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps({"not": "a list"}))
        norm = ErrorNormalizer(extra_pattern_path=bad_file)
        assert norm.normalize("connection refused") == "CONNECTION_REFUSED"

    def test_skips_bad_regex_entries(self, tmp_path):
        from tokenpak.orchestration.error_normalizer import ErrorNormalizer

        bad_file = tmp_path / "patterns.json"
        bad_file.write_text(
            json.dumps(
                [
                    {"regex": r"[invalid((", "normalized_signature": "BROKEN"},
                    {"regex": r"ok pattern", "normalized_signature": "OK_PATTERN"},
                ]
            )
        )
        norm = ErrorNormalizer(extra_pattern_path=bad_file)
        assert norm.normalize("ok pattern match") == "OK_PATTERN"


# ---------------------------------------------------------------------------
# error_normalizer.py — FailureSignatureDB
# ---------------------------------------------------------------------------


class TestFailureSignatureDB:
    def setup_method(self):
        from tokenpak.orchestration.error_normalizer import ErrorNormalizer, FailureSignatureDB

        norm = ErrorNormalizer(extra_pattern_path=Path("/nonexistent/path.json"))
        self.db = FailureSignatureDB(normalizer=norm)

    def test_record_failure_increments_count(self):
        rec = self.db.record_failure("HTTP 429 Too Many Requests")
        assert rec.count == 1
        rec2 = self.db.record_failure("rate limit exceeded")
        assert rec2.count == 2
        assert rec2.signature == "RATE_LIMIT"

    def test_record_failure_stores_recipe(self):
        rec = self.db.record_failure("connection refused", repair_recipe="restart_service")
        assert "restart_service" in rec.repair_recipes

    def test_lookup_normalizes_input(self):
        self.db.record_failure("EADDRINUSE", repair_recipe="kill_port")
        rec = self.db.lookup("address already in use")
        assert rec is not None
        assert "kill_port" in rec.repair_recipes

    def test_lookup_missing_returns_none(self):
        assert self.db.lookup("something completely unknown xyz789") is None

    def test_merge_synonym_stats(self):
        # Seed two synonyms that map to the same signature
        self.db.record_failure("connection refused", repair_recipe="r1")
        self.db.record_failure("connection refused", repair_recipe="r1")
        # merge_synonym_stats with both raw forms
        merged = self.db.merge_synonym_stats(["connection refused", "connection refused"])
        assert merged is not None
        assert merged.count >= 2

    def test_merge_synonym_stats_empty(self):
        result = self.db.merge_synonym_stats([])
        assert result is None

    def test_auto_learn_merge_suggestions_empty_db(self):
        suggestions = self.db.auto_learn_merge_suggestions()
        assert isinstance(suggestions, list)


# ---------------------------------------------------------------------------
# retry.py — helpers
# ---------------------------------------------------------------------------


class TestExtractHttpStatus:
    def setup_method(self):
        from tokenpak.orchestration.retry import _extract_http_status

        self.fn = _extract_http_status

    def test_status_code_attribute(self):
        exc = Exception("oops")
        exc.status_code = 429
        assert self.fn(exc) == "429"

    def test_code_attribute(self):
        exc = Exception("oops")
        exc.code = 500
        assert self.fn(exc) == "500"

    def test_scans_message_for_4xx(self):
        exc = Exception("Got HTTP 403 Forbidden")
        assert self.fn(exc) == "403"

    def test_scans_message_for_5xx(self):
        exc = Exception("server error: 502 Bad Gateway")
        assert self.fn(exc) == "502"

    def test_returns_none_when_no_status(self):
        exc = ValueError("something broke")
        assert self.fn(exc) is None

    def test_ignores_non_http_codes(self):
        # 200 is 2xx, not 4xx/5xx — should NOT match
        exc = Exception("response code was 200 OK")
        result = self.fn(exc)
        assert result is None


# ---------------------------------------------------------------------------
# retry.py — RetryAttempt
# ---------------------------------------------------------------------------


class TestRetryAttempt:
    def test_to_dict_basic(self):
        from tokenpak.orchestration.retry import RetryAttempt

        attempt = RetryAttempt(level=0, description="test", error="oops")
        d = attempt.to_dict()
        assert d["level"] == 0
        assert d["description"] == "test"
        assert d["error"] == "oops"
        assert "timestamp" in d

    def test_to_dict_with_http_status(self):
        from tokenpak.orchestration.retry import RetryAttempt

        attempt = RetryAttempt(level=1, description="retry", error="429", http_status="429")
        d = attempt.to_dict()
        assert d["http_status"] == "429"

    def test_to_dict_no_http_status_key_when_none(self):
        from tokenpak.orchestration.retry import RetryAttempt

        attempt = RetryAttempt(level=0, description="x", error="y")
        d = attempt.to_dict()
        assert "http_status" not in d


# ---------------------------------------------------------------------------
# retry.py — RetryExhaustedError / ImmediateAlertError
# ---------------------------------------------------------------------------


class TestRetryErrors:
    def test_retry_exhausted_error_message(self):
        from tokenpak.orchestration.retry import RetryAttempt, RetryExhaustedError

        attempts = [RetryAttempt(0, "a", "e"), RetryAttempt(1, "b", "f")]
        exc = RetryExhaustedError(context={"task_id": "t1"}, partial_state={}, attempts=attempts)
        assert "2 attempts" in str(exc)
        assert exc.context["task_id"] == "t1"
        assert exc.attempts is attempts

    def test_immediate_alert_error(self):
        from tokenpak.orchestration.retry import ImmediateAlertError

        original = RuntimeError("auth denied")
        exc = ImmediateAlertError(status_code="401", original=original)
        assert exc.status_code == "401"
        assert exc.original is original
        assert "401" in str(exc)


# ---------------------------------------------------------------------------
# retry.py — RetryEngine
# ---------------------------------------------------------------------------


class TestRetryEngineInit:
    def test_default_init(self, tmp_path):
        from tokenpak.orchestration.retry import RetryEngine

        fn = MagicMock(return_value="ok")
        engine = RetryEngine(fn=fn, context={"task_id": "t1"}, state_dir=tmp_path)
        assert engine.agent_id is not None
        assert isinstance(engine.wait_seconds, list)
        assert len(engine.wait_seconds) == 3

    def test_custom_per_error_merged(self, tmp_path):
        from tokenpak.orchestration.retry import RetryEngine

        fn = MagicMock(return_value="ok")
        engine = RetryEngine(
            fn=fn,
            context={},
            state_dir=tmp_path,
            per_error={"418": "wait"},
        )
        assert engine._per_error.get("418") == "wait"
        # Defaults are still present
        assert engine._per_error.get("429") == "wait"

    def test_model_from_context(self, tmp_path):
        from tokenpak.orchestration.retry import RetryEngine

        fn = MagicMock(return_value="ok")
        engine = RetryEngine(
            fn=fn,
            context={"model": "gpt-4o", "provider": "openai"},
            state_dir=tmp_path,
        )
        assert engine._current_model == "gpt-4o"
        assert engine._current_provider == "openai"


class TestRetryEngineSuccessPath:
    def test_success_on_first_attempt(self, tmp_path):
        from tokenpak.orchestration.retry import RetryEngine

        fn = MagicMock(return_value=42)
        engine = RetryEngine(
            fn=fn, context={"task_id": "t1"}, state_dir=tmp_path, wait_seconds=[0, 0, 0]
        )
        result = engine.run()
        assert result == 42

    def test_fn_called_with_context_and_state(self, tmp_path):
        from tokenpak.orchestration.retry import RetryEngine

        calls = []

        def fn(ctx, state):
            calls.append((ctx, state))
            return "done"

        engine = RetryEngine(
            fn=fn, context={"task_id": "x"}, state_dir=tmp_path, wait_seconds=[0, 0, 0]
        )
        engine.run()
        assert len(calls) >= 1
        assert calls[0][0]["task_id"] == "x"

    def test_partial_state_passed_through(self, tmp_path):
        from tokenpak.orchestration.retry import RetryEngine

        received_states = []

        def fn(ctx, state):
            received_states.append(dict(state))
            return "ok"

        partial = {"progress": 5}
        engine = RetryEngine(
            fn=fn, context={}, partial_state=partial, state_dir=tmp_path, wait_seconds=[0, 0, 0]
        )
        engine.run()
        assert received_states[0]["progress"] == 5


class TestRetryEngineEscalation:
    def test_exhausted_raises_retry_exhausted_error(self, tmp_path):
        from tokenpak.orchestration.retry import RetryEngine, RetryExhaustedError

        alert_calls = []

        def fn(ctx, state):
            raise RuntimeError("always fails")

        def on_alert(alert):
            alert_calls.append(alert)

        engine = RetryEngine(
            fn=fn,
            context={"task_id": "fail-task"},
            state_dir=tmp_path,
            wait_seconds=[0, 0],
            on_human_alert=on_alert,
        )
        with pytest.raises(RetryExhaustedError) as exc_info:
            engine.run()

        assert len(alert_calls) == 1
        assert "fail-task" in alert_calls[0]["task_id"]
        assert len(exc_info.value.attempts) > 0

    def test_immediate_alert_on_401(self, tmp_path):
        from tokenpak.orchestration.retry import (
            RetryEngine,
            RetryExhaustedError,
        )

        def fn(ctx, state):
            exc = Exception("HTTP 401 Unauthorized")
            raise exc

        alert_calls = []

        engine = RetryEngine(
            fn=fn,
            context={"task_id": "auth-fail"},
            state_dir=tmp_path,
            wait_seconds=[0],
            on_human_alert=lambda a: alert_calls.append(a),
        )
        # Auth errors should skip escalation chain and go to Level 4
        with pytest.raises(RetryExhaustedError):
            engine.run()
        assert len(alert_calls) == 1

    def test_model_downgrade_default(self, tmp_path):
        from tokenpak.orchestration.retry import RetryEngine

        engine = RetryEngine(fn=MagicMock(), context={}, state_dir=tmp_path)
        chain = engine._model_chain
        # Should return next model in chain
        next_model = engine._default_model_downgrade(chain[0])
        assert next_model == chain[1]

    def test_model_downgrade_at_end_returns_last(self, tmp_path):
        from tokenpak.orchestration.retry import RetryEngine

        engine = RetryEngine(fn=MagicMock(), context={}, state_dir=tmp_path)
        last = engine._model_chain[-1]
        result = engine._default_model_downgrade(last)
        assert result == last

    def test_provider_switch_default(self, tmp_path):
        from tokenpak.orchestration.retry import RetryEngine

        engine = RetryEngine(fn=MagicMock(), context={}, state_dir=tmp_path)
        chain = engine._provider_chain
        next_prov = engine._default_provider_switch(chain[0])
        assert next_prov == chain[1]


class TestRetryEngineState:
    def test_save_and_load_state(self, tmp_path):
        from tokenpak.orchestration.retry import RetryEngine

        engine = RetryEngine(
            fn=MagicMock(return_value="ok"),
            context={"task_id": "state-test"},
            partial_state={"step": 3},
            state_dir=tmp_path,
        )
        path = engine._save_state()
        assert path.exists()
        loaded = RetryEngine.load_state(path)
        assert loaded["context"]["task_id"] == "state-test"
        assert loaded["partial_state"]["step"] == 3


# ---------------------------------------------------------------------------
# locks.py — FileLockManager
# ---------------------------------------------------------------------------


class TestFileLockManager:
    def test_claim_returns_record(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager

        mgr = FileLockManager(agent_id="test-agent", lock_dir=tmp_path)
        record = mgr.claim("/tmp/fake-file.txt")
        assert record["agent"] == "test-agent"
        assert "expires" in record
        assert "acquired" in record

    def test_claim_and_query(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager

        mgr = FileLockManager(agent_id="test-agent", lock_dir=tmp_path)
        path = "/tmp/test-lock-target.txt"
        mgr.claim(path)
        rec = mgr.query(path)
        assert rec is not None
        assert rec["agent"] == "test-agent"

    def test_release_removes_lock(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager

        mgr = FileLockManager(agent_id="test-agent", lock_dir=tmp_path)
        path = "/tmp/test-file-release.txt"
        mgr.claim(path)
        result = mgr.release(path)
        assert result is True
        assert mgr.query(path) is None

    def test_release_returns_false_if_no_lock(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager

        mgr = FileLockManager(agent_id="test-agent", lock_dir=tmp_path)
        assert mgr.release("/tmp/not-locked.txt") is False

    def test_conflict_from_different_agent(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager, LockConflictError

        mgr_a = FileLockManager(agent_id="agent-a", lock_dir=tmp_path)
        mgr_b = FileLockManager(agent_id="agent-b", lock_dir=tmp_path)
        path = "/tmp/contested.txt"
        mgr_a.claim(path, timeout_s=600)
        with pytest.raises(LockConflictError):
            mgr_b.claim(path)

    def test_same_agent_can_re_claim(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager

        mgr = FileLockManager(agent_id="agent-x", lock_dir=tmp_path)
        path = "/tmp/same-agent.txt"
        r1 = mgr.claim(path, timeout_s=600)
        r2 = mgr.claim(path, timeout_s=600)  # should succeed (re-affirm)
        assert r2["agent"] == "agent-x"

    def test_expired_lock_can_be_stolen(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager

        mgr_a = FileLockManager(agent_id="agent-a", lock_dir=tmp_path)
        mgr_b = FileLockManager(agent_id="agent-b", lock_dir=tmp_path)
        path = "/tmp/expiring.txt"
        # claim with 0-second timeout → immediately expired
        mgr_a.claim(path, timeout_s=0)
        # B should be able to steal it
        record = mgr_b.claim(path, timeout_s=600)
        assert record["agent"] == "agent-b"

    def test_prune_expired_removes_stale_locks(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager

        mgr = FileLockManager(agent_id="agent-x", lock_dir=tmp_path)
        mgr.claim("/tmp/stale.txt", timeout_s=0)
        time.sleep(0.05)  # ensure it's expired
        removed = mgr.prune_expired()
        assert removed >= 1

    def test_locks_lists_active_only(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager

        mgr = FileLockManager(agent_id="agent-x", lock_dir=tmp_path)
        mgr.claim("/tmp/active-lock.txt", timeout_s=600)
        mgr.claim("/tmp/also-active.txt", timeout_s=600)
        mgr.claim("/tmp/expired.txt", timeout_s=0)
        live = mgr.locks()
        # The expired one should be pruned; at least the 2 active ones are returned
        agents = [r["agent"] for r in live]
        assert all(a == "agent-x" for a in agents)
        assert len(live) >= 2

    def test_suggest_alternatives_excludes_locked(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager

        mgr = FileLockManager(agent_id="agent-x", lock_dir=tmp_path)
        mgr.claim("/tmp/locked.txt", timeout_s=600)
        candidates = ["/tmp/locked.txt", "/tmp/free-a.txt", "/tmp/free-b.txt"]
        alts = mgr.suggest_alternatives("/tmp/locked.txt", candidates)
        assert "/tmp/locked.txt" not in alts
        assert "/tmp/free-a.txt" in alts

    def test_renew_extends_expiry(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager

        mgr = FileLockManager(agent_id="agent-x", lock_dir=tmp_path)
        path = "/tmp/renewable.txt"
        mgr.claim(path, timeout_s=60)
        original_expires = mgr.query(path)["expires"]
        updated = mgr.renew(path, timeout_s=600)
        assert updated["expires"] > original_expires

    def test_renew_raises_on_missing_lock(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager, LockExpiredError

        mgr = FileLockManager(agent_id="agent-x", lock_dir=tmp_path)
        with pytest.raises(LockExpiredError):
            mgr.renew("/tmp/no-such-lock.txt")

    def test_renew_raises_when_owned_by_other(self, tmp_path):
        from tokenpak.orchestration.locks import FileLockManager, LockConflictError

        mgr_a = FileLockManager(agent_id="agent-a", lock_dir=tmp_path)
        mgr_b = FileLockManager(agent_id="agent-b", lock_dir=tmp_path)
        path = "/tmp/owned-by-a.txt"
        mgr_a.claim(path, timeout_s=600)
        with pytest.raises(LockConflictError):
            mgr_b.renew(path)


# ---------------------------------------------------------------------------
# validation_framework.py — ValidationCheck / ValidationResult
# ---------------------------------------------------------------------------


class TestValidationDataClasses:
    def test_validation_check_fields(self):
        from tokenpak.orchestration.validation_framework import ValidationCheck

        check = ValidationCheck(name="my_check", passed=True, message="all good")
        assert check.name == "my_check"
        assert check.passed is True

    def test_validation_result_summary_pass(self):
        from tokenpak.orchestration.validation_framework import ValidationCheck, ValidationResult

        checks = [
            ValidationCheck(name="a", passed=True),
            ValidationCheck(name="b", passed=True),
        ]
        result = ValidationResult(
            passed=True,
            checks=checks,
            confidence=1.0,
            evidence={},
            validator_name="TestV",
        )
        summary = result.summary()
        assert "PASS" in summary
        assert "2/2" in summary

    def test_validation_result_summary_fail(self):
        from tokenpak.orchestration.validation_framework import ValidationCheck, ValidationResult

        checks = [
            ValidationCheck(name="a", passed=True),
            ValidationCheck(name="b", passed=False, message="broke"),
        ]
        result = ValidationResult(
            passed=False,
            checks=checks,
            confidence=0.5,
            evidence={},
            validator_name="TestV",
        )
        summary = result.summary()
        assert "FAIL" in summary
        assert "1/2" in summary

    def test_failed_checks_filters_correctly(self):
        from tokenpak.orchestration.validation_framework import ValidationCheck, ValidationResult

        checks = [
            ValidationCheck(name="a", passed=True),
            ValidationCheck(name="b", passed=False),
            ValidationCheck(name="c", passed=False),
        ]
        result = ValidationResult(
            passed=False, checks=checks, confidence=0.33, evidence={}, validator_name="T"
        )
        failed = result.failed_checks()
        assert len(failed) == 2
        assert all(not c.passed for c in failed)


# ---------------------------------------------------------------------------
# validation_framework.py — FileStateValidator
# ---------------------------------------------------------------------------


class TestFileStateValidator:
    def test_must_exist_passes(self, tmp_path):
        from tokenpak.orchestration.validation_framework import FileStateValidator

        f = tmp_path / "exists.txt"
        f.write_text("hello")
        v = FileStateValidator(must_exist=[str(f)])
        result = v.validate({}, {})
        assert result.passed

    def test_must_exist_fails_when_missing(self, tmp_path):
        from tokenpak.orchestration.validation_framework import FileStateValidator

        v = FileStateValidator(must_exist=[str(tmp_path / "missing.txt")])
        result = v.validate({}, {})
        assert not result.passed

    def test_must_not_exist_passes_when_absent(self, tmp_path):
        from tokenpak.orchestration.validation_framework import FileStateValidator

        v = FileStateValidator(must_not_exist=[str(tmp_path / "absent.txt")])
        result = v.validate({}, {})
        assert result.passed

    def test_must_not_exist_fails_when_present(self, tmp_path):
        from tokenpak.orchestration.validation_framework import FileStateValidator

        f = tmp_path / "present.txt"
        f.write_text("here")
        v = FileStateValidator(must_not_exist=[str(f)])
        result = v.validate({}, {})
        assert not result.passed

    def test_content_pattern_passes_when_matched(self, tmp_path):
        from tokenpak.orchestration.validation_framework import FileStateValidator

        f = tmp_path / "log.txt"
        f.write_text("Status: ok\nAll done")
        v = FileStateValidator(content_patterns={str(f): r"Status: ok"})
        result = v.validate({}, {})
        assert result.passed

    def test_content_pattern_fails_when_not_matched(self, tmp_path):
        from tokenpak.orchestration.validation_framework import FileStateValidator

        f = tmp_path / "log.txt"
        f.write_text("Status: error")
        v = FileStateValidator(content_patterns={str(f): r"Status: ok"})
        result = v.validate({}, {})
        assert not result.passed

    def test_no_checks_configured_vacuously_passes(self):
        from tokenpak.orchestration.validation_framework import FileStateValidator

        v = FileStateValidator()
        result = v.validate({}, {})
        assert result.passed

    def test_must_be_newer_than_passes(self, tmp_path):
        from tokenpak.orchestration.validation_framework import FileStateValidator

        f = tmp_path / "newfile.txt"
        f.write_text("content")
        past_ts = time.time() - 10
        v = FileStateValidator(must_be_newer_than={str(f): past_ts})
        result = v.validate({}, {})
        assert result.passed

    def test_must_be_newer_than_fails_for_old_file(self, tmp_path):
        from tokenpak.orchestration.validation_framework import FileStateValidator

        f = tmp_path / "oldfile.txt"
        f.write_text("content")
        future_ts = time.time() + 9999
        v = FileStateValidator(must_be_newer_than={str(f): future_ts})
        result = v.validate({}, {})
        assert not result.passed


# ---------------------------------------------------------------------------
# validation_framework.py — SchemaValidator
# ---------------------------------------------------------------------------


class TestSchemaValidator:
    def test_required_key_present(self):
        from tokenpak.orchestration.validation_framework import SchemaValidator

        v = SchemaValidator({"required_keys": ["status"]})
        result = v.validate({"status": "ok"}, {})
        assert result.passed

    def test_required_key_missing(self):
        from tokenpak.orchestration.validation_framework import SchemaValidator

        v = SchemaValidator({"required_keys": ["status"]})
        result = v.validate({}, {})
        assert not result.passed

    def test_disallowed_key_absent(self):
        from tokenpak.orchestration.validation_framework import SchemaValidator

        v = SchemaValidator({"disallowed_keys": ["error"]})
        result = v.validate({"status": "ok"}, {})
        assert result.passed

    def test_disallowed_key_present(self):
        from tokenpak.orchestration.validation_framework import SchemaValidator

        v = SchemaValidator({"disallowed_keys": ["error"]})
        result = v.validate({"status": "ok", "error": "boom"}, {})
        assert not result.passed

    def test_type_check_passes(self):
        from tokenpak.orchestration.validation_framework import SchemaValidator

        v = SchemaValidator({"types": {"count": int}})
        result = v.validate({"count": 5}, {})
        assert result.passed

    def test_type_check_fails(self):
        from tokenpak.orchestration.validation_framework import SchemaValidator

        v = SchemaValidator({"types": {"count": int}})
        result = v.validate({"count": "five"}, {})
        assert not result.passed

    def test_allowed_values_passes(self):
        from tokenpak.orchestration.validation_framework import SchemaValidator

        v = SchemaValidator({"allowed_values": {"status": ["ok", "pending"]}})
        result = v.validate({"status": "ok"}, {})
        assert result.passed

    def test_allowed_values_fails(self):
        from tokenpak.orchestration.validation_framework import SchemaValidator

        v = SchemaValidator({"allowed_values": {"status": ["ok", "pending"]}})
        result = v.validate({"status": "error"}, {})
        assert not result.passed

    def test_empty_schema_vacuously_passes(self):
        from tokenpak.orchestration.validation_framework import SchemaValidator

        v = SchemaValidator({})
        result = v.validate({"anything": True}, {})
        assert result.passed

    def test_combined_schema(self):
        from tokenpak.orchestration.validation_framework import SchemaValidator

        v = SchemaValidator(
            {
                "required_keys": ["id", "status"],
                "types": {"id": int, "status": str},
                "allowed_values": {"status": ["ok", "fail"]},
                "disallowed_keys": ["debug"],
            }
        )
        result = v.validate({"id": 1, "status": "ok"}, {})
        assert result.passed

    def test_validator_name_property(self):
        from tokenpak.orchestration.validation_framework import SchemaValidator

        v = SchemaValidator({})
        assert v.name == "SchemaValidator"


# ---------------------------------------------------------------------------
# validation_framework.py — ValidationOrchestrator
# ---------------------------------------------------------------------------


class TestValidationOrchestrator:
    def test_no_validators_returns_pass(self):
        from tokenpak.orchestration.validation_framework import ValidationOrchestrator

        orch = ValidationOrchestrator()
        result = orch.validate_step("deploy", {}, {})
        assert result.passed

    def test_registered_validator_runs(self, tmp_path):
        from tokenpak.orchestration.validation_framework import (
            FileStateValidator,
            ValidationOrchestrator,
        )

        f = tmp_path / "artifact.txt"
        f.write_text("produced")
        orch = ValidationOrchestrator()
        orch.register_step_validator("build", FileStateValidator(must_exist=[str(f)]))
        result = orch.validate_step("build", {}, {})
        assert result.passed

    def test_failed_validator_fails_step(self, tmp_path):
        from tokenpak.orchestration.validation_framework import (
            FileStateValidator,
            ValidationOrchestrator,
        )

        orch = ValidationOrchestrator()
        orch.register_step_validator(
            "build", FileStateValidator(must_exist=[str(tmp_path / "missing.txt")])
        )
        result = orch.validate_step("build", {}, {})
        assert not result.passed

    def test_validation_history_recorded(self):
        from tokenpak.orchestration.validation_framework import (
            SchemaValidator,
            ValidationOrchestrator,
        )

        orch = ValidationOrchestrator()
        orch.register_step_validator("check", SchemaValidator({"required_keys": ["x"]}))
        orch.validate_step("check", {"x": 1}, {})
        history = orch.validation_history()
        assert len(history) == 1
        assert history[0]["step"] == "check"
        assert history[0]["passed"] is True

    def test_handle_failure_raises_validation_error_when_escalation_disabled(self):
        from tokenpak.orchestration.validation_framework import (
            RetryPolicy,
            SchemaValidator,
            ValidationError,
            ValidationOrchestrator,
        )

        policy = RetryPolicy(max_retries=1, retry_delay_seconds=0, escalate_on_exhaustion=False)
        orch = ValidationOrchestrator(retry_policy=policy)
        orch.register_step_validator("step", SchemaValidator({"required_keys": ["must_have"]}))
        # Initial failing result
        initial = orch.validate_step("step", {}, {})
        assert not initial.passed
        with pytest.raises(ValidationError):
            orch.handle_failure("step", initial, expected={})

    def test_handle_failure_calls_on_escalate(self):
        from tokenpak.orchestration.validation_framework import (
            RetryPolicy,
            SchemaValidator,
            ValidationOrchestrator,
        )

        escalations = []
        policy = RetryPolicy(max_retries=1, retry_delay_seconds=0, escalate_on_exhaustion=True)
        orch = ValidationOrchestrator(
            retry_policy=policy,
            on_escalate=lambda step, result: escalations.append(step),
        )
        orch.register_step_validator("step", SchemaValidator({"required_keys": ["x"]}))
        initial = orch.validate_step("step", {}, {})
        orch.handle_failure("step", initial, expected={})
        assert "step" in escalations


# ---------------------------------------------------------------------------
# workflow_budget.py — WorkflowBudget
# ---------------------------------------------------------------------------


class TestWorkflowBudget:
    def test_init_basic(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a", "b", "c", "d"])
        assert budget.total == 1000
        assert budget.remaining == 1000
        assert set(budget.pending_steps) == {"a", "b", "c", "d"}

    def test_even_split_allocation(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a", "b", "c", "d"])
        # Each step gets 250
        assert budget.step_allocation("a") == 250
        assert budget.step_allocation("b") == 250

    def test_invalid_total_raises(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        with pytest.raises(ValueError):
            WorkflowBudget(total=0, steps=["a"])

    def test_empty_steps_raises(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        with pytest.raises(ValueError):
            WorkflowBudget(total=1000, steps=[])

    def test_record_usage_updates_remaining(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a", "b"])
        budget.record_usage("a", 400)
        assert budget.remaining == 600
        assert budget.step_usage("a") == 400

    def test_record_usage_removes_from_pending(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a", "b"])
        budget.record_usage("a", 400)
        assert "a" not in budget.pending_steps
        assert "a" in budget.completed_steps

    def test_overspend_generates_warning_event(self):
        from tokenpak.orchestration.workflow_budget import BudgetEventKind, WorkflowBudget

        # warn_pct=1.20 — step uses 121% of its 500-token allocation
        budget = WorkflowBudget(total=1000, steps=["a", "b"])
        events = budget.record_usage("a", 610)  # 610 > 500 * 1.20 = 600
        kinds = [e.kind for e in events]
        assert BudgetEventKind.WARNING in kinds

    def test_no_warning_for_normal_spend(self):
        from tokenpak.orchestration.workflow_budget import BudgetEventKind, WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a", "b"])
        events = budget.record_usage("a", 400)  # under 500 allocation
        kinds = [e.kind for e in events]
        assert BudgetEventKind.WARNING not in kinds

    def test_critical_event_when_budget_low(self):
        from tokenpak.orchestration.workflow_budget import BudgetEventKind, WorkflowBudget

        # Total 100, 4 steps → 25 each. Spend 85 on step a → 15 left (<20%)
        budget = WorkflowBudget(total=100, steps=["a", "b", "c", "d"])
        events = budget.record_usage("a", 85)
        kinds = [e.kind for e in events]
        assert BudgetEventKind.CRITICAL in kinds

    def test_exhausted_event_when_budget_zero(self):
        from tokenpak.orchestration.workflow_budget import BudgetEventKind, WorkflowBudget

        budget = WorkflowBudget(total=100, steps=["a", "b"])
        events = budget.record_usage("a", 100)
        kinds = [e.kind for e in events]
        assert BudgetEventKind.EXHAUSTED in kinds

    def test_rebalanced_event_on_underspend(self):
        from tokenpak.orchestration.workflow_budget import BudgetEventKind, WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a", "b"])
        events = budget.record_usage("a", 100)  # underspend by 400
        kinds = [e.kind for e in events]
        assert BudgetEventKind.REBALANCED in kinds

    def test_duplicate_record_raises(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a", "b"])
        budget.record_usage("a", 400)
        with pytest.raises(ValueError):
            budget.record_usage("a", 100)

    def test_negative_usage_raises(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a"])
        with pytest.raises(ValueError):
            budget.record_usage("a", -5)

    def test_unknown_step_raises_key_error(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a"])
        with pytest.raises(KeyError):
            budget.record_usage("z", 100)

    def test_unknown_step_allocation_raises_key_error(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a"])
        with pytest.raises(KeyError):
            budget.step_allocation("z")

    def test_snapshot_reflects_state(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a", "b"])
        budget.record_usage("a", 300)
        snap = budget.snapshot()
        assert snap["remaining"] == 700
        assert snap["spent"] == 300
        assert "a" in snap["completed_steps"]
        assert "b" in snap["pending_steps"]

    def test_step_usage_returns_none_before_record(self):
        from tokenpak.orchestration.workflow_budget import WorkflowBudget

        budget = WorkflowBudget(total=1000, steps=["a"])
        assert budget.step_usage("a") is None

    def test_is_warning_event(self):
        from tokenpak.orchestration.workflow_budget import BudgetEvent, BudgetEventKind

        warn = BudgetEvent(kind=BudgetEventKind.WARNING, step="a", message="test")
        crit = BudgetEvent(kind=BudgetEventKind.CRITICAL, step="a", message="test")
        normal = BudgetEvent(kind=BudgetEventKind.USAGE_RECORDED, step="a", message="test")
        assert warn.is_warning() is True
        assert crit.is_warning() is True
        assert normal.is_warning() is False


# ---------------------------------------------------------------------------
# capabilities.py — AgentCapabilities / TaskRequirements
# ---------------------------------------------------------------------------


class TestAgentCapabilities:
    def test_defaults(self):
        from tokenpak.orchestration.capabilities import AgentCapabilities

        caps = AgentCapabilities()
        assert caps.gpu is False
        assert caps.memory_gb == 4.0
        assert caps.max_concurrent == 1
        assert "anthropic" in caps.provider_access

    def test_to_dict_round_trip(self):
        from tokenpak.orchestration.capabilities import AgentCapabilities

        caps = AgentCapabilities(gpu=True, memory_gb=16.0, specialties=["code"])
        d = caps.to_dict()
        restored = AgentCapabilities.from_dict(d)
        assert restored.gpu is True
        assert restored.memory_gb == 16.0
        assert "code" in restored.specialties

    def test_from_dict_with_missing_fields_uses_defaults(self):
        from tokenpak.orchestration.capabilities import AgentCapabilities

        caps = AgentCapabilities.from_dict({})
        assert caps.gpu is False
        assert caps.memory_gb == 4.0


class TestTaskRequirements:
    def test_defaults(self):
        from tokenpak.orchestration.capabilities import TaskRequirements

        req = TaskRequirements()
        assert req.requires_gpu is None
        assert req.min_memory_gb is None
        assert req.required_specialties == []
        assert req.prefer_idle is True

    def test_to_dict(self):
        from tokenpak.orchestration.capabilities import TaskRequirements

        req = TaskRequirements(requires_gpu=True, min_memory_gb=8.0)
        d = req.to_dict()
        assert d["requires_gpu"] is True
        assert d["min_memory_gb"] == 8.0
