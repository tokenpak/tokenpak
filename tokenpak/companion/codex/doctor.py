# SPDX-License-Identifier: Apache-2.0
"""End-to-end verification for ``tokenpak codex`` installation.

Run via ``tokenpak codex doctor``. Exits 0 only if every check passes,
so it's safe to wire into CI or health checks.

Each check is a callable returning ``(ok: bool, detail: str)``. The
module stays self-contained — no cross-cutting framework — so adding a
check is "define function, append to CHECKS list".
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from ..config import CompanionConfig
from .mcp_config import SERVER_NAME
from .skills_installer import bundled_skill_names
from .rates_snapshot import DEFAULT_SNAPSHOT_PATH, count as rates_count


CheckFn = Callable[[], "tuple[bool, str]"]


# ── Individual checks ────────────────────────────────────────────────

def check_codex_binary() -> "tuple[bool, str]":
    path = shutil.which("codex")
    if not path:
        return False, "codex not on PATH"
    try:
        result = subprocess.run(
            ["codex", "--version"], capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"codex --version failed: {exc}"
    return result.returncode == 0, result.stdout.strip() or result.stderr.strip()


def check_hooks_feature() -> "tuple[bool, str]":
    try:
        result = subprocess.run(
            ["codex", "features", "list"], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"codex features list failed: {exc}"
    for line in result.stdout.splitlines():
        # Format: "codex_hooks     under development  true"
        parts = line.split()
        if parts and parts[0] == "codex_hooks":
            state = parts[-1].lower()
            return state == "true", f"codex_hooks={state}"
    return False, "codex_hooks feature not found in `codex features list`"


def check_mcp_registered() -> "tuple[bool, str]":
    try:
        result = subprocess.run(
            ["codex", "mcp", "get", SERVER_NAME],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"codex mcp get failed: {exc}"
    if result.returncode != 0:
        return False, f"{SERVER_NAME} not registered"
    return True, f"{SERVER_NAME} registered"


def check_hooks_json() -> "tuple[bool, str]":
    path = Path.home() / ".codex" / "hooks.json"
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
    path = Path.home() / ".codex" / "AGENTS.md"
    if not path.exists():
        return False, f"{path} missing"
    content = path.read_text()
    if "# TokenPak Companion" not in content:
        return False, "TokenPak section missing from AGENTS.md"
    return True, f"{path} ({len(content)} bytes)"


def check_skills_installed() -> "tuple[bool, str]":
    target = Path.home() / ".codex" / "skills"
    if not target.exists():
        return False, f"{target} missing"
    bundled = bundled_skill_names()
    missing = [name for name in bundled if not (target / name).exists()]
    if missing:
        return False, f"missing skills: {missing}"
    return True, f"{len(bundled)} skills present"


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
    ("codex_hooks feature", check_hooks_feature),
    ("MCP registration", check_mcp_registered),
    ("hooks.json schema", check_hooks_json),
    ("AGENTS.md", check_agents_md),
    ("skills installed", check_skills_installed),
    ("storage dbs", check_databases),
    ("rates snapshot", check_rates_snapshot),
    ("MCP import", check_mcp_import),
    ("MCP initialize ping", check_mcp_ping),
]


def run(refresh_rates: bool = False) -> int:
    """Run all checks, print a report, return an exit code."""
    if refresh_rates:
        from .rates_snapshot import refresh

        path = refresh()
        print(f"refreshed rates snapshot: {path}")

    results: list["tuple[str, bool, str]"] = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"check raised: {exc.__class__.__name__}: {exc}"
        results.append((name, ok, detail))

    name_width = max(len(n) for n, _, _ in results)
    all_ok = True
    for name, ok, detail in results:
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {name.ljust(name_width)}  {detail}")
        all_ok = all_ok and ok

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    summary = f"{passed}/{total} checks passed"
    print()
    print(summary if all_ok else f"{summary} — some checks failed")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    refresh_rates = "--refresh-rates" in args
    return run(refresh_rates=refresh_rates)


if __name__ == "__main__":
    raise SystemExit(main())
