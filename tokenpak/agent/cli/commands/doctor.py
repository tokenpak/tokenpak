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
                print(
                    Colors.warn(
                        f"Vault index         {index_path} — 0 blocks (run: tokenpak index)"
                    )
                )
                counts["warn"] += 1
        except json.JSONDecodeError:
            print(Colors.fail(f"Vault index         {index_path} — invalid JSON"))
            counts["fail"] += 1
    else:
        print(
            Colors.warn(
                f"Vault index         {index_path} — not found (run: tokenpak index <path>)"
            )
        )
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
            print(
                Colors.warn(
                    f"Proxy reachable     port {proxy_port} — connection refused (run: tokenpak proxy restart)"
                )
            )
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
                print(
                    Colors.warn(
                        f"Disk usage          {size_mb:.1f} MB — consider cleanup (tokenpak maintenance)"
                    )
                )
                counts["warn"] += 1
        else:
            print(Colors.warn("Disk usage          ~/.tokenpak not found"))
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
        print(
            Colors.warn(
                f"Dependencies        missing: {', '.join(missing_deps)} (run: pip install tokenpak)"
            )
        )
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
        ("ANTHROPIC_API_KEY", "Anthropic"),
        ("OPENAI_API_KEY", "OpenAI"),
        ("GOOGLE_API_KEY", "Google"),
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
        print(
            Colors.warn(
                "API keys            none found — set ANTHROPIC_API_KEY, "
                "OPENAI_API_KEY, or GOOGLE_API_KEY"
            )
        )
        counts["warn"] += 1

    # --- Check 9: Proxy health endpoint (degradation) -------------------------
    try:
        import urllib.request as _urlreq

        with _urlreq.urlopen("http://127.0.0.1:8766/degradation", timeout=3) as _r:
            _deg = json.loads(_r.read())
        if _deg.get("is_degraded"):
            recent = _deg.get("recent_events", [])
            detail = recent[0].get("detail", "") if recent else ""
            print(
                Colors.warn(
                    f"Proxy degradation   running in degraded mode — {detail[:60] or 'see tokenpak status'}"
                )
            )
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
                print(
                    Colors.ok(
                        f"Failover config     {failover_cfg_path} — {len(fo['chain'])} provider(s)"
                    )
                )
            elif fo.get("enabled"):
                print(Colors.warn("Failover config     enabled but no providers in chain"))
                counts["warn"] += 1
            else:
                print(
                    Colors.ok(f"Failover config     {failover_cfg_path} — disabled (no failover)")
                )
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
    @click.option("--fleet", is_flag=True, help="Check all agents in ~/.tokenpak/fleet.yaml")
    @click.option("--deploy", is_flag=True, help="Push latest doctor to all agents (use with --fleet)")
    def doctor_cmd(fix: bool, fleet: bool, deploy: bool) -> None:
        """Run diagnostics on your TokenPak installation.

        Checks: proxy health, vault index, auth config, disk space,
        Python version, and dependencies. Each check reports ✅/⚠️/❌
        with an actionable fix suggestion.

        Fleet mode: run doctor on all registered agents in ~/.tokenpak/fleet.yaml.

        Examples:

        \b
          tokenpak doctor                   # run all checks locally
          tokenpak doctor --fix             # run checks and auto-fix where possible
          tokenpak doctor --fleet           # check all agents in fleet
          tokenpak doctor --fleet --fix     # check + fix all agents
          tokenpak doctor --fleet --deploy  # push latest doctor to all agents first
        """
        if fleet:
            rc = run_fleet_doctor(fix=fix, deploy=deploy)
        else:
            rc = run_doctor(fix=fix)
        sys.exit(rc)

except ImportError:

    def doctor_cmd(*args, **kwargs):  # type: ignore
        print("click not installed; doctor command unavailable")


# ===========================================================================
# Fleet Doctor — tokenpak doctor --fleet
# ===========================================================================

import subprocess
import concurrent.futures
from pathlib import Path
import yaml


FLEET_CONFIG_FILE = Path.home() / ".tokenpak" / "fleet.yaml"

DEFAULT_FLEET_CONFIG = {
    "agents": [
        {"name": "trix",  "host": "trixbot",  "user": "trix"},
        {"name": "cali",  "host": "calibot",  "user": "cali"},
        {"name": "sue",   "host": "suewu",    "user": "sue"},
    ]
}


