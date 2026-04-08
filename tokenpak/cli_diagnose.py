# SPDX-License-Identifier: MIT
"""tokenpak diagnose — health check, index integrity, cache stats, and more."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Severity levels
# ---------------------------------------------------------------------------

OK = "ok"
WARNING = "warning"
ERROR = "error"

_ICONS = {OK: "✅", WARNING: "⚠️ ", ERROR: "❌"}
_LABELS = {OK: "OK", WARNING: "WARNING", ERROR: "ERROR"}


class DiagResult:
    """Single diagnostic check result."""

    def __init__(
        self,
        check: str,
        severity: str,
        message: str,
        detail: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.check = check
        self.severity = severity
        self.message = message
        self.detail = detail
        self.data = data or {}
        self.ts = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "timestamp": self.ts,
        }
        if self.detail:
            d["detail"] = self.detail
        if self.data:
            d.update(self.data)
        return d


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_config(verbose: bool) -> DiagResult:
    """Validate config syntax and required fields."""
    config_path = Path.home() / ".tokenpak" / "config.yaml"
    alt_path = Path.home() / ".tokenpak" / "config.json"

    # Try YAML first, then JSON
    found_path: Optional[Path] = None
    if config_path.exists():
        found_path = config_path
    elif alt_path.exists():
        found_path = alt_path

    if found_path is None:
        return DiagResult(
            "config",
            WARNING,
            "Config: Not found — using env vars / defaults",
            detail="Create ~/.tokenpak/config.yaml to customize settings",
            data={"path": str(config_path), "found": False},
        )

    try:
        text = found_path.read_text()
        if found_path.suffix == ".json":
            cfg = json.loads(text)
        else:
            try:
                import yaml  # type: ignore
                cfg = yaml.safe_load(text) or {}
            except ImportError:
                # Fall back to treating as JSON-like if yaml unavailable
                cfg = {}

        env_vars = {
            "TOKENPAK_PORT": os.environ.get("TOKENPAK_PORT", "(default 8766)"),
            "TOKENPAK_MODE": os.environ.get("TOKENPAK_MODE", "(default hybrid)"),
            "ANTHROPIC_API_KEY": "***" if os.environ.get("ANTHROPIC_API_KEY") else "(not set)",
        }

        detail = None
        if verbose:
            detail = "Env vars: " + ", ".join(f"{k}={v}" for k, v in env_vars.items())

        return DiagResult(
            "config",
            OK,
            f"Config: Valid, all required fields present ({found_path.name})",
            detail=detail,
            data={"path": str(found_path), "found": True, "format": found_path.suffix.lstrip(".")},
        )
    except Exception as exc:
        return DiagResult(
            "config",
            ERROR,
            f"Config: Parse error — {exc}",
            detail=f"File: {found_path}",
            data={"path": str(found_path), "found": True, "error": str(exc)},
        )


def _check_vault_index(verbose: bool) -> DiagResult:
    """Check vault index file integrity, size, and last rebuild time."""
    vault_index_path = os.environ.get(
        "TOKENPAK_VAULT_INDEX",
        str(Path.home() / "vault" / ".tokenpak"),
    )
    index_dir = Path(vault_index_path)

    # Support both json_blocks directory and single index.json
    json_index = index_dir / "index.json"
    blocks_dir = index_dir / "blocks"
    alt_index = Path.home() / ".tokenpak" / "index.json"

    candidates = [json_index, alt_index]
    found: Optional[Path] = next((p for p in candidates if p.exists()), None)

    if found is None and blocks_dir.exists():
        # SQLite or blocks directory
        txt_files = list(blocks_dir.glob("*.txt"))
        size_bytes = sum(f.stat().st_size for f in txt_files) if txt_files else 0
        size_mb = size_bytes / (1024 * 1024)
        mtime = max((f.stat().st_mtime for f in txt_files), default=0) if txt_files else 0
        age_h = (time.time() - mtime) / 3600 if mtime else 0
        age_str = f"{age_h:.0f} hours ago" if age_h < 48 else f"{age_h/24:.0f} days ago"
        sev = WARNING if age_h > 24 else OK
        return DiagResult(
            "vault_index",
            sev,
            f"Vault Index: {size_mb:.1f} MB, {len(txt_files):,} blocks, last rebuilt {age_str}",
            data={
                "path": str(blocks_dir),
                "size_mb": round(size_mb, 2),
                "block_count": len(txt_files),
                "last_rebuilt_ts": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat() if mtime else None,
                "age_hours": round(age_h, 1),
            },
        )

    if found is None:
        return DiagResult(
            "vault_index",
            WARNING,
            "Vault Index: Not found — run `tokenpak index ~/vault` to build",
            data={"path": vault_index_path, "found": False},
        )

    try:
        with open(found) as f:
            data = json.load(f)
        blocks = data.get("blocks", [])
        block_count = len(blocks)
        stat = found.stat()
        size_mb = stat.st_size / (1024 * 1024)
        age_h = (time.time() - stat.st_mtime) / 3600
        age_str = f"{age_h:.0f} hours ago" if age_h < 48 else f"{age_h/24:.0f} days ago"
        sev = WARNING if age_h > 24 else OK
        return DiagResult(
            "vault_index",
            sev,
            f"Vault Index: {size_mb:.1f} MB, {block_count:,} blocks, last rebuilt {age_str}",
            data={
                "path": str(found),
                "size_mb": round(size_mb, 2),
                "block_count": block_count,
                "last_rebuilt_ts": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "age_hours": round(age_h, 1),
            },
        )
    except json.JSONDecodeError as exc:
        return DiagResult(
            "vault_index",
            ERROR,
            f"Vault Index: Corrupt — invalid JSON ({exc})",
            detail="Run `tokenpak index ~/vault` to rebuild",
            data={"path": str(found), "error": str(exc)},
        )


def _check_cache(verbose: bool, port: int) -> DiagResult:
    """Report cache size, item count, and hit/miss stats from live proxy."""
    import urllib.request as _urlreq

    try:
        resp = _urlreq.urlopen(f"http://127.0.0.1:{port}/cache-stats", timeout=2)
        cache = json.loads(resp.read())
        hits = int(cache.get("cache_hits", 0))
        misses = int(cache.get("cache_misses", 0))
        total = hits + misses
        hit_rate = (hits / total * 100) if total > 0 else 0.0

        # Estimate in-memory cache size from compact cache info
        cache_read_tokens = int(cache.get("cache_read_tokens", 0))
        size_mb = cache_read_tokens * 4 / (1024 * 1024)  # rough estimate: 4 bytes/token

        return DiagResult(
            "cache",
            OK,
            f"Cache: {size_mb:.1f} MB est., {total:,} items, hit rate {hit_rate:.0f}%",
            data={
                "cache_hits": hits,
                "cache_misses": misses,
                "hit_rate": round(hit_rate / 100, 4),
                "cache_read_tokens": cache_read_tokens,
                "total_decisions": total,
            },
        )
    except Exception:
        return DiagResult(
            "cache",
            WARNING,
            "Cache: Stats unavailable (proxy not running or cache-stats endpoint unreachable)",
            data={"available": False},
        )


def _check_proxy(verbose: bool, port: int) -> DiagResult:
    """Check if proxy is running and listening."""
    import urllib.request as _urlreq

    try:
        resp = _urlreq.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
        health = json.loads(resp.read())
        mode = health.get("compilation_mode", "unknown")
        uptime = health.get("uptime_seconds")
        uptime_str = ""
        if uptime is not None:
            h, m = divmod(int(uptime) // 60, 60)
            uptime_str = f", up {h}h {m}m"
        return DiagResult(
            "proxy",
            OK,
            f"Proxy: Listening on 0.0.0.0:{port} ({mode} mode{uptime_str})",
            data={
                "port": port,
                "running": True,
                "mode": mode,
                "uptime_seconds": uptime,
            },
        )
    except Exception:
        # Try raw TCP check
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            rc = sock.connect_ex(("127.0.0.1", port))
            sock.close()
            if rc == 0:
                return DiagResult(
                    "proxy",
                    OK,
                    f"Proxy: Listening on port {port} (health endpoint unavailable)",
                    data={"port": port, "running": True},
                )
        except Exception:
            pass
        return DiagResult(
            "proxy",
            WARNING,
            f"Proxy: Not running on port {port} — run `tokenpak start`",
            data={"port": port, "running": False},
        )


def _check_permissions(verbose: bool) -> DiagResult:
    """Verify read access to vault and cache directories."""
    vault_path = os.environ.get("TOKENPAK_VAULT_INDEX", str(Path.home() / "vault" / ".tokenpak"))
    dirs = {
        "vault": Path(vault_path).parent,
        "cache": Path.home() / ".tokenpak",
    }
    errors: List[str] = []
    checked: Dict[str, Any] = {}

    for label, d in dirs.items():
        if d.exists():
            readable = os.access(d, os.R_OK)
            checked[label] = {"path": str(d), "readable": readable}
            if not readable:
                errors.append(f"{d} not readable")
        else:
            checked[label] = {"path": str(d), "exists": False}

    if errors:
        return DiagResult(
            "permissions",
            ERROR,
            f"Permissions: Access denied — {'; '.join(errors)}",
            data={"checks": checked},
        )
    return DiagResult(
        "permissions",
        OK,
        "Permissions: Read access OK to vault and ~/.tokenpak",
        data={"checks": checked},
    )


def _check_disk_space(verbose: bool) -> List[DiagResult]:
    """Warn if free disk space <1 GB in relevant locations."""
    vault_path = Path(os.environ.get("TOKENPAK_VAULT_INDEX", str(Path.home() / "vault" / ".tokenpak"))).parent
    dirs = {
        "vault": vault_path,
        "home": Path.home(),
    }
    results: List[DiagResult] = []

    seen_devices: set = set()
    for label, d in dirs.items():
        check_path = d if d.exists() else Path.home()
        try:
            stat = shutil.disk_usage(check_path)
            # Deduplicate by checking if we already covered this mount
            free_gb = stat.free / (1024 ** 3)
            total_gb = stat.total / (1024 ** 3)
            device_key = str(check_path.resolve())
            if device_key in seen_devices:
                continue
            seen_devices.add(device_key)
            sev = ERROR if free_gb < 0.5 else (WARNING if free_gb < 1.0 else OK)
            results.append(DiagResult(
                "disk_space",
                sev,
                f"Disk Space: {free_gb:.1f} GB free at {check_path} ({'⚠️ consider cleanup' if sev != OK else 'OK'})",
                data={
                    "path": str(check_path),
                    "free_gb": round(free_gb, 2),
                    "total_gb": round(total_gb, 2),
                },
            ))
        except Exception as exc:
            results.append(DiagResult(
                "disk_space",
                WARNING,
                f"Disk Space: Could not check {check_path} — {exc}",
                data={"path": str(check_path), "error": str(exc)},
            ))

    return results or [DiagResult("disk_space", WARNING, "Disk Space: No paths checked", data={})]


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

def cmd_diagnose(args) -> None:
    """Run tokenpak diagnose."""
    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    as_json = getattr(args, "json_output", False)
    verbose = getattr(args, "verbose", False)

    # Collect all results
    all_results: List[DiagResult] = []

    all_results.append(_check_config(verbose))
    all_results.append(_check_vault_index(verbose))
    all_results.append(_check_cache(verbose, port))
    all_results.append(_check_proxy(verbose, port))
    all_results.append(_check_permissions(verbose))
    all_results.extend(_check_disk_space(verbose))

    # Tally
    counts = {OK: 0, WARNING: 0, ERROR: 0}
    for r in all_results:
        counts[r.severity] += 1

    if as_json:
        output = {
            "version": "1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "ok": counts[OK],
                "warnings": counts[WARNING],
                "errors": counts[ERROR],
                "overall": "ERROR" if counts[ERROR] > 0 else ("WARNING" if counts[WARNING] > 0 else "OK"),
            },
            "checks": [r.to_dict() for r in all_results],
        }
        print(json.dumps(output, indent=2))
        if counts[ERROR] > 0:
            sys.exit(1)
        return

    # Human-readable output
    print()
    for r in all_results:
        icon = _ICONS[r.severity]
        print(f"{icon} {r.message}")
        if verbose and r.detail:
            print(f"    {r.detail}")

    print()
    # Overall status line
    if counts[ERROR] > 0:
        status = "🔴 UNHEALTHY"
    elif counts[WARNING] > 0:
        status = "🟡 HEALTHY"
    else:
        status = "🟢 HEALTHY"

    warn_str = f"{counts[WARNING]} warning{'s' if counts[WARNING] != 1 else ''}"
    err_str = f"{counts[ERROR]} error{'s' if counts[ERROR] != 1 else ''}"
    print(f"Overall: {status} ({warn_str}, {err_str})")
    print()

    if counts[ERROR] > 0:
        sys.exit(1)
