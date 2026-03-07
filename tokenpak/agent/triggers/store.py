"""Trigger CRUD — persist triggers to ~/.tokenpak/triggers.yaml."""

from __future__ import annotations

import datetime
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

DEFAULT_CONFIG = Path.home() / ".tokenpak" / "triggers.yaml"


@dataclass
class TriggerLog:
    trigger_id: str
    event: str
    action: str
    fired_at: str
    exit_code: int
    output: str


@dataclass
class Trigger:
    id: str
    event: str  # e.g. "file:changed:*.py", "timer:5m", "cost:daily>10"
    action: str  # tokenpak sub-command or shell script path
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())


class TriggerStore:
    """Load/save triggers from YAML config."""

    def __init__(self, config_path: Path = DEFAULT_CONFIG):
        self.config_path = config_path
        self._triggers: List[Trigger] = []
        self._logs: List[TriggerLog] = []
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.config_path.exists():
            self._triggers = []
            self._logs = []
            return
        data = yaml.safe_load(self.config_path.read_text()) or {}
        self._triggers = [Trigger(**t) for t in data.get("triggers", [])]
        self._logs = [TriggerLog(**lg) for lg in data.get("logs", [])]

    def _save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "triggers": [asdict(t) for t in self._triggers],
            "logs": [asdict(lg) for lg in self._logs[-200:]],  # keep last 200
        }
        self.config_path.write_text(yaml.dump(data, sort_keys=False))

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def list(self) -> List[Trigger]:
        return list(self._triggers)

    def add(self, event: str, action: str) -> Trigger:
        trigger = Trigger(id=str(uuid.uuid4())[:8], event=event, action=action)
        self._triggers.append(trigger)
        self._save()
        return trigger

    def remove(self, trigger_id: str) -> bool:
        before = len(self._triggers)
        self._triggers = [t for t in self._triggers if t.id != trigger_id]
        if len(self._triggers) < before:
            self._save()
            return True
        return False

    def get(self, trigger_id: str) -> Optional[Trigger]:
        for t in self._triggers:
            if t.id == trigger_id:
                return t
        return None

    # ── logs ─────────────────────────────────────────────────────────────────

    def log_fire(self, trigger: Trigger, exit_code: int, output: str) -> None:
        entry = TriggerLog(
            trigger_id=trigger.id,
            event=trigger.event,
            action=trigger.action,
            fired_at=datetime.datetime.now().isoformat(),
            exit_code=exit_code,
            output=output[:500],
        )
        self._logs.append(entry)
        self._save()

    def list_logs(self, limit: int = 20) -> List[TriggerLog]:
        return list(reversed(self._logs[-limit:]))
