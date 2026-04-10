"""tokenpak.agent.agentic.validation_framework — Post-Action Validation Framework.

Pluggable validation layer (Layer 7 of the deterministic architecture).
Validators confirm that actions actually produced their intended effects.
Failed validations trigger automatic retry or escalation via RetryEngine.

Usage
-----
    from tokenpak.agent.agentic.validation_framework import (
        ValidationOrchestrator, ServiceHealthValidator, TestSuiteValidator,
        FileStateValidator, SchemaValidator,
    )

    orchestrator = ValidationOrchestrator(max_retries=2)
    orchestrator.register_step_validator("deploy", ServiceHealthValidator(url="http://localhost:8080/health"))

    result = orchestrator.validate_step("deploy", action_result={"status": "ok"}, expected={"healthy": True})
    if not result.passed:
        orchestrator.handle_failure("deploy", result)  # triggers retry / escalation
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core data classes
# ---------------------------------------------------------------------------


@dataclass
class ValidationCheck:
    """Single named check within a validation run."""

    name: str
    passed: bool
    message: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    """Aggregate result from a PostActionValidator.validate() call."""

    passed: bool
    checks: List[ValidationCheck]
    confidence: float  # 0.0–1.0
    evidence: Dict[str, Any]
    validator_name: str = ""
    duration_seconds: float = 0.0

    # Human-readable summary
    def summary(self) -> str:
        total = len(self.checks)
        ok = sum(1 for c in self.checks if c.passed)
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.validator_name}: {ok}/{total} checks (confidence={self.confidence:.2f})"

    def failed_checks(self) -> List[ValidationCheck]:
        return [c for c in self.checks if not c.passed]


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class PostActionValidator(ABC):
    """Base class for all post-action validators."""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def validate(self, action_result: dict, expected: dict) -> ValidationResult:
        """Run validation checks and return a ValidationResult."""
        ...

    def _make_result(
        self,
        checks: List[ValidationCheck],
        evidence: Dict[str, Any],
        duration: float = 0.0,
    ) -> ValidationResult:
        passed = all(c.passed for c in checks)
        total = len(checks)
        confidence = (sum(1 for c in checks if c.passed) / total) if total else 0.0
        return ValidationResult(
            passed=passed,
            checks=checks,
            confidence=confidence,
            evidence=evidence,
            validator_name=self.name,
            duration_seconds=duration,
        )


# ---------------------------------------------------------------------------
# Built-in Validators
# ---------------------------------------------------------------------------


class ServiceHealthValidator(PostActionValidator):
    """Validate that a service is reachable and healthy.

    Checks HTTP health endpoint and/or process presence.

    Args:
        url:         HTTP(S) URL to poll (e.g. ``http://localhost:8080/health``).
                     If *None*, the HTTP check is skipped.
        process_name: Substring to match in running process list via ``pgrep``.
                      If *None*, the process check is skipped.
        timeout:     HTTP request timeout in seconds (default 5).
        expected_status: Expected HTTP status code (default 200).
    """

    def __init__(
        self,
        url: Optional[str] = None,
        process_name: Optional[str] = None,
        timeout: int = 5,
        expected_status: int = 200,
    ):
        self.url = url
        self.process_name = process_name
        self.timeout = timeout
        self.expected_status = expected_status

    def validate(self, action_result: dict, expected: dict) -> ValidationResult:
        start = time.monotonic()
        checks: List[ValidationCheck] = []
        evidence: Dict[str, Any] = {}

        # -- HTTP health check --
        if self.url:
            try:
                req = urllib.request.Request(self.url)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    code = resp.getcode()
                    body = resp.read(512).decode("utf-8", errors="replace")
                evidence["http_status"] = code
                evidence["http_body_snippet"] = body[:200]
                checks.append(
                    ValidationCheck(
                        name="http_health",
                        passed=(code == self.expected_status),
                        message=f"HTTP {code} (expected {self.expected_status})",
                        evidence={"status_code": code},
                    )
                )
            except (urllib.error.URLError, OSError) as exc:
                evidence["http_error"] = str(exc)
                checks.append(
                    ValidationCheck(
                        name="http_health",
                        passed=False,
                        message=f"HTTP request failed: {exc}",
                        evidence={"error": str(exc)},
                    )
                )

        # -- Process check --
        if self.process_name:
            try:
                result = subprocess.run(
                    ["pgrep", "-f", self.process_name], capture_output=True, text=True
                )
                running = result.returncode == 0
                pids = result.stdout.strip().splitlines()
                evidence["process_pids"] = pids
                checks.append(
                    ValidationCheck(
                        name="process_running",
                        passed=running,
                        message=f"Process '{self.process_name}' {'found' if running else 'not found'} (pids={pids})",
                        evidence={"pids": pids},
                    )
                )
            except FileNotFoundError:
                # pgrep not available — skip gracefully
                checks.append(
                    ValidationCheck(
                        name="process_running",
                        passed=False,
                        message="pgrep not available",
                    )
                )

        if not checks:
            checks.append(
                ValidationCheck(
                    name="no_checks_configured",
                    passed=False,
                    message="ServiceHealthValidator: no url or process_name configured",
                )
            )

        return self._make_result(checks, evidence, time.monotonic() - start)


class TestSuiteValidator(PostActionValidator):
    """Validate by running a test suite and comparing pass/fail counts.

    Args:
        command:      Shell command to run tests (e.g. ``["pytest", "tests/"]``).
        min_pass_pct: Minimum percentage of tests that must pass (0–100, default 100).
        cwd:          Working directory for the test command.
        timeout:      Max seconds to wait for tests (default 120).
    """

    def __init__(
        self,
        command: List[str],
        min_pass_pct: float = 100.0,
        cwd: Optional[str] = None,
        timeout: int = 120,
    ):
        self.command = command
        self.min_pass_pct = min_pass_pct
        self.cwd = cwd
        self.timeout = timeout

    def validate(self, action_result: dict, expected: dict) -> ValidationResult:
        start = time.monotonic()
        checks: List[ValidationCheck] = []
        evidence: Dict[str, Any] = {}

        try:
            proc = subprocess.run(
                self.command,
                capture_output=True,
                text=True,
                cwd=self.cwd,
                timeout=self.timeout,
            )
            stdout = proc.stdout
            stderr = proc.stderr
            returncode = proc.returncode
            evidence["returncode"] = returncode
            evidence["stdout_tail"] = stdout[-1000:]
            evidence["stderr_tail"] = stderr[-500:]

            # Check exit code
            exit_ok = returncode == 0
            checks.append(
                ValidationCheck(
                    name="test_exit_code",
                    passed=exit_ok,
                    message=f"Test suite exited with code {returncode}",
                    evidence={"returncode": returncode},
                )
            )

            # Try to parse pytest-style summary: "X passed, Y failed"
            passed_count: Optional[int] = None
            failed_count: Optional[int] = None
            for line in (stdout + stderr).splitlines():
                m = re.search(r"(\d+) passed", line)
                if m:
                    passed_count = int(m.group(1))
                m = re.search(r"(\d+) failed", line)
                if m:
                    failed_count = int(m.group(1))

            if passed_count is not None:
                total = (passed_count or 0) + (failed_count or 0)
                pct = (passed_count / total * 100) if total else 100.0
                evidence["passed"] = passed_count
                evidence["failed"] = failed_count
                evidence["pass_pct"] = round(pct, 1)
                checks.append(
                    ValidationCheck(
                        name="pass_rate",
                        passed=(pct >= self.min_pass_pct),
                        message=f"{passed_count}/{total} tests passed ({pct:.1f}% >= {self.min_pass_pct}% required)",
                        evidence={"pass_pct": pct, "required": self.min_pass_pct},
                    )
                )

        except subprocess.TimeoutExpired:
            checks.append(
                ValidationCheck(
                    name="test_timeout",
                    passed=False,
                    message=f"Test suite timed out after {self.timeout}s",
                )
            )
        except Exception as exc:
            checks.append(
                ValidationCheck(
                    name="test_execution",
                    passed=False,
                    message=f"Failed to run test suite: {exc}",
                    evidence={"error": str(exc)},
                )
            )

        return self._make_result(checks, evidence, time.monotonic() - start)


class FileStateValidator(PostActionValidator):
    """Validate that expected files exist, were modified, or match content patterns.

    Args:
        must_exist:   List of paths that must exist after the action.
        must_not_exist: List of paths that must NOT exist.
        must_be_newer_than: Dict[path, timestamp] — file mtime must be >= timestamp.
        content_patterns: Dict[path, regex] — file must contain a match for regex.
    """

    def __init__(
        self,
        must_exist: Optional[List[str]] = None,
        must_not_exist: Optional[List[str]] = None,
        must_be_newer_than: Optional[Dict[str, float]] = None,
        content_patterns: Optional[Dict[str, str]] = None,
    ):
        self.must_exist = must_exist or []
        self.must_not_exist = must_not_exist or []
        self.must_be_newer_than = must_be_newer_than or {}
        self.content_patterns = content_patterns or {}

    def validate(self, action_result: dict, expected: dict) -> ValidationResult:
        start = time.monotonic()
        checks: List[ValidationCheck] = []
        evidence: Dict[str, Any] = {}

        for path_str in self.must_exist:
            p = Path(path_str).expanduser()
            exists = p.exists()
            evidence[f"exists:{path_str}"] = exists
            checks.append(
                ValidationCheck(
                    name=f"exists:{p.name}",
                    passed=exists,
                    message=f"{'Found' if exists else 'Missing'}: {path_str}",
                    evidence={"path": str(p), "exists": exists},
                )
            )

        for path_str in self.must_not_exist:
            p = Path(path_str).expanduser()
            absent = not p.exists()
            evidence[f"absent:{path_str}"] = absent
            checks.append(
                ValidationCheck(
                    name=f"absent:{p.name}",
                    passed=absent,
                    message=f"{'Absent (ok)' if absent else 'Unexpectedly present'}: {path_str}",
                    evidence={"path": str(p), "absent": absent},
                )
            )

        for path_str, min_ts in self.must_be_newer_than.items():
            p = Path(path_str).expanduser()
            if p.exists():
                mtime = p.stat().st_mtime
                newer = mtime >= min_ts
                evidence[f"mtime:{path_str}"] = mtime
                checks.append(
                    ValidationCheck(
                        name=f"newer:{p.name}",
                        passed=newer,
                        message=f"{path_str} mtime={mtime:.0f} {'≥' if newer else '<'} {min_ts:.0f}",
                        evidence={"mtime": mtime, "min_ts": min_ts},
                    )
                )
            else:
                checks.append(
                    ValidationCheck(
                        name=f"newer:{p.name}",
                        passed=False,
                        message=f"{path_str} does not exist (can't check mtime)",
                    )
                )

        for path_str, pattern in self.content_patterns.items():
            p = Path(path_str).expanduser()
            if p.exists():
                try:
                    text = p.read_text(errors="replace")
                    match = bool(re.search(pattern, text))
                    checks.append(
                        ValidationCheck(
                            name=f"content:{p.name}",
                            passed=match,
                            message=f"Pattern {'found' if match else 'NOT found'} in {path_str}: {pattern!r}",
                            evidence={"pattern": pattern, "match": match},
                        )
                    )
                except OSError as exc:
                    checks.append(
                        ValidationCheck(
                            name=f"content:{p.name}",
                            passed=False,
                            message=f"Could not read {path_str}: {exc}",
                        )
                    )
            else:
                checks.append(
                    ValidationCheck(
                        name=f"content:{p.name}",
                        passed=False,
                        message=f"{path_str} does not exist (can't check content)",
                    )
                )

        if not checks:
            checks.append(
                ValidationCheck(
                    name="no_checks_configured",
                    passed=True,
                    message="FileStateValidator: no checks configured (vacuously passing)",
                )
            )

        return self._make_result(checks, evidence, time.monotonic() - start)


class SchemaValidator(PostActionValidator):
    """Validate that action_result matches an expected JSON schema (structural subset).

    This is a lightweight structural validator — it does NOT require jsonschema.
    It checks required keys, type hints, and optional allowed values.

    Args:
        schema: Dict describing expected structure::

            {
                "required_keys": ["status", "id"],
                "types": {"status": str, "id": int},
                "allowed_values": {"status": ["ok", "pending"]},
                "disallowed_keys": ["error"],
            }
    """

    def __init__(self, schema: Dict[str, Any]):
        self.schema = schema

    def validate(self, action_result: dict, expected: dict) -> ValidationResult:
        start = time.monotonic()
        checks: List[ValidationCheck] = []
        evidence: Dict[str, Any] = {"action_result_keys": list(action_result.keys())}

        # Required keys
        for key in self.schema.get("required_keys", []):
            present = key in action_result
            checks.append(
                ValidationCheck(
                    name=f"required_key:{key}",
                    passed=present,
                    message=f"Key '{key}' {'present' if present else 'MISSING'} in action_result",
                    evidence={"key": key, "present": present},
                )
            )

        # Disallowed keys
        for key in self.schema.get("disallowed_keys", []):
            absent = key not in action_result
            checks.append(
                ValidationCheck(
                    name=f"disallowed_key:{key}",
                    passed=absent,
                    message=f"Key '{key}' {'absent (ok)' if absent else 'unexpectedly present'}",
                    evidence={"key": key, "absent": absent},
                )
            )

        # Type checks
        for key, expected_type in self.schema.get("types", {}).items():
            if key not in action_result:
                continue  # covered by required_keys check
            val = action_result[key]
            type_ok = isinstance(val, expected_type)
            checks.append(
                ValidationCheck(
                    name=f"type:{key}",
                    passed=type_ok,
                    message=f"'{key}' type={type(val).__name__} {'==' if type_ok else '!='} {expected_type.__name__}",
                    evidence={
                        "key": key,
                        "actual_type": type(val).__name__,
                        "expected_type": expected_type.__name__,
                    },
                )
            )

        # Allowed values
        for key, allowed in self.schema.get("allowed_values", {}).items():
            if key not in action_result:
                continue
            val = action_result[key]
            value_ok = val in allowed
            checks.append(
                ValidationCheck(
                    name=f"allowed_value:{key}",
                    passed=value_ok,
                    message=f"'{key}'={val!r} {'∈' if value_ok else '∉'} allowed={allowed}",
                    evidence={"key": key, "value": val, "allowed": allowed},
                )
            )

        if not checks:
            checks.append(
                ValidationCheck(
                    name="empty_schema",
                    passed=True,
                    message="SchemaValidator: empty schema (vacuously passing)",
                )
            )

        return self._make_result(checks, evidence, time.monotonic() - start)


# ---------------------------------------------------------------------------
# Orchestrator / Workflow Integration
# ---------------------------------------------------------------------------


class ValidationError(Exception):
    """Raised when validation fails and retry is exhausted."""


@dataclass
class RetryPolicy:
    max_retries: int = 2
    retry_delay_seconds: float = 1.0
    escalate_on_exhaustion: bool = True


class ValidationOrchestrator:
    """Register validators per workflow step and run them after each step completes.

    Example::

        orch = ValidationOrchestrator()
        orch.register_step_validator("build", FileStateValidator(must_exist=["dist/app.whl"]))
        orch.register_step_validator("deploy", ServiceHealthValidator(url="http://localhost/health"))

        result = orch.validate_step("deploy", action_result={...}, expected={})
        if not result.passed:
            orch.handle_failure("deploy", result)  # auto-retry or raise

    Args:
        retry_policy: Controls how many retries occur on failure.
        on_escalate:  Optional callback called when retries are exhausted.
                      Signature: ``(step_name: str, result: ValidationResult) -> None``
    """

    def __init__(
        self,
        retry_policy: Optional[RetryPolicy] = None,
        on_escalate: Optional[Callable[[str, ValidationResult], None]] = None,
    ):
        self._validators: Dict[str, List[PostActionValidator]] = {}
        self.retry_policy = retry_policy or RetryPolicy()
        self.on_escalate = on_escalate
        self._history: List[Dict[str, Any]] = []

    def register_step_validator(self, step_name: str, validator: PostActionValidator) -> None:
        """Attach a validator to a workflow step (multiple validators per step allowed)."""
        self._validators.setdefault(step_name, []).append(validator)
        logger.debug("Registered %s for step '%s'", validator.name, step_name)

    def validate_step(
        self, step_name: str, action_result: dict, expected: dict
    ) -> ValidationResult:
        """Run all validators registered for *step_name* and merge results.

        Returns a merged ValidationResult (passes only when ALL validators pass).
        """
        validators = self._validators.get(step_name, [])
        if not validators:
            logger.debug("No validators for step '%s' — skipping", step_name)
            return ValidationResult(
                passed=True,
                checks=[
                    ValidationCheck(
                        name="no_validators", passed=True, message="No validators registered"
                    )
                ],
                confidence=1.0,
                evidence={},
                validator_name="ValidationOrchestrator",
            )

        all_checks: List[ValidationCheck] = []
        all_evidence: Dict[str, Any] = {}
        any_failed = False

        for v in validators:
            try:
                r = v.validate(action_result, expected)
            except Exception as exc:
                logger.exception("Validator %s raised: %s", v.name, exc)
                r = ValidationResult(
                    passed=False,
                    checks=[
                        ValidationCheck(name="validator_error", passed=False, message=str(exc))
                    ],
                    confidence=0.0,
                    evidence={"exception": str(exc)},
                    validator_name=v.name,
                )
            all_checks.extend(r.checks)
            all_evidence[v.name] = r.evidence
            if not r.passed:
                any_failed = True
            logger.info(r.summary())

        total = len(all_checks)
        ok = sum(1 for c in all_checks if c.passed)
        merged = ValidationResult(
            passed=not any_failed,
            checks=all_checks,
            confidence=ok / total if total else 1.0,
            evidence=all_evidence,
            validator_name="ValidationOrchestrator",
        )
        self._history.append(
            {
                "step": step_name,
                "passed": merged.passed,
                "timestamp": time.time(),
            }
        )
        return merged

    def handle_failure(
        self,
        step_name: str,
        result: ValidationResult,
        retry_fn: Optional[Callable[[], dict]] = None,
        expected: Optional[dict] = None,
    ) -> ValidationResult:
        """Handle a failed validation with automatic retry and optional escalation.

        Args:
            step_name:  Workflow step name.
            result:     The initial (failed) ValidationResult.
            retry_fn:   Optional callable that re-executes the action and returns
                        a new action_result dict. If *None*, only re-validates the
                        original result (useful for transient flakiness checks).
            expected:   Expected dict forwarded to validators on retry.

        Returns:
            The final ValidationResult (may still be failing if retries exhausted).

        Raises:
            ValidationError: if retries exhausted and escalation is disabled.
        """
        policy = self.retry_policy
        current_result = result
        expected = expected or {}

        for attempt in range(1, policy.max_retries + 1):
            logger.warning(
                "Validation failed for step '%s' (attempt %d/%d). Retrying in %.1fs…",
                step_name,
                attempt,
                policy.max_retries,
                policy.retry_delay_seconds,
            )
            time.sleep(policy.retry_delay_seconds)

            if retry_fn is not None:
                try:
                    new_action_result = retry_fn()
                except Exception as exc:
                    logger.error("retry_fn raised on attempt %d: %s", attempt, exc)
                    continue

                current_result = self.validate_step(step_name, new_action_result, expected)
            else:
                # Re-validate in case state changed externally
                current_result = self.validate_step(step_name, {}, expected)

            if current_result.passed:
                logger.info("Validation passed on retry %d for step '%s'", attempt, step_name)
                return current_result

        # Retries exhausted
        logger.error(
            "Validation exhausted after %d retries for step '%s'", policy.max_retries, step_name
        )
        if policy.escalate_on_exhaustion:
            if self.on_escalate:
                self.on_escalate(step_name, current_result)
            else:
                # Default escalation: log a structured alert
                alert = {
                    "event": "validation_exhausted",
                    "step": step_name,
                    "failed_checks": [
                        {"name": c.name, "message": c.message}
                        for c in current_result.failed_checks()
                    ],
                    "confidence": current_result.confidence,
                    "timestamp": time.time(),
                }
                logger.critical("ESCALATION: %s", json.dumps(alert))
        else:
            raise ValidationError(
                f"Validation for step '{step_name}' failed after {policy.max_retries} retries. "
                f"Failed checks: {[c.name for c in current_result.failed_checks()]}"
            )

        return current_result

    def validation_history(self) -> List[Dict[str, Any]]:
        """Return list of past validation events (step, passed, timestamp)."""
        return list(self._history)


# ---------------------------------------------------------------------------
# Workflow integration helper
# ---------------------------------------------------------------------------


def make_validated_step_handler(
    step_name: str,
    handler: Callable,
    orchestrator: ValidationOrchestrator,
    expected: Optional[dict] = None,
    retry_fn: Optional[Callable] = None,
) -> Callable:
    """Wrap a workflow step handler to auto-validate after execution.

    Returns a new handler that runs the original, validates the output,
    and calls handle_failure if validation fails.

    Example::

        handlers = {
            "deploy": make_validated_step_handler(
                "deploy",
                my_deploy_fn,
                orchestrator,
                expected={"healthy": True},
            )
        }
        wf_manager.run(wf_id, handlers)
    """

    def _wrapped(step, wf):
        action_result = handler(step, wf)
        if action_result is None:
            action_result = {}
        result = orchestrator.validate_step(step_name, action_result, expected or {})
        if not result.passed:
            result = orchestrator.handle_failure(
                step_name, result, retry_fn=retry_fn, expected=expected or {}
            )
            if not result.passed:
                raise ValidationError(
                    f"Step '{step_name}' failed validation: "
                    + ", ".join(c.name for c in result.failed_checks())
                )
        return action_result

    return _wrapped


__all__ = [
    "PostActionValidator",
    "ValidationResult",
    "ValidationCheck",
    "ValidationError",
    "RetryPolicy",
    "ValidationOrchestrator",
    "ServiceHealthValidator",
    "TestSuiteValidator",
    "FileStateValidator",
    "SchemaValidator",
    "make_validated_step_handler",
]