def load_fleet_config() -> dict:
    """Load fleet.yaml; create with defaults if missing."""
    if FLEET_CONFIG_FILE.exists():
        try:
            with open(FLEET_CONFIG_FILE) as f:
                data = yaml.safe_load(f)
            if data and "agents" in data:
                return data
        except Exception:
            pass
    # Create default
    FLEET_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FLEET_CONFIG_FILE, "w") as f:
        yaml.safe_dump(DEFAULT_FLEET_CONFIG, f, default_flow_style=False)
    return DEFAULT_FLEET_CONFIG


def _run_remote_doctor(agent: dict, fix: bool = False, timeout: int = 30) -> dict:
    """SSH into an agent and run tokenpak doctor. Returns result dict."""
    name = agent.get("name", "?")
    host = agent.get("host", "")
    user = agent.get("user", "")

    cmd_parts = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
    if user:
        cmd_parts += [f"{user}@{host}"]
    else:
        cmd_parts += [host]

    remote_cmd = "tokenpak doctor"
    if fix:
        remote_cmd += " --fix"
    cmd_parts.append(remote_cmd)

    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        # Parse summary line: "N errors, M warnings."
        errors = 0
        warnings = 0
        for line in output.splitlines():
            if "error" in line and "warning" in line:
                parts = line.strip().split()
                try:
                    errors = int(parts[0])
                    warnings = int(parts[2])
                except (ValueError, IndexError):
                    pass
        return {
            "name": name,
            "host": host,
            "success": result.returncode == 0,
            "output": output,
            "errors": errors,
            "warnings": warnings,
        }
    except subprocess.TimeoutExpired:
        return {"name": name, "host": host, "success": False, "output": f"[timeout after {timeout}s]", "errors": 1, "warnings": 0}
    except Exception as exc:
        return {"name": name, "host": host, "success": False, "output": str(exc), "errors": 1, "warnings": 0}


def _deploy_doctor(agent: dict, timeout: int = 30) -> dict:
    """SCP the latest doctor.py to an agent's tokenpak installation."""
    name = agent.get("name", "?")
    host = agent.get("host", "")
    user = agent.get("user", "")

    local_doctor = Path(__file__)
    remote_target = f"{user}@{host}:~/.local/lib/python3/dist-packages/tokenpak/agent/cli/commands/doctor.py"
    if not user:
        remote_target = f"{host}:~/.local/lib/python3/dist-packages/tokenpak/agent/cli/commands/doctor.py"

    cmd = [
        "scp",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        str(local_doctor),
        remote_target,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "name": name,
            "host": host,
            "success": result.returncode == 0,
            "output": result.stdout + result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"name": name, "host": host, "success": False, "output": f"[scp timeout after {timeout}s]"}
    except Exception as exc:
        return {"name": name, "host": host, "success": False, "output": str(exc)}


def run_fleet_doctor(fix: bool = False, deploy: bool = False) -> int:
    """Run fleet-wide doctor checks. Returns 0 if all pass, 1 if any fail."""
    fleet_cfg = load_fleet_config()
    agents = fleet_cfg.get("agents", [])

    if not agents:
        print(Colors.warn("Fleet config has no agents defined"))
        return 1

    print("\nTOKENPAK  |  Fleet Doctor")
    print(f"Checking {len(agents)} agent(s) in parallel...\n")

    if deploy:
        print("Deploying latest doctor to all agents...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents)) as ex:
            deploy_futures = {ex.submit(_deploy_doctor, a): a for a in agents}
            for fut in concurrent.futures.as_completed(deploy_futures):
                r = fut.result()
                status = Colors.ok(f"  Deployed to {r['name']} ({r['host']})") if r["success"] else Colors.fail(f"  Deploy failed on {r['name']} ({r['host']}): {r['output'][:60]}")
                print(status)
        print()

    # Run checks in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents)) as ex:
        futures = {ex.submit(_run_remote_doctor, a, fix): a for a in agents}
        results = []
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    # Sort by agent name for deterministic output
    results.sort(key=lambda r: r["name"])

    total_errors = 0
    total_warnings = 0
    all_ok = True

    print("──────────────────────────────────────────────────────")
    for r in results:
        icon = "✅" if r["errors"] == 0 else "❌"
        warn_note = f", {r['warnings']}w" if r["warnings"] else ""
        print(f"  {icon}  {r['name']:10s}  ({r['host']})  — {r['errors']} errors{warn_note}")
        total_errors += r["errors"]
        total_warnings += r["warnings"]
        if r["errors"] > 0:
            all_ok = False
            # Print remote output indented
            for line in r["output"].splitlines():
                print(f"     {line}")

    print("──────────────────────────────────────────────────────")
    print(f"Fleet: {total_errors} total error(s), {total_warnings} total warning(s) across {len(agents)} agents")
    return 0 if all_ok else 1
