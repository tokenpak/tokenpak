# SPDX-License-Identifier: Apache-2.0
"""Launcher for ``tokenpak claude`` — one command to start Claude Code with
the companion active.

What it does:
    1. Loads companion config from env vars
    2. Ensures the tokenpak proxy is running (if configured)
    3. Generates config files: MCP config, settings overlay, system prompt
    4. Execs into ``claude`` with the right flags

Config files are written to the fixed location ~/.tokenpak/companion/run/
(not tempfile) so they persist across relaunches and are inspectable.

What the user sees:
    $ tokenpak claude

      📦 TokenPak Claude Companion
         TokenPak v1.8.0
         Ready • Mode: Balanced • Budget: Unlimited
         Proxy active → http://localhost:8766

         Your API bill called. It's crying.

    [Claude Code TUI starts normally]
"""

from __future__ import annotations

import json
import os
import random
import re
import shlex
import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Any, TextIO

from .._formatting.colors import Color
from . import _style
from .config import CompanionConfig

# ---------------------------------------------------------------------------
# Launcher permission defaults (runtime-only)
#
# Per-client modes are driven by TokenPak-owned state and remain distinct from
# bare mode: bare strips companion layers because an external gateway owns
# them and always bypasses. Launcher defaults leave every companion layer
# active and inject only session arguments. Neither path writes launcher
# arguments into ~/.claude/settings.json. The compatibility helpers below remain for
# compatibility with the former global full-bypass alias.
# ---------------------------------------------------------------------------

_FLEET_BYPASS_FLAG = "--dangerously-skip-permissions"


def _launcher_mode_state() -> tuple[str, str | None]:
    """Return the fail-closed Claude launcher default and any state warning."""
    try:
        from tokenpak.cli.commands.permissions import _get_launcher_mode_status

        return _get_launcher_mode_status("claude-code")
    except Exception as exc:
        return "inherit", f"could not read launcher permission state ({type(exc).__name__})"


def _fleet_mode_enabled() -> bool:
    """Compatibility helper: true when Claude resolves to full-bypass."""
    try:
        return _launcher_mode_state()[0] == "full-bypass"
    except Exception:
        return False


def _apply_fleet_mode(
    claude_args: list[str],
    fleet: bool,
    stream: TextIO | None = None,
) -> list[str]:
    """Inject the bypass flag + print the mandatory banner when fleet is on.

    Returns a new list (never mutates the input). The stderr banner is the
    canonical user-visible guardrail for fleet launches — do not remove or
    soften it. No duplicate flag is added when one is already present
    (e.g. bare mode or a user-passed flag).
    """
    out = list(claude_args)
    if not fleet:
        return out
    if _FLEET_BYPASS_FLAG not in out:
        out.append(_FLEET_BYPASS_FLAG)
    print(
        f"tokenpak: fleet mode — bypass flags injected ({_FLEET_BYPASS_FLAG})",
        file=stream if stream is not None else sys.stderr,
    )
    return out


def _has_permission_mode(args: list[str]) -> bool:
    return any(arg == "--permission-mode" or arg.startswith("--permission-mode=") for arg in args)


def _apply_launcher_mode(
    claude_args: list[str],
    mode: str,
) -> tuple[list[str], tuple[str, ...], str | None]:
    """Apply a Claude launcher default while respecting explicit argv."""
    out = list(claude_args)
    if mode != "full-bypass":
        return out, (), None

    has_full = _FLEET_BYPASS_FLAG in out
    has_permission_mode = _has_permission_mode(out)
    if has_full:
        return out, (_FLEET_BYPASS_FLAG,), None
    if has_permission_mode:
        return out, (), "an explicit permission mode takes precedence"
    out.append(_FLEET_BYPASS_FLAG)
    return out, (_FLEET_BYPASS_FLAG,), None


