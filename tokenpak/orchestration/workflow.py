"""tokenpak.agent.agentic.workflow — Workflow state machine with crash recovery.

Workflows are ordered step sequences with optional inter-step dependencies.
State is persisted to disk after each mutation so crashes are recoverable.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_WORKFLOW_DIR = Path(os.path.expanduser("~/.tokenpak/workflows"))


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


@dataclass
class WorkflowStep:
    name: str
    description: str = ""
    depends_on: List[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    output: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["status"] = StepStatus(d.get("status", "pending"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def duration_seconds(self):
        if self.started_at and self.completed_at:
            return round(self.completed_at - self.started_at, 3)

    def is_done(self):
        return self.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)

    def is_terminal(self):
        return self.status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED)


@dataclass
class WorkflowRecord:
    id: str
    name: str
    template: Optional[str]
    steps: List[WorkflowStep]
    status: WorkflowStatus
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "template": self.template,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            id=d["id"],
            name=d["name"],
            template=d.get("template"),
            steps=[WorkflowStep.from_dict(s) for s in d.get("steps", [])],
            status=WorkflowStatus(d.get("status", "pending")),
            created_at=d.get("created_at", time.time()),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            metadata=d.get("metadata", {}),
            tags=d.get("tags", []),
        )

    def completion_pct(self):
        if not self.steps:
            return 0.0
        return round(sum(1 for s in self.steps if s.is_done()) / len(self.steps) * 100, 1)

    def current_step(self):
        for s in self.steps:
            if s.status == StepStatus.RUNNING:
                return s

    def next_pending_step(self):
        done = {s.name for s in self.steps if s.is_done()}
        for s in self.steps:
            if s.status == StepStatus.PENDING and all(d in done for d in s.depends_on):
                return s

    def duration_seconds(self):
        if self.started_at and self.completed_at:
            return round(self.completed_at - self.started_at, 3)


WORKFLOW_TEMPLATES: Dict[str, List[dict]] = {
    "deploy": [
        {"name": "lint", "description": "Run linter checks", "depends_on": []},
        {"name": "test", "description": "Run test suite", "depends_on": ["lint"]},
        {"name": "build", "description": "Build artefacts", "depends_on": ["test"]},
        {"name": "staging-deploy", "description": "Deploy to staging", "depends_on": ["build"]},
        {
            "name": "smoke-test",
            "description": "Run smoke tests on staging",
            "depends_on": ["staging-deploy"],
        },
        {
            "name": "prod-deploy",
            "description": "Deploy to production",
            "depends_on": ["smoke-test"],
        },
    ],
    "refactor": [
        {
            "name": "baseline-tests",
            "description": "Capture baseline test results",
            "depends_on": [],
        },
        {
            "name": "static-analysis",
            "description": "Run static analysis",
            "depends_on": ["baseline-tests"],
        },
        {
            "name": "refactor-code",
            "description": "Apply refactoring changes",
            "depends_on": ["static-analysis"],
        },
        {
            "name": "regression-tests",
            "description": "Run full regression suite",
            "depends_on": ["refactor-code"],
        },
        {
            "name": "review",
            "description": "Human review before merge",
            "depends_on": ["regression-tests"],
        },
    ],
    "data-pipeline": [
        {"name": "ingest", "description": "Pull raw data from source", "depends_on": []},
        {
            "name": "validate",
            "description": "Validate schema + integrity",
            "depends_on": ["ingest"],
        },
        {"name": "transform", "description": "Apply transformations", "depends_on": ["validate"]},
        {"name": "load", "description": "Load into target store", "depends_on": ["transform"]},
        {"name": "verify", "description": "Verify loaded data", "depends_on": ["load"]},
    ],
    "release": [
        {"name": "changelog", "description": "Update CHANGELOG.md", "depends_on": []},
        {
            "name": "version-bump",
            "description": "Bump version numbers",
            "depends_on": ["changelog"],
        },
        {"name": "build", "description": "Build release artefacts", "depends_on": ["version-bump"]},
        {"name": "publish", "description": "Publish to registry", "depends_on": ["build"]},
        {"name": "tag", "description": "Create git tag", "depends_on": ["publish"]},
        {"name": "announce", "description": "Post release announcement", "depends_on": ["tag"]},
    ],
    "proxy": [
        {
            "name": "vault_inject",
            "description": "Search vault index and inject relevant context",
            "depends_on": [],
        },
        {
            "name": "compress",
            "description": "Apply style-contract-aware compaction to request body",
            "depends_on": ["vault_inject"],
        },
        {
            "name": "forward",
            "description": "Forward request to upstream API",
            "depends_on": ["compress"],
        },
        {
            "name": "log_metrics",
            "description": "Log cost/token metrics to SQLite monitor",
            "depends_on": ["forward"],
        },
    ],
}


def list_templates():
    return sorted(WORKFLOW_TEMPLATES.keys())


def template_steps(name):
    if name not in WORKFLOW_TEMPLATES:
        raise ValueError(f"Unknown template '{name}'. Available: {list_templates()}")
    return [WorkflowStep(**s) for s in WORKFLOW_TEMPLATES[name]]


class WorkflowManager:
    def __init__(self, workflow_dir=DEFAULT_WORKFLOW_DIR):
        self._dir = Path(workflow_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, wf_id):
        return self._dir / f"{wf_id}.json"

    def _save(self, wf):
        path = self._path_for(wf.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(wf.to_dict(), indent=2))
        tmp.replace(path)

    def _load_file(self, path):
        try:
            return WorkflowRecord.from_dict(json.loads(path.read_text()))
        except Exception:
            return None

    def load(self, wf_id):
        return self._load_file(self._path_for(wf_id))

    def list_workflows(self, status=None, tags=None, limit=None):
        records = []
        for path in sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            wf = self._load_file(path)
            if wf is None:
                continue
            if status and wf.status != status:
                continue
            if tags and not all(t in wf.tags for t in tags):
                continue
            records.append(wf)
        return records[:limit] if limit else records

    def incomplete_workflows(self):
        return [
            w
            for w in self.list_workflows()
            if w.status in (WorkflowStatus.RUNNING, WorkflowStatus.PENDING)
        ]

    def create(self, name, steps=None, template=None, metadata=None, tags=None, wf_id=None):
        if template and not steps:
            steps = template_steps(template)
        if not steps:
            raise ValueError("Must provide steps or a template.")
        wf = WorkflowRecord(
            id=wf_id or str(uuid.uuid4()),
            name=name,
            template=template,
            steps=list(steps),
            status=WorkflowStatus.PENDING,
            created_at=time.time(),
            metadata=metadata or {},
            tags=tags or [],
        )
        self._save(wf)
        return wf

    def start(self, wf_id):
        wf = self._require(wf_id)
        if wf.status not in (WorkflowStatus.PENDING, WorkflowStatus.PAUSED):
            raise ValueError(f"Cannot start workflow in status '{wf.status.value}'.")
        wf.status = WorkflowStatus.RUNNING
        if wf.started_at is None:
            wf.started_at = time.time()
        self._save(wf)
        return wf

    def begin_step(self, wf_id, step_name):
        wf = self._require(wf_id)
        step = self._get_step(wf, step_name)
        if step.status != StepStatus.PENDING:
            raise ValueError(f"Step '{step_name}' is already {step.status.value}.")
        step.status = StepStatus.RUNNING
        step.started_at = time.time()
        self._save(wf)
        return wf

    def complete_step(self, wf_id, step_name, output=None):
        wf = self._require(wf_id)
        step = self._get_step(wf, step_name)
        step.status = StepStatus.COMPLETED
        step.completed_at = time.time()
        step.output = output
        step.error = None
        self._maybe_close(wf)
        self._save(wf)
        return wf

    def fail_step(self, wf_id, step_name, error, skip_dependents=True):
        wf = self._require(wf_id)
        step = self._get_step(wf, step_name)
        step.status = StepStatus.FAILED
        step.completed_at = time.time()
        step.error = error
        if skip_dependents:
            failed = {step_name}
            for s in wf.steps:
                if s.status == StepStatus.PENDING and any(d in failed for d in s.depends_on):
                    s.status = StepStatus.SKIPPED
                    failed.add(s.name)
        wf.status = WorkflowStatus.FAILED
        wf.completed_at = time.time()
        self._save(wf)
        return wf

    def skip_step(self, wf_id, step_name, reason=""):
        wf = self._require(wf_id)
        step = self._get_step(wf, step_name)
        step.status = StepStatus.SKIPPED
        step.completed_at = time.time()
        step.metadata["skip_reason"] = reason
        self._maybe_close(wf)
        self._save(wf)
        return wf

    def cancel(self, wf_id):
        wf = self._require(wf_id)
        for step in wf.steps:
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                step.status = StepStatus.SKIPPED
        wf.status = WorkflowStatus.CANCELLED
        wf.completed_at = time.time()
        self._save(wf)
        return wf

    def pause(self, wf_id):
        wf = self._require(wf_id)
        for step in wf.steps:
            if step.status == StepStatus.RUNNING:
                step.status = StepStatus.PENDING
                step.started_at = None
        wf.status = WorkflowStatus.PAUSED
        self._save(wf)
        return wf

    def resume(self, wf_id):
        wf = self._require(wf_id)
        if wf.status == WorkflowStatus.COMPLETED:
            raise ValueError("Workflow already completed.")
        if wf.status == WorkflowStatus.CANCELLED:
            raise ValueError("Cannot resume a cancelled workflow.")
        for step in wf.steps:
            if step.status == StepStatus.RUNNING:
                step.status = StepStatus.PENDING
                step.started_at = None
        wf.status = WorkflowStatus.RUNNING
        if wf.started_at is None:
            wf.started_at = time.time()
        self._save(wf)
        return wf

    def delete(self, wf_id):
        path = self._path_for(wf_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def run(self, wf_id, handlers, on_step_start=None, on_step_done=None):
        loaded = self.load(wf_id)
        wf = self.resume(wf_id) if loaded.status != WorkflowStatus.PENDING else self.start(wf_id)
        while True:
            wf = self.load(wf_id)
            nxt = wf.next_pending_step()
            if nxt is None:
                break
            wf = self.begin_step(wf_id, nxt.name)
            step = self._get_step(wf, nxt.name)
            if on_step_start:
                on_step_start(step, wf)
            handler = handlers.get(nxt.name)
            try:
                output = handler(step, wf) if handler else None
                wf = self.complete_step(wf_id, nxt.name, output=output)
            except Exception as exc:
                wf = self.fail_step(wf_id, nxt.name, error=str(exc))
                if on_step_done:
                    on_step_done(self._get_step(wf, nxt.name), wf)
                break
            if on_step_done:
                on_step_done(self._get_step(wf, nxt.name), wf)
        return self.load(wf_id)

    def history(self, limit=20, name_filter=None):
        all_wf = self.list_workflows(limit=None)
        if name_filter:
            all_wf = [w for w in all_wf if name_filter.lower() in w.name.lower()]
        return all_wf[:limit]

    def _require(self, wf_id):
        wf = self.load(wf_id)
        if wf is None:
            raise KeyError(f"Workflow '{wf_id}' not found.")
        return wf

    @staticmethod
    def _get_step(wf, step_name):
        for s in wf.steps:
            if s.name == step_name:
                return s
        raise KeyError(f"Step '{step_name}' not found.")

    @staticmethod
    def _maybe_close(wf):
        if all(s.is_terminal() for s in wf.steps):
            wf.status = WorkflowStatus.COMPLETED
            wf.completed_at = time.time()


_manager = None


def get_manager(workflow_dir=None):
    global _manager
    if _manager is None:
        _manager = WorkflowManager(workflow_dir or DEFAULT_WORKFLOW_DIR)
    return _manager
