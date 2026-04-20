# SPDX-License-Identifier: Apache-2.0
"""
# TOKENPAK CORE — DO NOT MODIFY
TokenPak agent startup version validation.

Checks:
1. Proxy reachable at expected version
2. Config hash matches lock file (warn if drifted)
3. No deprecated config fields

Usage:
    from tokenpak.debug.version_check import run_startup_check
    warnings = run_startup_check()
    for w in warnings: print(f"⚠️ {w}")
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

PROXY_URL = "http://localhost:8766"
LOCK_FILE = Path.home() / "vault" / "System" / "tokenpak.lock.json"
OPENCLAW_CFG = Path.home() / ".openclaw" / "openclaw.json"
MEMORY_DIR = Path.home() / ".openclaw" / "workspace" / "memory"

# Fields that were removed in past versions
DEPRECATED_CONFIG_FIELDS = {
    "meta.legacyMode",
    "meta.experimentalFeatures",
}


def _compute_config_hash(cfg: dict) -> str:
    normalized = {k: v for k, v in sorted(cfg.items()) if k != "meta"}
    raw = json.dumps(normalized, sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:12]


def _query_proxy_version() -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"{PROXY_URL}/version", timeout=3) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _load_lock() -> dict:
    if LOCK_FILE.exists():
        try:
            return json.loads(LOCK_FILE.read_text())
        except Exception:
            return {}
    return {}


def _load_config() -> Optional[dict]:
    if OPENCLAW_CFG.exists():
        try:
            return json.loads(OPENCLAW_CFG.read_text())
        except Exception:
            return None
    return None


def _log_warning(message: str):
    """Append warning to today's memory file."""
    try:
        import datetime

        today = datetime.date.today().isoformat()
        mem_path = MEMORY_DIR / f"{today}.md"
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        existing = mem_path.read_text() if mem_path.exists() else ""
        ts = datetime.datetime.now().strftime("%H:%M")
        if "## Startup Warnings" not in existing:
            existing += "\n## Startup Warnings\n"
        existing += f"- [{ts}] {message}\n"
        mem_path.write_text(existing)
    except Exception:
        pass  # Never crash on logging


def run_startup_check(agent_name: str = "agent") -> List[str]:
    """
    Run all startup version checks.
    Returns list of warning strings (empty = all good).
    Warnings are also logged to today's memory file.
    """
    warnings: List[str] = []

    # 1. Check proxy reachability + version
    proxy_info = _query_proxy_version()
    if proxy_info is None:
        w = "TokenPak proxy not reachable at localhost:8766"
        warnings.append(w)
        _log_warning(w)
    else:
        lock = _load_lock()
        lock_proxy_ver = lock.get("proxyVersion")
        proxy_ver = proxy_info.get("version")
        if lock_proxy_ver and proxy_ver and lock_proxy_ver != proxy_ver:
            w = f"Proxy version drift: lock={lock_proxy_ver}, running={proxy_ver}"
            warnings.append(w)
            _log_warning(w)

    # 2. Config hash vs lock
    cfg = _load_config()
    if cfg is not None:
        current_hash = _compute_config_hash(cfg)
        lock = _load_lock()
        lock_hash = lock.get("configHash")
        if lock_hash and lock_hash != current_hash:
            w = f"Config hash drift: lock={lock_hash}, current={current_hash} — run `tokenpak config sync`"
            warnings.append(w)
            _log_warning(w)

        # 3. Deprecated fields
        for deprecated_path in DEPRECATED_CONFIG_FIELDS:
            parts = deprecated_path.split(".")
            obj = cfg
            for part in parts:
                if isinstance(obj, dict) and part in obj:
                    obj = obj[part]
                else:
                    obj = None  # type: ignore[assignment]
                    break
            if obj is not None:
                w = f"Deprecated config field found: {deprecated_path} — remove it"
                warnings.append(w)
                _log_warning(w)

    return warnings


if __name__ == "__main__":
    print("TokenPak startup check...")
    issues = run_startup_check("manual")
    if issues:
        for issue in issues:
            print(f"  ⚠️  {issue}")
    else:
        print("  ✓ All checks passed")