def _launcher_mode_banner(
    mode: str,
    flags: tuple[str, ...],
    skip_reason: str | None,
) -> str | None:
    """Build the mandatory launch-time warning for a non-inherit mode."""
    if mode != "full-bypass":
        return None
    reset = "tokenpak permissions launcher inherit --client claude-code"
    if skip_reason:
        return (
            f"tokenpak WARNING: claude-code launcher default {mode} skipped: "
            f"{skip_reason}. Reset: `{reset}`."
        )
    risk = "all Claude Code permission and safety checks are bypassed"
    rendered = " ".join(flags)
    return (
        f"tokenpak WARNING: claude-code launcher mode {mode} active; arguments: "
        f"{rendered}; {risk}. Use only in a trusted, externally isolated environment. "
        "Managed policy may still constrain or reject this launch. "
        f"Reset: `{reset}`."
    )


# System prompt fragment injected via --append-system-prompt-file
_SYSTEM_PROMPT = """\
## TokenPak companion

A TokenPak companion is active in this session. You have these MCP tools:

- **estimate_tokens** — Estimate token count for text or a file. Reserve for a go/no-go decision on very large content.
- **check_budget** — Report the remaining TokenPak cost budget for this session and today.
- **load_pak** — Batch prior-session Paks and optional journals; omit IDs to list available. Legacy alias: load_capsule.
- **prune_context** — Compress verbose tool output with TokenPak to reduce token count.
- **journal_read** — Read TokenPak session-journal entries (omit session_id to list sessions).
- **journal_write** — Save an important decision, milestone, or note to the TokenPak journal for future sessions.
- **session_info** — Get TokenPak companion status and configuration.

For prior work, retrieve before answering. Prefer available native memory;
otherwise batch journal/Paks. Persist each fact once.

The TokenPak companion estimates cost, enforces the budget, and journals each
prompt automatically via hooks — do not call tools for routine accounting;
every tool call re-sends the conversation. Compressed context it injects is
wrapped in `[PAK ...]` envelopes.
"""


