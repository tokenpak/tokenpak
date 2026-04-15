# SPDX-License-Identifier: Apache-2.0
"""Generate and install Codex hooks.json for the tokenpak companion.

Codex hooks are configured via ``~/.codex/hooks.json`` (global) or
``<repo>/.codex/hooks.json`` (project-level).  The companion installs
two hooks:

- **UserPromptSubmit** → token estimation, budget gating, journal seed
- **Stop** → session closeout, journal summary, cost recording

Hooks must be enabled in config.toml::

    [features]
    codex_hooks = true
"""

from __future__ import annotations

import json
from pathlib import Path


# Hook script paths relative to this file
_HOOKS_DIR = Path(__file__).parent
_PRE_SEND_HOOK = _HOOKS_DIR / "hooks_pre_send.sh"
_STOP_HOOK = _HOOKS_DIR / "hooks_stop.sh"


def generate_hooks_json() -> dict:
    """Build the hooks.json structure for Codex companion hooks."""
    return {
        "hooks": [
            {
                "event": "UserPromptSubmit",
                "commands": [
                    {
                        "type": "command",
                        "command": f"bash {_PRE_SEND_HOOK}",
                        "timeout": 10,
                        "statusMessage": "tokenpak: estimating cost...",
                    }
                ],
            },
            {
                "event": "Stop",
                "commands": [
                    {
                        "type": "command",
                        "command": f"bash {_STOP_HOOK}",
                        "timeout": 15,
                        "statusMessage": "tokenpak: closing session...",
                    }
                ],
            },
        ]
    }


def install_hooks(target: str = "global") -> Path:
    """Write hooks.json to the appropriate Codex config directory.

    Args:
        target: "global" for ~/.codex/hooks.json, or a repo path for
                <repo>/.codex/hooks.json.

    Returns:
        Path to the written hooks.json file.

    If hooks.json already exists and contains non-tokenpak hooks, the
    tokenpak hooks are merged in rather than overwriting.
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
        except (json.JSONDecodeError, KeyError):
            merged = new_hooks
    else:
        merged = new_hooks

    hooks_path.write_text(json.dumps(merged, indent=2) + "\n")
    return hooks_path


def _merge_hooks(existing: dict, new: dict) -> dict:
    """Merge tokenpak hooks into existing hooks.json without clobbering.

    Replaces any existing tokenpak hooks (identified by command path
    containing 'tokenpak') and preserves all other hooks.
    """
    existing_entries = existing.get("hooks", [])
    new_entries = new.get("hooks", [])

    # Index new hooks by event
    new_by_event: dict[str, dict] = {}
    for entry in new_entries:
        new_by_event[entry["event"]] = entry

    merged: list[dict] = []
    seen_events: set[str] = set()

    for entry in existing_entries:
        event = entry.get("event", "")
        if event in new_by_event:
            # Filter out old tokenpak commands, keep others
            existing_cmds = entry.get("commands", [])
            non_tokenpak = [
                c for c in existing_cmds
                if "tokenpak" not in c.get("command", "")
            ]
            # Merge: non-tokenpak existing + new tokenpak
            combined_cmds = non_tokenpak + new_by_event[event].get("commands", [])
            merged.append({**entry, "commands": combined_cmds})
            seen_events.add(event)
        else:
            merged.append(entry)

    # Add any new events not in existing
    for event, entry in new_by_event.items():
        if event not in seen_events:
            merged.append(entry)

    return {"hooks": merged}


def ensure_hooks_feature_enabled() -> bool:
    """Check and enable the codex_hooks feature in config.toml.

    Returns True if the feature is or was successfully enabled.
    """
    config_path = Path.home() / ".codex" / "config.toml"

    if config_path.exists():
        content = config_path.read_text()
        if "codex_hooks" in content:
            # Already configured (enabled or disabled) — don't override
            return "codex_hooks = true" in content or 'codex_hooks = true' in content
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        content = ""

    # Append the feature flag
    if "[features]" in content:
        # Add under existing [features] section
        content = content.replace(
            "[features]",
            "[features]\ncodex_hooks = true",
        )
    else:
        content += "\n[features]\ncodex_hooks = true\n"

    config_path.write_text(content)
    return True
