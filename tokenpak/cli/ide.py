# SPDX-License-Identifier: Apache-2.0
"""IDE integration helpers for `tokenpak setup`.

Detects common IDE hosts (Cursor, VSCode, and variants) via environment signals
and offers to write `ANTHROPIC_BASE_URL` to the user's shell profile so that
IDE-launched Claude Code / Anthropic SDK calls route through the local proxy.

Design: per-IDE handlers live in a registry keyed by `name`. Handlers expose
a `detect(env)` predicate and a static label. The registry is the single
source of truth; adding a new IDE is `register(Handler())`. There is no
hardcoded enumeration of IDEs at the call site (`feedback_always_dynamic`,
2026-04-16) — detection is signal-based and unknowns are a graceful no-op.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Mapping, Optional

ExportLine = str
EnvLike = Mapping[str, str]


@dataclass(frozen=True)
class IDEHandler:
    """A detector for one IDE host.

    `name` is the slug used in messages. `detect` returns True if the given
    environment indicates this IDE is currently running. `label` is the
    human-facing name.
    """

    name: str
    label: str
    detect: Callable[[EnvLike], bool]


_REGISTRY: List[IDEHandler] = []


def register(handler: IDEHandler) -> None:
    """Add a handler to the detection registry. Idempotent on `name`."""
    for i, existing in enumerate(_REGISTRY):
        if existing.name == handler.name:
            _REGISTRY[i] = handler
            return
    _REGISTRY.append(handler)


def registered() -> List[IDEHandler]:
    """Return all registered handlers (for tests + introspection)."""
    return list(_REGISTRY)


def detect(env: Optional[EnvLike] = None) -> List[IDEHandler]:
    """Return every registered handler whose `detect` fires for `env`.

    Multiple matches are possible (e.g. Cursor runs with VSCODE_PID set).
    Call-sites de-duplicate by `name` if they need to."""
    env = env if env is not None else os.environ
    return [h for h in _REGISTRY if h.detect(env)]


# ── Built-in handlers ────────────────────────────────────────────────────────
#
# Each handler reads ONLY from the env dict passed in. No global state.


def _cursor_detect(env: EnvLike) -> bool:
    # Cursor exports CURSOR_* env vars. It also sets VSCODE_* because it
    # forks VSCode, so we bias Cursor over VSCode when both fire.
    if any(k.startswith("CURSOR_") for k in env.keys()):
        return True
    return env.get("TERM_PROGRAM") == "cursor"


def _vscode_detect(env: EnvLike) -> bool:
    if env.get("TERM_PROGRAM") == "vscode":
        return True
    if env.get("VSCODE_PID") or env.get("VSCODE_IPC_HOOK") or env.get("VSCODE_IPC_HOOK_CLI"):
        return True
    return False


register(IDEHandler(name="cursor", label="Cursor", detect=_cursor_detect))
register(IDEHandler(name="vscode", label="VSCode", detect=_vscode_detect))


# ── Shell-profile writer ─────────────────────────────────────────────────────


_EXPORT_MARKER = "# Added by `tokenpak setup` — route Anthropic SDK traffic through the local proxy"


def _candidate_profile_paths(home: Path, shell: Optional[str]) -> List[Path]:
    """Return likely shell profile paths for this user, in preference order.

    Preference: match the detected shell if possible, else fall back to
    whatever profile file already exists on disk. We never create a new
    shell config file silently; if none exists the caller prints the
    manual export instead.
    """
    shell = (shell or "").lower()
    if "zsh" in shell:
        order = [".zshrc", ".bashrc", ".bash_profile"]
    elif "fish" in shell:
        order = [".config/fish/config.fish", ".zshrc", ".bashrc"]
    elif "bash" in shell:
        order = [".bashrc", ".bash_profile", ".zshrc"]
    else:
        order = [".zshrc", ".bashrc", ".bash_profile", ".config/fish/config.fish"]
    return [home / rel for rel in order]


def _format_export(shell_path: Path, base_url: str) -> ExportLine:
    """Format the export line in the syntax the target shell uses."""
    if shell_path.name == "config.fish" or "fish" in shell_path.parts:
        return f'set -gx ANTHROPIC_BASE_URL "{base_url}"\n'
    return f'export ANTHROPIC_BASE_URL="{base_url}"\n'


def resolve_profile(home: Optional[Path] = None, shell: Optional[str] = None) -> Optional[Path]:
    """Pick the shell profile to write to, or None if none exists."""
    home = home or Path.home()
    shell = shell if shell is not None else os.environ.get("SHELL", "")
    for candidate in _candidate_profile_paths(home, shell):
        if candidate.exists():
            return candidate
    return None


def write_export(
    profile_path: Path,
    base_url: str,
) -> bool:
    """Append an ANTHROPIC_BASE_URL export to `profile_path`. Idempotent.

    Returns True if the file was modified, False if the export already
    points at this URL. Existing exports with a different URL are replaced
    by appending a fresh (commented) stanza — we do not mutate the user's
    original line.
    """
    existing = profile_path.read_text() if profile_path.exists() else ""
    desired = _format_export(profile_path, base_url).strip()
    if desired in existing:
        return False

    stanza = f"\n{_EXPORT_MARKER}\n{_format_export(profile_path, base_url)}"
    with open(profile_path, "a", encoding="utf-8") as f:
        f.write(stanza)
    return True


# ── Orchestration used by cmd_setup ──────────────────────────────────────────


def run_setup_step(
    port: int,
    *,
    env: Optional[EnvLike] = None,
    home: Optional[Path] = None,
    shell: Optional[str] = None,
    prompt: Optional[Callable[[str], str]] = None,
    printer: Callable[[str], None] = print,
    auto_yes: bool = False,
) -> dict:
    """The IDE integration step invoked from `cmd_setup`.

    Returns a small dict describing what happened — used by tests. Writes
    nothing if no IDE is detected OR if the user declines the prompt.
    """
    env = env if env is not None else os.environ
    prompt = prompt or input
    base_url = f"http://127.0.0.1:{port}"

    detected: List[IDEHandler] = detect(env)
    # Dedup by name (Cursor handler also fires vscode_detect since Cursor
    # inherits VSCode env). Prefer the first match per name.
    seen: set = set()
    unique: List[IDEHandler] = []
    for h in detected:
        if h.name not in seen:
            seen.add(h.name)
            unique.append(h)

    if not unique:
        return {"detected": [], "wrote": None, "profile": None, "base_url": base_url}

    labels = ", ".join(h.label for h in unique)
    printer("")
    printer(f"IDE integration: detected {labels}.")
    printer("  To route IDE-launched Claude Code / Anthropic SDK calls through tokenpak,")
    printer(f"  set ANTHROPIC_BASE_URL={base_url} in your shell.")

    profile = resolve_profile(home=home, shell=shell)
    if profile is None:
        printer("")
        printer("  No shell profile found (.zshrc / .bashrc / fish config).")
        printer("  Run this manually to route IDE traffic through tokenpak:")
        printer(f'    export ANTHROPIC_BASE_URL="{base_url}"')
        return {
            "detected": [h.name for h in unique],
            "wrote": None,
            "profile": None,
            "base_url": base_url,
        }

    if auto_yes:
        answer = "yes"
    else:
        answer = prompt(f"  Append export to {profile}? (yes/no) [yes]: ").strip().lower()
        if answer == "":
            answer = "yes"

    if answer not in ("yes", "y"):
        printer("  Skipped. Run this manually to enable:")
        printer(f'    export ANTHROPIC_BASE_URL="{base_url}"')
        return {
            "detected": [h.name for h in unique],
            "wrote": False,
            "profile": str(profile),
            "base_url": base_url,
        }

    modified = write_export(profile, base_url)
    if modified:
        printer(f"  ✅ Appended to {profile}.")
        printer(f"     Start a new shell (or `source {profile}`) and relaunch your IDE.")
    else:
        printer(f"  ✅ Already present in {profile}. No change.")
    return {
        "detected": [h.name for h in unique],
        "wrote": modified,
        "profile": str(profile),
        "base_url": base_url,
    }


__all__ = [
    "IDEHandler",
    "detect",
    "register",
    "registered",
    "resolve_profile",
    "run_setup_step",
    "write_export",
]