def main(args: list[str] | None = None) -> int:
    """Entry point for ``tokenpak claude``."""
    args = args if args is not None else sys.argv[1:]

    config = CompanionConfig.from_env()
    config.profile_overrides()

    # Ensure journal dir and fixed run dir exist
    config.journal_dir.mkdir(parents=True, exist_ok=True)
    config.run_dir.mkdir(parents=True, exist_ok=True)

    # Generate config files at fixed location (AC5: ~/.tokenpak/companion/run/)
    mcp_config_path = _write_mcp_config(config)
    settings_path = _write_settings(config)
    prompt_path = _write_system_prompt(config)

    _TEAL = Color.TEAL
    _DIM = Color.DIM
    _RESET = Color.RESET

    mode = config.profile.capitalize()
    budget = f"${config.budget_daily_usd:.2f}/day" if config.budget_daily_usd > 0 else "Unlimited"

    # Route through tokenpak proxy for compression/caching/dedup.
    # Auto-detect if proxy is running when no explicit proxy_url is set.
    env = os.environ.copy()

    # Bare mode: strip Claude Code native context layers so an external
    # gateway (e.g. OpenClaw) can inject its own tools/history/memory.
    if config.bare:
        env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        env["CLAUDE_CODE_SKIP_PROMPT_HISTORY"] = "1"

    # Captured before any override: the child inherits a copy of this
    # environment, so a base URL already exported here routes Claude even when
    # TokenPak selects no proxy of its own.  Reporting only TokenPak's own
    # selection would understate where traffic actually goes.
    inherited_base_url = env.get("ANTHROPIC_BASE_URL")

    proxy_url = config.proxy_url
    if not proxy_url:
        default_proxy = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
        try:
            import httpx

            resp = httpx.get(f"{default_proxy}/health", timeout=1.0)
            if resp.status_code == 200:
                proxy_url = default_proxy
        except Exception:
            pass
    if proxy_url:
        env["ANTHROPIC_BASE_URL"] = proxy_url

    # Print styled startup banner
    from tokenpak.cli.commands.status import MEME_LINES, _get_version

    meme = random.choice(MEME_LINES)
    version = _get_version()  # dynamic, e.g. "v1.8.0" (single source: tokenpak.__version__)

    print(file=sys.stderr)
    bare_tag = " \u2022 Bare: ON" if config.bare else ""
    print(f"  \U0001f4e6 Token{_TEAL}Pak{_RESET} Claude Companion", file=sys.stderr)
    print(f"     {_DIM}TokenPak {version}{_RESET}", file=sys.stderr)
    print(
        f"     {_DIM}Ready \u2022 Mode: {mode} \u2022 Budget: {budget}{bare_tag}{_RESET}",
        file=sys.stderr,
    )
    if proxy_url:
        print(f"     {_DIM}Proxy active \u2192 {proxy_url}{_RESET}", file=sys.stderr)
    elif inherited_base_url:
        # TokenPak selected nothing, but the child is still routed. Saying
        # nothing here reads as a direct connection.
        print(
            f"     {_DIM}Routed by inherited ANTHROPIC_BASE_URL \u2192 "
            f"{inherited_base_url} (not selected by TokenPak){_RESET}",
            file=sys.stderr,
        )
    print(file=sys.stderr)
    print(f"     {_DIM}{meme}{_RESET}", file=sys.stderr)
    print(file=sys.stderr)

    # Prefix session name with 📦 so tokenpak sessions are visually distinct
    # in terminal tabs. If the user provided --name/-n, prefix their value;
    # otherwise inject a default name that fits the terminal. The resolved
    # label is handed to the SessionStart hook so it restores this exact
    # label after /clear instead of keeping a copy of its own.
    args, session_label = _resolve_session_name(args)
    _write_session_title(config, session_label)

    # Build claude command
    claude_args = ["claude"]

    if config.mcp_enabled:
        claude_args.extend(["--mcp-config", mcp_config_path])

    if config.bare:
        # Bare mode: skip system prompt, settings/hooks overlay, and bypass
        # permissions — the external gateway (OpenClaw) owns those layers.
        claude_args.append("--dangerously-skip-permissions")
    else:
        claude_args.extend(["--append-system-prompt-file", prompt_path])
        claude_args.extend(["--settings", settings_path])

    # Pass through any user-provided args
    claude_args.extend(args)

    # Per-client launcher permission default — state only; see the section
    # near the top of this module for the bare-mode contrast.
    launcher_mode, state_warning = _launcher_mode_state()
    if state_warning:
        print(
            f"tokenpak WARNING: invalid launcher permission state: {state_warning}; using inherit.",
            file=sys.stderr,
        )
    claude_args, mode_flags, skip_reason = _apply_launcher_mode(
        claude_args,
        launcher_mode,
    )
    launcher_banner = _launcher_mode_banner(
        launcher_mode,
        mode_flags,
        skip_reason,
    )
    if launcher_banner:
        print(launcher_banner, file=sys.stderr)

    # Exec into claude — replaces this process. The session title is owned by
    # Claude Code natively: the branded launch name is passed via ``--name``
    # (see _prefix_session_name) — Claude Code uses it for the title bar,
    # session picker, and terminal title — and the UserPromptSubmit hook
    # renames it to a prompt-derived title after the first turn via the
    # native ``sessionTitle`` field. We never emit OSC-0 escapes manually;
    # Claude Code repaints the title on its own render loop and would clobber
    # them (this was the root cause of the abandoned OSC-0 tab-title attempt).
    os.execvpe("claude", claude_args, env)

    # Only reached if exec fails
    print("tokenpak: failed to launch claude", file=sys.stderr)
    return 1


