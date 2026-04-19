"""tokenpak.agent.agentic.precondition_gates — Automatic Precondition Gates.

Learns from workflow step failures: when a recurring missing prerequisite is
detected N times, it auto-registers a gate for that step so future runs skip
(or block) the step cleanly rather than failing hard.

Gate Types
----------
- ``env_check``      — verify env vars are set before action
- ``file_exists``    — required files are present
- ``service_running``— dependent service is alive
- ``test_passing``   — baseline tests pass before change
- ``diff_clean``     — no uncommitted git changes in a given path

Persistence
-----------
Gates are stored in ``~/.tokenpak/preconditions.json``.
Failure patterns are stored in ``~/.tokenpak/precondition_failures.jsonl``.

Usage
-----
    from tokenpak.agent.agentic.precondition_gates import PreconditionGates

    gates = PreconditionGates()

    # Before executing a step:
    passed, reason = gates.check("my_step")
    if not passed:
        # skip step — gate blocked it
        ...

    # After a step fails (with precondition hint):
    gates.record_failure("my_step", "env_check", {"vars": ["API_KEY"]})

    # Periodically promote recurring patterns → gates:
    gates.promote_patterns()
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_GATES_PATH = Path.home() / ".tokenpak" / "preconditions.json"
DEFAULT_FAILURES_PATH = Path.home() / ".tokenpak" / "precondition_failures.jsonl"
AUTO_PROMOTE_THRESHOLD = 3          # failures before auto-gate
SUPPORTED_GATE_TYPES = frozenset([
    "env_check",
    "file_exists",
    "service_running",
    "test_passing",
    "diff_clean",
])


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Gate:
    """A single precondition gate for a workflow step."""

    step: str
    gate_type: str
    params: Dict[str, Any] = field(default_factory=dict)
    auto_promoted: bool = False
    promoted_at: Optional[float] = None
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Gate":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class GateResult:
    """Outcome of evaluating a gate."""

    passed: bool
    gate_type: str
    reason: str
    step: str


# ---------------------------------------------------------------------------
# Gate checkers
# ---------------------------------------------------------------------------

def _check_env_check(params: Dict[str, Any]) -> Tuple[bool, str]:
    """Verify all required env vars are set and non-empty."""
    missing = []
    for var in params.get("vars", []):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        return False, f"Missing env vars: {', '.join(missing)}"
    return True, "All env vars present"


def _check_file_exists(params: Dict[str, Any]) -> Tuple[bool, str]:
    """Verify required files are present."""
    missing = []
    for path_str in params.get("paths", []):
        if not Path(os.path.expanduser(path_str)).exists():
            missing.append(path_str)
    if missing:
        return False, f"Missing files: {', '.join(missing)}"
    return True, "All required files present"


def _check_service_running(params: Dict[str, Any]) -> Tuple[bool, str]:
    """Check if a service/process is running via systemctl or pgrep."""
    services = params.get("services", [])
    not_running = []
    for svc in services:
        # Try systemctl first
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", svc],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            # Fallback: pgrep
            pgrep = subprocess.run(
                ["pgrep", "-x", svc],
                capture_output=True,
                timeout=5,
            )
            if pgrep.returncode != 0:
                not_running.append(svc)
    if not_running:
        return False, f"Services not running: {', '.join(not_running)}"
    return True, "All services running"


def _check_test_passing(params: Dict[str, Any]) -> Tuple[bool, str]:
    """Run a baseline test command and verify it passes."""
    cmd = params.get("command", "")
    if not cmd:
        return True, "No test command configured"
    cwd = params.get("cwd", None)
    try:
        result = subprocess.run(
            cmd if isinstance(cmd, list) else cmd.split(),
            capture_output=True,
            text=True,
            timeout=params.get("timeout", 60),
            cwd=cwd,
        )
        if result.returncode != 0:
            snippet = (result.stdout + result.stderr)[-300:]
            return False, f"Tests failed (rc={result.returncode}): {snippet}"
        return True, "Tests passing"
    except subprocess.TimeoutExpired:
        return False, "Test command timed out"
    except FileNotFoundError as exc:
        return False, f"Test command not found: {exc}"


def _check_diff_clean(params: Dict[str, Any]) -> Tuple[bool, str]:
    """Verify no uncommitted git changes in the given path."""
    path = params.get("path", ".")
    path = os.path.expanduser(path)
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=path,
        )
        if result.returncode != 0:
            return False, f"git status failed: {result.stderr.strip()}"
        if result.stdout.strip():
            lines = result.stdout.strip().splitlines()
            return False, f"Uncommitted changes ({len(lines)} files): {lines[0]}"
        return True, "Working tree clean"
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, f"diff_clean check error: {exc}"


_CHECKERS = {
    "env_check": _check_env_check,
    "file_exists": _check_file_exists,
    "service_running": _check_service_running,
    "test_passing": _check_test_passing,
    "diff_clean": _check_diff_clean,
}


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

class PreconditionGates:
    """
    Manage precondition gates for workflow steps.

    Args:
        gates_path:    Path to ``preconditions.json`` (gates store).
        failures_path: Path to ``precondition_failures.jsonl`` (failure log).
        threshold:     How many failures trigger auto-promotion of a gate.
    """

    def __init__(
        self,
        gates_path: Path = DEFAULT_GATES_PATH,
        failures_path: Path = DEFAULT_FAILURES_PATH,
        threshold: int = AUTO_PROMOTE_THRESHOLD,
    ) -> None:
        self.gates_path = Path(gates_path)
        self.failures_path = Path(failures_path)
        self.threshold = threshold
        self._gates: Dict[str, List[Gate]] = {}  # step → [Gate, ...]
        self._load_gates()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_gates(self) -> None:
        if self.gates_path.exists():
            try:
                raw = json.loads(self.gates_path.read_text())
                for step, gate_list in raw.get("gates", {}).items():
                    self._gates[step] = [Gate.from_dict(g) for g in gate_list]
            except (json.JSONDecodeError, OSError, TypeError) as exc:
                logger.warning("Could not load preconditions.json: %s", exc)
                self._gates = {}

    def _save_gates(self) -> None:
        self.gates_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated": time.time(),
            "gates": {
                step: [g.to_dict() for g in gate_list]
                for step, gate_list in self._gates.items()
            },
        }
        self.gates_path.write_text(json.dumps(payload, indent=2))

    def _append_failure(self, record: dict) -> None:
        self.failures_path.parent.mkdir(parents=True, exist_ok=True)
        with self.failures_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")

    def _load_failures(self) -> List[dict]:
        if not self.failures_path.exists():
            return []
        records = []
        with self.failures_path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records

    # ── Public API ────────────────────────────────────────────────────────────

    def add_gate(self, gate: Gate) -> None:
        """Manually register a gate for a step."""
        if gate.gate_type not in SUPPORTED_GATE_TYPES:
            raise ValueError(
                f"Unknown gate type {gate.gate_type!r}. "
                f"Supported: {sorted(SUPPORTED_GATE_TYPES)}"
            )
        self._gates.setdefault(gate.step, []).append(gate)
        self._save_gates()
        logger.info("Gate added: step=%s type=%s", gate.step, gate.gate_type)

    def remove_gate(self, step: str, gate_type: str) -> bool:
        """Remove a gate by step + type. Returns True if removed."""
        gates = self._gates.get(step, [])
        before = len(gates)
        self._gates[step] = [g for g in gates if g.gate_type != gate_type]
        if len(self._gates[step]) < before:
            self._save_gates()
            return True
        return False

    def list_gates(self, step: Optional[str] = None) -> List[Gate]:
        """List all gates, optionally filtered by step."""
        if step:
            return list(self._gates.get(step, []))
        return [g for gates in self._gates.values() for g in gates]

    def check(self, step: str) -> Tuple[bool, str]:
        """
        Evaluate all gates for *step*.

        Returns:
            (True, "All gates passed") if every gate passes, or
            (False, "<reason>") for the first failing gate.

        A failed gate means the step should be *skipped* (not counted as
        a step failure).
        """
        gates = self._gates.get(step, [])
        for gate in gates:
            checker = _CHECKERS.get(gate.gate_type)
            if checker is None:
                logger.warning("No checker for gate type %r — skipping", gate.gate_type)
                continue
            try:
                passed, reason = checker(gate.params)
            except Exception as exc:
                logger.exception("Gate check raised: step=%s type=%s", step, gate.gate_type)
                passed, reason = False, f"Gate check error: {exc}"

            if not passed:
                logger.info(
                    "Gate BLOCKED step=%s gate_type=%s reason=%s",
                    step,
                    gate.gate_type,
                    reason,
                )
                return False, f"[{gate.gate_type}] {reason}"

        return True, "All gates passed"

    def record_failure(
        self,
        step: str,
        gate_type: str,
        params: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record a step failure where a precondition of *gate_type* was missing.

        After ``threshold`` such failures the pattern is auto-promoted into an
        active gate the next time :meth:`promote_patterns` is called (or
        immediately if ``auto_promote=True`` was set).
        """
        if gate_type not in SUPPORTED_GATE_TYPES:
            logger.warning("Unknown gate_type %r — recording anyway", gate_type)
        record = {
            "step": step,
            "gate_type": gate_type,
            "params": params or {},
            "context": context or {},
            "ts": time.time(),
        }
        self._append_failure(record)
        logger.debug("Failure recorded: step=%s gate_type=%s", step, gate_type)

    def promote_patterns(self) -> List[Gate]:
        """
        Scan the failure log for patterns that exceed the threshold and
        auto-promote them to active gates.  Returns newly promoted gates.
        """
        failures = self._load_failures()
        # Count (step, gate_type, params_key) occurrences
        counts: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for rec in failures:
            step = rec.get("step", "")
            gt = rec.get("gate_type", "")
            params = rec.get("params") or {}
            # Use a stable string key for params to deduplicate
            params_key = json.dumps(params, sort_keys=True)
            key = (step, gt, params_key)
            if key not in counts:
                counts[key] = {"count": 0, "params": params}
            counts[key]["count"] += 1

        promoted: List[Gate] = []
        for (step, gate_type, _), info in counts.items():
            if info["count"] < self.threshold:
                continue
            # Skip if gate already exists
            existing = self._gates.get(step, [])
            if any(g.gate_type == gate_type for g in existing):
                continue
            if gate_type not in SUPPORTED_GATE_TYPES:
                continue
            gate = Gate(
                step=step,
                gate_type=gate_type,
                params=info["params"],
                auto_promoted=True,
                promoted_at=time.time(),
                description=(
                    f"Auto-promoted after {info['count']} failures"
                ),
            )
            self._gates.setdefault(step, []).append(gate)
            promoted.append(gate)
            logger.info(
                "Auto-promoted gate: step=%s gate_type=%s (failures=%d)",
                step,
                gate_type,
                info["count"],
            )

        if promoted:
            self._save_gates()

        return promoted

    def gate_summary(self) -> Dict[str, Any]:
        """Return a summary dict suitable for logging/display."""
        failures = self._load_failures()
        return {
            "total_gates": sum(len(v) for v in self._gates.values()),
            "gated_steps": list(self._gates.keys()),
            "total_failures_logged": len(failures),
            "auto_promoted": sum(
                1
                for gates in self._gates.values()
                for g in gates
                if g.auto_promoted
            ),
        }
