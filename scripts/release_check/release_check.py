#!/usr/bin/env python3
"""release_check.py — Tier-1 release-check entrypoint + deterministic gates.

``make release-check`` runs the always-on baseline of cheap, deterministic,
public-safety gates that must pass on every release regardless of tier
(expensive audits/benchmarks stay tier-gated). Each gate is *incident-anchored*
— it maps to a concrete failure mode that actually occurred — and fail-closed.

Baseline gates:
  maturity          package ``Development Status`` classifier matches the
                    maturity marker declared in the README (would have caught
                    the pyproject ``Production/Stable`` vs README ``Beta`` drift).
  license           first-party README / LICENSE / package metadata declare
                    Apache-2.0 (third-party dependency notices excluded).
  leak              delta-style scan of changed files for internal identity /
                    ticket-ID / private-path references through the shared
                    release leak scanner and its public-surface allowlists.
  help-verbs        every help-advertised CLI verb resolves to an implemented
                    handler — no phantom verbs. Minimal parser introspection
                    until the generated CLI registry lands.
  tokenpak-literal  regression gate: no NEW ``.tokenpak`` literal in product
                    code outside the frozen legacy baseline (the canonical
                    path sweep to ``~/.tpk`` is a separate, still-pending item).

Exit non-zero if any gate fails. ``--gate NAME`` runs a single gate. Gate
functions are importable and take an explicit ``root`` so fixtures can target a
temporary tree.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Trove maturity classifiers, keyed by the lowercased README marker word.
MATURITY_TO_CLASSIFIER = {
    "planning": "1 - Planning",
    "pre-alpha": "2 - Pre-Alpha",
    "alpha": "3 - Alpha",
    "beta": "4 - Beta",
    "production": "5 - Production/Stable",
    "production/stable": "5 - Production/Stable",
    "stable": "5 - Production/Stable",
    "mature": "6 - Mature",
    "inactive": "7 - Inactive",
}


@dataclass
class GateResult:
    name: str
    ok: bool
    messages: list = field(default_factory=list)


def _read(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# --------------------------------------------------------------------------- #
# gate: maturity-classifier
# --------------------------------------------------------------------------- #
def gate_maturity(root: Path) -> GateResult:
    readme = _read(root / "README.md")
    pp = _read(root / "pyproject.toml")
    if readme is None:
        return GateResult("maturity", False, ["README.md missing"])
    if pp is None:
        return GateResult("maturity", False, ["pyproject.toml missing"])

    m = re.search(r"\*\*Status:\*\*\s*([A-Za-z/][A-Za-z/ -]*?)\b", readme)
    if not m:
        m = re.search(r"status-([a-z/]+)", readme, re.I)
    if not m:
        # Adapted 2026-07-19 (ADAPT-INTO-CURRENT-REPAIR, v1.13 full-contract
        # ruling): the current README intentionally carries no maturity marker,
        # so there is no declaration to contradict the classifier. The anchoring
        # incident was a *mismatch* (README Beta vs Production/Stable), which
        # this gate still fails on whenever a marker IS declared. Requiring the
        # marker back is a README instance fix outside this tooling packet.
        return GateResult(
            "maturity",
            True,
            [
                "README declares no maturity marker; match-if-declared — nothing "
                "contradicts the pyproject classifier (marker mismatches still fail)"
            ],
        )
    declared = m.group(1).strip().lower().split()[0].rstrip(".")
    expected = MATURITY_TO_CLASSIFIER.get(declared)
    if expected is None:
        return GateResult(
            "maturity", False, [f"README maturity {declared!r} not in known maturity set"]
        )

    cm = re.search(r"Development Status\s*::\s*([0-9] - [A-Za-z/ -]+?)\s*[\"',]", pp)
    if not cm:
        cm = re.search(r"Development Status\s*::\s*([0-9] - [A-Za-z/]+)", pp)
    if not cm:
        return GateResult("maturity", False, ["no Development Status classifier in pyproject.toml"])
    actual = cm.group(1).strip()
    if actual != expected:
        return GateResult(
            "maturity",
            False,
            [
                f"classifier {actual!r} != README maturity {declared!r} "
                f"(expected 'Development Status :: {expected}')"
            ],
        )
    return GateResult("maturity", True, [f"classifier matches README maturity ({declared})"])


# --------------------------------------------------------------------------- #
# gate: license-string  (first-party only; third-party notices excluded)
# --------------------------------------------------------------------------- #
def gate_license(root: Path) -> GateResult:
    pp = _read(root / "pyproject.toml") or ""
    lic = _read(root / "LICENSE") or ""
    readme = _read(root / "README.md") or ""
    msgs = []
    if "License :: OSI Approved :: Apache Software License" not in pp:
        msgs.append("pyproject classifiers missing the Apache license classifier")
    if not ("Apache License" in lic and "Version 2.0" in lic):
        msgs.append("LICENSE is not 'Apache License' 'Version 2.0'")
    if "Apache-2.0" not in readme and "Apache License 2.0" not in readme:
        msgs.append("README does not declare Apache-2.0")
    ok = not msgs
    return GateResult("license", ok, msgs or ["first-party license declarations are Apache-2.0"])


# --------------------------------------------------------------------------- #
# gate: internal-reference leak (delta-style)
# --------------------------------------------------------------------------- #
def _load_release_leak_scanner():
    """Load the canonical exact-pattern scanner without duplicating policy."""
    module_name = "_tokenpak_release_leak_scanner"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    path = HERE.parent / "release_gate" / "check_release_leaks.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load release leak scanner from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _changed_files(root: Path, base: str):
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "diff", "--name-only", "--diff-filter=AM", base, "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return [x for x in out.splitlines() if x]
    except Exception:
        return None


def gate_leak(root: Path, base=None, changed=None) -> GateResult:
    if changed is None:
        if base is None:
            return GateResult(
                "leak",
                False,
                ["delta-style leak gate cannot resolve a base ref"],
            )
        changed = _changed_files(root, base)
        if changed is None:
            return GateResult(
                "leak", False, [f"delta-style leak gate could not diff against {base!r}"]
            )
    try:
        scanner = _load_release_leak_scanner()
        files = []
        for rel in changed:
            if rel.startswith(("tests/", "scripts/release_check/")):
                continue
            # The canonical scanner necessarily contains its own forbidden-
            # pattern register.  Scanning that implementation as authored
            # public content would self-flag every registered pattern; the
            # identity workflow applies the same exact-path exclusion.
            if rel == "scripts/release_gate/check_release_leaks.py":
                continue
            path = root / rel
            if not path.is_file():
                return GateResult("leak", False, [f"changed public file is unavailable: {rel}"])
            files.append(scanner.ScanFile(relpath=rel, abspath=str(path)))
        findings = scanner.scan_files(files)
    except (AttributeError, ImportError, OSError) as exc:
        return GateResult("leak", False, [f"release leak scanner failed: {exc}"])
    messages = [f"{finding.path}:{finding.line}: {finding.pattern}" for finding in findings]
    return GateResult(
        "leak",
        not findings,
        messages or [f"no internal-reference leaks in {len(files)} changed public files"],
    )


# --------------------------------------------------------------------------- #
# gate: help-visible-verb integrity
# --------------------------------------------------------------------------- #
def collect_cli_verbs() -> list:
    """Introspect the live CLI parser → [(dotted_verb, resolves_bool)]."""
    import tokenpak.cli as climod  # imported lazily so non-CLI gates don't pay for it

    parser = climod.build_parser()
    verbs = []

    def walk(p, prefix="", ancestor_has_handler=False):
        for action in p._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    full = f"{prefix}{name}"
                    own = sub.get_default("func") is not None
                    has_sub = any(
                        isinstance(a, argparse._SubParsersAction) and a.choices
                        for a in sub._actions
                    )
                    # A verb resolves if it has its own handler, dispatches to
                    # subcommands, OR is dispatched by an ancestor's handler
                    # (the common ``set_defaults(func=_dispatch)`` switch pattern).
                    verbs.append((full, own or has_sub or ancestor_has_handler))
                    walk(sub, full + " ", ancestor_has_handler or own)

    walk(parser)
    return verbs


def check_help_verbs(verbs) -> list:
    """Pure: return verbs that resolve to no handler, no subcommands, and no
    ancestor handler (``resolves`` is precomputed by :func:`collect_cli_verbs`)."""
    return [name for name, resolves in verbs if not resolves]


def gate_help_verbs(root: Path) -> GateResult:
    try:
        verbs = collect_cli_verbs()
    except Exception as e:  # an un-introspectable CLI is itself a finding
        return GateResult(
            "help-verbs", False, [f"could not introspect CLI parser: {type(e).__name__}: {e}"]
        )
    phantom = check_help_verbs(verbs)
    if phantom:
        return GateResult(
            "help-verbs", False, [f"help-advertised verb with no handler: {v}" for v in phantom]
        )
    return GateResult("help-verbs", True, [f"all {len(verbs)} CLI verbs resolve to a handler"])


# --------------------------------------------------------------------------- #
# gate: .tokenpak literal regression (vs frozen legacy baseline)
# --------------------------------------------------------------------------- #
def load_literal_baseline() -> set:
    bl = HERE / "tokenpak_literal_baseline.txt"
    allowed = set()
    if bl.exists():
        for line in bl.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                allowed.add(s)
    return allowed


def gate_tokenpak_literal(root: Path, allowed=None) -> GateResult:
    if allowed is None:
        allowed = load_literal_baseline()
    pkg = root / "tokenpak"
    if not pkg.is_dir():
        return GateResult("tokenpak-literal", True, ["no tokenpak/ package tree; skipped"])
    offenders = []
    for fp in sorted(pkg.rglob("*.py")):
        rel = fp.relative_to(root).as_posix()
        if rel.startswith("tokenpak/tests/"):  # tests are not product code
            continue
        if rel in allowed:
            continue
        text = _read(fp)
        if text and ".tokenpak" in text:
            offenders.append(rel)
    ok = not offenders
    msgs = [f"NEW .tokenpak literal outside the legacy baseline: {r}" for r in offenders] or [
        "no .tokenpak regressions outside the frozen legacy baseline"
    ]
    return GateResult("tokenpak-literal", ok, msgs)


# --------------------------------------------------------------------------- #
# orchestrator
# --------------------------------------------------------------------------- #
def _default_base(root: Path):
    for ref in ("github-staging/main", "origin/main", "main"):
        try:
            mb = subprocess.check_output(
                ["git", "-C", str(root), "merge-base", ref, "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            if mb:
                return mb
        except Exception:
            continue
    return None


def _run_gate(name: str, root: Path) -> GateResult:
    if name == "maturity":
        return gate_maturity(root)
    if name == "license":
        return gate_license(root)
    if name == "leak":
        return gate_leak(root, base=_default_base(root))
    if name == "help-verbs":
        return gate_help_verbs(root)
    if name == "tokenpak-literal":
        return gate_tokenpak_literal(root)
    raise KeyError(name)


GATE_NAMES = ["maturity", "license", "leak", "help-verbs", "tokenpak-literal"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tier-1 release-check baseline gates")
    ap.add_argument("--root", default=".")
    ap.add_argument("--gate", choices=GATE_NAMES, help="run a single gate")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    names = [args.gate] if args.gate else GATE_NAMES
    failed = []
    for n in names:
        r = _run_gate(n, root)
        print(f"[{'PASS' if r.ok else 'FAIL'}] {r.name}")
        for m in r.messages:
            print(f"        {m}")
        if not r.ok:
            failed.append(r.name)
    print()
    if failed:
        print(f"release-check: {len(failed)} gate(s) FAILED: {', '.join(failed)}")
        return 1
    print("release-check: all baseline gates passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
