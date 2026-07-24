# SPDX-License-Identifier: Apache-2.0
"""Comprehensive diagnostics for TokenPak installation."""

import json
import socket
import sys
from pathlib import Path


class Colors:
    """ANSI color codes."""

    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    RESET = "\033[0m"

    @staticmethod
    def ok(text):
        return f"{Colors.GREEN}✅{Colors.RESET}  {text}"

    @staticmethod
    def warn(text):
        return f"{Colors.YELLOW}⚠️{Colors.RESET}   {text}"

    @staticmethod
    def fail(text):
        return f"{Colors.RED}❌{Colors.RESET}  {text}"


def cmd_doctor(args):
    """Run comprehensive diagnostics on TokenPak installation."""
    print("\nTOKENPAK  |  Doctor")
    print("──────────────────────────────\n")

    results = {"pass": 0, "warn": 0, "fail": 0}
    fixes_needed = []

    # Check 1: Python version
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        print(Colors.ok(f"Python version      {py_version} — OK"))
        results["pass"] += 1
    else:
        print(Colors.fail(f"Python version      {py_version} — requires ≥3.10"))
        results["fail"] += 1

    # Check 2: Config file
    config_path = Path.home() / ".tokenpak" / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                json.load(f)
            print(Colors.ok(f"Config file         {config_path} — valid"))
            results["pass"] += 1
        except json.JSONDecodeError:
            print(Colors.fail(f"Config file         {config_path} — invalid JSON"))
            results["fail"] += 1
            fixes_needed.append("reset config")
    else:
        print(Colors.warn(f"Config file         {config_path} — not found"))
        results["warn"] += 1
        fixes_needed.append("create config")

    # Check 3: Vault index
    index_path = Path.home() / ".tokenpak" / "index.json"
    if index_path.exists():
        try:
            with open(index_path) as f:
                data = json.load(f)
                block_count = len(data.get("blocks", []))
            if block_count > 0:
                print(Colors.ok(f"Vault index         {index_path} — {block_count} blocks"))
                results["pass"] += 1
            else:
                print(
                    Colors.warn(
                        f"Vault index         {index_path} — 0 blocks (run: tokenpak index)"
                    )
                )
                results["warn"] += 1
        except json.JSONDecodeError:
            print(Colors.fail(f"Vault index         {index_path} — invalid JSON"))
            results["fail"] += 1
    else:
        print(Colors.warn(f"Vault index         {index_path} — not found"))
        results["warn"] += 1

    # Check 4: Proxy port
    import os as _os

    proxy_port = int(_os.environ.get("TOKENPAK_PORT", "8766"))
    proxy_health = None
    try:
        import urllib.request as _urlreq

        resp = _urlreq.urlopen(f"http://127.0.0.1:{proxy_port}/health", timeout=2)
        proxy_health = json.loads(resp.read())
        status = proxy_health.get("status", "unknown")
        reqs = proxy_health.get("requests_total")
        errors = proxy_health.get("requests_errors")
        reqs_text = str(reqs) if reqs is not None else "unavailable"
        errors_text = str(errors) if errors is not None else "unavailable"
        print(
            Colors.ok(
                f"Proxy reachable     port {proxy_port} — status={status}, "
                f"{reqs_text} requests, {errors_text} errors"
            )
        )
        results["pass"] += 1

        # Circuit breakers
        cbs = proxy_health.get("circuit_breakers", {})
        providers = cbs.get("providers", {}) if isinstance(cbs, dict) else {}
        for name, cb in providers.items():
            state = cb.get("state", "unknown") if isinstance(cb, dict) else "unknown"
            if state in ("open", "half_open"):
                print(Colors.fail(f"Circuit breaker     {name} — {state.upper()}"))
                results["fail"] += 1
            else:
                print(Colors.ok(f"Circuit breaker     {name} — {state}"))
                results["pass"] += 1

    except Exception:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("127.0.0.1", proxy_port))
            sock.close()
            if result == 0:
                print(
                    Colors.ok(
                        f"Proxy reachable     port {proxy_port} — OK (health endpoint failed)"
                    )
                )
                results["pass"] += 1
            else:
                print(
                    Colors.warn(
                        f"Proxy reachable     port {proxy_port} — connection refused (run: tokenpak serve)"
                    )
                )
                results["warn"] += 1
                fixes_needed.append("start proxy")
        except Exception:
            print(Colors.warn(f"Proxy reachable     port {proxy_port} — check failed"))
            results["warn"] += 1

    # Check 5: Disk usage
    tokenpak_dir = Path.home() / ".tokenpak"
    try:
        total_size = sum(f.stat().st_size for f in tokenpak_dir.rglob("*") if f.is_file())
        size_mb = total_size / (1024 * 1024)
        if size_mb < 500:
            print(Colors.ok(f"Disk usage          {size_mb:.1f} MB — OK"))
            results["pass"] += 1
        else:
            print(Colors.warn(f"Disk usage          {size_mb:.1f} MB — consider cleanup"))
            results["warn"] += 1
    except Exception:
        print(Colors.warn("Disk usage          could not measure"))
        results["warn"] += 1

    # Check 6: Log file
    log_path = Path.home() / ".tokenpak" / "debug.log"
    if log_path.exists():
        log_size_mb = log_path.stat().st_size / (1024 * 1024)
        print(Colors.ok(f"Debug log           {log_path} — {log_size_mb:.2f} MB"))
        results["pass"] += 1
    else:
        print(Colors.ok("Debug log           (not present)"))
        results["pass"] += 1

    # Summary
    print("\n──────────────────────────────")
    summary = f"{results['fail']} error{'s' if results['fail'] != 1 else ''}, {results['warn']} warning{'s' if results['warn'] != 1 else ''}."
    print(summary)

    if hasattr(args, "fix") and args.fix:
        print("\nAuto-fix requested. Fixing issues...")
        for fix in fixes_needed:
            if fix == "create config":
                tokenpak_dir.mkdir(parents=True, exist_ok=True)
                default_config = {"version": "1.0", "port": 8766, "compress": True}
                with open(config_path, "w") as f:
                    json.dump(default_config, f, indent=2)
                print(f"  ✓ Created {config_path}")

    if results["fail"] > 0:
        sys.exit(1)
