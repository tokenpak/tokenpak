"""
Premade macros for TokenPak.

Available macros:
  morning-standup  — cost summary + model usage + alert review
  pre-deploy       — run tests + check budget + snapshot stats
  weekly-report    — 7-day cost, savings, model usage, top recipes
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PREMADE_MACROS: Dict[str, Dict[str, Any]] = {
    "morning-standup": {
        "description": "Daily cost summary, model usage breakdown, and alert review",
        "steps": [
            {"name": "cost_summary", "cmd": "tokenpak status --json", "label": "💰 Today's Cost"},
            {
                "name": "model_usage",
                "cmd": "tokenpak stats --today --json",
                "label": "📊 Model Usage",
            },
            {"name": "alerts", "cmd": "tokenpak budget list --json", "label": "🚨 Budget Alerts"},
        ],
    },
    "pre-deploy": {
        "description": "Run tests, verify budget headroom, and snapshot current stats before deploying",
        "steps": [
            {
                "name": "budget_check",
                "cmd": "tokenpak budget status --json",
                "label": "💳 Budget Headroom",
            },
            {
                "name": "stats_snapshot",
                "cmd": "tokenpak stats --snapshot --json",
                "label": "📸 Stats Snapshot",
            },
        ],
    },
    "weekly-report": {
        "description": "7-day cost, token savings, model usage breakdown, and top recipes",
        "steps": [
            {
                "name": "weekly_cost",
                "cmd": "tokenpak stats --days 7 --json",
                "label": "📅 7-Day Cost & Usage",
            },
            {
                "name": "savings",
                "cmd": "tokenpak stats --days 7 --savings --json",
                "label": "💡 Token Savings",
            },
            {
                "name": "top_recipes",
                "cmd": "tokenpak recipes list --top --json",
                "label": "🍳 Top Recipes",
            },
        ],
    },
}

INSTALL_DIR = Path.home() / ".tokenpak" / "macros"


class PremadeMacroRunner:
    """Runs premade macros and formats their output."""

    def install(self, name: str) -> Path:
        """
        Install a premade macro as a JSON descriptor in ~/.tokenpak/macros/.

        Args:
            name: Macro name (e.g., "morning-standup")

        Returns:
            Path to the installed macro file.

        Raises:
            ValueError: If macro is not found.
        """
        if name not in PREMADE_MACROS:
            raise ValueError(
                f"Unknown premade macro: '{name}'. Available: {', '.join(PREMADE_MACROS.keys())}"
            )

        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        macro_path = INSTALL_DIR / f"{name}.json"
        macro_data = {
            "name": name,
            "installed_at": datetime.now().isoformat(),
            **PREMADE_MACROS[name],
        }
        macro_path.write_text(json.dumps(macro_data, indent=2))
        return macro_path

    def run(self, name: str, json_output: bool = False) -> Dict[str, Any]:
        """
        Run a premade macro and return structured results.

        Args:
            name: Macro name
            json_output: If True, return raw JSON from each step

        Returns:
            Dict with {name, started_at, finished_at, steps: [{name, label, output, success}]}
        """
        if name not in PREMADE_MACROS:
            raise ValueError(
                f"Unknown macro: '{name}'. Available: {', '.join(PREMADE_MACROS.keys())}"
            )

        macro = PREMADE_MACROS[name]
        started_at = datetime.now()
        step_results = []

        for step in macro["steps"]:
            result = self._run_step(step)
            step_results.append(result)

        finished_at = datetime.now()
        return {
            "name": name,
            "description": macro["description"],
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": (finished_at - started_at).total_seconds(),
            "steps": step_results,
        }

    def _run_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        """Run a single macro step."""
        try:
            result = subprocess.run(
                step["cmd"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return {
                "name": step["name"],
                "label": step["label"],
                "cmd": step["cmd"],
                "output": result.stdout.strip(),
                "error": result.stderr.strip(),
                "success": result.returncode == 0,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "name": step["name"],
                "label": step["label"],
                "cmd": step["cmd"],
                "output": "",
                "error": "Step timed out (60s)",
                "success": False,
                "returncode": -1,
            }

    def format_output(self, result: Dict[str, Any]) -> str:
        """Format macro results for human-readable display."""
        lines = [
            f"{'=' * 60}",
            f"  {result['name'].upper().replace('-', ' ')}",
            f"  {result['description']}",
            f"  Started: {result['started_at'][:19]}",
            f"{'=' * 60}",
        ]
        for step in result["steps"]:
            status = "✅" if step["success"] else "❌"
            lines.append(f"\n{status} {step['label']}")
            if step["output"]:
                for line in step["output"].splitlines():
                    lines.append(f"   {line}")
            if step["error"] and not step["success"]:
                lines.append(f"   ⚠️  {step['error']}")

        dur = result.get("duration_seconds", 0)
        lines.append(f"\n{'=' * 60}")
        lines.append(f"  Completed in {dur:.1f}s")
        lines.append(f"{'=' * 60}")
        return "\n".join(lines)

    def list_available(self) -> List[Dict[str, str]]:
        """List all premade macros."""
        return [
            {"name": name, "description": data["description"]}
            for name, data in PREMADE_MACROS.items()
        ]


# Module-level singleton
_runner: Optional[PremadeMacroRunner] = None


def _get_runner() -> PremadeMacroRunner:
    global _runner
    if _runner is None:
        _runner = PremadeMacroRunner()
    return _runner


def install_macro(name: str) -> Path:
    """Install a premade macro."""
    return _get_runner().install(name)


def run_macro(name: str) -> Dict[str, Any]:
    """Run a premade macro."""
    return _get_runner().run(name)


def list_macros() -> List[Dict[str, str]]:
    """List available premade macros."""
    return _get_runner().list_available()


def format_macro_output(result: Dict[str, Any]) -> str:
    """Format macro results for display."""
    return _get_runner().format_output(result)
