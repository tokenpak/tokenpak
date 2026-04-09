"""tokenpak/agent/cli/commands/setup.py

``tokenpak setup`` — first-time client configuration wizard
============================================================
Detects installed LLM clients and writes (or prints) the configuration
needed to route them through the TokenPak proxy.

Providers handled
-----------------
- **Claude Code**: writes ``~/.claude/settings.json`` (env.ANTHROPIC_BASE_URL).
- **OpenAI SDK**: prints ``export OPENAI_BASE_URL`` snippet (cannot write env vars).
- **Google AI SDK**: prints ``export GOOGLE_AI_BASE_URL`` snippet.

Constraints (AC-1.8)
---------------------
- NEVER writes API keys or credentials — only proxy URLs.
- Backs up any modified file before overwriting.
- Asks for confirmation before writing; ``--yes`` skips for CI/automation.
- Idempotent: re-running when already configured prints "already configured" and exits.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROXY_URL = "http://localhost:8766"
OPENAI_PROXY_URL = f"{PROXY_URL}/v1"

# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def detect_claude_code() -> bool:
    """Return True if ~/.claude/settings.json exists (Claude Code is installed)."""
    return _claude_settings_path().exists()


def detect_openai() -> bool:
    """Return True if the ``openai`` package is importable."""
    import importlib.util

    return importlib.util.find_spec("openai") is not None


def detect_google() -> bool:
    """Return True if ``google.generativeai`` is importable."""
    import importlib.util

    return importlib.util.find_spec("google.generativeai") is not None


# ---------------------------------------------------------------------------
# Configuration writers
# ---------------------------------------------------------------------------


def configure_claude_code(yes: bool) -> bool:
    """Write ANTHROPIC_BASE_URL into ~/.claude/settings.json.

    Returns True if the file was actually modified.
    """
    settings_path = _claude_settings_path()

    # Load existing settings or start fresh dict
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}
    else:
        existing = {}

    # Idempotency check
    current_url = existing.get("env", {}).get("ANTHROPIC_BASE_URL", "")
    if current_url == PROXY_URL:
        print(f"  ✓ Claude Code already configured (ANTHROPIC_BASE_URL={PROXY_URL})")
        return False

    # Describe what we'll do
    if current_url:
        print(
            f"  Claude Code: ANTHROPIC_BASE_URL is currently '{current_url}'\n"
            f"  → Will update to '{PROXY_URL}' in {settings_path}"
        )
    else:
        print(
            f"  Claude Code: will add ANTHROPIC_BASE_URL={PROXY_URL}\n"
            f"               into {settings_path}"
        )

    # Confirm (skip when --yes)
    if not yes:
        try:
            resp = input("  Write? [Y/n]: ").strip().lower()
        except EOFError:
            resp = ""
        if resp in ("n", "no"):
            print("  Skipped.")
            return False

    # Backup existing file
    if settings_path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = settings_path.with_suffix(f".bak.{ts}")
        shutil.copy2(settings_path, backup)
        print(f"  Backup: {backup}")
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)

    # Write (URL only — no credentials)
    existing.setdefault("env", {})["ANTHROPIC_BASE_URL"] = PROXY_URL
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"  ✓ Written: {settings_path}")
    return True


def print_openai_snippet() -> None:
    """Print the one-liner needed to route OpenAI SDK through tokenpak."""
    print(f"  OpenAI SDK detected — add this to your shell config or .env file:")
    print(f"    export OPENAI_BASE_URL={OPENAI_PROXY_URL}")


def print_google_snippet() -> None:
    """Print the one-liner needed to route Google AI SDK through tokenpak."""
    print(f"  Google AI SDK detected — add this to your shell config or .env file:")
    print(f"    export GOOGLE_AI_BASE_URL={PROXY_URL}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_setup_cmd(args) -> None:
    """Entry point for ``tokenpak setup``."""
    yes: bool = getattr(args, "yes", False)

    print("TokenPak Setup Wizard")
    print("=" * 40)
    print(f"Proxy address: {PROXY_URL}")
    print()

    detected: list[str] = []

    # ── Claude Code ──────────────────────────────────────────────────────────
    if detect_claude_code():
        detected.append("claude-code")
        print("Detected: Claude Code  (~/.claude/settings.json)")
        configure_claude_code(yes)
        print()

    # ── OpenAI ───────────────────────────────────────────────────────────────
    if detect_openai():
        detected.append("openai")
        openai_url = os.environ.get("OPENAI_BASE_URL", "")
        print("Detected: OpenAI SDK")
        if openai_url == OPENAI_PROXY_URL:
            print(f"  ✓ OPENAI_BASE_URL already set to {OPENAI_PROXY_URL}")
        else:
            print_openai_snippet()
        print()

    # ── Google AI ─────────────────────────────────────────────────────────────
    if detect_google():
        detected.append("google")
        google_url = os.environ.get("GOOGLE_AI_BASE_URL", "")
        print("Detected: Google AI SDK")
        if google_url == PROXY_URL:
            print(f"  ✓ GOOGLE_AI_BASE_URL already set to {PROXY_URL}")
        else:
            print_google_snippet()
        print()

    # ── Nothing detected ─────────────────────────────────────────────────────
    if not detected:
        print("No recognized LLM clients detected.")
        print()
        print("Supported clients and how to configure them:")
        print(
            f"  Claude Code  → install Claude Code; re-run `tokenpak setup` to\n"
            f"                 auto-write ~/.claude/settings.json"
        )
        print(
            f"  OpenAI SDK   → pip install openai\n"
            f"                 then: export OPENAI_BASE_URL={OPENAI_PROXY_URL}"
        )
        print(
            f"  Google AI    → pip install google-generativeai\n"
            f"                 then: export GOOGLE_AI_BASE_URL={PROXY_URL}"
        )
        print()

    print("Setup complete. Run `tokenpak serve` and your client is now using tokenpak.")
