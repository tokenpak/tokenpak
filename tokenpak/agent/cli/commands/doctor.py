"""doctor command — diagnose common TokenPak issues."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path


class Colors:
    """ANSI color codes + emoji markers."""
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    RESET = "\033[0m"

    @staticmethod
    def ok(text: str) -> str:
        return f"{Colors.GREEN}✅{Colors.RESET}  {text}"

    @staticmethod
    def warn(text: str) -> str:
        return f"{Colors.YELLOW}⚠️{Colors.RESET}   {text}"

    @staticmethod
    def fail(text: str) -> str:
        return f"{Colors.RED}❌{Colors.RESET}  {text}"


def run_doctor(fix: bool = False) -> int:
    """Run all diagnostic checks. Returns exit code (0=ok/warn, 1=fail)."""
    print("\nTOKENPAK  |  Doctor")
    print("──────────────────────────────\n")

    counts = {"pass": 0, "warn": 0, "fail": 0}
    fixes: list[tuple[str, Path]] = []
    tokenpak_dir = Path.home() / ".tokenpak"

    # --- Check 1: Python version -----------------------------------------------
    v = sys.version_info
    py_version = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 10):
        print(Colors.ok(f"Python version      {py_version} — OK"))
        counts["pass"] += 1
    else:
        print(Colors.fail(f"Python version      {py_version} — requires ≥3.10"))
        counts["fail"] += 1

    # --- Check 2: Config file --------------------------------------------------
    config_path = tokenpak_dir / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                json.load(f)
            print(Colors.ok(f"Config file         {config_path} — valid"))
            counts["pass"] += 1
        except json.JSONDecodeError:
            print(Colors.fail(f"Config file         {config_path} — invalid JSON"))
            counts["fail"] += 1
            fixes.append(("reset config", config_path))
    else:
        print(Colors.warn(f"Config file         {config_path} — not found"))
        counts["warn"] += 1
        fixes.append(("create config", config_path))

    # --- Check 3: Vault index --------------------------------------------------
    index_path = tokenpak_dir / "index.json"
    if index_path.exists():
        try:
            with open(index_path) as f:
                data = json.load(f)
            block_count = len(data.get("blocks", []))
            if block_count > 0:
                print(Colors.ok(f"Vault index         {index_path} — {block_count} blocks"))
                counts["pass"] += 1
            else:
                print(Colors.warn(f"Vault index         {index_path} — 0 blocks (run: tokenpak index)"))
                counts["warn"] += 1
        except json.JSONDecodeError:
            print(Colors.fail(f"Vault index         {index_path} — invalid JSON"))
            counts["fail"] += 1
    else:
        print(Colors.warn(f"Vault index         {index_path} — not found (run: tokenpak index <path>)"))
        counts["warn"] += 1

    # --- Check 4: Proxy port ---------------------------------------------------
    proxy_port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        rc = sock.connect_ex(("127.0.0.1", proxy_port))
        sock.close()
        if rc == 0:
            print(Colors.ok(f"Proxy reachable     port {proxy_port} — OK"))
            counts["pass"] += 1
        else:
            print(Colors.warn(f"Proxy reachable     port {proxy_port} — connection refused (run: tokenpak proxy restart)"))
            counts["warn"] += 1
    except Exception:
        print(Colors.warn(f"Proxy reachable     port {proxy_port} — check failed"))
        counts["warn"] += 1

    # --- Check 5: Disk usage --------------------------------------------------
    try:
        if tokenpak_dir.exists():
            total_bytes = sum(f.stat().st_size for f in tokenpak_dir.rglob("*") if f.is_file())
            size_mb = total_bytes / (1024 * 1024)
            if size_mb < 500:
                print(Colors.ok(f"Disk usage          {size_mb:.1f} MB — OK"))
                counts["pass"] += 1
            else:
                print(Colors.warn(f"Disk usage          {size_mb:.1f} MB — consider cleanup (tokenpak maintenance)"))
                counts["warn"] += 1
        else:
            print(Colors.warn(f"Disk usage          ~/.tokenpak not found"))
            counts["warn"] += 1
    except Exception:
        print(Colors.warn("Disk usage          could not measure"))
        counts["warn"] += 1

    # --- Check 6: Python deps -------------------------------------------------
    missing_deps: list[str] = []
    for pkg in ["click", "yaml", "httpx"]:
        try:
            __import__(pkg)
        except ImportError:
            missing_deps.append(pkg)
    if missing_deps:
        print(Colors.warn(f"Dependencies        missing: {', '.join(missing_deps)} (run: pip install tokenpak)"))
        counts["warn"] += 1
    else:
        print(Colors.ok("Dependencies        all core packages present"))
        counts["pass"] += 1

    # --- Check 7: Log file ----------------------------------------------------
    log_path = tokenpak_dir / "debug.log"
    if log_path.exists():
        log_mb = log_path.stat().st_size / (1024 * 1024)
        print(Colors.ok(f"Debug log           {log_path} — {log_mb:.2f} MB"))
        counts["pass"] += 1
    else:
        print(Colors.ok("Debug log           (not present)"))
        counts["pass"] += 1

    # --- Check 8: API key environment variables --------------------------------
    api_key_checks = [
        ("ANTHROPIC_API_KEY",  "Anthropic"),
        ("OPENAI_API_KEY",     "OpenAI"),
        ("GOOGLE_API_KEY",     "Google"),
    ]
    found_keys = []
    missing_keys = []
    for env_var, provider in api_key_checks:
        val = os.environ.get(env_var, "").strip()
        if val:
            found_keys.append(provider)
        else:
            missing_keys.append((env_var, provider))

    if found_keys:
        print(Colors.ok(f"API keys            {', '.join(found_keys)} — env vars set"))
        counts["pass"] += 1
    else:
        print(Colors.warn(
            "API keys            none found — set ANTHROPIC_API_KEY, "
            "OPENAI_API_KEY, or GOOGLE_API_KEY"
        ))
        counts["warn"] += 1

    # --- Check 9: Proxy health endpoint (degradation) -------------------------
    try:
        import urllib.request as _urlreq
        with _urlreq.urlopen("http://127.0.0.1:8766/degradation", timeout=3) as _r:
            _deg = json.loads(_r.read())
        if _deg.get("is_degraded"):
            recent = _deg.get("recent_events", [])
            detail = recent[0].get("detail", "") if recent else ""
            print(Colors.warn(
                f"Proxy degradation   running in degraded mode — {detail[:60] or 'see tokenpak status'}"
            ))
            counts["warn"] += 1
        else:
            print(Colors.ok("Proxy degradation   not degraded — no recent issues"))
            counts["pass"] += 1
    except Exception:
        # Proxy not running — already reported in Check 4
        pass

    # --- Check 10: Failover config --------------------------------------------
    failover_cfg_path = tokenpak_dir / "config.yaml"
    if failover_cfg_path.exists():
        try:
            import yaml
            with open(failover_cfg_path) as _f:
                _fc = yaml.safe_load(_f) or {}
            fo = _fc.get("failover", {})
            if fo.get("enabled") and fo.get("chain"):
                print(Colors.ok(f"Failover config     {failover_cfg_path} — {len(fo['chain'])} provider(s)"))
            elif fo.get("enabled"):
                print(Colors.warn(f"Failover config     enabled but no providers in chain"))
                counts["warn"] += 1
            else:
                print(Colors.ok(f"Failover config     {failover_cfg_path} — disabled (no failover)"))
            counts["pass"] += 1
        except Exception as _e:
            print(Colors.warn(f"Failover config     could not parse config.yaml: {_e}"))
            counts["warn"] += 1
    else:
        print(Colors.ok("Failover config     not configured (optional)"))
        counts["pass"] += 1

    # --- Summary --------------------------------------------------------------
    print("\n──────────────────────────────")
    err_s = "s" if counts["fail"] != 1 else ""
    warn_s = "s" if counts["warn"] != 1 else ""
    print(f"{counts['fail']} error{err_s}, {counts['warn']} warning{warn_s}.")

    # --- Auto-fix -------------------------------------------------------------
    if fix and fixes:
        print("\nAuto-fix requested. Fixing issues...")
        for fix_type, fix_path in fixes:
            if fix_type in ("create config", "reset config"):
                if fix_type == "reset config" and fix_path.exists():
                    backup = Path(str(fix_path) + ".backup")
                    fix_path.rename(backup)
                    print(f"  ✓ Backed up invalid config → {backup}")
                tokenpak_dir.mkdir(parents=True, exist_ok=True)
                default_cfg = {"version": "1.0", "port": 8766, "compress": True}
                with open(fix_path, "w") as f:
                    json.dump(default_cfg, f, indent=2)
                print(f"  ✓ Created {fix_path}")

    return 1 if counts["fail"] > 0 else 0


try:
    import click

    @click.command("doctor")
    @click.option("--fix", is_flag=True, help="Auto-fix issues where possible")
    def doctor_cmd(fix: bool) -> None:
        """Run diagnostics on your TokenPak installation.

        Checks: proxy health, vault index, auth config, disk space,
        Python version, and dependencies. Each check reports ✅/⚠️/❌
        with an actionable fix suggestion.

        Examples:

        \b
          tokenpak doctor          # run all checks
          tokenpak doctor --fix    # run checks and auto-fix where possible
        """
        rc = run_doctor(fix=fix)
        sys.exit(rc)

except ImportError:
    def doctor_cmd(*args, **kwargs):  # type: ignore
        print("click not installed; doctor command unavailable")
