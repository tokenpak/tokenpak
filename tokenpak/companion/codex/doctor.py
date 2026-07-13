# SPDX-License-Identifier: Apache-2.0
"""End-to-end verification for ``tokenpak codex`` installation.

Run via ``tokenpak codex doctor``. Exits non-zero only if a check
FAILs; WARN rows are advisory (e.g. a migration orphan) and never affect
the exit code, so it's still safe to wire into CI or health checks.

Each check is a callable returning ``(ok: bool, detail: str)`` for the
binary PASS/FAIL contract, or ``(_WARN, detail)`` to surface an advisory
WARN row.  The module stays self-contained — no cross-cutting framework —
so adding a check is "define function, append to CHECKS list".
"""

from __future__ import annotations

import contextvars
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Callable

from ..config import CompanionConfig
from .mcp_config import SERVER_NAME
from .rates_snapshot import DEFAULT_SNAPSHOT_PATH
from .rates_snapshot import count as rates_count
from .skills_installer import (
    _configured_skill_paths,
    _default_skills_root,
    bundled_skill_names,
)

if _TYPE_CHECKING:
    from .session_home import SessionPaths

CheckFn = Callable[[], "tuple[bool | str, str]"]

# Sentinel a check returns (in the ``ok`` slot) to render an advisory
# WARN row.  Underscore-private so it stays out of the released public-API
# snapshot; ``run`` normalizes it to the "WARN" status.
_WARN = "WARN"

_ACTIVE_CODEX_HOME: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "tokenpak_codex_doctor_home", default=None
)
_ACTIVE_SESSION_MODE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tokenpak_codex_doctor_mode", default="shared"
)
_ACTIVE_RETENTION_REPORT: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "tokenpak_codex_doctor_retention", default=None
)


