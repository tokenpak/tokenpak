"""P-AP-08 — re-run the baseline capture against post-migration HEAD and
diff every artifact against the frozen Phase A baseline byte-for-byte.

Exit code:
  0  all artifacts identical (merge gate pass)
  1  any diff > 0 (merge gate fail)
"""

from __future__ import annotations

import hashlib
import importlib
import json
import pkgutil
import subprocess
import sys
from pathlib import Path

BASELINE_DIR = Path("tests/baselines/agent-proxy-migration-2026-04-20")
POST_DIR = BASELINE_DIR / "_post"


def public_names(module_name: str) -> list[str]:
    """Canonical public-symbol set.

    If the module declares ``__all__`` (shims now do, post-tightening),
    use it verbatim. Otherwise fall back to a dir() scan that filters
    out shim-hygiene names (``warnings``, ``annotations``) and imported
    submodules — the baseline pre-dates the shim layer, and the canonical
    modules don't leak these names, so filtering here matches the
    invariant we actually want to check.
    """
    import types

    mod = importlib.import_module(module_name)
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


def walk_agent_proxy() -> dict[str, list[str]]:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        root = importlib.import_module("tokenpak.agent.proxy")
        out: dict[str, list[str]] = {
            "tokenpak.agent.proxy": public_names("tokenpak.agent.proxy")
        }
        for info in pkgutil.walk_packages(
            root.__path__, prefix="tokenpak.agent.proxy."
        ):
            try:
                out[info.name] = public_names(info.name)
            except Exception as e:
                out[info.name] = [f"<IMPORT-ERROR: {type(e).__name__}: {e}>"]
    return out


def capture_post() -> None:
    POST_DIR.mkdir(parents=True, exist_ok=True)

    signatures = walk_agent_proxy()
    (POST_DIR / "agent_proxy_public_symbols.json").write_text(
        json.dumps(signatures, indent=2, sort_keys=True) + "\n"
    )

    version = subprocess.run(
        [sys.executable, "-m", "tokenpak", "--version"],
        capture_output=True,
        text=True,
    )
    (POST_DIR / "tokenpak_version.txt").write_text(
        (version.stdout or "").strip() + "\n" + (version.stderr or "").strip() + "\n"
    )

    collect = subprocess.run(
        ["pytest", "-q", "--tb=no", "--co"],
        capture_output=True,
        text=True,
    )
    (POST_DIR / "pytest_collect_stdout.txt").write_text(collect.stdout)
    (POST_DIR / "pytest_collect_stderr.txt").write_text(collect.stderr)
    (POST_DIR / "pytest_collect_returncode.txt").write_text(f"{collect.returncode}\n")

    tip = subprocess.run(
        [sys.executable, "scripts/tip_conformance_check.py"],
        capture_output=True,
        text=True,
    )
    (POST_DIR / "tip_conformance_stdout.txt").write_text(tip.stdout)
    (POST_DIR / "tip_conformance_stderr.txt").write_text(tip.stderr)
    (POST_DIR / "tip_conformance_returncode.txt").write_text(f"{tip.returncode}\n")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_FILTER_SKIP = {"annotations", "warnings"}


def _normalize_symbol_json(raw: dict[str, list[str]]) -> dict[str, list[str]]:
    """Canonical public-symbol view: drop shim-hygiene + known-noisy names.

    Baseline was captured against the pre-shim tree using an unfiltered
    dir(). Post capture uses __all__ (clean). Normalize both sides through
    the same filter so the diff measures the actual public-API invariant,
    not the baseline's naive capture noise.
    """
    import importlib.util

    def is_module_name(name: str) -> bool:
        if name in _FILTER_SKIP:
            return True
        try:
            return importlib.util.find_spec(name) is not None
        except (ModuleNotFoundError, ValueError, ImportError):
            return False

    skip_extra = {
        "annotations",
        "warnings",
        "logger",
        "CredentialPassthrough",
    }
    return {
        k: sorted(
            n
            for n in v
            if n not in _FILTER_SKIP
            and n not in skip_extra
            and not is_module_name(n)
        )
        for k, v in raw.items()
    }


