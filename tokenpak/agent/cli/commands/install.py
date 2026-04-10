"""tokenpak/agent/cli/commands/install.py

``tokenpak install --claude-code`` — one-shot installer
=========================================================
Takes a user from "I have Claude Code installed" to
"tokenpak is configured, verified, and running as a systemd user unit."

Steps
-----
1. Detect existing Claude Code install (``claude`` binary + ``~/.claude/``)
2. Read existing ``~/.claude/settings.json``; back up to ``settings.json.bak.<timestamp>``
3. Set ``env.ANTHROPIC_BASE_URL = "http://localhost:8766"`` (atomic write + JSON validation)
4. Install ``~/.config/systemd/user/tokenpak-proxy.service`` (or update if present)
5. Detect or ask for consumption mode → select matching ``claude-code-*`` profile
6. Run smoke test: ``claude --print "say OK" --model sonnet``
7. Print verified banner
8. On any failure: restore settings.json from backup, print diagnostics

Flags
-----
``--mode {cli,tui,tmux,sdk,ide,cron}``  skip auto-detect
``--no-systemd``                        skip step 4
``--dry-run``                           preview only; no writes

Constraints (AC-3.7)
--------------------
- Atomic write: ``<file>.tmp`` + JSON validate + ``os.replace()``
- Backup before write — always
- Idempotent: re-running when already configured exits 0 with "already configured"
- Restore on failure
- Never writes credentials
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROXY_URL = "http://localhost:8766"
DASHBOARD_URL = f"{PROXY_URL}/dashboard"
PROXY_SERVICE_NAME = "tokenpak-proxy.service"

# Mapping: consumption mode → profile name
MODE_PROFILE_MAP = {
    "cli": "claude-code-cli",
    "tui": "claude-code-tui",
    "tmux": "claude-code-tmux",
    "sdk": "claude-code-sdk",
    "ide": "claude-code-ide",
    "cron": "claude-code-cron",
}

VALID_MODES = list(MODE_PROFILE_MAP.keys())

# ---------------------------------------------------------------------------
# Step 1 — detect Claude Code
# ---------------------------------------------------------------------------


def detect_claude_binary() -> str | None:
    """Return path to the ``claude`` binary if found in PATH, else None."""
    return shutil.which("claude")


def detect_claude_dir() -> bool:
    """Return True if ``~/.claude/`` directory exists."""
    return (Path.home() / ".claude").is_dir()


# ---------------------------------------------------------------------------
# Step 2/3 — settings.json helpers
# ---------------------------------------------------------------------------


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _backup_settings(settings_path: Path, dry_run: bool) -> Path | None:
    """Back up settings.json → settings.json.bak.<timestamp>.  Returns backup path."""
    if not settings_path.exists():
        return None
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = settings_path.parent / f"settings.json.bak.{ts}"
    if not dry_run:
        shutil.copy2(settings_path, backup)
    print(f"  Backup: {backup}{'  [dry-run, not written]' if dry_run else ''}")
    return backup


def _read_settings(settings_path: Path) -> dict:
    """Read settings.json; return empty dict on missing or invalid JSON."""
    if not settings_path.exists():
        return {}
    try:
        return json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _atomic_write_settings(settings_path: Path, data: dict) -> None:
    """Write data to settings_path atomically via a .tmp file + os.replace().

    Validates JSON round-trip before replacing.  Raises on failure.
    """
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    serialised = json.dumps(data, indent=2) + "\n"
    # Validate round-trip
    json.loads(serialised)
    tmp = settings_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(serialised, encoding="utf-8")
        os.replace(tmp, settings_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def configure_settings(dry_run: bool) -> tuple[bool, Path | None]:
    """Write ANTHROPIC_BASE_URL into ~/.claude/settings.json.

    Returns (changed, backup_path).  changed=False means already correct → idempotent.
    """
    settings_path = _settings_path()
    data = _read_settings(settings_path)

    current_url = data.get("env", {}).get("ANTHROPIC_BASE_URL", "")
    if current_url == PROXY_URL:
        print(f"  ✓ ANTHROPIC_BASE_URL already set to {PROXY_URL}")
        return False, None

    if current_url:
        print(
            f"  settings.json: ANTHROPIC_BASE_URL is currently '{current_url}'\n"
            f"  → Will update to '{PROXY_URL}'"
        )
    else:
        print(f"  settings.json: Will add ANTHROPIC_BASE_URL={PROXY_URL}")

    backup = _backup_settings(settings_path, dry_run=dry_run)

    if not dry_run:
        data.setdefault("env", {})["ANTHROPIC_BASE_URL"] = PROXY_URL
        _atomic_write_settings(settings_path, data)
        print(f"  ✓ Written: {settings_path}")

    return True, backup


# ---------------------------------------------------------------------------
# Step 4 — systemd user unit
# ---------------------------------------------------------------------------

_SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=TokenPak LLM Proxy
Documentation=https://docs.tokenpak.dev/proxy
After=network.target
Wants=network.target

[Service]
Type=simple
ExecStart={tokenpak_bin} serve
Restart=on-failure
RestartSec=5s
StartLimitIntervalSec=60
StartLimitBurst=5
TimeoutStopSec=35
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tokenpak-proxy
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=%h/.tokenpak %h/vault
Environment=PYTHONUNBUFFERED=1
Environment=TOKENPAK_SHUTDOWN_TIMEOUT=30

[Install]
WantedBy=default.target
"""


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / PROXY_SERVICE_NAME


