"""Capture module-signature + test-suite baseline for the agent/proxy → proxy/* migration.

Live-traffic byte-fidelity capture (request.bin / response.bin) requires a running
tokenpak serve against real provider endpoints, which this session cannot produce.

The pragmatic baseline — checkable under Phase D diff — is:

  1. Module public-symbol sets for every module under tokenpak.agent.proxy.*
  2. Full `make check` test summary (counts + names of tests)
  3. `tokenpak --version`

If the migration is a pure relocation, all three invariants hold after the move.
Phase D (P-AP-08) diffs post-migration capture against this baseline.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import subprocess
import sys
from pathlib import Path

BASELINE_DIR = Path("tests/baselines/agent-proxy-migration-2026-04-20")


def public_names(module_name: str) -> list[str]:
    mod = importlib.import_module(module_name)
    all_ = getattr(mod, "__all__", None)
    if all_ is not None:
        return sorted(all_)
    return sorted(n for n in dir(mod) if not n.startswith("_"))


def walk_agent_proxy() -> dict[str, list[str]]:
    """Return {module_name: sorted_public_names} for tokenpak.agent.proxy and descendants."""
    root = importlib.import_module("tokenpak.agent.proxy")
    out: dict[str, list[str]] = {"tokenpak.agent.proxy": public_names("tokenpak.agent.proxy")}
    for info in pkgutil.walk_packages(root.__path__, prefix="tokenpak.agent.proxy."):
        try:
            out[info.name] = public_names(info.name)
        except Exception as e:
            out[info.name] = [f"<IMPORT-ERROR: {type(e).__name__}: {e}>"]
    return out


def capture() -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    signatures = walk_agent_proxy()
    (BASELINE_DIR / "agent_proxy_public_symbols.json").write_text(
        json.dumps(signatures, indent=2, sort_keys=True) + "\n"
    )

    version = subprocess.run(
        [sys.executable, "-m", "tokenpak", "--version"],
        capture_output=True,
        text=True,
    )
    (BASELINE_DIR / "tokenpak_version.txt").write_text(
        (version.stdout or "").strip() + "\n" + (version.stderr or "").strip() + "\n"
    )

    check = subprocess.run(
        ["pytest", "-q", "--tb=no", "--co"],
        capture_output=True,
        text=True,
    )
    (BASELINE_DIR / "pytest_collect_stdout.txt").write_text(check.stdout)
    (BASELINE_DIR / "pytest_collect_stderr.txt").write_text(check.stderr)
    (BASELINE_DIR / "pytest_collect_returncode.txt").write_text(f"{check.returncode}\n")

    tip = subprocess.run(
        [sys.executable, "scripts/tip_conformance_check.py"],
        capture_output=True,
        text=True,
    )
    (BASELINE_DIR / "tip_conformance_stdout.txt").write_text(tip.stdout)
    (BASELINE_DIR / "tip_conformance_stderr.txt").write_text(tip.stderr)
    (BASELINE_DIR / "tip_conformance_returncode.txt").write_text(f"{tip.returncode}\n")

    print(f"baseline captured -> {BASELINE_DIR}")
    print(f"  modules: {len(signatures)}")
    print(f"  make check: exit {check.returncode}")
    print(f"  make tip-check: exit {tip.returncode}")


if __name__ == "__main__":
    capture()