_SESSION_PREFIX = "\U0001f4e6"  # 📦
# Session labels are PLAIN TEXT by design. The label travels as argv data
# (``--name``) and is rendered by the host CLI, which owns that surface and
# sanitizes control bytes in it — a 2026-08 host update began displaying
# embedded SGR sequences as literal ``[38;2;…m`` text in the chat header.
# Styling belongs only on streams this process writes to a TTY itself (the
# stderr banner above), never inside data handed to another program to
# display. The ``SessionStart`` hook re-emits this exact string after a
# session is re-created; it reads it from the run dir rather than keeping
# its own copy, so this stays the only definition.
_DEFAULT_SESSION_LABEL = f"{_SESSION_PREFIX} TokenPak Claude Companion"
# Shorter fallback for narrow terminals.
_SHORT_SESSION_LABEL = f"{_SESSION_PREFIX} TokenPak"
# Columns the host CLI spends on its own chrome around the label (padding
# plus the minimum rule on either side). Measured against the rendered
# header, with headroom: below this the header wraps and every row after it
# is laid out against the wrong width.
_LABEL_CHROME_COLUMNS = 10

_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def _display_width(text: str) -> int:
    """Columns ``text`` occupies once styling is stripped.

    Wide and fullwidth characters count as two — the same assumption host
    CLIs make — so a terminal that renders them narrower only ever leaves us
    with more room than we reserved, never less.
    """
    plain = _SGR_RE.sub("", text)
    width = 0
    for ch in plain:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _terminal_columns() -> int:
    """Best available terminal width, defaulting to the conventional 80."""
    try:
        return shutil.get_terminal_size(fallback=(80, 24)).columns
    except Exception:
        return 80


def _session_label_for_width(columns: int | None = None) -> str | None:
    """Widest branded label that fits, or ``None`` when none does.

    The host CLI does not truncate an over-long session name — it wraps it,
    which makes the header taller than it accounts for and knocks every row
    below it onto the wrong line. So we pick a label that fits, or pass no
    name at all and let the host use its own default.
    """
    if columns is None:
        columns = _terminal_columns()
    for label in (_DEFAULT_SESSION_LABEL, _SHORT_SESSION_LABEL):
        if _display_width(label) + _LABEL_CHROME_COLUMNS <= columns:
            return label
    return None


def _resolve_session_name(args: list[str]) -> tuple[list[str], str | None]:
    """Prefix the Claude Code session name with 📦.

    Handles ``--name VALUE``, ``-n VALUE``, and ``--name=VALUE`` forms.
    If no name flag is present, injects the widest branded label that fits
    the current terminal, and injects nothing when even the short form would
    overflow.

    Returns a new argv list (never mutates the input) alongside the label
    that ended up in effect, so the ``SessionStart`` hook can restore that
    same label — a user-supplied ``--name`` included — rather than assuming
    the branded default.
    """
    args = list(args)  # shallow copy
    for i, arg in enumerate(args):
        if arg in ("--name", "-n") and i + 1 < len(args):
            args[i + 1] = f"{_SESSION_PREFIX} {args[i + 1]}"
            return args, args[i + 1]
        if arg.startswith("--name="):
            _, val = arg.split("=", 1)
            label = f"{_SESSION_PREFIX} {val}"
            args[i] = f"--name={label}"
            return args, label
    # No name flag found — inject the branded label when it fits.
    default_label = _session_label_for_width()
    if default_label is not None:
        args.extend(["--name", default_label])
    return args, default_label


def _prefix_session_name(args: list[str]) -> list[str]:
    """Argv-only view of :func:`_resolve_session_name`."""
    return _resolve_session_name(args)[0]


def _write_mcp_config(config: CompanionConfig) -> str:
    """Write the MCP server configuration to fixed run_dir."""
    mcp_data = {
        "mcpServers": {
            "tokenpak-companion": {
                "type": "stdio",
                "command": sys.executable,
                # -P keeps the launch directory off sys.path so a ``tokenpak``
                # dir/symlink in the cwd can't shadow the installed package
                # (which would resolve it as a namespace package and drop
                # ``__version__``, crashing the server on import).
                "args": ["-P", "-m", "tokenpak.companion.mcp.server"],
            }
        }
    }
    path = config.run_dir / "mcp.json"
    path.write_text(json.dumps(mcp_data, indent=2))
    return str(path)