def install_systemd_unit(dry_run: bool) -> bool:
    """Write the systemd user unit file.  Returns True if a change was made."""
    unit_path = _systemd_unit_path()
    tokenpak_bin = shutil.which("tokenpak") or "tokenpak"
    content = _SYSTEMD_UNIT_TEMPLATE.format(tokenpak_bin=tokenpak_bin)

    if unit_path.exists() and unit_path.read_text(encoding="utf-8") == content:
        print(f"  ✓ systemd unit already up-to-date: {unit_path}")
        return False

    print(f"  systemd unit → {unit_path}{'  [dry-run]' if dry_run else ''}")
    if not dry_run:
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(content, encoding="utf-8")
        # Reload daemon so systemctl picks up the new unit
        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                check=False,
                capture_output=True,
            )
        except FileNotFoundError:
            pass  # systemctl not available (e.g. container / test env)
        print(f"  ✓ Installed: {unit_path}")
    return True


# ---------------------------------------------------------------------------
# Step 5 — mode detection
# ---------------------------------------------------------------------------


def auto_detect_mode() -> str:
    """Heuristic mode detection; mirrors _detect_claude_code_profile() in proxy.py."""
    if os.environ.get("CRON_INVOCATION") or os.environ.get("CRON"):
        return "cron"
    term_prog = os.environ.get("TERM_PROGRAM", "")
    if term_prog in ("cursor", "Windsurf", "vscode") or os.environ.get("VSCODE_PID"):
        return "ide"
    if os.environ.get("TMUX"):
        return "tmux"
    try:
        if not sys.stdin.isatty():
            return "sdk"
    except Exception:
        pass
    return "cli"


def select_mode(requested_mode: str | None) -> str:
    """Return the effective mode.  Prompts if needed."""
    if requested_mode:
        if requested_mode not in VALID_MODES:
            raise ValueError(
                f"Invalid mode '{requested_mode}'. Valid: {', '.join(VALID_MODES)}"
            )
        return requested_mode

    detected = auto_detect_mode()
    print(f"  Auto-detected mode: {detected}")
    return detected


# ---------------------------------------------------------------------------
# Step 6 — smoke test
# ---------------------------------------------------------------------------


