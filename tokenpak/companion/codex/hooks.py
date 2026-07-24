# SPDX-License-Identifier: Apache-2.0
"""Generate and install Codex hooks.json for the tokenpak companion.

Codex hooks are configured via ``$CODEX_HOME/hooks.json`` (global) or
``<repo>/.codex/hooks.json`` (project-level).  The companion installs
five hooks (5 of 6 Codex stable lifecycle events; PermissionRequest is
deferred to L5 — see L1 audit delta hooks #10):

- **SessionStart** → capsule auto-load + branded banner
- **UserPromptSubmit** → token estimation, budget gating, journal seed
- **PreToolUse** → per-tool budget gate + trace stamp
- **PostToolUse** → token-out journal
- **Stop** → session closeout, journal summary, cost recording

Hooks must be enabled via the ``hooks`` feature flag.

The event set is held in :data:`_TOKENPAK_HOOK_EVENTS` — a declarative
module-level table keyed by Codex event name. Adding a new event means
appending an entry (and shipping a matching script); install / merge /
uninstall flow through it without further code changes.  Per
``feedback_always_dynamic.md``, no hardcoded enumeration of events lives
inside a function body.
"""

from __future__ import annotations

__all__ = (
    "TOKENPAK_HOOK_MARKER",
    "ensure_hooks_feature_enabled",
    "generate_hooks_json",
    "install_hooks",
)


import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast

HookEntry = dict[str, object]
HookGroup = dict[str, object]


class HooksDocument(TypedDict):
    hooks: dict[str, list[HookGroup]]


_HOOKS_DIR = Path(__file__).parent
_SESSION_START_HOOK = _HOOKS_DIR / "hooks_session_start.sh"
_PRE_SEND_HOOK = _HOOKS_DIR / "hooks_pre_send.sh"
_PRE_TOOL_USE_HOOK = _HOOKS_DIR / "hooks_pre_tool_use.sh"
_POST_TOOL_USE_HOOK = _HOOKS_DIR / "hooks_post_tool_use.sh"
_STOP_HOOK = _HOOKS_DIR / "hooks_stop.sh"

# Substring used to identify tokenpak-owned hook commands across merges.
TOKENPAK_HOOK_MARKER = "tokenpak"

