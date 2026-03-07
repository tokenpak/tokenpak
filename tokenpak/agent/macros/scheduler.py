"""
Macro scheduler for TokenPak.

Supports:
- Cron-expression scheduling via system crontab
- One-shot "at"-style scheduling
- List and cancel scheduled macros
"""

import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_SCHEDULE_PATH = Path.home() / ".tokenpak" / "scheduled.json"
CRON_COMMENT_TAG = "# tokenpak-schedule"


@dataclass
class ScheduledMacro:
    """A scheduled macro run."""

    id: str
    name: str
    schedule_type: str  # "cron" or "at"
    schedule: str  # cron expr or ISO datetime
    command: str  # full command to run
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduledMacro":
        return cls(**data)


class MacroScheduler:
    """
    Scheduler for macros using system cron and at-style one-shots.

    Persists schedule info in ~/.tokenpak/scheduled.json.
    """

    def __init__(self, schedule_path: Optional[Path] = None):
        self.schedule_path = schedule_path or DEFAULT_SCHEDULE_PATH
        self._schedules: Dict[str, ScheduledMacro] = {}
        self._load()

    def _load(self) -> None:
        if self.schedule_path.exists():
            try:
                data = json.loads(self.schedule_path.read_text())
                self._schedules = {
                    sid: ScheduledMacro.from_dict(sdata)
                    for sid, sdata in data.get("schedules", {}).items()
                }
            except (json.JSONDecodeError, KeyError, TypeError):
                self._schedules = {}

    def _save(self) -> None:
        self.schedule_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schedules": {sid: s.to_dict() for sid, s in self._schedules.items()},
            "version": 1,
            "updated_at": datetime.now().isoformat(),
        }
        self.schedule_path.write_text(json.dumps(data, indent=2))

    def _crontab_lines(self) -> List[str]:
        """Read current crontab lines."""
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.splitlines()
            return []
        except FileNotFoundError:
            return []

    def _write_crontab(self, lines: List[str]) -> bool:
        """Write lines to crontab."""
        try:
            content = "\n".join(lines) + "\n"
            proc = subprocess.run(["crontab", "-"], input=content, text=True, capture_output=True)
            return proc.returncode == 0
        except FileNotFoundError:
            return False

    def _add_cron_entry(self, schedule_id: str, cron_expr: str, command: str) -> bool:
        """Add a cron entry tagged with schedule_id."""
        lines = self._crontab_lines()
        tag = f"{CRON_COMMENT_TAG}:{schedule_id}"
        # Remove any existing entry with this id
        lines = [l for l in lines if tag not in l]
        lines.append(f"{cron_expr} {command} {tag}")
        return self._write_crontab(lines)

    def _remove_cron_entry(self, schedule_id: str) -> bool:
        """Remove a cron entry by schedule_id tag."""
        lines = self._crontab_lines()
        tag = f"{CRON_COMMENT_TAG}:{schedule_id}"
        new_lines = [l for l in lines if tag not in l]
        if len(new_lines) == len(lines):
            return False  # nothing removed
        return self._write_crontab(new_lines)

    def schedule_cron(
        self,
        name: str,
        cron_expr: str,
        command: Optional[str] = None,
        description: str = "",
    ) -> ScheduledMacro:
        """
        Schedule a macro on a cron expression.

        Args:
            name: Macro name
            cron_expr: Cron expression (e.g., "0 9 * * 1-5")
            command: Override command (defaults to `tokenpak macro run <name>`)
            description: Optional description

        Returns:
            ScheduledMacro record
        """
        schedule_id = str(uuid.uuid4())[:8]
        cmd = command or f"tokenpak macro run {name}"
        scheduled = ScheduledMacro(
            id=schedule_id,
            name=name,
            schedule_type="cron",
            schedule=cron_expr,
            command=cmd,
            description=description,
        )
        self._schedules[schedule_id] = scheduled
        self._save()
        self._add_cron_entry(schedule_id, cron_expr, cmd)
        return scheduled

    def schedule_at(
        self,
        name: str,
        run_at: str,
        command: Optional[str] = None,
        description: str = "",
    ) -> ScheduledMacro:
        """
        Schedule a one-shot macro run at a specific time.

        Args:
            name: Macro name
            run_at: ISO datetime or human-readable time string
            command: Override command (defaults to `tokenpak macro run <name>`)
            description: Optional description

        Returns:
            ScheduledMacro record
        """
        schedule_id = str(uuid.uuid4())[:8]
        cmd = command or f"tokenpak macro run {name}"
        scheduled = ScheduledMacro(
            id=schedule_id,
            name=name,
            schedule_type="at",
            schedule=run_at,
            command=cmd,
            description=description,
        )
        self._schedules[schedule_id] = scheduled
        self._save()
        # Use 'at' command for one-shot scheduling if available
        self._schedule_at_command(run_at, cmd)
        return scheduled

    def _schedule_at_command(self, run_at: str, command: str) -> bool:
        """Submit a one-shot job via system 'at' command."""
        try:
            proc = subprocess.run(
                ["at", run_at],
                input=command + "\n",
                text=True,
                capture_output=True,
            )
            return proc.returncode == 0
        except FileNotFoundError:
            return False  # 'at' not available

    def list_scheduled(self) -> List[ScheduledMacro]:
        """List all scheduled macros."""
        return sorted(
            [s for s in self._schedules.values() if s.enabled],
            key=lambda s: s.created_at,
        )

    def cancel(self, schedule_id: str) -> bool:
        """
        Cancel a scheduled run by ID.

        Returns True if cancelled, False if not found.
        """
        if schedule_id not in self._schedules:
            return False
        scheduled = self._schedules[schedule_id]
        if scheduled.schedule_type == "cron":
            self._remove_cron_entry(schedule_id)
        # Mark disabled (keep history)
        scheduled.enabled = False
        self._save()
        return True

    def get(self, schedule_id: str) -> Optional[ScheduledMacro]:
        """Get a scheduled macro by ID."""
        return self._schedules.get(schedule_id)


# Module-level singleton
_scheduler: Optional[MacroScheduler] = None


def _get_scheduler() -> MacroScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MacroScheduler()
    return _scheduler


def schedule_cron(
    name: str, cron_expr: str, command: Optional[str] = None, description: str = ""
) -> ScheduledMacro:
    return _get_scheduler().schedule_cron(name, cron_expr, command, description)


def schedule_at(
    name: str, run_at: str, command: Optional[str] = None, description: str = ""
) -> ScheduledMacro:
    return _get_scheduler().schedule_at(name, run_at, command, description)


def list_scheduled() -> List[ScheduledMacro]:
    return _get_scheduler().list_scheduled()


def cancel_schedule(schedule_id: str) -> bool:
    return _get_scheduler().cancel(schedule_id)
