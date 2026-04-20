"""
YAML Macro Engine for TokenPak.

Macros are stored as YAML files in ~/.tokenpak/macros/.

YAML format:
    name: my-macro
    description: What this macro does
    variables:
      env: production
      retries: 3
    continue_on_error: false   # fail-fast is default (false)
    steps:
      - name: step1
        label: "📊 Check Status"
        cmd: "tokenpak status --env ${env}"
      - name: step2
        label: "🔁 Retry test"
        cmd: "tokenpak probe --retries ${retries}"

CLI commands:
    tokenpak macro create --name <name> [--description ...] [--file macro.yaml]
    tokenpak macro list
    tokenpak macro show <name>
    tokenpak macro run <name> [--dry-run] [--continue-on-error] [--var K=V ...]
    tokenpak macro delete <name>
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

MACROS_DIR = Path.home() / ".tokenpak" / "macros"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_yaml() -> None:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required for the macro engine. Install it: pip install pyyaml"
        )


def _resolve_vars(text: str, variables: Dict[str, Any]) -> str:
    """Substitute ${VAR} and $VAR placeholders with values from variables dict."""

    def replacer(match):
        key = match.group(1) or match.group(2)
        return str(variables.get(key, match.group(0)))

    # ${VAR} style
    text = re.sub(r"\$\{([^}]+)\}", replacer, text)
    # $VAR style (word boundary)
    text = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)\b", replacer, text)
    return text


# ── Data model ────────────────────────────────────────────────────────────────


class MacroStep:
    """A single step within a macro."""

    def __init__(self, name: str, cmd: str, label: str = "", timeout: int = 60):
        self.name = name
        self.cmd = cmd
        self.label = label or name
        self.timeout = timeout

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "cmd": self.cmd,
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MacroStep":
        return cls(
            name=data["name"],
            cmd=data.get("cmd", ""),
            label=data.get("label", data["name"]),
            timeout=data.get("timeout", 60),
        )


class MacroDefinition:
    """A user-defined macro loaded from YAML."""

    def __init__(
        self,
        name: str,
        steps: List[MacroStep],
        description: str = "",
        variables: Optional[Dict[str, Any]] = None,
        continue_on_error: bool = False,
    ):
        self.name = name
        self.description = description
        self.steps = steps
        self.variables = variables or {}
        self.continue_on_error = continue_on_error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "variables": self.variables,
            "continue_on_error": self.continue_on_error,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MacroDefinition":
        steps = [MacroStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            variables=data.get("variables") or {},
            continue_on_error=data.get("continue_on_error", False),
            steps=steps,
        )

    def to_yaml(self) -> str:
        _require_yaml()
        return yaml.dump(self.to_dict(), default_flow_style=False, sort_keys=False)

    @classmethod
    def from_yaml(cls, text: str) -> "MacroDefinition":
        _require_yaml()
        data = yaml.safe_load(text)
        return cls.from_dict(data)


# ── Run result ────────────────────────────────────────────────────────────────


class StepResult:
    def __init__(
        self,
        name: str,
        label: str,
        cmd: str,
        output: str,
        error: str,
        success: bool,
        returncode: int,
        dry_run: bool = False,
    ):
        self.name = name
        self.label = label
        self.cmd = cmd
        self.output = output
        self.error = error
        self.success = success
        self.returncode = returncode
        self.dry_run = dry_run

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "cmd": self.cmd,
            "output": self.output,
            "error": self.error,
            "success": self.success,
            "returncode": self.returncode,
            "dry_run": self.dry_run,
        }


class MacroResult:
    def __init__(
        self,
        macro_name: str,
        steps: List[StepResult],
        started_at: str,
        finished_at: str,
        success: bool,
        dry_run: bool = False,
    ):
        self.macro_name = macro_name
        self.steps = steps
        self.started_at = started_at
        self.finished_at = finished_at
        self.success = success
        self.dry_run = dry_run

    @property
    def duration_seconds(self) -> float:
        try:
            s = datetime.fromisoformat(self.started_at)
            e = datetime.fromisoformat(self.finished_at)
            return (e - s).total_seconds()
        except Exception:
            return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "macro_name": self.macro_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "success": self.success,
            "dry_run": self.dry_run,
            "steps": [s.to_dict() for s in self.steps],
        }

    def format(self) -> str:
        """Return human-readable output."""
        lines = [
            f"{'=' * 60}",
            f"  {self.macro_name.upper()}",
        ]
        if self.dry_run:
            lines.append("  [DRY RUN — no commands executed]")
        lines.append(f"  Started: {self.started_at[:19]}")
        lines.append(f"{'=' * 60}")

        for step in self.steps:
            status = "🔍" if step.dry_run else ("✅" if step.success else "❌")
            lines.append(f"\n{status} {step.label}")
            if step.dry_run:
                lines.append(f"   $ {step.cmd}")
            else:
                if step.output:
                    for line in step.output.splitlines():
                        lines.append(f"   {line}")
                if step.error and not step.success:
                    lines.append(f"   ⚠️  {step.error}")

        dur = self.duration_seconds
        overall = "✅ PASS" if self.success else "❌ FAIL"
        lines.append(f"\n{'=' * 60}")
        lines.append(f"  {overall}  — completed in {dur:.1f}s")
        lines.append(f"{'=' * 60}")
        return "\n".join(lines)


# ── Engine ────────────────────────────────────────────────────────────────────


class MacroEngine:
    """
    Core YAML macro engine.

    Manages user-defined macros stored as YAML files in ~/.tokenpak/macros/.
    """

    def __init__(self, macros_dir: Optional[Path] = None):
        self.macros_dir = macros_dir or MACROS_DIR

    def _path(self, name: str) -> Path:
        return self.macros_dir / f"{name}.yaml"

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        steps: List[Dict[str, Any]],
        description: str = "",
        variables: Optional[Dict[str, Any]] = None,
        continue_on_error: bool = False,
        overwrite: bool = False,
    ) -> Path:
        """
        Create a new macro YAML file.

        Args:
            name: Macro identifier (e.g., "morning-standup")
            steps: List of step dicts with keys: name, cmd, label (optional), timeout (optional)
            description: Human-readable description
            variables: Default variable values
            continue_on_error: If True, keep running after step failure
            overwrite: Allow overwriting an existing macro

        Returns:
            Path to the created YAML file.

        Raises:
            ValueError: If name already exists and overwrite=False.
        """
        _require_yaml()
        path = self._path(name)
        if path.exists() and not overwrite:
            raise ValueError(
                f"Macro '{name}' already exists at {path}. Use overwrite=True to replace."
            )

        step_objs = [MacroStep.from_dict(s) for s in steps]
        macro = MacroDefinition(
            name=name,
            description=description,
            variables=variables or {},
            continue_on_error=continue_on_error,
            steps=step_objs,
        )

        self.macros_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(macro.to_yaml())
        return path

    def create_from_yaml(self, yaml_text: str, overwrite: bool = False) -> Path:
        """Create a macro from raw YAML string."""
        _require_yaml()
        macro = MacroDefinition.from_yaml(yaml_text)
        path = self._path(macro.name)
        if path.exists() and not overwrite:
            raise ValueError(f"Macro '{macro.name}' already exists. Use overwrite=True to replace.")
        self.macros_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_text)
        return path

    def show(self, name: str) -> MacroDefinition:
        """
        Load and return a macro definition.

        Raises:
            FileNotFoundError: If macro does not exist.
        """
        _require_yaml()
        path = self._path(name)
        if not path.exists():
            raise FileNotFoundError(f"Macro '{name}' not found at {path}")
        return MacroDefinition.from_yaml(path.read_text())

    def list(self) -> List[MacroDefinition]:
        """Return all user-defined macros, sorted by name."""
        _require_yaml()
        if not self.macros_dir.exists():
            return []
        macros = []
        for f in sorted(self.macros_dir.glob("*.yaml")):
            try:
                macros.append(MacroDefinition.from_yaml(f.read_text()))
            except Exception:
                pass  # skip malformed files
        return macros

    def delete(self, name: str) -> bool:
        """
        Delete a macro by name.

        Returns:
            True if deleted, False if not found.
        """
        path = self._path(name)
        if not path.exists():
            return False
        path.unlink()
        return True

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    # ── Execution ─────────────────────────────────────────────────────────────

    def run(
        self,
        name: str,
        variables: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        continue_on_error: Optional[bool] = None,
    ) -> MacroResult:
        """
        Execute a macro by name.

        Args:
            name: Macro name.
            variables: Runtime variable overrides (merged with macro defaults).
            dry_run: If True, print commands without executing.
            continue_on_error: Override the macro's continue_on_error setting.

        Returns:
            MacroResult with step results and summary.
        """
        macro = self.show(name)
        return self.run_definition(
            macro,
            variables=variables,
            dry_run=dry_run,
            continue_on_error=continue_on_error,
        )

    def run_definition(
        self,
        macro: MacroDefinition,
        variables: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        continue_on_error: Optional[bool] = None,
    ) -> MacroResult:
        """
        Execute a MacroDefinition object.

        Args:
            macro: MacroDefinition to execute.
            variables: Runtime variable overrides.
            dry_run: If True, commands are printed but not run.
            continue_on_error: Override the macro's setting when provided.

        Returns:
            MacroResult
        """
        # Merge variables: macro defaults < runtime overrides
        merged_vars: Dict[str, Any] = {**macro.variables, **(variables or {})}

        # Decide fail-fast vs continue-on-error
        keep_going = continue_on_error if continue_on_error is not None else macro.continue_on_error

        started_at = datetime.now().isoformat()
        step_results: List[StepResult] = []
        overall_success = True

        for step in macro.steps:
            resolved_cmd = _resolve_vars(step.cmd, merged_vars)
            resolved_label = _resolve_vars(step.label, merged_vars)

            if dry_run:
                step_results.append(
                    StepResult(
                        name=step.name,
                        label=resolved_label,
                        cmd=resolved_cmd,
                        output="",
                        error="",
                        success=True,
                        returncode=0,
                        dry_run=True,
                    )
                )
                continue

            result = self._run_step(step.name, resolved_label, resolved_cmd, step.timeout)
            step_results.append(result)

            if not result.success:
                overall_success = False
                if not keep_going:
                    break  # fail-fast

        finished_at = datetime.now().isoformat()

        if dry_run:
            overall_success = True  # dry-run always "succeeds"

        return MacroResult(
            macro_name=macro.name,
            steps=step_results,
            started_at=started_at,
            finished_at=finished_at,
            success=overall_success,
            dry_run=dry_run,
        )

    def _run_step(self, name: str, label: str, cmd: str, timeout: int = 60) -> StepResult:
        """Execute a single step command."""
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return StepResult(
                name=name,
                label=label,
                cmd=cmd,
                output=proc.stdout.strip(),
                error=proc.stderr.strip(),
                success=proc.returncode == 0,
                returncode=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            return StepResult(
                name=name,
                label=label,
                cmd=cmd,
                output="",
                error=f"Step timed out after {timeout}s",
                success=False,
                returncode=-1,
            )
        except Exception as exc:
            return StepResult(
                name=name,
                label=label,
                cmd=cmd,
                output="",
                error=str(exc),
                success=False,
                returncode=-1,
            )


# ── Module-level singleton ────────────────────────────────────────────────────

_engine: Optional[MacroEngine] = None


def _get_engine() -> MacroEngine:
    global _engine
    if _engine is None:
        _engine = MacroEngine()
    return _engine


def create_macro(
    name: str,
    steps: List[Dict[str, Any]],
    description: str = "",
    variables: Optional[Dict[str, Any]] = None,
    continue_on_error: bool = False,
    overwrite: bool = False,
) -> Path:
    return _get_engine().create(name, steps, description, variables, continue_on_error, overwrite)


def show_macro(name: str) -> MacroDefinition:
    return _get_engine().show(name)


def list_user_macros() -> List[MacroDefinition]:
    return _get_engine().list()


def delete_macro(name: str) -> bool:
    return _get_engine().delete(name)


def run_user_macro(
    name: str,
    variables: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    continue_on_error: Optional[bool] = None,
) -> MacroResult:
    return _get_engine().run(name, variables, dry_run, continue_on_error)
