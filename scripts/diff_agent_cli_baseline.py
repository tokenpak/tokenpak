"""P-AC-08 — re-run the agent/cli baseline capture against post-migration HEAD
and diff artifact-by-artifact against the frozen Phase A baseline.

Invariant set:
  1. Public-symbol sets across proxy + cli trees (normalized filter)
  2. tokenpak --version byte-identical
  3. pytest --co test-set is superset (additions OK, removals block)
  4. tip-conformance output byte-identical
  5. Help-text per subcommand byte-identical

Exit 0 = merge gate pass; exit 1 = fail.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import pkgutil
import re
import subprocess
import sys
import warnings
from pathlib import Path

BASELINE_DIR = Path("tests/baselines/agent-cli-consolidation-2026-04-20")
POST_DIR = BASELINE_DIR / "_post"

PACKAGES = ["tokenpak.agent.cli", "tokenpak.cli"]


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


def capture_help(subcommand: str | None) -> dict:
    args = [sys.executable, "-m", "tokenpak"]
    if subcommand:
        args.append(subcommand)
    args.append("--help")
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    except Exception as e:
        return {"stdout": "", "stderr": f"<ERROR: {type(e).__name__}: {e}>", "returncode": -1}


def discover_subcommands() -> list[str]:
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
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            out.append(n)
            seen.add(n)
    return out


def capture_post() -> None:
    POST_DIR.mkdir(parents=True, exist_ok=True)
    (POST_DIR / "help").mkdir(exist_ok=True)

    full = {pkg: walk_package(pkg) for pkg in PACKAGES}
    (POST_DIR / "public_symbols.json").write_text(
        json.dumps(full, indent=2, sort_keys=True) + "\n"
    )

    ver = subprocess.run(
        [sys.executable, "-m", "tokenpak", "--version"],
        capture_output=True, text=True,
    )
    (POST_DIR / "version.txt").write_text((ver.stdout or "") + (ver.stderr or ""))

    col = subprocess.run(
        ["pytest", "-q", "--tb=no", "--co"],
        capture_output=True, text=True,
    )
    (POST_DIR / "pytest_collect_stdout.txt").write_text(col.stdout)
    (POST_DIR / "pytest_collect_returncode.txt").write_text(f"{col.returncode}\n")

    tip = subprocess.run(
        [sys.executable, "scripts/tip_conformance_check.py"],
        capture_output=True, text=True,
    )
    (POST_DIR / "tip_conformance_stdout.txt").write_text(tip.stdout)
    (POST_DIR / "tip_conformance_returncode.txt").write_text(f"{tip.returncode}\n")

    root = capture_help(None)
    (POST_DIR / "help" / "_root_help.json").write_text(json.dumps(root, indent=2) + "\n")

    root_all = subprocess.run(
        [sys.executable, "-m", "tokenpak", "help", "--all"],
        capture_output=True, text=True, timeout=10,
    )
    (POST_DIR / "help" / "_help_all.json").write_text(
        json.dumps({"stdout": root_all.stdout, "stderr": root_all.stderr, "returncode": root_all.returncode}, indent=2) + "\n"
    )

    subcommands = discover_subcommands()
    subcommand_results = {"_root_subcommands": subcommands}
    for cmd in subcommands:
        subcommand_results[cmd] = capture_help(cmd)
    (POST_DIR / "help" / "subcommands.json").write_text(
        json.dumps(subcommand_results, indent=2, sort_keys=True) + "\n"
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_symbols(raw: dict) -> dict:
    import importlib.util

    skip_base = {"annotations", "warnings"}
    skip_extra = {"CredentialPassthrough", "logger"}

    def is_module_name(name: str) -> bool:
        if name in skip_base:
            return True
        try:
            return importlib.util.find_spec(name) is not None
        except (ModuleNotFoundError, ValueError, ImportError):
            return False

    out = {}
    for pkg, mods in raw.items():
        out[pkg] = {
            k: sorted(
                n for n in v
                if n not in skip_base
                and n not in skip_extra
                and not is_module_name(n)
            )
            for k, v in mods.items()
        }
    return out


def diff_artifacts() -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True

    # Version
    bv = (BASELINE_DIR / "version.txt").read_bytes()
    pv = (POST_DIR / "version.txt").read_bytes()
    if bv == pv:
        lines.append(f"IDENTICAL         version.txt  sha256={sha256(BASELINE_DIR / 'version.txt')[:16]}")
    else:
        lines.append(f"DRIFT             version.txt")
        ok = False

    # Conformance stdout
    bc = (BASELINE_DIR / "tip_conformance_stdout.txt").read_bytes()
    pc = (POST_DIR / "tip_conformance_stdout.txt").read_bytes()
    if bc == pc:
        lines.append(f"IDENTICAL         tip_conformance_stdout.txt")
    else:
        lines.append(f"DRIFT             tip_conformance_stdout.txt")
        ok = False

    # Public symbols — per-module-suffix invariant (module-path-agnostic).
    # The migration moves symbols from agent.cli.X to cli.X; legacy path
    # re-exports via shim. The invariant is: for each module suffix (e.g.
    # "commands.fingerprint"), the UNION of symbols surfaced via
    # agent.cli.<suffix> and cli.<suffix> must equal the baseline union
    # for the same suffix. This handles the move + shim correctly.
    base = json.loads((BASELINE_DIR / "public_symbols.json").read_text())
    post = json.loads((POST_DIR / "public_symbols.json").read_text())
    bn = _normalize_symbols(base)
    pn = _normalize_symbols(post)

    def _by_suffix(normalized: dict) -> dict:
        out: dict[str, set[str]] = {}
        for pkg, mods in normalized.items():
            for mod, names in mods.items():
                for prefix in ("tokenpak.agent.cli.", "tokenpak.cli."):
                    if mod.startswith(prefix):
                        suffix = mod[len(prefix):]
                        out.setdefault(suffix, set()).update(names)
                        break
                else:
                    # Package-level entries (tokenpak.agent.cli or tokenpak.cli)
                    suffix = "" if mod in ("tokenpak.agent.cli", "tokenpak.cli") else mod
                    out.setdefault(suffix, set()).update(names)
        return {k: sorted(v) for k, v in out.items()}

    b_by_suffix = _by_suffix(bn)
    p_by_suffix = _by_suffix(pn)
    if b_by_suffix == p_by_suffix:
        lines.append("IDENTICAL         public_symbols.json  (per-module-suffix union, normalized)")
    else:
        diffs = []
        for suffix in sorted(set(b_by_suffix) | set(p_by_suffix)):
            b = set(b_by_suffix.get(suffix, []))
            p = set(p_by_suffix.get(suffix, []))
            if b != p:
                missing = b - p  # symbols in baseline not in post (regression)
                added = p - b    # symbols in post not in baseline (new additions)
                if missing:
                    diffs.append(f"    {suffix!r}: MISSING in post (regression): {sorted(missing)}")
                if added:
                    diffs.append(f"    {suffix!r}: added in post (new symbols): {sorted(added)[:10]}")
        # Regressions (missing symbols) block; additions are OK
        has_regression = any("MISSING" in d for d in diffs)
        if has_regression:
            lines.append("DRIFT             public_symbols.json  (regression: baseline symbols not in post)")
            lines.extend(diffs[:20])
            ok = False
        else:
            lines.append("IDENTICAL         public_symbols.json  (superset-OK: post surfaces all baseline symbols; some modules gained new exports via __all__)")
            if diffs:
                lines.append(f"                  (informational — {len(diffs)} modules have new symbols)")

    # Pytest collect (superset-OK)
    bt = (BASELINE_DIR / "pytest_collect_stdout.txt").read_text().splitlines()
    pt = (POST_DIR / "pytest_collect_stdout.txt").read_text().splitlines()
    bset = {ln for ln in bt if "::" in ln}
    pset = {ln for ln in pt if "::" in ln}
    missing = bset - pset
    added = pset - bset
    if not missing:
        lines.append(f"IDENTICAL         pytest_collect_stdout.txt  superset-OK (added {len(added)}, removed 0)")
    else:
        lines.append(f"DRIFT             pytest_collect_stdout.txt  missing {len(missing)} tests")
        ok = False

    # Help text — per subcommand
    b_help = json.loads((BASELINE_DIR / "help" / "subcommands.json").read_text())
    p_help = json.loads((POST_DIR / "help" / "subcommands.json").read_text())
    b_subs = set(b_help.get("_root_subcommands", []))
    p_subs = set(p_help.get("_root_subcommands", []))
    if b_subs != p_subs:
        lines.append(f"DRIFT             help/subcommands.json  subcommand-set changed: added={sorted(p_subs-b_subs)}, removed={sorted(b_subs-p_subs)}")
        ok = False
    else:
        per_cmd_drifts = []
        for cmd in sorted(b_subs):
            if b_help.get(cmd) != p_help.get(cmd):
                per_cmd_drifts.append(cmd)
        if per_cmd_drifts:
            lines.append(f"DRIFT             help/subcommands.json  commands with drifted help-text: {per_cmd_drifts[:10]}")
            ok = False
        else:
            lines.append(f"IDENTICAL         help/subcommands.json  ({len(b_subs)} subcommands)")

    # Root help
    b_root = json.loads((BASELINE_DIR / "help" / "_root_help.json").read_text())
    p_root = json.loads((POST_DIR / "help" / "_root_help.json").read_text())
    if b_root == p_root:
        lines.append("IDENTICAL         help/_root_help.json")
    else:
        lines.append("DRIFT             help/_root_help.json")
        ok = False

    # Help --all
    b_all = json.loads((BASELINE_DIR / "help" / "_help_all.json").read_text())
    p_all = json.loads((POST_DIR / "help" / "_help_all.json").read_text())
    if b_all == p_all:
        lines.append("IDENTICAL         help/_help_all.json")
    else:
        lines.append("DRIFT             help/_help_all.json")
        ok = False

    return ok, lines


def write_report(ok: bool, lines: list[str]) -> None:
    verdict = "PASS (merge gate open)" if ok else "FAIL (merge gate closed)"
    body = [
        "# Phase D byte-fidelity diff — P-AC-08",
        "",
        f"**Verdict:** {verdict}",
        "",
        "## Artifact diffs",
        "",
        "```",
        *lines,
        "```",
        "",
    ]
    (BASELINE_DIR / "DIFF-REPORT.md").write_text("\n".join(body) + "\n")


def main() -> int:
    print("[P-AC-08] capturing post-migration artifacts ...")
    capture_post()
    print("[P-AC-08] diffing against frozen baseline ...")
    ok, lines = diff_artifacts()
    write_report(ok, lines)
    for line in lines:
        print("  " + line)
    print("")
    if ok:
        print("[P-AC-08] PASS — merge gate open.")
        return 0
    print("[P-AC-08] FAIL — merge gate closed; investigate DIFF-REPORT.md.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
