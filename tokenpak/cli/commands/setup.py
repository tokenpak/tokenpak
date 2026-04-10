"""tokenpak.cli.commands.setup — setup wizard for tokenpak installation."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

PROXY_URL = os.environ.get("TOKENPAK_PROXY_URL", "http://127.0.0.1:8766")
OPENAI_PROXY_URL = os.environ.get("TOKENPAK_OPENAI_PROXY_URL", "http://127.0.0.1:8767")


def detect_claude_code() -> Optional[Path]:
    """Find the Claude Code settings directory."""
    p = Path.home() / ".claude"
    return p if p.exists() else None


def detect_openai() -> bool:
    """Detect if OpenAI API key is configured."""
    return bool(os.environ.get("OPENAI_API_KEY"))


def detect_google() -> bool:
    """Detect if Google API key is configured."""
    return bool(
        os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    )


def configure_claude_code(
    proxy_url: str = PROXY_URL,
    openai_proxy_url: str = OPENAI_PROXY_URL,
    claude_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Write ANTHROPIC_BASE_URL into Claude Code settings.json."""
    base = claude_dir or detect_claude_code() or Path.home() / ".claude"
    base.mkdir(parents=True, exist_ok=True)
    settings_path = base / "settings.json"
    try:
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    except Exception:
        settings = {}

    env = settings.setdefault("env", {})
    env["ANTHROPIC_BASE_URL"] = proxy_url
    if detect_openai():
        env["OPENAI_BASE_URL"] = openai_proxy_url

    with tempfile.NamedTemporaryFile("w", dir=base, delete=False, suffix=".tmp") as f:
        json.dump(settings, f, indent=2)
        tmp = f.name
    os.replace(tmp, settings_path)
    return settings


def run_setup_cmd(args) -> None:
    claude_dir = getattr(args, "claude_dir", None)
    if claude_dir:
        claude_dir = Path(claude_dir)
    configure_claude_code(claude_dir=claude_dir)


__all__ = [
    "PROXY_URL",
    "OPENAI_PROXY_URL",
    "configure_claude_code",
    "detect_claude_code",
    "detect_openai",
    "detect_google",
    "run_setup_cmd",
]
