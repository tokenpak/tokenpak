#!/usr/bin/env python3
"""Trust-conformance release gate.

Consolidates the project's release-readiness *trust* assertions into one check
so that published artifacts cannot silently drift away from the project's
licensing, security-contact, contribution, and public-claim standards.

Assertions
----------
  1. NOTICE present and non-empty (Apache-2.0 attribution requirement,
     License section 4(d)).
  2. LICENSE present and Apache-2.0; packaging metadata license is consistent
     with it (no conflicting SPDX expression / classifier).
  3. SECURITY.md present, with a vulnerability-reporting section and a
     canonical project contact.
  4. CONTRIBUTING.md present, with a Developer Certificate of Origin (DCO)
     sign-off section.
  5. README carries the canonical project hero / identity line.
  6. Regression guard: no retired or unsupported public claim has reappeared
     (a retired compression-era hero, a "same results" promise, an
     unqualified savings percentage, or an untiered "works with <products>"
     client list).

Run modes
---------
  --mode advisory    report findings, always exit 0 (report-only)
  --mode enforcing   exit non-zero when any ERROR-severity finding exists

Baseline scope
--------------
  --scope staging    integration baseline (assertions are live here first)
  --scope public     same assertion set; callers keep this advisory until the
                     public tree has been promoted to the conformant baseline

The script is content-only and side-effect free: it never writes to a database
or mutates state. A drift simply makes the gate exit non-zero in enforcing
mode, exactly like the other release gates; recording that failure against the
release-gate trust counter is the orchestration layer's job, not this script's.

Usage
-----
  python -m scripts.release_gate.trust_conformance            # advisory, cwd
  python -m scripts.release_gate.trust_conformance --mode enforcing
  python -m scripts.release_gate.trust_conformance --json     # machine output
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Canonical reference values.
#
# These are the public-facing identity anchors the gate checks against. They
# are intentionally declared here (rather than buried in a regex) so a future
# brand/identity change is a one-line, reviewed edit.
# --------------------------------------------------------------------------- #

# At least one of these phrases must appear in README for the canonical hero /
# identity line to be considered present.
CANONICAL_HERO_MARKERS = (
    "logistics layer for AI context",
)

# A canonical contact for the security policy is satisfied by either a project
# domain address or the platform's private vulnerability-reporting channel.
CANONICAL_CONTACT_PATTERNS = (
    r"[A-Za-z0-9._%+-]+@tokenpak\.ai",
    r"security/advisories/new",
)

# Retired / forbidden public claims (the regression guard). Each entry is a
# (compiled regex, human description) pair. A match is an ERROR — these are the
# exact drift shapes that must never reship.
_FORBIDDEN_CLAIM_SPECS = (
    (r"compress(?:es|ion|ing)?\s+(?:your\s+)?(?:llm\s+)?context",
     "retired compression-era hero ('compress(es) ... context')"),
    (r"deterministic\s+context\s+compression",
     "retired 'deterministic context compression' tagline"),
    (r"\bsame\s+results\b",
     "unsupported 'same results' equivalence claim"),
    (r"\b(?:save[s]?|cut[s]?|reduce[s]?)\b[^.\n]{0,40}\b\d{1,3}\s?%",
     "unqualified savings percentage"),
    (r"\b\d{1,3}\s?%\b[^.\n]{0,30}\b(?:savings|cheaper|less|reduction|fewer)\b",
     "unqualified savings percentage"),
)

# An untiered client-list claim: a "works with / compatible with" lead-in that
# enumerates products without a tier / plan / status qualifier nearby.
_CLIENT_LIST_LEAD = re.compile(
    r"^[\s>*-]*(?:works with|compatible with)\b(.*)$",
    re.IGNORECASE | re.MULTILINE,
)
_TIER_QUALIFIERS = re.compile(
    r"\b(?:pro|enterprise|team|plan|tier|beta|preview|paid|premium)\b",
    re.IGNORECASE,
)

SEVERITY_ERROR = "ERROR"
SEVERITY_WARN = "WARN"


@dataclass
class Finding:
    severity: str
    check: str
    message: str
    baseline_dependent: bool = False


@dataclass
class Report:
    scope: str
    mode: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: str, check: str, message: str,
            baseline_dependent: bool = False) -> None:
        self.findings.append(Finding(severity, check, message, baseline_dependent))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_WARN]


# --------------------------------------------------------------------------- #
# Individual checks. Each appends Findings to the report.
# --------------------------------------------------------------------------- #

def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def check_notice(root: Path, report: Report) -> None:
    text = _read(root / "NOTICE")
    if not text or not text.strip():
        report.add(SEVERITY_ERROR, "notice",
                   "NOTICE file is missing or empty (Apache-2.0 section 4(d) "
                   "attribution requirement).", baseline_dependent=True)
        return
    if "apache license" not in text.lower():
        report.add(SEVERITY_WARN, "notice",
                   "NOTICE does not reference the Apache License; confirm it is "
                   "the intended attribution notice.")


def check_license(root: Path, report: Report) -> None:
    text = _read(root / "LICENSE")
    if not text or not text.strip():
        report.add(SEVERITY_ERROR, "license", "LICENSE file is missing or empty.")
        return
    low = text.lower()
    is_apache = "apache license" in low and "version 2.0" in low
    if not is_apache:
        report.add(SEVERITY_ERROR, "license",
                   "LICENSE is not the Apache License 2.0 text.")
    # Packaging-metadata consistency.
    pyproject = _read(root / "pyproject.toml")
    if pyproject:
        plow = pyproject.lower()
        mentions_apache = ("apache" in plow) or ("license = \"apache" in plow)
        mentions_other_spdx = bool(
            re.search(r"license\s*=\s*\"(?!.*apache)(mit|bsd|gpl|mpl|isc)\b",
                      plow, re.IGNORECASE))
        if mentions_other_spdx:
            report.add(SEVERITY_ERROR, "license",
                       "pyproject.toml declares a license that conflicts with the "
                       "Apache-2.0 LICENSE file.")
        elif not mentions_apache:
            report.add(SEVERITY_WARN, "license",
                       "pyproject.toml does not clearly declare the Apache-2.0 "
                       "license; confirm packaging metadata matches LICENSE.")


def check_security(root: Path, report: Report) -> None:
    text = _read(root / "SECURITY.md")
    if not text or not text.strip():
        report.add(SEVERITY_ERROR, "security",
                   "SECURITY.md is missing or empty.")
        return
    low = text.lower()
    has_reporting = bool(re.search(r"report(?:ing)?\b[^.\n]{0,40}"
                                   r"(?:security|vulnerab)", low)) \
        or "reporting a security" in low
    if not has_reporting:
        report.add(SEVERITY_ERROR, "security",
                   "SECURITY.md has no vulnerability-reporting section.")
    has_contact = any(re.search(p, text, re.IGNORECASE)
                      for p in CANONICAL_CONTACT_PATTERNS)
    if not has_contact:
        report.add(SEVERITY_ERROR, "security",
                   "SECURITY.md does not list a canonical contact "
                   "(project-domain email or private advisory channel).")


def check_contributing_dco(root: Path, report: Report) -> None:
    text = _read(root / "CONTRIBUTING.md")
    if not text or not text.strip():
        report.add(SEVERITY_ERROR, "contributing",
                   "CONTRIBUTING.md is missing or empty.")
        return
    low = text.lower()
    has_dco = ("developer certificate of origin" in low
               or "developercertificate.org" in low
               or "signed-off-by" in low)
    if not has_dco:
        report.add(SEVERITY_ERROR, "contributing",
                   "CONTRIBUTING.md has no Developer Certificate of Origin "
                   "(DCO) sign-off section.")


def check_canonical_hero(root: Path, report: Report) -> None:
    text = _read(root / "README.md")
    if not text:
        report.add(SEVERITY_ERROR, "hero", "README.md is missing.",
                   baseline_dependent=True)
        return
    if not any(marker.lower() in text.lower() for marker in CANONICAL_HERO_MARKERS):
        report.add(SEVERITY_ERROR, "hero",
                   "README is missing the canonical hero / identity line "
                   f"(expected one of: {', '.join(CANONICAL_HERO_MARKERS)}).",
                   baseline_dependent=True)
    # The top-level title should still name the product.
    first_heading = next((ln for ln in text.splitlines()
                          if ln.lstrip().startswith("# ")), "")
    if "tokenpak" not in first_heading.lower():
        report.add(SEVERITY_WARN, "hero",
                   "README top-level heading does not name the product.")


def check_forbidden_claims(root: Path, report: Report) -> None:
    text = _read(root / "README.md")
    if not text:
        return
    for pattern, desc in _FORBIDDEN_CLAIM_SPECS:
        if re.search(pattern, text, re.IGNORECASE):
            report.add(SEVERITY_ERROR, "claim-regression",
                       f"README contains a {desc}.")
    # Untiered client-list guard.
    for m in _CLIENT_LIST_LEAD.finditer(text):
        tail = m.group(1)
        # Heuristic: a real list enumerates at least two items.
        looks_like_list = tail.count(",") >= 1 or "·" in tail or "•" in tail
        if looks_like_list and not _TIER_QUALIFIERS.search(tail):
            report.add(SEVERITY_ERROR, "claim-regression",
                       "README has an untiered 'works with / compatible with' "
                       "client list (no tier / plan qualifier).")
            break


CHECKS = (
    check_notice,
    check_license,
    check_security,
    check_contributing_dco,
    check_canonical_hero,
    check_forbidden_claims,
)


def run(root: Path, scope: str, mode: str) -> Report:
    report = Report(scope=scope, mode=mode)
    for check in CHECKS:
        check(root, report)
    return report


# --------------------------------------------------------------------------- #
# CLI / output
# --------------------------------------------------------------------------- #

def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for cand in (cur, *cur.parents):
        if (cand / "pyproject.toml").is_file() or (cand / ".git").exists():
            return cand
    return cur


def _render_text(report: Report) -> str:
    lines = [
        f"Trust-conformance gate  scope={report.scope}  mode={report.mode}",
        "-" * 60,
    ]
    if not report.findings:
        lines.append("PASS — all trust-conformance assertions satisfied.")
        return "\n".join(lines)
    for f in report.findings:
        tag = "[baseline]" if f.baseline_dependent else ""
        lines.append(f"  {f.severity:<5} {f.check:<16} {f.message} {tag}".rstrip())
    lines.append("-" * 60)
    lines.append(f"{len(report.errors)} error(s), {len(report.warnings)} warning(s).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Trust-conformance release gate.")
    ap.add_argument("--mode", choices=("advisory", "enforcing"),
                    default="advisory",
                    help="advisory: always exit 0; enforcing: exit non-zero on ERROR")
    ap.add_argument("--scope", choices=("staging", "public"), default="staging",
                    help="baseline scope label")
    ap.add_argument("--root", default=None,
                    help="repository root (default: auto-detect from cwd)")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of text")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else _find_repo_root(Path.cwd())
    report = run(root, scope=args.scope, mode=args.mode)

    if args.json:
        print(json.dumps({
            "scope": report.scope,
            "mode": report.mode,
            "root": str(root),
            "errors": len(report.errors),
            "warnings": len(report.warnings),
            "findings": [vars(f) for f in report.findings],
        }, indent=2))
    else:
        print(_render_text(report))

    if args.mode == "enforcing" and report.errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