# Declarative event table — adding an event here is the only code touch
# needed for install / merge / uninstall to pick it up.
_TOKENPAK_HOOK_EVENTS: dict[str, HookGroup] = {
    "SessionStart": {
        "hooks": [
            {
                "type": "command",
                "command": f"bash {_SESSION_START_HOOK}",
                "timeout": 5,
                "statusMessage": "tokenpak: loading capsule...",
            }
        ]
    },
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
    "PreToolUse": {
        "hooks": [
            {
                "type": "command",
                "command": f"bash {_PRE_TOOL_USE_HOOK}",
                "timeout": 5,
                "statusMessage": "tokenpak: checking budget...",
            }
        ]
    },
    "PostToolUse": {
        "hooks": [
            {
                "type": "command",
                "command": f"bash {_POST_TOOL_USE_HOOK}",
                "timeout": 5,
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


def _tokenpak_hook_events() -> dict[str, HookGroup]:
    """Return the declarative event table.

    Retained as a thin accessor so existing callers (and tests) keep a
    stable import surface even though the data now lives in
    :data:`_TOKENPAK_HOOK_EVENTS` at module top.
    """
    return _TOKENPAK_HOOK_EVENTS


def generate_hooks_json() -> HooksDocument:
    """Build the hooks.json structure matching Codex's documented schema.

    Codex expects::

        {"hooks": {"<EventName>": [{"hooks": [{command...}]}]}}
    """
    return {"hooks": {event: [group] for event, group in _TOKENPAK_HOOK_EVENTS.items()}}


def _selected_codex_home(codex_home: Path | None = None) -> Path:
    """Resolve the active Codex home at call time."""
    if codex_home is not None:
        return Path(codex_home).expanduser()
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _codex_env(codex_home: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(_selected_codex_home(codex_home))
    return env


def install_hooks(target: str = "global") -> Path:
    """Write hooks.json using the active public Codex configuration."""
    return _install_hooks(target)


def _install_hooks(target: str = "global", *, codex_home: Path | None = None) -> Path:
    """Write hooks.json to the appropriate Codex config directory.

    Args:
        target: ``"global"`` for ``$CODEX_HOME/hooks.json``, or a repo path
                for ``<repo>/.codex/hooks.json``.
        codex_home: Internal explicit selected Codex home for a global install.

    Returns:
        Path to the written hooks.json file.

    Existing non-tokenpak hooks are preserved; tokenpak entries are
    replaced idempotently.
    """
    if target == "global":
        hooks_dir = _selected_codex_home(codex_home)
    else:
        hooks_dir = Path(target) / ".codex"

    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_path = hooks_dir / "hooks.json"

    new_hooks = generate_hooks_json()

    if hooks_path.exists():
        try:
            loaded: object = json.loads(hooks_path.read_text())
            if not isinstance(loaded, dict):
                raise TypeError("hooks.json root must be an object")
            existing = cast(dict[str, object], loaded)
            merged = _merge_hooks(existing, new_hooks)
        except (json.JSONDecodeError, KeyError, TypeError):
            merged = new_hooks
    else:
        merged = new_hooks

    hooks_path.write_text(json.dumps(merged, indent=2) + "\n")
    return hooks_path


def _merge_hooks(existing: dict[str, object], new: HooksDocument) -> HooksDocument:
    """Merge tokenpak hooks into existing hooks.json without clobbering.

    Handles both the Codex-native shape
    (``{"hooks": {"Event": [{"hooks": [...]}]}}``) and the legacy
    pre-v1 shape we previously wrote; legacy entries are discarded.

    Non-tokenpak hooks — identified by the absence of
    :data:`TOKENPAK_HOOK_MARKER` in the command string — are preserved.
    """
    existing_hooks = existing.get("hooks")
    new_hooks = new["hooks"]

    preserved: dict[str, list[HookGroup]] = {}

    if isinstance(existing_hooks, dict):
        typed_existing_hooks = cast(dict[str, object], existing_hooks)
        for event, groups in typed_existing_hooks.items():
            if not isinstance(groups, list):
                continue
            kept_groups: list[HookGroup] = []
            for group in groups:
                if not isinstance(group, dict):
                    continue
                typed_group = cast(HookGroup, group)
                commands = typed_group.get("hooks", [])
                if not isinstance(commands, list):
                    continue
                non_tokenpak: list[HookEntry] = []
                for command_entry in commands:
                    if not isinstance(command_entry, dict):
                        continue
                    typed_entry = cast(HookEntry, command_entry)
                    command = typed_entry.get("command")
                    if not isinstance(command, str) or TOKENPAK_HOOK_MARKER not in command:
                        non_tokenpak.append(typed_entry)
                if non_tokenpak:
                    kept = {**typed_group, "hooks": non_tokenpak}
                    kept_groups.append(kept)
            if kept_groups:
                preserved[event] = kept_groups
    # Legacy array-shaped hooks: we drop them silently (schema mismatch
    # means Codex never ran them anyway).

    merged_hooks: dict[str, list[HookGroup]] = {}
    for event, groups in preserved.items():
        merged_hooks.setdefault(event, []).extend(groups)
    for event, groups in new_hooks.items():
        merged_hooks.setdefault(event, []).extend(groups)

    return {"hooks": merged_hooks}


def ensure_hooks_feature_enabled() -> bool:
    """Enable hooks using the active public Codex configuration."""
    return _ensure_hooks_feature_enabled()


def _ensure_hooks_feature_enabled(codex_home: Path | None = None) -> bool:
    """Enable the ``hooks`` feature via ``codex features enable``.

    Uses the Codex-native command rather than hand-writing config.toml,
    so we inherit any future config-schema changes for free. Idempotent.

    Also suppresses the "Under-development features enabled" warning,
    since Codex re-prints it on every session otherwise — the user has
    explicitly opted in by installing the companion.
    """
    try:
        result = subprocess.run(
            ["codex", "features", "enable", "hooks"],
            capture_output=True,
            env=_codex_env(codex_home),
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"tokenpak: codex not available: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(
            f"tokenpak: failed to enable hooks feature: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return False

    if codex_home is None:
        _suppress_unstable_warning()
    else:
        _suppress_unstable_warning(codex_home)
    return True


def _suppress_unstable_warning(codex_home: Path | None = None) -> None:
    """Add the warning suppression to the selected ``config.toml``.

    Best-effort: if the file can't be read/written we stay silent rather
    than fail the install. The warning is cosmetic.
    """
    config_path = _selected_codex_home(codex_home) / "config.toml"
    try:
        content = config_path.read_text() if config_path.exists() else ""
    except OSError:
        return

    if "suppress_unstable_features_warning" in content:
        return

    lines = content.splitlines()
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line.lstrip().startswith("["):
            insert_at = i
            break

    lines.insert(insert_at, "suppress_unstable_features_warning = true")
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("\n".join(lines).rstrip() + "\n")
    except OSError:
        pass
