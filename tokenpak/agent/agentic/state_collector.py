# SPDX-License-Identifier: Apache-2.0
"""tokenpak.agent.agentic.state_collector — Structured pre-reasoning fact gathering.

Collects deterministic environment facts before any LLM reasoning begins.
Output is a compact StructuredState object (<500 tokens) fed to workflow routing.

Layer 3 of the deterministic architecture: structured fact collection.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Schema version ────────────────────────────────────────────────────────────

SCHEMA_VERSION = "1.0"
STALE_THRESHOLD_SECONDS = 300  # 5 minutes


# ── StructuredState ───────────────────────────────────────────────────────────


@dataclass
class GitState:
    branch: Optional[str] = None
    uncommitted_count: int = 0
    remote_ahead: int = 0
    remote_behind: int = 0
    last_commit: Optional[str] = None
    available: bool = True
    error: Optional[str] = None


@dataclass
class ServiceState:
    running_processes: List[str] = field(default_factory=list)
    open_ports: List[int] = field(default_factory=list)
    available: bool = True
    error: Optional[str] = None


@dataclass
class EnvState:
    vars: Dict[str, str] = field(default_factory=dict)
    drift_keys: List[str] = field(default_factory=list)
    available: bool = True
    error: Optional[str] = None


@dataclass
class FileState:
    recently_changed: List[str] = field(default_factory=list)
    available: bool = True
    error: Optional[str] = None


@dataclass
class TestState:
    last_run: Optional[str] = None
    total: int = 0
    passed: int = 0
    failed: int = 0
    failing_tests: List[str] = field(default_factory=list)
    available: bool = True
    error: Optional[str] = None


@dataclass
class StructuredState:
    schema_version: str = SCHEMA_VERSION
    collected_at: float = field(default_factory=time.time)
    git: GitState = field(default_factory=GitState)
    services: ServiceState = field(default_factory=ServiceState)
    env: EnvState = field(default_factory=EnvState)
    files: FileState = field(default_factory=FileState)
    tests: TestState = field(default_factory=TestState)
    errors: List[str] = field(default_factory=list)

    def is_stale(self, threshold: float = STALE_THRESHOLD_SECONDS) -> bool:
        """Return True if state is older than threshold seconds."""
        return (time.time() - self.collected_at) > threshold

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, compact: bool = True) -> str:
        d = self.to_dict()
        if compact:
            return json.dumps(d, separators=(",", ":"))
        return json.dumps(d, indent=2)

    def token_estimate(self) -> int:
        """Rough token estimate (~4 chars per token)."""
        return len(self.to_json(compact=True)) // 4

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructuredState":
        return cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            collected_at=data.get("collected_at", time.time()),
            git=GitState(**data.get("git", {})),
            services=ServiceState(**data.get("services", {})),
            env=EnvState(**data.get("env", {})),
            files=FileState(**data.get("files", {})),
            tests=TestState(**data.get("tests", {})),
            errors=data.get("errors", []),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(cmd: List[str], cwd: Optional[str] = None, timeout: int = 10) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    except Exception as exc:  # noqa: BLE001
        return -1, "", str(exc)


# ── StateCollector ────────────────────────────────────────────────────────────


class StateCollector:
    """
    Collects structured environment facts before LLM reasoning.

    Usage::

        collector = StateCollector(cwd="/path/to/project")
        state = collector.collect_all()
        print(state.to_json())
    """

    # Known-good env keys to watch for drift
    WATCHED_ENV_KEYS: List[str] = [
        "PATH",
        "VIRTUAL_ENV",
        "CONDA_DEFAULT_ENV",
        "NODE_ENV",
        "TOKENPAK_ENV",
        "HOME",
        "USER",
    ]

    # Services/processes of interest
    WATCHED_PROCESSES: List[str] = [
        "tokenpak",
        "uvicorn",
        "gunicorn",
        "nginx",
        "redis",
        "postgres",
        "mysql",
    ]

    # Typical ports to probe
    WATCHED_PORTS: List[int] = [8000, 8080, 5432, 6379, 3306, 3000]

    def __init__(
        self,
        cwd: Optional[str] = None,
        known_good_env: Optional[Dict[str, str]] = None,
        pytest_results_path: Optional[str] = None,
    ):
        self.cwd = cwd or os.getcwd()
        self.known_good_env: Dict[str, str] = known_good_env or {}
        self.pytest_results_path = pytest_results_path

    # ── Individual collectors ─────────────────────────────────────────────────

    def collect_git_state(self) -> GitState:
        """Collect git branch, uncommitted files, and remote diff."""
        state = GitState()

        # Check git available
        rc, _, _ = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=self.cwd)
        if rc != 0:
            state.available = False
            state.error = "not a git repository"
            return state

        # Branch
        rc, branch, err = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.cwd)
        if rc == 0:
            state.branch = branch
        else:
            state.error = err

        # Uncommitted files
        rc, out, _ = _run(["git", "status", "--porcelain"], cwd=self.cwd)
        if rc == 0:
            state.uncommitted_count = len([l for l in out.splitlines() if l.strip()])

        # Last commit message (short)
        rc, out, _ = _run(
            ["git", "log", "-1", "--pretty=format:%s (%ar)"], cwd=self.cwd
        )
        if rc == 0:
            state.last_commit = out[:120]  # cap length

        # Remote diff — fetch first (quiet)
        _run(["git", "fetch", "--quiet"], cwd=self.cwd, timeout=15)
        rc, out, _ = _run(
            ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
            cwd=self.cwd,
        )
        if rc == 0:
            parts = out.split()
            if len(parts) == 2:
                state.remote_ahead = int(parts[0])
                state.remote_behind = int(parts[1])

        return state

    def collect_service_state(self) -> ServiceState:
        """Collect running processes and open ports of interest."""
        state = ServiceState()

        # Running processes
        rc, out, err = _run(["ps", "aux"])
        if rc != 0:
            state.available = False
            state.error = err
            return state

        ps_lines = out.lower()
        for proc in self.WATCHED_PROCESSES:
            if proc in ps_lines:
                state.running_processes.append(proc)

        # Open ports via ss (preferred) or netstat fallback
        rc, out, _ = _run(["ss", "-tlnp"])
        if rc != 0:
            rc, out, _ = _run(["netstat", "-tlnp"])

        if rc == 0:
            for port in self.WATCHED_PORTS:
                if f":{port}" in out or f" {port} " in out:
                    state.open_ports.append(port)

        return state

    def collect_env_state(self) -> EnvState:
        """Collect relevant env vars and detect drift from known-good baseline."""
        state = EnvState()

        try:
            for key in self.WATCHED_ENV_KEYS:
                val = os.environ.get(key)
                if val is not None:
                    state.vars[key] = val

            # Drift detection
            for key, expected in self.known_good_env.items():
                current = os.environ.get(key)
                if current != expected:
                    state.drift_keys.append(key)

        except Exception as exc:  # noqa: BLE001
            state.available = False
            state.error = str(exc)

        return state

    def collect_file_state(self) -> FileState:
        """Collect recently changed files (last 10 min) in cwd."""
        state = FileState()

        rc, out, err = _run(
            ["find", self.cwd, "-maxdepth", "4", "-newer", "/tmp", "-type", "f",
             "!", "-path", "*/.git/*", "!", "-path", "*/__pycache__/*",
             "!", "-path", "*/.mypy_cache/*"],
        )
        if rc != 0:
            # Fallback: files modified in last 10 minutes
            rc, out, err = _run(
                ["find", self.cwd, "-maxdepth", "4", "-mmin", "-10", "-type", "f",
                 "!", "-path", "*/.git/*"],
            )

        if rc == 0:
            files = [f.strip() for f in out.splitlines() if f.strip()]
            # Make paths relative
            cwd_path = Path(self.cwd)
            rel_files = []
            for f in files[:50]:  # cap at 50
                try:
                    rel_files.append(str(Path(f).relative_to(cwd_path)))
                except ValueError:
                    rel_files.append(f)
            state.recently_changed = rel_files
        else:
            state.available = False
            state.error = err

        return state

    def collect_test_state(self) -> TestState:
        """Collect last pytest results from .pytest_cache or results file."""
        state = TestState()

        # Try reading from lastfailed cache
        cache_path = Path(self.cwd) / ".pytest_cache" / "v" / "cache" / "lastfailed"
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text())
                state.failing_tests = list(data.keys())
                state.failed = len(state.failing_tests)
                state.available = True
                state.last_run = "from .pytest_cache"
            except Exception as exc:  # noqa: BLE001
                state.error = f"cache parse error: {exc}"

        # Try explicit results file
        if self.pytest_results_path:
            results_path = Path(self.pytest_results_path)
            if results_path.exists():
                try:
                    data = json.loads(results_path.read_text())
                    state.total = data.get("total", 0)
                    state.passed = data.get("passed", 0)
                    state.failed = data.get("failed", 0)
                    state.failing_tests = data.get("failing_tests", [])
                    state.last_run = data.get("timestamp", "unknown")
                    state.available = True
                except Exception as exc:  # noqa: BLE001
                    state.error = f"results parse error: {exc}"
        else:
            # No dedicated results file — mark as unavailable but not an error
            if not cache_path.exists():
                state.available = False
                state.error = "no test results found"

        return state

    # ── Combined collector ────────────────────────────────────────────────────

    def collect_all(self) -> StructuredState:
        """Collect all subsystem states into a single compact StructuredState."""
        errors: List[str] = []

        git = self.collect_git_state()
        if git.error:
            errors.append(f"git: {git.error}")

        services = self.collect_service_state()
        if services.error:
            errors.append(f"services: {services.error}")

        env = self.collect_env_state()
        if env.error:
            errors.append(f"env: {env.error}")

        files = self.collect_file_state()
        if files.error:
            errors.append(f"files: {files.error}")

        tests = self.collect_test_state()
        if tests.error:
            errors.append(f"tests: {tests.error}")

        return StructuredState(
            schema_version=SCHEMA_VERSION,
            collected_at=time.time(),
            git=git,
            services=services,
            env=env,
            files=files,
            tests=tests,
            errors=errors,
        )
