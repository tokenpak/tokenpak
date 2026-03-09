#!/usr/bin/env python3
"""
migrate_beta_to_v1.py — TokenPak beta → v1.0 migration checker

Scans Python files for deprecated patterns introduced in v0.9.0 beta
and reports (or optionally auto-fixes) what needs updating.

Usage:
    python scripts/migrate_beta_to_v1.py --path ./your_project/
    python scripts/migrate_beta_to_v1.py --path ./your_project/ --fix
    python scripts/migrate_beta_to_v1.py --path ./your_project/ --fix --dry-run
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Known migration rules: (pattern, replacement, description)
# ---------------------------------------------------------------------------

TEXT_REPLACEMENTS: list[tuple[str, str, str]] = [
    (
        r"\blegacy_compress\s*\(",
        "compress_context(",
        "legacy_compress() is deprecated — use compress_context() instead (removed in v1.2)",
    ),
    (
        r"from tokenpak\.engines import CompactionEngine\b",
        "from tokenpak.engines.base import CompactionEngine",
        "CompactionEngine import path changed; also importable as `from tokenpak import CompressionEngine`",
    ),
    (
        r'"host"\s*:\s*"0\.0\.0\.0"',
        '"host": "127.0.0.1"',
        'Default proxy host changed to 127.0.0.1; external access requires explicit --host 0.0.0.0',
    ),
    (
        r"--host\s+0\.0\.0\.0",
        "--host 0.0.0.0",  # no change needed — explicit is fine
        None,  # None = informational only, not a problem
    ),
]

# Patterns that indicate BaseHTTPRequestHandler usage (deprecated proxy subclassing)
DEPRECATED_PATTERNS: list[tuple[str, str]] = [
    (
        r"BaseHTTPRequestHandler",
        "Direct BaseHTTPRequestHandler subclassing is deprecated; use the async ProxyServer class directly",
    ),
    (
        r"from http\.server import.*BaseHTTPRequestHandler",
        "BaseHTTPRequestHandler import for proxy subclassing is no longer needed in v1.0",
    ),
    (
        r"legacy_compress\b",
        "legacy_compress() deprecated — replace with compress_context()",
    ),
]


@dataclass
class Finding:
    file: Path
    line_no: int
    line: str
    message: str
    fixable: bool = False
    old_text: str = ""
    new_text: str = ""


@dataclass
class MigrationReport:
    scanned: int = 0
    findings: list[Finding] = field(default_factory=list)
    fixed: int = 0

    @property
    def clean(self) -> bool:
        return len(self.findings) == 0


def scan_file(path: Path) -> list[Finding]:
    """Scan a single Python file for deprecated patterns."""
    findings: list[Finding] = []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings

    lines = source.splitlines()

    for i, line in enumerate(lines, start=1):
        # Check fixable text replacements
        for pattern, replacement, description in TEXT_REPLACEMENTS:
            if description is None:
                continue  # informational — skip
            match = re.search(pattern, line)
            if match:
                fixed_line = re.sub(pattern, replacement, line)
                findings.append(
                    Finding(
                        file=path,
                        line_no=i,
                        line=line.rstrip(),
                        message=description,
                        fixable=(fixed_line != line),
                        old_text=line.rstrip(),
                        new_text=fixed_line.rstrip(),
                    )
                )

        # Check non-fixable deprecated patterns
        for pattern, description in DEPRECATED_PATTERNS:
            if re.search(pattern, line):
                # Avoid double-reporting already caught by TEXT_REPLACEMENTS
                already_reported = any(
                    f.line_no == i and f.file == path for f in findings
                )
                if not already_reported:
                    findings.append(
                        Finding(
                            file=path,
                            line_no=i,
                            line=line.rstrip(),
                            message=description,
                            fixable=False,
                        )
                    )

    return findings


def apply_fix(path: Path, findings: list[Finding]) -> int:
    """Apply all fixable findings to a file. Returns count of fixes applied."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0

    fixed_count = 0
    for finding in findings:
        if finding.fixable and finding.old_text and finding.new_text:
            # Replace first occurrence of the old line
            source = source.replace(finding.old_text, finding.new_text, 1)
            fixed_count += 1

    path.write_text(source, encoding="utf-8")
    return fixed_count


def collect_python_files(root: Path) -> list[Path]:
    """Recursively collect all .py files under root."""
    return sorted(root.rglob("*.py"))


def print_report(report: MigrationReport, fix: bool, dry_run: bool) -> None:
    mode = ""
    if fix and dry_run:
        mode = " [DRY RUN]"
    elif fix:
        mode = " [FIX MODE]"

    print(f"\n{'='*60}")
    print(f"TokenPak beta → v1.0 Migration Report{mode}")
    print(f"{'='*60}")
    print(f"Files scanned:  {report.scanned}")
    print(f"Issues found:   {len(report.findings)}")
    if fix:
        print(f"Fixes applied:  {report.fixed}")
    print()

    if report.clean:
        print("✅ No issues found — your project is v1.0 compatible!")
        return

    # Group by file
    by_file: dict[Path, list[Finding]] = {}
    for f in report.findings:
        by_file.setdefault(f.file, []).append(f)

    for path, file_findings in by_file.items():
        print(f"📄 {path}")
        for f in file_findings:
            icon = "🔧" if f.fixable else "⚠️ "
            print(f"  {icon} Line {f.line_no}: {f.message}")
            print(f"     Found:   {f.line}")
            if f.fixable and f.new_text:
                print(f"     Fix to:  {f.new_text}")
        print()

    print("Legend: 🔧 = auto-fixable with --fix  |  ⚠️  = manual review needed")

    fixable = sum(1 for f in report.findings if f.fixable)
    manual = len(report.findings) - fixable
    print(f"\nAuto-fixable: {fixable}  |  Manual: {manual}")

    if fixable > 0 and not fix:
        print("\nRun with --fix to automatically apply safe replacements.")
    if manual > 0:
        print("\nManual items require human review — auto-fix cannot safely resolve them.")

    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan for TokenPak beta → v1.0 deprecated patterns",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("."),
        help="Root directory to scan (default: current directory)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-apply safe text replacements (use with --dry-run to preview)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --fix: show what would be changed without writing files",
    )
    args = parser.parse_args()

    root = args.path.resolve()
    if not root.exists():
        print(f"Error: path does not exist: {root}", file=sys.stderr)
        return 1

    if root.is_file():
        py_files = [root] if root.suffix == ".py" else []
    else:
        py_files = collect_python_files(root)

    if not py_files:
        print(f"No Python files found under {root}")
        return 0

    report = MigrationReport(scanned=len(py_files))

    for py_file in py_files:
        file_findings = scan_file(py_file)
        report.findings.extend(file_findings)

        if args.fix and not args.dry_run:
            fixable = [f for f in file_findings if f.fixable]
            if fixable:
                report.fixed += apply_fix(py_file, fixable)

    print_report(report, fix=args.fix, dry_run=args.dry_run)

    # Exit 1 if issues remain that need attention
    remaining = len(report.findings) - report.fixed
    return 1 if remaining > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
