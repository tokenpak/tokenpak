#!/usr/bin/env python3
"""Delta scanner for first-party license declarations.

The scanner reads the license ledger frontmatter for the canonical value,
forbidden values, and allowlist contexts. It blocks current first-party license
declarations that use a superseded value while skipping dependency metadata,
vendored trees, comparison rows, private commercial package paths, and
historical notes.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

TEXT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".ini",
        ".js",
        ".json",
        ".md",
        ".py",
        ".rst",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)

SKIP_PREFIXES = (
    ".git/",
    ".pytest_cache/",
    ".ruff_cache/",
    "build/",
    "dist/",
    "htmlcov/",
    "node_modules/",
    "packages/tests/",
    "sdk/dist/",
    "sdk/tests/",
    "site-packages/",
    "tests/",
    "third_party/",
    "third-party/",
    "tokenpak-paid/",
    "vendor/",
    "vendored/",
)

SKIP_BASENAMES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
    "Cargo.lock",
    "composer.lock",
}

LEDGER_PATHS = {
    "decisions/ledger/license.md",
}

DECLARATION_HINT = re.compile(
    r"\b(licen[cs]e|licensed|spdx-license-identifier)\b|License :: OSI",
    re.IGNORECASE,
)
HISTORICAL_HINT = re.compile(
    r"\b(previous|previously|prior|historical|history|changelog|legacy|old|was|"
    r"superseded|baseline)\b",
    re.IGNORECASE,
)
COMPARISON_HINT = re.compile(
    r"\b(comparison|competitor|third[- ]party|other tool|matrix|table)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LicensePolicy:
    canonical_value: str
    forbidden_values: tuple[str, ...]
    allowlist_contexts: tuple[str, ...]


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    matched: str
    text: str


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_inline_list(value: str) -> tuple[str, ...]:
    value = value.strip()
    if not value:
        return ()
    if value.startswith("["):
        parsed = ast.literal_eval(value)
        return tuple(str(item) for item in parsed)
    return tuple(_unquote(item) for item in value.split(",") if item.strip())


def _frontmatter(text: str, path: Path) -> list[str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SystemExit(f"error: {path} does not start with YAML frontmatter")
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:idx]
    raise SystemExit(f"error: {path} has unterminated YAML frontmatter")


def load_policy(path: Path) -> LicensePolicy:
    lines = _frontmatter(path.read_text(encoding="utf-8"), path)
    data: dict[str, object] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            i += 1
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if key == "allowlist_contexts" and not raw_value:
            values: list[str] = []
            i += 1
            while i < len(lines) and lines[i].startswith("  - "):
                values.append(_unquote(lines[i][4:].strip()))
                i += 1
            data[key] = tuple(values)
            continue
        if key == "forbidden_values":
            data[key] = _parse_inline_list(raw_value)
        elif key == "canonical_value":
            data[key] = _unquote(raw_value)
        i += 1

    missing = [k for k in ("canonical_value", "forbidden_values", "allowlist_contexts") if k not in data]
    if missing:
        raise SystemExit(f"error: {path} missing required field(s): {', '.join(missing)}")
    return LicensePolicy(
        canonical_value=str(data["canonical_value"]),
        forbidden_values=tuple(str(v) for v in data["forbidden_values"]),
        allowlist_contexts=tuple(str(v) for v in data["allowlist_contexts"]),
    )


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9.+-]){re.escape(term)}(?![A-Za-z0-9.+-])",
        re.IGNORECASE,
    )


def _is_skipped_path(relpath: str) -> bool:
    relpath = relpath.replace(os.sep, "/")
    if relpath in LEDGER_PATHS:
        return True
    if any(relpath.startswith(prefix) for prefix in SKIP_PREFIXES):
        return True
    if relpath.endswith(".min.js"):
        return True
    return relpath.rsplit("/", 1)[-1] in SKIP_BASENAMES


def _is_text_path(relpath: str) -> bool:
    suffix = Path(relpath).suffix
    return suffix in TEXT_SUFFIXES or suffix == ""


def _line_allowlisted(relpath: str, line: str) -> bool:
    base = relpath.rsplit("/", 1)[-1].lower()
    if relpath.startswith("scripts/release_gate/") and (
        "re.search" in line or "regex" in line.lower() or "forbidden_values" in line
    ):
        return True
    if "comparison" in base and COMPARISON_HINT.search(line):
        return True
    if HISTORICAL_HINT.search(line):
        return True
    if COMPARISON_HINT.search(line):
        return True
    return False


def _find_matching_term(line: str, patterns: list[tuple[str, re.Pattern[str]]]) -> str | None:
    for term, pattern in patterns:
        if pattern.search(line):
            return term
    return None


def scan_text(relpath: str, text: str, policy: LicensePolicy) -> list[Finding]:
    patterns = [(term, _term_pattern(term)) for term in policy.forbidden_values]
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not DECLARATION_HINT.search(line):
            continue
        matched = _find_matching_term(line, patterns)
        if not matched or _line_allowlisted(relpath, line):
            continue
        findings.append(Finding(relpath, line_no, matched, line.strip()))
    return findings


def collect_paths(root: Path, paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        rel = raw.strip().replace(os.sep, "/")
        if not rel or _is_skipped_path(rel) or not _is_text_path(rel):
            continue
        path = root / rel
        if path.is_file():
            out.append(Path(rel))
    return out


def collect_git_tracked(root: Path) -> list[Path]:
    try:
        raw = subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "-z"],
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return collect_paths(root, raw.decode("utf-8", errors="ignore").split("\0"))


def read_paths_file(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def scan_paths(root: Path, paths: list[Path], policy: LicensePolicy) -> list[Finding]:
    findings: list[Finding] = []
    for rel in paths:
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(scan_text(rel.as_posix(), text, policy))
    return findings


def report_findings(findings: list[Finding], annotation: str, canonical_value: str) -> None:
    for finding in findings:
        if annotation != "none":
            print(
                f"::{annotation} file={finding.path},line={finding.line}::"
                f"license policy forbids {finding.matched!r}; use {canonical_value}."
            )
        print(
            f"  {finding.path}:{finding.line}: "
            f"forbidden license declaration {finding.matched!r}: {finding.text[:200]}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--git-tracked", action="store_true")
    src.add_argument("--paths-from", metavar="FILE")
    src.add_argument("--paths", nargs="*")
    parser.add_argument("--root", default=".")
    parser.add_argument("--ledger", default="decisions/ledger/license.md")
    parser.add_argument("--annotation", choices=["error", "warning", "none"], default="error")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    ledger = Path(args.ledger)
    if not ledger.is_absolute():
        ledger = root / ledger
    policy = load_policy(ledger)

    if args.git_tracked:
        targets = collect_git_tracked(root)
        source = "tracked files"
    elif args.paths_from:
        targets = collect_paths(root, read_paths_file(Path(args.paths_from)))
        source = args.paths_from
    else:
        targets = collect_paths(root, args.paths or [])
        source = "provided paths"

    findings = scan_paths(root, targets, policy)
    if findings:
        report_findings(findings, args.annotation, policy.canonical_value)
        print(
            f"license-policy: {len(findings)} finding(s) in {source}; "
            f"canonical value is {policy.canonical_value}.",
        )
        return 1

    print(
        f"license-policy: scanned {len(targets)} file(s) from {source}; "
        f"no forbidden declarations. "
        f"canonical={policy.canonical_value}; allowlists={len(policy.allowlist_contexts)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
