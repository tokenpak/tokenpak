"""Capture help-text + structural baseline for the agent/cli consolidation.

Mirrors capture_agent_proxy_baseline.py + capture_services_stage_baseline.py
patterns. Extended to capture `tokenpak <subcommand> --help` output for every
discoverable subcommand (CLI's byte-fidelity invariant is help-text identity).

Phase D (P-AC-08) diffs the post-migration capture against this baseline;
0-byte delta is the merge gate.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
import subprocess
import sys
import warnings
from pathlib import Path

BASELINE_DIR = Path("tests/baselines/agent-cli-consolidation-2026-04-20")

PACKAGES = [
    "tokenpak.agent.cli",
    "tokenpak.cli",
]


def public_names(module_name: str) -> list[str]:
    import types

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            mod = importlib.import_module(module_name)
        except Exception as e:
            return [f"<IMPORT-ERROR: {type(e).__name__}: {e}>"]
    all_ = getattr(mod, "__all__", None)
    if all_ is not None:
        return sorted(all_)
    skip = {"annotations", "warnings"}
    return sorted(
        n
        for n in dir(mod)
        if not n.startswith("_")
        and n not in skip
        and not isinstance(getattr(mod, n, None), types.ModuleType)
    )


def walk_package(package_name: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {package_name: public_names(package_name)}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            root = importlib.import_module(package_name)
        except Exception as e:
            return {package_name: [f"<IMPORT-ERROR: {type(e).__name__}: {e}>"]}
    for info in pkgutil.walk_packages(root.__path__, prefix=package_name + "."):
        out[info.name] = public_names(info.name)
    return out


def capture_help(subcommand: str | None) -> dict[str, str | int]:
    """Run `python3 -m tokenpak [<cmd>] --help`; capture stdout + stderr + rc."""
    args = [sys.executable, "-m", "tokenpak"]
    if subcommand:
        args.append(subcommand)
    args.append("--help")
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "<TIMEOUT>", "returncode": -1}
    except Exception as e:
        return {"stdout": "", "stderr": f"<ERROR: {type(e).__name__}: {e}>", "returncode": -1}


def discover_subcommands() -> list[str]:
    """Parse subcommand names from `tokenpak help --all` output.

    The tokenpak CLI emits a custom help format (not raw argparse). Parse
    the grouped output where command rows look like:

        Essential Commands:
          start            Start the proxy (localhost:8766)

    Indentation is four spaces under a two-space section header.
    """
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak", "help", "--all"],
        capture_output=True, text=True, timeout=10,
    )
    text = result.stdout or ""
    names: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s{4}([a-z][\w-]*)\s{2,}\S", line)
        if m:
            names.append(m.group(1))
    # Dedup preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            out.append(n)
            seen.add(n)
    return out


def capture() -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    (BASELINE_DIR / "help").mkdir(exist_ok=True)

    # Public symbols
    full = {pkg: walk_package(pkg) for pkg in PACKAGES}
    (BASELINE_DIR / "public_symbols.json").write_text(
        json.dumps(full, indent=2, sort_keys=True) + "\n"
    )

    # Version
    ver = subprocess.run(
        [sys.executable, "-m", "tokenpak", "--version"],
        capture_output=True, text=True,
    )
    (BASELINE_DIR / "version.txt").write_text(
        (ver.stdout or "") + (ver.stderr or "")
    )

    # Pytest collection
    col = subprocess.run(
        ["pytest", "-q", "--tb=no", "--co"],
        capture_output=True, text=True,
    )
    (BASELINE_DIR / "pytest_collect_stdout.txt").write_text(col.stdout)
    (BASELINE_DIR / "pytest_collect_returncode.txt").write_text(f"{col.returncode}\n")

    # Conformance
    tip = subprocess.run(
        [sys.executable, "scripts/tip_conformance_check.py"],
        capture_output=True, text=True,
    )
    (BASELINE_DIR / "tip_conformance_stdout.txt").write_text(tip.stdout)
    (BASELINE_DIR / "tip_conformance_returncode.txt").write_text(f"{tip.returncode}\n")

    # Root help
    root = capture_help(None)
    (BASELINE_DIR / "help" / "_root_help.json").write_text(
        json.dumps(root, indent=2) + "\n"
    )

    # Also capture "help --all" output explicitly (the real command list)
    root_all = subprocess.run(
        [sys.executable, "-m", "tokenpak", "help", "--all"],
        capture_output=True, text=True, timeout=10,
    )
    (BASELINE_DIR / "help" / "_help_all.json").write_text(
        json.dumps({"stdout": root_all.stdout, "stderr": root_all.stderr, "returncode": root_all.returncode}, indent=2) + "\n"
    )

    # Per-subcommand help
    subcommands = discover_subcommands()
    subcommand_results = {"_root_subcommands": subcommands}
    for cmd in subcommands:
        subcommand_results[cmd] = capture_help(cmd)

    (BASELINE_DIR / "help" / "subcommands.json").write_text(
        json.dumps(subcommand_results, indent=2, sort_keys=True) + "\n"
    )

    total_modules = sum(len(v) for v in full.values())
    print(f"baseline captured -> {BASELINE_DIR}")
    print(f"  packages: {len(PACKAGES)}  modules: {total_modules}")
    print(f"  subcommands discovered: {len(subcommands)}")
    print(f"  pytest --co exit: {col.returncode}")
    print(f"  tip-check exit: {tip.returncode}")
    print(f"  version: {(ver.stdout or '').strip() or '(empty)'}")


if __name__ == "__main__":
    capture()