def _write_session_title(config: CompanionConfig, label: str | None) -> str:
    """Write the ``SessionStart`` payload the label hook prints.

    Generating this from the Python constant is what keeps the shell hook
    from carrying a second, hand-copied copy of the label. When no label
    fits the terminal, the file is removed so the hook emits nothing and the
    host keeps its own default.
    """
    path = config.run_dir / "session_title.json"
    if label is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return str(path)
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "sessionTitle": label,
        }
    }
    # ensure_ascii keeps any non-ASCII byte in a user-supplied --name value
    # in escaped form, which is always valid inside a JSON string.
    path.write_text(json.dumps(payload, ensure_ascii=True))
    return str(path)


def _write_settings(config: CompanionConfig) -> str:
    """Write the settings overlay with hook configuration and permissions.

    Claude Code's ``--settings <file>`` argument replaces the user-level
    settings at ``~/.claude/settings.json`` wholesale — it does NOT merge.
    So a minimal overlay file strips everything the user carefully
    configured: allowed directories, custom permissions, attribution
    defaults, effort level, etc. In particular, workspace-scoped users
    rely on ``permissions.additionalDirectories`` to reach configured
    workspace directories from their CWD. Preserve those settings for
    inherited-permission launches and so a later explicit permission mode
    does not unexpectedly change the user's configured workspace scope.

    Load the user's ``~/.claude/settings.json`` as the base and layer the
    companion's MCP permission + pre-send hook on top. Falls back to a
    minimal dict when the user has no global settings.

    Persistent top-HR session label via ``SessionStart`` hook
    ---------------------------------------------------------
    The launcher passes ``--name "<styled label>"`` at startup, painting
    ``📦 TokenPak Claude Companion`` (white ``📦 Token``, accent ``Pak``,
    muted ``Claude Companion``) over a solid fill so every glyph keeps
    contrast against the host's own header chrome. The label is only
    passed when it fits the current terminal — see
    :func:`_session_label_for_width`. But ``--name`` is per-session:
    ``/clear`` creates a *new* session (new ``session_id``) and the new
    session inherits no name — the top-HR reverts to default white/gray
    chrome with no branding.

    Claude Code's ``SessionStart`` hook fires on session-creation
    events (``startup``, ``clear``, ``resume``, ``compact``). When a
    hook emits ``hookSpecificOutput.sessionTitle``, the TUI uses that
    string for the new session's display label. We register a tiny
    bash hook (``hooks/session_start_name.sh``) so the label — including
    its styling — is reasserted after every session re-creation.

    The hook does not carry its own copy of the label. :func:`main`
    writes the resolved label to ``<run_dir>/session_title.json`` before
    exec and the hook simply prints that file, so the escape sequences
    have exactly one definition (in ``_formatting.colors``) and the two
    surfaces cannot drift. Escapes are stored as JSON ``\\u001b``
    literals — real ESC bytes are invalid in JSON strings — and decoded
    back to ESC by the consumer's parser. When the file is absent the
    hook stays silent and the host keeps its own default label.

    The terminal tab title is left to Claude Code, which repaints it on
    its own render loop; we never emit OSC-0 ourselves.

    User overrides win: only injects when the user has not configured
    a ``SessionStart`` entry in their global settings.
    """
    # Prefer the bash hook (~30ms) when available; fall back to the
    # Python hook (~400ms) when only the .py is installed. When neither
    # exists, hook_cmd stays None and the UserPromptSubmit entry is
    # skipped below — avoids the 2026-04-18 regression where Claude Code
    # logged "bash: ...: No such file or directory" on every prompt
    # after the .sh file was stripped from a host.
    hooks_dir = Path(__file__).parent / "hooks"
    hook_sh = hooks_dir / "pre_send.sh"
    hook_py = hooks_dir / "pre_send.py"
    if hook_sh.is_file():
        hook_cmd = f"bash {hook_sh}"
    elif hook_py.is_file():
        # -P keeps a sibling tokenpak/ directory in the user's cwd from
        # shadowing the installed package when the bash hook is unavailable.
        hook_cmd = f"python3 -P {hook_py}"
    else:
        hook_cmd = None

    # SessionStart hook that re-emits the session label after /clear.
    # Skipped when the bundled script is missing on this host (same
    # defensive pattern as pre_send.sh above). The payload path is passed
    # explicitly so the hook never has to re-derive the run dir.
    session_name_hook = hooks_dir / "session_start_name.sh"
    session_title_path = config.run_dir / "session_title.json"
    session_name_cmd = (
        f"bash {shlex.quote(str(session_name_hook))} {shlex.quote(str(session_title_path))}"
        if session_name_hook.is_file()
        else None
    )

    settings: dict[str, Any] = {}
    user_settings_path = Path.home() / ".claude" / "settings.json"
    if user_settings_path.is_file():
        try:
            settings = json.loads(user_settings_path.read_text())
        except Exception:
            settings = {}
    if not config.hooks_enabled:
        settings.pop("hooks", None)

    # Ensure permissions.allow includes the companion's MCP glob
    permissions = settings.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])
    companion_glob = "mcp__tokenpak-companion__*"
    if companion_glob not in allow:
        allow.append(companion_glob)

    # Auto-add common workspace dirs to additionalDirectories when the
    # user hasn't configured them. Applies to multi-host setups whose
    # user-level ``~/.claude/settings.json`` is bare (e.g. ``{env: {...}}``
    # only) — without this, workspace-scoped sessions can't reach their
    # vault checkout or any operator-state directory, and every session
    # trips the sandbox. Only adds dirs that actually exist on this host
    # — no phantom paths. Operators who need additional candidate dirs
    # beyond ``~/vault`` can list them (absolute paths or names relative to
    # ``$HOME``) in the ``TOKENPAK_COMPANION_EXTRA_DIRS`` environment variable.
    # Entries are separated by the platform path separator (``:`` on POSIX,
    # ``;`` on Windows), matching the convention used by ``$PATH``.
    add_dirs = permissions.setdefault("additionalDirectories", [])
    candidates: list[Path] = [Path.home() / "vault"]
    extra = os.environ.get("TOKENPAK_COMPANION_EXTRA_DIRS", "")
    for entry in (s.strip() for s in extra.split(os.pathsep) if s.strip()):
        path = Path(entry)
        candidates.append(path if path.is_absolute() else Path.home() / entry)
    for candidate in candidates:
        if candidate.is_dir():
            candidate_str = str(candidate)
            if candidate_str not in add_dirs:
                add_dirs.append(candidate_str)

    # Install pre-send hook — companion-owned for this launch context.
    # Replaces any existing UserPromptSubmit entry (companion hooks are
    # authoritative here; user-level hooks would conflict with budget
    # gating + journal write-through).
    if config.hooks_enabled and hook_cmd is not None:
        hooks = settings.setdefault("hooks", {})
        hooks["UserPromptSubmit"] = [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd,
                    }
                ],
            }
        ]

    # Install SessionStart hook — restores the branded top-HR label
    # after /clear. Only injected when the user has not configured
    # their own SessionStart hook (their override wins). Unlike the
    # UserPromptSubmit hook above, this is purely cosmetic and never
    # competes with user logic on the same matcher.
    if config.hooks_enabled and session_name_cmd is not None:
        hooks = settings.setdefault("hooks", {})
        if "SessionStart" not in hooks:
            hooks["SessionStart"] = [
                {
                    "matcher": "clear",
                    "hooks": [
                        {
                            "type": "command",
                            "command": session_name_cmd,
                        }
                    ],
                }
            ]

    path = config.run_dir / "settings.json"
    path.write_text(json.dumps(settings, indent=2))
    return str(path)


def _write_system_prompt(config: CompanionConfig) -> str:
    """Write the prompt fragment, adding style guidance only for lean."""
    path = config.run_dir / "companion-prompt.md"
    path.write_text(_SYSTEM_PROMPT + _style.directive(config.style))
    return str(path)
