"""Tests for tokenpak.agentic.validation_framework.

Covers:
  1. ABC interface enforcement
  2. ServiceHealthValidator (process check path)
  3. TestSuiteValidator (exit code + pass-rate parsing)
  4. FileStateValidator (exist / absent / content pattern)
  5. SchemaValidator (required keys / types / allowed values)
  6. ValidationOrchestrator step registration + merged result
  7. ValidationOrchestrator retry on failure (retry_fn path)
  8. ValidationOrchestrator escalation via on_escalate callback
  9. make_validated_step_handler raises ValidationError on exhaustion
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "tokenpak.agentic.validation_framework", reason="module not available in current build"
)
import time
from unittest.mock import MagicMock, patch

import pytest
from tokenpak.agentic.validation_framework import (
    FileStateValidator,
    PostActionValidator,
    RetryPolicy,
    SchemaValidator,
    ServiceHealthValidator,
    TestSuiteValidator,
    ValidationCheck,
    ValidationError,
    ValidationOrchestrator,
    ValidationResult,
    make_validated_step_handler,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dummy_result(passed: bool = True) -> ValidationResult:
    return ValidationResult(
        passed=passed,
        checks=[ValidationCheck(name="dummy", passed=passed, message="")],
        confidence=1.0 if passed else 0.0,
        evidence={},
    )


# ---------------------------------------------------------------------------
# 1. ABC enforcement
# ---------------------------------------------------------------------------


class TestABCInterface:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            PostActionValidator()  # type: ignore[abstract]

    def test_concrete_subclass_works(self):
        class MyValidator(PostActionValidator):
            def validate(self, action_result, expected):
                check = ValidationCheck(name="ok", passed=True, message="fine")
                return self._make_result([check], {})

        v = MyValidator()
        r = v.validate({}, {})
        assert r.passed is True
        assert r.confidence == 1.0
        assert r.validator_name == "MyValidator"

    def test_concrete_subclass_fail(self):
        class FailValidator(PostActionValidator):
            def validate(self, action_result, expected):
                checks = [
                    ValidationCheck(name="c1", passed=False, message="bad"),
                    ValidationCheck(name="c2", passed=True, message="ok"),
                ]
                return self._make_result(checks, {})

        v = FailValidator()
        r = v.validate({}, {})
        assert r.passed is False
        assert r.confidence == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 2. ServiceHealthValidator — process check
# ---------------------------------------------------------------------------


class TestServiceHealthValidator:
    def test_process_found(self):
        """pgrep returns 0 → process found."""
        v = ServiceHealthValidator(process_name="python")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="1234\n", stderr="")
            r = v.validate({}, {})
        assert r.passed is True
        assert any(c.name == "process_running" and c.passed for c in r.checks)

    def test_process_not_found(self):
        v = ServiceHealthValidator(process_name="nonexistent_xyz")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            r = v.validate({}, {})
        assert r.passed is False

    def test_http_success(self):
        v = ServiceHealthValidator(url="http://localhost:9999/health", expected_status=200)
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = b'{"status":"ok"}'
        with patch("urllib.request.urlopen", return_value=mock_resp):
            r = v.validate({}, {})
        assert r.passed is True

    def test_http_failure_wrong_status(self):
        v = ServiceHealthValidator(url="http://localhost:9999/health", expected_status=200)
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.getcode.return_value = 503
        mock_resp.read.return_value = b""
        with patch("urllib.request.urlopen", return_value=mock_resp):
            r = v.validate({}, {})
        assert r.passed is False

    def test_no_config_fails(self):
        v = ServiceHealthValidator()  # no url, no process_name
        r = v.validate({}, {})
        assert r.passed is False


# ---------------------------------------------------------------------------
# 3. TestSuiteValidator
# ---------------------------------------------------------------------------


class TestTestSuiteValidator:
    def _make_proc(self, returncode, stdout="", stderr=""):
        return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_exit_zero_no_summary(self):
        v = TestSuiteValidator(command=["pytest"])
        with patch("subprocess.run", return_value=self._make_proc(0)):
            r = v.validate({}, {})
        assert r.passed is True

    def test_exit_nonzero_fails(self):
        v = TestSuiteValidator(command=["pytest"])
        with patch("subprocess.run", return_value=self._make_proc(1, stdout="1 failed")):
            r = v.validate({}, {})
        assert r.passed is False

    def test_pass_rate_check(self):
        v = TestSuiteValidator(command=["pytest"], min_pass_pct=80.0)
        stdout = "5 passed, 1 failed in 2.3s"
        with patch("subprocess.run", return_value=self._make_proc(1, stdout=stdout)):
            r = v.validate({}, {})
        # exit code 1 → fails, but pass_rate check should reflect 5/6 ≈ 83% > 80%
        checks_by_name = {c.name: c for c in r.checks}
        assert checks_by_name["pass_rate"].passed is True

    def test_timeout_produces_failure(self):
        import subprocess

        v = TestSuiteValidator(command=["pytest"], timeout=1)
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=1)
        ):
            r = v.validate({}, {})
        assert r.passed is False
        assert any(c.name == "test_timeout" for c in r.checks)


# ---------------------------------------------------------------------------
# 4. FileStateValidator
# ---------------------------------------------------------------------------


class TestFileStateValidator:
    def test_must_exist_pass(self, tmp_path):
        f = tmp_path / "output.txt"
        f.write_text("hello")
        v = FileStateValidator(must_exist=[str(f)])
        r = v.validate({}, {})
        assert r.passed is True

    def test_must_exist_fail(self, tmp_path):
        v = FileStateValidator(must_exist=[str(tmp_path / "missing.txt")])
        r = v.validate({}, {})
        assert r.passed is False

    def test_must_not_exist_pass(self, tmp_path):
        v = FileStateValidator(must_not_exist=[str(tmp_path / "nope.txt")])
        r = v.validate({}, {})
        assert r.passed is True

    def test_must_not_exist_fail(self, tmp_path):
        f = tmp_path / "present.txt"
        f.write_text("oops")
        v = FileStateValidator(must_not_exist=[str(f)])
        r = v.validate({}, {})
        assert r.passed is False

    def test_content_pattern_match(self, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("Build succeeded at 12:00")
        v = FileStateValidator(content_patterns={str(f): r"Build succeeded"})
        r = v.validate({}, {})
        assert r.passed is True

    def test_content_pattern_no_match(self, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("Build failed miserably")
        v = FileStateValidator(content_patterns={str(f): r"Build succeeded"})
        r = v.validate({}, {})
        assert r.passed is False

    def test_newer_than_pass(self, tmp_path):
        f = tmp_path / "out.bin"
        f.write_bytes(b"\x00")
        past = time.time() - 10
        v = FileStateValidator(must_be_newer_than={str(f): past})
        r = v.validate({}, {})
        assert r.passed is True

    def test_newer_than_fail(self, tmp_path):
        f = tmp_path / "out.bin"
        f.write_bytes(b"\x00")
        future = time.time() + 3600
        v = FileStateValidator(must_be_newer_than={str(f): future})
        r = v.validate({}, {})
        assert r.passed is False

    def test_vacuous_pass(self):
        v = FileStateValidator()
        r = v.validate({}, {})
        assert r.passed is True


# ---------------------------------------------------------------------------
# 5. SchemaValidator
# ---------------------------------------------------------------------------


class TestSchemaValidator:
    def test_required_keys_present(self):
        v = SchemaValidator({"required_keys": ["status", "id"]})
        r = v.validate({"status": "ok", "id": 42}, {})
        assert r.passed is True

    def test_required_key_missing(self):
        v = SchemaValidator({"required_keys": ["status", "id"]})
        r = v.validate({"status": "ok"}, {})  # "id" missing
        assert r.passed is False

    def test_type_check_pass(self):
        v = SchemaValidator({"types": {"count": int, "name": str}})
        r = v.validate({"count": 5, "name": "foo"}, {})
        assert r.passed is True

    def test_type_check_fail(self):
        v = SchemaValidator({"types": {"count": int}})
        r = v.validate({"count": "five"}, {})
        assert r.passed is False

    def test_allowed_values_pass(self):
        v = SchemaValidator({"allowed_values": {"status": ["ok", "pending"]}})
        r = v.validate({"status": "ok"}, {})
        assert r.passed is True

    def test_allowed_values_fail(self):
        v = SchemaValidator({"allowed_values": {"status": ["ok", "pending"]}})
        r = v.validate({"status": "error"}, {})
        assert r.passed is False

    def test_disallowed_key_absent(self):
        v = SchemaValidator({"disallowed_keys": ["error"]})
        r = v.validate({"status": "ok"}, {})
        assert r.passed is True

    def test_disallowed_key_present(self):
        v = SchemaValidator({"disallowed_keys": ["error"]})
        r = v.validate({"status": "ok", "error": "oops"}, {})
        assert r.passed is False

    def test_empty_schema_vacuous_pass(self):
        v = SchemaValidator({})
        r = v.validate({"anything": 1}, {})
        assert r.passed is True


# ---------------------------------------------------------------------------
# 6. ValidationOrchestrator — registration and merged result
# ---------------------------------------------------------------------------


class TestValidationOrchestrator:
    def test_no_validators_passes(self):
        orch = ValidationOrchestrator()
        r = orch.validate_step("step1", {}, {})
        assert r.passed is True

    def test_single_validator_pass(self):
        orch = ValidationOrchestrator()
        orch.register_step_validator("step1", SchemaValidator({"required_keys": ["x"]}))
        r = orch.validate_step("step1", {"x": 1}, {})
        assert r.passed is True

    def test_single_validator_fail(self):
        orch = ValidationOrchestrator()
        orch.register_step_validator("step1", SchemaValidator({"required_keys": ["x"]}))
        r = orch.validate_step("step1", {}, {})
        assert r.passed is False

    def test_multiple_validators_all_pass(self):
        orch = ValidationOrchestrator()
        orch.register_step_validator("step1", SchemaValidator({"required_keys": ["a"]}))
        orch.register_step_validator("step1", SchemaValidator({"required_keys": ["b"]}))
        r = orch.validate_step("step1", {"a": 1, "b": 2}, {})
        assert r.passed is True

    def test_multiple_validators_one_fails_merges_to_fail(self):
        orch = ValidationOrchestrator()
        orch.register_step_validator("step1", SchemaValidator({"required_keys": ["a"]}))
        orch.register_step_validator("step1", SchemaValidator({"required_keys": ["missing_key"]}))
        r = orch.validate_step("step1", {"a": 1}, {})
        assert r.passed is False

    def test_history_recorded(self):
        orch = ValidationOrchestrator()
        orch.register_step_validator("step1", SchemaValidator({}))
        orch.validate_step("step1", {}, {})
        history = orch.validation_history()
        assert len(history) == 1
        assert history[0]["step"] == "step1"


# ---------------------------------------------------------------------------
# 7. Orchestrator retry_fn path
# ---------------------------------------------------------------------------


class TestOrchestratorRetry:
    def test_retry_fn_succeeds_on_second_attempt(self):
        orch = ValidationOrchestrator(
            retry_policy=RetryPolicy(max_retries=2, retry_delay_seconds=0)
        )
        orch.register_step_validator("step1", SchemaValidator({"required_keys": ["ok"]}))

        call_count = [0]

        def retry_fn():
            call_count[0] += 1
            return {"ok": True}  # succeed on first retry

        initial_result = orch.validate_step("step1", {}, {})  # fails (no "ok")
        assert initial_result.passed is False

        final = orch.handle_failure("step1", initial_result, retry_fn=retry_fn)
        assert final.passed is True
        assert call_count[0] == 1

    def test_retry_exhausted_triggers_escalation(self):
        escalations = []
        orch = ValidationOrchestrator(
            retry_policy=RetryPolicy(
                max_retries=2, retry_delay_seconds=0, escalate_on_exhaustion=True
            ),
            on_escalate=lambda step, res: escalations.append((step, res)),
        )
        orch.register_step_validator("step1", SchemaValidator({"required_keys": ["never"]}))

        def bad_retry_fn():
            return {}  # never has "never" key

        initial = orch.validate_step("step1", {}, {})
        orch.handle_failure("step1", initial, retry_fn=bad_retry_fn)
        assert len(escalations) == 1
        assert escalations[0][0] == "step1"

    def test_retry_exhausted_raises_when_no_escalation(self):
        orch = ValidationOrchestrator(
            retry_policy=RetryPolicy(
                max_retries=1, retry_delay_seconds=0, escalate_on_exhaustion=False
            ),
        )
        orch.register_step_validator("step1", SchemaValidator({"required_keys": ["never"]}))
        initial = orch.validate_step("step1", {}, {})
        with pytest.raises(ValidationError):
            orch.handle_failure("step1", initial, retry_fn=lambda: {})


# ---------------------------------------------------------------------------
# 8. Escalation callback
# ---------------------------------------------------------------------------


class TestEscalation:
    def test_custom_escalation_receives_result(self):
        received = []

        def my_escalate(step, result):
            received.append(result)

        orch = ValidationOrchestrator(
            retry_policy=RetryPolicy(max_retries=1, retry_delay_seconds=0),
            on_escalate=my_escalate,
        )
        orch.register_step_validator("s", SchemaValidator({"required_keys": ["x"]}))
        initial = orch.validate_step("s", {}, {})
        orch.handle_failure("s", initial, retry_fn=lambda: {})
        assert len(received) == 1
        assert isinstance(received[0], ValidationResult)


# ---------------------------------------------------------------------------
# 9. make_validated_step_handler raises ValidationError on exhaustion
# ---------------------------------------------------------------------------


class TestMakeValidatedStepHandler:
    def test_passes_through_on_success(self):
        orch = ValidationOrchestrator()
        orch.register_step_validator("s", SchemaValidator({"required_keys": ["result"]}))
        handler = make_validated_step_handler("s", lambda step, wf: {"result": 42}, orch)
        out = handler(None, None)
        assert out == {"result": 42}

    def test_raises_on_exhaustion(self):
        orch = ValidationOrchestrator(
            retry_policy=RetryPolicy(
                max_retries=1, retry_delay_seconds=0, escalate_on_exhaustion=False
            ),
        )
        orch.register_step_validator("s", SchemaValidator({"required_keys": ["never"]}))
        handler = make_validated_step_handler("s", lambda step, wf: {}, orch)
        with pytest.raises(ValidationError):
            handler(None, None)