def _selected_codex_home(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    active = _ACTIVE_CODEX_HOME.get()
    if active is not None:
        return active
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _codex_env() -> dict[str, str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(_selected_codex_home())
    return env


def _status_of(raw: "bool | str") -> str:
    """Map a check's raw ``ok`` return into ``PASS`` / ``WARN`` / ``FAIL``.

    Checks may return ``True``/``False`` (the original binary contract) or
    the :data:`_WARN` sentinel to surface an advisory row that prints but
    does not fail the exit code.
    """
    if raw == _WARN:
        return "WARN"
    return "PASS" if raw else "FAIL"


# ── Individual checks ────────────────────────────────────────────────


def check_codex_binary() -> "tuple[bool, str]":
    path = shutil.which("codex")
    if not path:
        return False, "codex not on PATH"
    try:
        result = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"codex --version failed: {exc}"
    return result.returncode == 0, result.stdout.strip() or result.stderr.strip()


def check_hooks_feature() -> "tuple[bool, str]":
    try:
        result = subprocess.run(
            ["codex", "features", "list"],
            capture_output=True,
            env=_codex_env(),
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"codex features list failed: {exc}"
    for line in result.stdout.splitlines():
        # Format: "hooks     under development  true"
        parts = line.split()
        if parts and parts[0] == "hooks":
            state = parts[-1].lower()
            return state == "true", f"hooks={state}"
    return False, "hooks feature not found in `codex features list`"


def check_mcp_registered() -> "tuple[bool, str]":
    try:
        result = subprocess.run(
            ["codex", "mcp", "get", SERVER_NAME],
            capture_output=True,
            env=_codex_env(),
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"codex mcp get failed: {exc}"
    if result.returncode != 0:
        return False, f"{SERVER_NAME} not registered"
    return True, f"{SERVER_NAME} registered"


def check_hooks_json() -> "tuple[bool, str]":
    path = _selected_codex_home() / "hooks.json"
    if not path.exists():
        return False, f"{path} missing"
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return False, f"{path} invalid JSON: {exc}"

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False, "top-level 'hooks' is not a dict (schema mismatch)"

    missing = [e for e in ("UserPromptSubmit", "Stop") if e not in hooks]
    if missing:
        return False, f"events missing from hooks.json: {missing}"

    for event in ("UserPromptSubmit", "Stop"):
        groups = hooks.get(event, [])
        found = any(
            "tokenpak" in cmd.get("command", "")
            for group in groups
            if isinstance(group, dict)
            for cmd in group.get("hooks", [])
            if isinstance(cmd, dict)
        )
        if not found:
            return False, f"no tokenpak hook registered for {event}"

    return True, "UserPromptSubmit + Stop both wired"


def check_agents_md() -> "tuple[bool, str]":
    path = _selected_codex_home() / "AGENTS.md"
    if not path.exists():
        return False, f"{path} missing"
    content = path.read_text()
    if "# TokenPak Companion" not in content:
        return False, "TokenPak section missing from AGENTS.md"
    return True, f"{path} ({len(content)} bytes)"


def check_skills_installed() -> "tuple[bool, str]":
    # Canonical user-scope skill-discovery path Codex actually scans:
    # ``$HOME/.agents/skills`` — not the pre-L3 ``~/.codex/skills`` location.
    target = _default_skills_root()
    if not target.exists():
        return False, f"{target} missing"
    bundled = bundled_skill_names()
    missing = [name for name in bundled if not (target / name).exists()]
    if missing:
        return False, f"missing skills at {target}: {missing}"
    return True, f"{len(bundled)} skills present at {target}"


def _check_skills_config() -> "tuple[bool, str]":
    """Verify the selected home explicitly references every bundled skill."""
    if _ACTIVE_SESSION_MODE.get() == "shared":
        return True, "shared mode uses Codex's canonical user skill discovery root"
    config_path = _selected_codex_home() / "config.toml"
    if not config_path.exists():
        return False, f"{config_path} missing"
    expected = {
        _default_skills_root() / name
        for name in bundled_skill_names()
        if (_default_skills_root() / name / "SKILL.md").is_file()
    }
    try:
        configured = set(_configured_skill_paths(config_path))
    except (OSError, ValueError) as exc:
        return False, f"{config_path} skill config invalid: {exc}"
    missing = sorted(str(path) for path in expected - configured)
    if missing:
        return False, f"selected config missing skill paths: {missing}"
    return True, f"{len(expected)} skill paths referenced by {config_path}"


def _check_skills_legacy_orphans() -> "tuple[bool | str, str]":
    """WARN when pre-L3 installs left skill trees at ``~/.codex/skills``.

    Doctor flags the orphan rather than auto-migrating: a user may have
    customized a skill in place, and a silent overwrite would clobber the
    edit.  Advisory only — a stale legacy copy shadows nothing once the
    launcher installs into the canonical ``~/.agents/skills`` path, it
    just wastes space and can confuse manual inspection.
    """
    from .skills_installer import _orphaned_legacy_skills

    orphans = _orphaned_legacy_skills()
    if not orphans:
        return True, "no legacy ~/.codex/skills orphans"
    return _WARN, (
        f"legacy skills at ~/.codex/skills: {orphans} — remove them or run "
        "`tokenpak codex --install-only` then delete the old copies"
    )


def _retention_report():
    from .session_home import inspect_isolated_homes

    cached = _ACTIVE_RETENTION_REPORT.get()
    if cached is None:
        cached = inspect_isolated_homes()
        _ACTIVE_RETENTION_REPORT.set(cached)
    return cached


def _check_orphaned_codex_homes() -> "tuple[bool | str, str]":
    report = _retention_report()
    detail = (
        f"total={len(report.homes)}, active={len(report.active)}, "
        f"orphaned={len(report.orphaned)}, protected={len(report.unsafe)}, "
        f"quarantined={len(report.quarantines)}, "
        f"inventory={'complete' if report.inventory_complete else 'incomplete'} at {report.root}"
    )
    if report.orphaned or report.unsafe or report.quarantines or not report.inventory_complete:
        return _WARN, detail
    return True, detail


def _check_codex_home_disk_usage() -> "tuple[bool | str, str]":
    from .session_home import (
        RETENTION_MAX_AGE_S,
        RETENTION_MAX_HOMES,
        RETENTION_MAX_TOTAL_BYTES,
    )

    report = _retention_report()
    used_mb = report.total_bytes / (1024 * 1024)
    cap_mb = RETENTION_MAX_TOTAL_BYTES / (1024 * 1024)
    detail = (
        f"{used_mb:.1f}/{cap_mb:.0f} MB, {len(report.homes)}/{RETENTION_MAX_HOMES} homes, "
        f"age cap={RETENTION_MAX_AGE_S // 86400} days, "
        f"inventory={'complete' if report.inventory_complete else 'incomplete'}"
    )
    if report.over_size or report.over_count or report.over_age or not report.inventory_complete:
        return _WARN, detail
    return True, detail


def check_databases() -> "tuple[bool, str]":
    config = CompanionConfig.from_env()
    journal = config.journal_dir / "journal.db"
    budget = config.journal_dir / "budget.db"
    missing = [p.name for p in (journal, budget) if not p.exists()]
    if missing:
        return False, f"missing dbs: {missing} (run `tokenpak codex --install-only`)"
    return True, f"journal.db + budget.db in {config.journal_dir}"


def check_rates_snapshot() -> "tuple[bool, str]":
    n = rates_count()
    if n == 0:
        return False, f"{DEFAULT_SNAPSHOT_PATH} missing or empty"
    if n < 10:
        return False, f"only {n} rate entries — registry load may have failed"
    return True, f"{n} model rates in snapshot"


def check_mcp_import() -> "tuple[bool, str]":
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import tokenpak.companion.mcp.server"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, "MCP server import hung"
    if result.returncode != 0:
        return False, f"import failed: {result.stderr.strip()}"
    return True, "tokenpak.companion.mcp.server importable"


def check_mcp_ping() -> "tuple[bool, str]":
    """Spawn the MCP server and send a JSON-RPC initialize. Short timeout."""
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "tokenpak-doctor", "version": "0.1.0"},
        },
    }
    payload = (json.dumps(req) + "\n").encode()

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "tokenpak.companion.mcp.server"],
            input=payload,
            capture_output=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        # Server keeps running after initialize — that's fine, we got a reply.
        stdout = (exc.stdout or b"").decode(errors="replace")
        return _parse_initialize_reply(stdout)

    stdout = proc.stdout.decode(errors="replace")
    return _parse_initialize_reply(stdout)


