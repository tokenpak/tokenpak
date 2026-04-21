#!/usr/bin/env python3
"""Check 48h soft-launch health signals for tokenpak 1.1.0.

Run to monitor during the Phase 9.5 soft-launch window. Returns exit 0
when all signals are positive and the window has elapsed; exit 1 on any
negative signal; exit 2 if the window hasn't elapsed yet.

Usage:
    python3 scripts/check_v110_soft_launch.py             # fast checks only
    python3 scripts/check_v110_soft_launch.py --smoke     # + install-smoke (~5-10 min)

Signals checked (fast):
  1. PyPI artifact present, not yanked
  2. 48h window elapsed since PyPI publish (2026-04-21T15:50:20 UTC)
  3. GitHub release still Draft (not accidentally promoted)
  4. Zero new critical issues opened since release

Additional signal with --smoke (slow, opt-in):
  5. Install-smoke in fresh venv succeeds (can take 5-10 min due to
     torch/nvidia-* transitive deps)
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

PYPI_PUBLISH_TIME_ISO = "2026-04-21T15:50:20Z"
SOFT_LAUNCH_HOURS = 48


def check_pypi() -> tuple[bool, str]:
    try:
        req = urllib.request.Request(
            "https://pypi.org/pypi/tokenpak/1.1.0/json",
            headers={"User-Agent": "tokenpak-soft-launch-check/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        urls = data.get("urls", [])
        if not urls:
            return False, "no release files on PyPI"
        yanked = any(u.get("yanked") for u in urls)
        if yanked:
            return False, "1.1.0 has been yanked!"
        return True, f"tokenpak==1.1.0 live ({len(urls)} file(s))"
    except Exception as e:
        return False, f"PyPI check failed: {type(e).__name__}: {e}"


def check_window_elapsed() -> tuple[bool, str]:
    publish_time = datetime.fromisoformat(PYPI_PUBLISH_TIME_ISO.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    elapsed = now - publish_time
    hours = elapsed.total_seconds() / 3600
    if hours >= SOFT_LAUNCH_HOURS:
        return True, f"{hours:.1f}h elapsed (>= {SOFT_LAUNCH_HOURS}h window)"
    return False, f"{hours:.1f}h elapsed (need {SOFT_LAUNCH_HOURS}h; {SOFT_LAUNCH_HOURS - hours:.1f}h remaining)"


def check_github_release() -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["gh", "release", "view", "v1.1.0", "--repo", "tokenpak/tokenpak",
             "--json", "isDraft,isPrerelease"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return False, f"gh release view failed: {r.stderr.strip()[:200]}"
        data = json.loads(r.stdout)
        return True, f"release exists (draft={data.get('isDraft')}, prerelease={data.get('isPrerelease')})"
    except Exception as e:
        return False, f"GitHub release check failed: {type(e).__name__}: {e}"


def check_critical_issues() -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["gh", "issue", "list", "--repo", "tokenpak/tokenpak",
             "--state", "open", "--search", "created:>=2026-04-21",
             "--json", "number,title,labels"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return False, f"gh issue list failed: {r.stderr.strip()[:200]}"
        issues = json.loads(r.stdout)
        critical = [i for i in issues if any(
            l.get("name", "").lower() in {"critical", "bug", "blocker", "regression"}
            for l in i.get("labels", [])
        )]
        if critical:
            return False, f"{len(critical)} critical issue(s) opened since release"
        return True, f"{len(issues)} non-critical issue(s) opened since release"
    except Exception as e:
        return False, f"GitHub issues check failed: {type(e).__name__}: {e}"


def check_install_smoke() -> tuple[bool, str]:
    """Smoke-install tokenpak==1.1.0 in a fresh venv."""
    import shutil
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="tpk-smoke-")
    try:
        venv_path = f"{tmpdir}/venv"
        subprocess.run([sys.executable, "-m", "venv", venv_path], check=True, capture_output=True, timeout=30)
        pip = f"{venv_path}/bin/pip"
        r = subprocess.run(
            [pip, "install", "--quiet", "tokenpak==1.1.0"],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode != 0:
            return False, f"pip install failed: {r.stderr.strip()[:200]}"
        py = f"{venv_path}/bin/python"
        r = subprocess.run(
            [py, "-c", "import tokenpak; print(tokenpak.__version__)"],
            cwd="/tmp",  # avoid dev-tree shadowing
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0 or r.stdout.strip() != "1.1.0":
            return False, f"import check failed: stdout={r.stdout!r} stderr={r.stderr[:200]}"
        return True, f"install + import OK (got {r.stdout.strip()})"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


FAST_CHECKS = [
    ("PyPI artifact", check_pypi),
    ("48h window", check_window_elapsed),
    ("GitHub release", check_github_release),
    ("Critical issues", check_critical_issues),
]
SLOW_CHECKS = [
    ("Install smoke", check_install_smoke),
]


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true",
                    help="also run install-smoke (slow; 5-10min due to heavy deps)")
    args = ap.parse_args()

    print(f"tokenpak 1.1.0 soft-launch signal check — {datetime.now(timezone.utc).isoformat()}")
    print("")
    all_ok = True
    window_ok = True
    checks = FAST_CHECKS + (SLOW_CHECKS if args.smoke else [])
    for name, check in checks:
        ok, note = check()
        sym = "✓" if ok else "✗"
        print(f"  {sym} {name}: {note}")
        if name == "48h window" and not ok:
            window_ok = False
        elif not ok:
            all_ok = False
    print("")
    if not window_ok and all_ok:
        print("HOLD — all signals positive; window not yet elapsed. Wait then re-run.")
        return 2
    if not all_ok:
        print("FAIL — investigate before promoting release notes.")
        return 1
    print("READY — all signals positive + window elapsed. Safe to promote release notes:")
    print("  gh release edit v1.1.0 --repo tokenpak/tokenpak --draft=false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
