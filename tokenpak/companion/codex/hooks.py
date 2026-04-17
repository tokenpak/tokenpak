# SPDX-License-Identifier: Apache-2.0
"""Generate and install Codex hooks.json for the tokenpak companion.

Codex hooks are configured via ``~/.codex/hooks.json`` (global) or
``<repo>/.codex/hooks.json`` (project-level).  The companion installs
two hooks:

- **UserPromptSubmit** → token estimation, budget gating, journal seed
- **Stop** → session closeout, journal summary, cost recording

Hooks must be enabled via the ``codex_hooks`` feature flag.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


_HOOKS_DIR = Path(__file__).parent
_PRE_SEND_HOOK = _HOOKS_DIR / "hooks_pre_send.sh"
_STOP_HOOK = _HOOKS_DIR / "hooks_stop.sh"

# Substring used to identify tokenpak-owned hook commands across merges.
TOKENPAK_HOOK_MARKER = "tokenpak"

# Events the companion owns. Add more here and they'll flow through
# install / merge / uninstall without further code changes.
def _tokenpak_hook_events() -> dict[str, dict]:
    """Return {event_name: hook_group} for every event the companion installs."""
    return {
        "UserPromptSubmit": {
            "hooks": [
                {
                    "type": "command",
                    "command": f"bash {_PRE_SEND_HOOK}",
                    "timeout": 10,
                    "statusMessage": "tokenpak: estimating cost...",
                }
            ]
        },
        "Stop": {
            "hooks": [
                {
                    "type": "command",
                    "command": f"bash {_STOP_HOOK}",
                    "timeout": 15,
                    "statusMessage": "tokenpak: closing session...",
                }
            ]
        },
    }


def generate_hooks_json() -> dict:
    """Build the hooks.json structure matching Codex's documented schema.

    Codex expects::

        {"hooks": {"<EventName>": [{"hooks": [{command...}]}]}}
    """
    return {
        "hooks": {event: [group] for event, group in _tokenpak_hook_events().items()}
    }


def install_hooks(target: str = "global") -> Path:
    """Write hooks.json to the appropriate Codex config directory.

    Args:
        target: ``"global"`` for ``~/.codex/hooks.json``, or a repo path
                for ``<repo>/.codex/hooks.json``.

    Returns:
        Path to the written hooks.json file.

    Existing non-tokenpak hooks are preserved; tokenpak entries are
    replaced idempotently.
    """
    if target == "global":
        hooks_dir = Path.home() / ".codex"
    else:
        hooks_dir = Path(target) / ".codex"

    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_path = hooks_dir / "hooks.json"

    new_hooks = generate_hooks_json()

    if hooks_path.exists():
        try:
            existing = json.loads(hooks_path.read_text())
            merged = _merge_hooks(existing, new_hooks)
        except (json.JSONDecodeError, KeyError, TypeError):
            merged = new_hooks
    else:
        merged = new_hooks

    hooks_path.write_text(json.dumps(merged, indent=2) + "\n")
    return hooks_path


def _merge_hooks(existing: dict, new: dict) -> dict:
    """Merge tokenpak hooks into existing hooks.json without clobbering.

    Handles both the Codex-native shape
    (``{"hooks": {"Event": [{"hooks": [...]}]}}``) and the legacy
    pre-v1 shape we previously wrote; legacy entries are discarded.

    Non-tokenpak hooks — identified by the absence of
    :data:`TOKENPAK_HOOK_MARKER` in the command string — are preserved.
    """
    existing_hooks = existing.get("hooks")
    new_hooks = new.get("hooks", {})

    preserved: dict[str, list[dict]] = {}

    if isinstance(existing_hooks, dict):
        for event, groups in existing_hooks.items():
            if not isinstance(groups, list):
                continue
            kept_groups: list[dict] = []
            for group in groups:
                if not isinstance(group, dict):
                    continue
                commands = group.get("hooks", [])
                non_tokenpak = [
                    c
                    for c in commands
                    if isinstance(c, dict)
                    and TOKENPAK_HOOK_MARKER not in c.get("command", "")
                ]
                if non_tokenpak:
                    kept = {**group, "hooks": non_tokenpak}
                    kept_groups.append(kept)
            if kept_groups:
                preserved[event] = kept_groups
    # Legacy array-shaped hooks: we drop them silently (schema mismatch
    # means Codex never ran them anyway).

    merged_hooks: dict[str, list[dict]] = {}
    for event, groups in preserved.items():
        merged_hooks.setdefault(event, []).extend(groups)
    for event, groups in new_hooks.items():
        merged_hooks.setdefault(event, []).extend(groups)

    return {"hooks": merged_hooks}


def ensure_hooks_feature_enabled() -> bool:
    """Enable the ``codex_hooks`` feature via ``codex features enable``.

    Uses the Codex-native command rather than hand-writing config.toml,
    so we inherit any future config-schema changes for free. Idempotent:
    calling on an already-enabled feature still returns True.
    """
    try:
        result = subprocess.run(
            ["codex", "features", "enable", "codex_hooks"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"tokenpak: codex not available: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(
            f"tokenpak: failed to enable codex_hooks feature: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return False
    return True