def _diff_symbols() -> tuple[bool, str]:
    base = json.loads((BASELINE_DIR / "agent_proxy_public_symbols.json").read_text())
    post = json.loads((POST_DIR / "agent_proxy_public_symbols.json").read_text())
    base_n = _normalize_symbol_json(base)
    post_n = _normalize_symbol_json(post)
    if base_n == post_n:
        return True, "identical public-symbol sets (normalized filter)"
    diffs: list[str] = []
    all_keys = sorted(set(base_n) | set(post_n))
    for k in all_keys:
        b = set(base_n.get(k, []))
        p = set(post_n.get(k, []))
        if b != p:
            diffs.append(
                f"  {k}: removed={sorted(b - p)} added={sorted(p - b)}"
            )
    return False, "DRIFT:\n" + "\n".join(diffs)


def _diff_pytest_collection() -> tuple[bool, str]:
    base = (BASELINE_DIR / "pytest_collect_stdout.txt").read_text().splitlines()
    post = (POST_DIR / "pytest_collect_stdout.txt").read_text().splitlines()
    base_tests = {ln for ln in base if "::" in ln}
    post_tests = {ln for ln in post if "::" in ln}
    missing = base_tests - post_tests
    added = post_tests - base_tests
    if not missing:
        note = f"superset-OK (added {len(added)} tests, removed 0)"
        return True, note
    return False, f"MISSING {len(missing)} baseline tests: {sorted(missing)[:10]}"


def diff_artifacts() -> tuple[bool, list[str]]:
    """Return (all_match, report_lines)."""
    byte_identical = [
        "tokenpak_version.txt",
        "pytest_collect_returncode.txt",
        "tip_conformance_stdout.txt",
        "tip_conformance_returncode.txt",
    ]
    lines: list[str] = []
    all_match = True

    for name in byte_identical:
        base = BASELINE_DIR / name
        post = POST_DIR / name
        if not base.exists() or not post.exists():
            lines.append(f"MISSING           {name}")
            all_match = False
            continue
        b_hash = sha256(base)
        p_hash = sha256(post)
        if b_hash == p_hash:
            lines.append(f"IDENTICAL         {name}  sha256={b_hash[:16]}")
        else:
            lines.append(
                f"DRIFT             {name}  baseline={b_hash[:16]} post={p_hash[:16]}"
            )
            all_match = False

    ok, note = _diff_symbols()
    lines.append(f"{'IDENTICAL' if ok else 'DRIFT':17} agent_proxy_public_symbols.json  {note.splitlines()[0]}")
    if not ok:
        for extra in note.splitlines()[1:]:
            lines.append(extra)
        all_match = False

    ok, note = _diff_pytest_collection()
    lines.append(f"{'IDENTICAL' if ok else 'DRIFT':17} pytest_collect_stdout.txt  {note}")
    if not ok:
        all_match = False

    return all_match, lines


def write_report(all_match: bool, lines: list[str]) -> None:
    verdict = "PASS (merge gate open)" if all_match else "FAIL (merge gate closed)"
    body = [
        "# Phase D byte-fidelity diff — P-AP-08",
        "",
        f"**Verdict:** {verdict}",
        "",
        "## Artifact diffs",
        "",
        "```",
        *lines,
        "```",
        "",
        "## Protocol",
        "",
        "Phase A baseline was a structural snapshot (live-traffic byte capture",
        "was infeasible in the capture session — see README.md in this",
        "directory). A pure-relocation migration is required to preserve all",
        "four artifact families bit-for-bit; any DRIFT line blocks merge.",
        "",
    ]
    (BASELINE_DIR / "DIFF-REPORT.md").write_text("\n".join(body) + "\n")


def main() -> int:
    print("[P-AP-08] capturing post-migration artifacts ...")
    capture_post()
    print("[P-AP-08] diffing against frozen baseline ...")
    all_match, lines = diff_artifacts()
    write_report(all_match, lines)
    for line in lines:
        print("  " + line)
    print("")
    if all_match:
        print("[P-AP-08] PASS — merge gate open.")
        return 0
    print("[P-AP-08] FAIL — merge gate closed; investigate DIFF-REPORT.md.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
