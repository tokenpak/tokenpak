"""tokenpak.cli.commands.install — install/configure tokenpak for Claude Code."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, cast

PROXY_URL = os.environ.get("TOKENPAK_PROXY_URL", "http://127.0.0.1:8766")

MODE_PROFILE_MAP: Dict[str, str] = {
    "cli": "balanced",
    "bare": "aggressive",
    "tui": "balanced",
    "tmux": "agentic",
    "ide": "safe",
    "cron": "aggressive",
}


def _settings_path() -> Path:
    """Return path to Claude Code settings.json."""
    return Path.home() / ".claude" / "settings.json"


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "tokenpak-proxy.service"


def _read_settings() -> Dict[str, Any]:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return cast(Dict[str, Any], json.loads(p.read_text()))
    except Exception:
        return {}


def _atomic_write_settings(data: Dict[str, Any]) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=p.parent, delete=False, suffix=".tmp") as f:
        json.dump(data, f, indent=2)
        tmp = f.name
    os.replace(tmp, p)


def _backup_settings() -> Optional[Path]:
    p = _settings_path()
    if not p.exists():
        return None
    bak = p.with_suffix(".json.bak")
    shutil.copy2(p, bak)
    return bak


def restore_backup(bak: Optional[Path]) -> bool:
    if bak and bak.exists():
        shutil.copy2(bak, _settings_path())
        return True
    return False


def detect_claude_binary() -> Optional[str]:
    return shutil.which("claude")


def detect_claude_dir() -> Optional[Path]:
    p = Path.home() / ".claude"
    return p if p.exists() else None


def auto_detect_mode() -> str:
    """Detect the Claude Code consumption mode."""
    term = os.environ.get("TERM_PROGRAM", "")
    if os.environ.get("TMUX"):
        return "tmux"
    if term in ("vscode", "cursor", "windsurf"):
        return "ide"
    if not os.isatty(0) if hasattr(os, "isatty") else True:
        return "cron"
    return "cli"


def select_mode(mode: Optional[str] = None) -> str:
    return mode or auto_detect_mode()


def configure_settings(mode: str = "cli", proxy_url: str = PROXY_URL) -> Dict[str, Any]:
    settings = _read_settings()
    env = settings.setdefault("env", {})
    env["ANTHROPIC_BASE_URL"] = proxy_url
    env["TOKENPAK_PROFILE"] = MODE_PROFILE_MAP.get(mode, "balanced")
    _atomic_write_settings(settings)
    return settings


def install_systemd_unit(proxy_url: str = PROXY_URL) -> Path:
    unit = _systemd_unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(f"""[Unit]
Description=TokenPak Proxy
After=network.target

[Service]
ExecStart=tokenpak serve --port 8766
Environment=TOKENPAK_PROXY_URL={proxy_url}
Restart=on-failure

[Install]
WantedBy=default.target
""")
    return unit


def run_smoke_test(proxy_url: str = PROXY_URL) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"{proxy_url}/health", timeout=5) as r:
            return bool(r.status == 200)
    except Exception:
        return False


def run_install_cmd(args: Any) -> None:
    mode = getattr(args, "mode", None) or auto_detect_mode()
    proxy_url = getattr(args, "proxy_url", PROXY_URL) or PROXY_URL
    configure_settings(mode=mode, proxy_url=proxy_url)
    if getattr(args, "systemd", False):
        install_systemd_unit(proxy_url=proxy_url)

    # Auto-detect and configure OpenClaw if installed
    _setup_openclaw_if_present(proxy_url)


def _setup_openclaw_if_present(proxy_url: str) -> None:
    """Auto-detect OpenClaw and configure tokenpak integration."""
    try:
        from tokenpak.sdk.openclaw import detect_openclaw, setup_openclaw
    except ImportError:
        return

    if not detect_openclaw():
        return

    print("  OpenClaw detected — configuring tokenpak providers...")
    result = setup_openclaw(proxy_url=proxy_url)

    if "error" in result:
        print(f"  OpenClaw setup error: {result['error']}")
        return

    configs = result.get("configs", [])
    if not isinstance(configs, list):
        return
    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        path = cfg.get("path", "?")
        if cfg.get("error"):
            print(f"  {path}: {cfg['error']}")
            continue
        added = cfg.get("providers_added", [])
        updated = cfg.get("providers_updated", [])
        claude_code = cfg.get("claude_code_backend", False)
        bits = []
        if added:
            bits.append(f"+{len(added)} ({', '.join(added)})")
        if updated:
            bits.append(f"~{len(updated)} ({', '.join(updated)})")
        if claude_code:
            bits.append("claude-code backend")
        summary = "; ".join(bits) or "up to date"
        print(f"  {path}: {summary}")


__all__ = [
    "PROXY_URL",
    "MODE_PROFILE_MAP",
    "_atomic_write_settings",
    "_backup_settings",
    "_read_settings",
    "_settings_path",
    "_systemd_unit_path",
    "auto_detect_mode",
    "configure_settings",
    "detect_claude_binary",
    "detect_claude_dir",
    "install_systemd_unit",
    "restore_backup",
    "run_install_cmd",
    "run_smoke_test",
    "select_mode",
]