def _parse_initialize_reply(stdout: str) -> "tuple[bool, str]":
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = msg.get("result")
        if isinstance(result, dict):
            server_info = result.get("serverInfo", {})
            name = server_info.get("name", "")
            return True, f"MCP initialize OK (server={name or 'unnamed'})"
    return False, "no JSON-RPC response from MCP server"


# ── Runner ──────────────────────────────────────────────────────────

CHECKS: list["tuple[str, CheckFn]"] = [
    ("codex binary", check_codex_binary),
    ("hooks feature", check_hooks_feature),
    ("MCP registration", check_mcp_registered),
    ("hooks.json schema", check_hooks_json),
    ("AGENTS.md", check_agents_md),
    ("skills installed", check_skills_installed),
    ("skills selected config", _check_skills_config),
    ("skills legacy orphans", _check_skills_legacy_orphans),
    ("orphaned codex homes", _check_orphaned_codex_homes),
    ("codex home disk usage", _check_codex_home_disk_usage),
    ("storage dbs", check_databases),
    ("rates snapshot", check_rates_snapshot),
    ("MCP import", check_mcp_import),
    ("MCP initialize ping", check_mcp_ping),
]


def _run_selected(
    refresh_rates: bool = False,
    *,
    paths: "SessionPaths | None" = None,
    codex_home: Path | None = None,
    session_mode: str | None = None,
    workspace_dir: Path | None = None,
) -> int:
    """Internal selected-home doctor runner."""
    if paths is None:
        from .session_home import InvalidSessionMode, current_paths, select_paths

        try:
            if codex_home is not None:
                paths = select_paths(
                    session_mode,
                    workspace_dir=workspace_dir,
                    selected_home=codex_home,
                )
            else:
                paths = current_paths(session_mode, workspace_dir=workspace_dir)
        except (InvalidSessionMode, ValueError) as exc:
            print(f"tokenpak codex doctor: {exc}", file=sys.stderr)
            return 2

    if refresh_rates:
        from .rates_snapshot import refresh

        path = refresh()
        print(f"refreshed rates snapshot: {path}")

    print("selected Codex paths:")
    for label, value in paths.report_rows():
        print(f"  {label}: {value}")
    print()

    results: list["tuple[str, str, str]"] = []
    active = _ACTIVE_CODEX_HOME.set(paths.home)
    active_mode = _ACTIVE_SESSION_MODE.set(paths.mode)
    retention = _ACTIVE_RETENTION_REPORT.set(None)
    try:
        for name, fn in CHECKS:
            try:
                raw, detail = fn()
            except Exception as exc:
                raw, detail = False, f"check raised: {exc.__class__.__name__}: {exc}"
            results.append((name, _status_of(raw), detail))
    finally:
        _ACTIVE_SESSION_MODE.reset(active_mode)
        _ACTIVE_CODEX_HOME.reset(active)
        _ACTIVE_RETENTION_REPORT.reset(retention)

    name_width = max(len(n) for n, _, _ in results)
    any_fail = False
    for name, status, detail in results:
        print(f"  [{status}] {name.ljust(name_width)}  {detail}")
        if status == "FAIL":
            any_fail = True

    passed = sum(1 for _, s, _ in results if s == "PASS")
    warned = sum(1 for _, s, _ in results if s == "WARN")
    failed = sum(1 for _, s, _ in results if s == "FAIL")
    total = len(results)
    parts = [f"{passed}/{total} checks passed"]
    if warned:
        parts.append(f"{warned} warning{'s' if warned != 1 else ''}")
    if failed:
        parts.append(f"{failed} failed")
    print()
    print(", ".join(parts))
    return 1 if any_fail else 0


def run(refresh_rates: bool = False) -> int:
    """Run all checks, print a report, return an exit code."""
    return _run_selected(refresh_rates)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    refresh_rates = "--refresh-rates" in args
    return run(refresh_rates=refresh_rates)


if __name__ == "__main__":
    raise SystemExit(main())