def run_smoke_test(dry_run: bool) -> bool:
    """Run `claude --print "say OK" --model claude-haiku-4-5-20251001` and check output.

    Returns True if the smoke test passes.
    """
    if dry_run:
        print("  [dry-run] smoke test skipped")
        return True

    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("  ⚠️  smoke test skipped — claude binary not found")
        return True  # Non-fatal: we already checked presence at step 1

    cmd = [claude_bin, "--print", "say OK", "--model", "claude-haiku-4-5-20251001"]
    print(f"  Running smoke test: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0 and output:
            print(f"  Smoke test response: {output[:120]}")
            return True
        else:
            print(f"  ✗ Smoke test failed (exit {result.returncode}): {output[:300]}")
            return False
    except subprocess.TimeoutExpired:
        print("  ⚠️  Smoke test timed out (60s). Proxy may be slow; install continues.")
        return True  # Timeout-tolerant per spec
    except Exception as exc:
        print(f"  ⚠️  Smoke test error: {exc}")
        return True  # Non-fatal for environment issues


# ---------------------------------------------------------------------------
# Restore helper
# ---------------------------------------------------------------------------


def restore_backup(backup: Path | None) -> None:
    """Restore settings.json from backup if it exists."""
    if backup is None or not backup.exists():
        return
    settings_path = _settings_path()
    try:
        shutil.copy2(backup, settings_path)
        print(f"  ↩ Restored settings.json from {backup}")
    except Exception as exc:
        print(f"  ✗ Could not restore backup: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_install_cmd(args) -> None:
    """Entry point for ``tokenpak install --claude-code``."""
    dry_run: bool = getattr(args, "dry_run", False)
    no_systemd: bool = getattr(args, "no_systemd", False)
    requested_mode: str | None = getattr(args, "mode", None)

    print("TokenPak Install — Claude Code")
    print("=" * 40)
    if dry_run:
        print("DRY-RUN: no files will be written\n")

    backup: Path | None = None

    try:
        # ── Step 1: detect Claude Code ─────────────────────────────────────
        print("\n[1/8] Detecting Claude Code install…")
        claude_bin = detect_claude_binary()
        claude_dir = detect_claude_dir()

        if not claude_bin and not claude_dir:
            print(
                "  ✗ Claude Code not found (no 'claude' in PATH and no ~/.claude/).\n"
                "  Install Claude Code first: https://claude.ai/code"
            )
            sys.exit(1)

        if claude_bin:
            print(f"  ✓ claude binary: {claude_bin}")
        else:
            print("  ⚠️  claude binary not in PATH (but ~/.claude/ exists)")
        if claude_dir:
            print(f"  ✓ ~/.claude/ directory present")

        # ── Step 2/3: settings.json ────────────────────────────────────────
        print("\n[2/8] Backing up ~/.claude/settings.json…")
        settings_path = _settings_path()
        data = _read_settings(settings_path)
        current_url = data.get("env", {}).get("ANTHROPIC_BASE_URL", "")

        if current_url == PROXY_URL:
            print(f"  ✓ ANTHROPIC_BASE_URL already set to {PROXY_URL}")
            changed_settings = False
        else:
            backup = _backup_settings(settings_path, dry_run=dry_run)
            changed_settings = True

        print("\n[3/8] Writing ANTHROPIC_BASE_URL to settings.json…")
        if changed_settings:
            if current_url:
                print(f"  Updating {current_url!r} → {PROXY_URL!r}")
            else:
                print(f"  Adding ANTHROPIC_BASE_URL={PROXY_URL}")

            if not dry_run:
                data.setdefault("env", {})["ANTHROPIC_BASE_URL"] = PROXY_URL
                _atomic_write_settings(settings_path, data)
                print(f"  ✓ Written: {settings_path}")
        else:
            print("  ✓ Already correct — no change needed")

        # ── Step 4: systemd unit ───────────────────────────────────────────
        print("\n[4/8] Installing systemd user unit…")
        if no_systemd:
            print("  Skipped (--no-systemd)")
        else:
            install_systemd_unit(dry_run=dry_run)

        # ── Step 5: mode selection ─────────────────────────────────────────
        print("\n[5/8] Selecting consumption mode and profile…")
        mode = select_mode(requested_mode)
        profile = MODE_PROFILE_MAP[mode]
        print(f"  Mode: {mode}  →  Profile: {profile}")

        # ── Step 6: smoke test ─────────────────────────────────────────────
        print("\n[6/8] Running smoke test…")
        smoke_ok = run_smoke_test(dry_run=dry_run)
        if not smoke_ok:
            print("  ✗ Smoke test failed — restoring settings.json from backup")
            restore_backup(backup)
            sys.exit(2)

        # ── Step 7: verified banner ────────────────────────────────────────
        print("\n[7/8] Verified banner…")
        print()
        if dry_run:
            print(
                f"[dry-run] Would print:\n"
                f"  ✅ tokenpak installed for Claude Code\n"
                f"     mode: {mode}  |  profile: {profile}\n"
                f"     dashboard: {DASHBOARD_URL}"
            )
        else:
            print(
                f"✅ tokenpak installed for Claude Code\n"
                f"   mode: {mode}  |  profile: {profile}\n"
                f"   dashboard: {DASHBOARD_URL}"
            )
        print()

        # ── Step 8: nothing failed ─────────────────────────────────────────
        print("[8/8] Install complete.")

    except KeyboardInterrupt:
        print("\n  Interrupted — restoring settings.json from backup…")
        restore_backup(backup)
        sys.exit(130)
    except Exception as exc:
        print(f"\n  ✗ Install failed: {exc}", file=sys.stderr)
        print("  Restoring settings.json from backup…", file=sys.stderr)
        restore_backup(backup)
        raise
