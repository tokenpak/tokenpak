#!/usr/bin/env python3
"""Fail-closed release-audit helpers used by ``make audit``.

The helpers operate on repository files and generated fixtures only. They never
read a user's live telemetry database. Accepted findings are supplied by the
release captain as an external JSON receipt so internal governance data is not
committed to the public repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
MYPY_ROOTS = (
    "tokenpak/core",
    "tokenpak/services",
    "tokenpak/proxy",
    "tokenpak/security",
    "tokenpak/compression",
    "tokenpak/cache",
)
MYPY_LOGICAL_COMMAND = ("python", "-m", "mypy", "--strict", *MYPY_ROOTS)
MYPY_SUMMARY = re.compile(
    r"Found (?P<errors>\d+) errors in (?P<files>\d+) files "
    r"\(checked (?P<checked>\d+) source files\)"
)
AUDIT_SURFACES = ("README.md", "docs", "site", "tokenpak/dashboard")
HARD_FILLER = (
    "revolutionary",
    "game-changing",
    "cutting-edge",
    "industry-leading",
    "next-gen",
    "best-in-class",
    "simply",
    "easily",
)
UPDATED_DATE = re.compile(
    r"(?:last\s+)?updated[:* ]+(?P<value>20\d{2}-\d{2}-\d{2})",
    re.IGNORECASE,
)
A3_WAIVER_RELEASE = "1.13.0"
A3_WAIVER_BASE = "ae1e139b73d7441b87873e2fe5e721dd0908c9b3"
A3_WAIVER_BASELINE_CHECKED_FILES = 267
A3_WAIVER_BASELINE_RAW_SHA256 = "85ce0f3ec7fac7d5173a1f661a0681a4a4347f1f1ea0241e060f56d2ad548ed9"
A3_WAIVER_BASELINE_NORMALIZED_SHA256 = (
    "3292ef557612595511aee0bee38ca79a7392dd13b5bb742bab181aca17a8d58f"
)
A3_WAIVER_AUTHORIZATION_SOURCE = "Release audit exception approved for version 1.13.0"
A3_WAIVER_DELTA_ATTRIBUTION = (
    "The final RC checks one additional empty source, tokenpak/services/__init__.py, added by "
    "the accepted A4 import-contract carrier. The full transcript differs only in the terminal "
    "checked-source count; all 2694 ordered error lines are byte-identical to the approved baseline."
)


class AuditError(RuntimeError):
    """Raised when a release-audit contract fails."""


@dataclass(frozen=True)
class MypyEvidence:
    returncode: int
    error_count: int
    unique_error_count: int
    file_count: int
    checked_file_count: int
    in_scope_error_count: int
    in_scope_file_count: int
    raw_sha256: str
    normalized_sha256: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect_mypy_evidence(output: bytes, returncode: int) -> MypyEvidence:
    """Derive deterministic evidence from one complete mypy transcript."""
    text = output.decode("utf-8", errors="replace")
    error_lines = [line for line in text.splitlines() if ": error:" in line]
    # Preserve mypy's deterministic emission order. The approved A3 baseline
    # normalized by removing note/summary lines, not by re-sorting errors.
    normalized_lines = list(dict.fromkeys(error_lines))
    normalized = ("\n".join(normalized_lines) + "\n").encode("utf-8") if normalized_lines else b""
    summary = MYPY_SUMMARY.search(text)
    if returncode == 1 and summary is None:
        raise AuditError("mypy failed without the required terminal summary")

    files = {line.split(":", 1)[0] for line in error_lines}
    in_scope_lines = [
        line for line in error_lines if line.startswith(tuple(f"{root}/" for root in MYPY_ROOTS))
    ]
    in_scope_files = {line.split(":", 1)[0] for line in in_scope_lines}
    summary_errors = int(summary.group("errors")) if summary else 0
    summary_files = int(summary.group("files")) if summary else 0
    checked_files = int(summary.group("checked")) if summary else 0
    if summary and summary_errors != len(error_lines):
        raise AuditError(
            f"mypy summary reports {summary_errors} errors but transcript has {len(error_lines)}"
        )
    if summary and summary_files != len(files):
        raise AuditError(
            f"mypy summary reports {summary_files} files but transcript has {len(files)}"
        )

    return MypyEvidence(
        returncode=returncode,
        error_count=len(error_lines),
        unique_error_count=len(normalized_lines),
        file_count=len(files),
        checked_file_count=checked_files,
        in_scope_error_count=len(in_scope_lines),
        in_scope_file_count=len(in_scope_files),
        raw_sha256=_sha256(output),
        normalized_sha256=_sha256(normalized),
    )


def validate_accepted_finding(
    evidence: MypyEvidence,
    receipt: dict[str, Any],
    *,
    release_version: str,
    python_version: str,
    mypy_version: str,
) -> None:
    """Validate an exact, release-scoped accepted-finding receipt."""
    expected: dict[str, Any] = {
        "schema_version": 1,
        "gate": "A3",
        "approval": "APPROVE-A3-V113-ONLY-WAIVER",
        "release_version": A3_WAIVER_RELEASE,
        "python_version": python_version,
        "mypy_version": mypy_version,
        "command": list(MYPY_LOGICAL_COMMAND),
        "expected_exit": evidence.returncode,
        "error_count": evidence.error_count,
        "unique_error_count": evidence.unique_error_count,
        "file_count": evidence.file_count,
        "checked_file_count": evidence.checked_file_count,
        "in_scope_error_count": evidence.in_scope_error_count,
        "in_scope_file_count": evidence.in_scope_file_count,
        "raw_sha256": evidence.raw_sha256,
        "normalized_sha256": evidence.normalized_sha256,
        "approved_base_commit": A3_WAIVER_BASE,
        "approved_baseline_checked_file_count": A3_WAIVER_BASELINE_CHECKED_FILES,
        "approved_baseline_raw_sha256": A3_WAIVER_BASELINE_RAW_SHA256,
        "approved_baseline_normalized_sha256": A3_WAIVER_BASELINE_NORMALIZED_SHA256,
        "delta_attribution": A3_WAIVER_DELTA_ATTRIBUTION,
        "authorization_source": A3_WAIVER_AUTHORIZATION_SOURCE,
        "expires_after_release": A3_WAIVER_RELEASE,
    }
    mismatches = [
        f"{key}: receipt={receipt.get(key)!r}, measured={value!r}"
        for key, value in expected.items()
        if receipt.get(key) != value
    ]
    if release_version != A3_WAIVER_RELEASE:
        mismatches.append(
            f"release invocation: requested={release_version!r}, authorized={A3_WAIVER_RELEASE!r}"
        )
    if A3_WAIVER_BASELINE_NORMALIZED_SHA256 != evidence.normalized_sha256:
        mismatches.append("approved baseline error set/order does not match final-RC evidence")
    if mismatches:
        raise AuditError("accepted-finding receipt mismatch:\n  " + "\n  ".join(mismatches))


def _toolchain_versions() -> tuple[str, str]:
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    try:
        from mypy.version import __version__ as mypy_version
    except ImportError as exc:  # pragma: no cover - exercised by the command environment
        raise AuditError("mypy is not installed in the selected Python environment") from exc
    return python_version, mypy_version


def run_mypy_gate(receipt_path: Path | None, release_version: str | None) -> None:
    """Run the exact strict command and consume only exact accepted evidence."""
    python_version, mypy_version = _toolchain_versions()
    if python_version != "3.12" or mypy_version != "2.3.0":
        raise AuditError(
            "A3 requires exact Python 3.12 / mypy 2.3.0; "
            f"found Python {python_version} / mypy {mypy_version}"
        )

    command = [sys.executable, "-m", "mypy", "--strict", *MYPY_ROOTS]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    sys.stdout.buffer.write(completed.stdout)
    sys.stdout.buffer.flush()
    evidence = collect_mypy_evidence(completed.stdout, completed.returncode)
    if completed.returncode == 0:
        print("A3 strict mypy: PASS")
        return
    if completed.returncode != 1:
        raise AuditError(f"mypy exited unexpectedly with status {completed.returncode}")
    if receipt_path is None or not release_version:
        raise AuditError(
            "A3 strict mypy failed and no exact accepted-finding receipt/release was supplied"
        )

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    validate_accepted_finding(
        evidence,
        receipt,
        release_version=release_version,
        python_version=python_version,
        mypy_version=mypy_version,
    )
    print(
        "A3 strict mypy: ACCEPTED FINDING matched exactly "
        f"for release {release_version} ({evidence.error_count} errors, "
        f"raw {evidence.raw_sha256}, normalized {evidence.normalized_sha256})"
    )


def scan_forbidden_lines(lines: Iterable[tuple[str, int, str]]) -> list[str]:
    """Return B3 findings; permit only grammatical ``not just`` constructions."""
    findings: list[str] = []
    hard_pattern = re.compile(
        r"\b(" + "|".join(re.escape(term) for term in HARD_FILLER) + r")\b",
        re.IGNORECASE,
    )
    just_pattern = re.compile(r"\bjust\b", re.IGNORECASE)
    for path, line_number, line in lines:
        for match in hard_pattern.finditer(line):
            findings.append(f"{path}:{line_number}: forbidden qualifier {match.group(0)!r}")
        for match in just_pattern.finditer(line):
            prefix = line[: match.start()]
            if re.search(r"(?:\bnot|n['’]t)\s+$", prefix, re.IGNORECASE):
                continue
            findings.append(f"{path}:{line_number}: forbidden qualifier 'just'")
    return findings


def _tracked_surface_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", *AUDIT_SURFACES],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        check=True,
    )
    return [ROOT / value.decode("utf-8") for value in completed.stdout.split(b"\0") if value]


def run_forbidden_gate() -> None:
    lines: list[tuple[str, int, str]] = []
    for path in _tracked_surface_files():
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            lines.append((relative, line_number, line))
    findings = scan_forbidden_lines(lines)
    if findings:
        raise AuditError("B3 forbidden phrases found:\n  " + "\n  ".join(findings))
    print(f"B3 forbidden phrases: PASS ({len(lines)} tracked surface lines scanned)")


def scan_doc_patterns(lines: Iterable[tuple[str, int, str]], *, base_date: date) -> list[str]:
    findings: list[str] = []
    for path, line_number, line in lines:
        if re.search(r"\bTODO\b", line) or re.search(r"coming soon", line, re.IGNORECASE):
            findings.append(f"{path}:{line_number}: forbidden release-doc pattern")
        for match in UPDATED_DATE.finditer(line):
            value = date.fromisoformat(match.group("value"))
            if value < base_date:
                findings.append(
                    f"{path}:{line_number}: stale updated date {value.isoformat()} "
                    f"predates {base_date.isoformat()}"
                )
    return findings


def run_docs_pattern_gate(base_ref: str) -> None:
    try:
        base_date_text = subprocess.run(
            ["git", "log", "-1", "--format=%cs", base_ref],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout.strip()
        changed = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD", "--", "README.md", "docs"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout.splitlines()
    except subprocess.CalledProcessError as exc:
        raise AuditError(
            f"C6 cannot resolve release base {base_ref!r}: {exc.stderr.strip()}"
        ) from exc

    lines: list[tuple[str, int, str]] = []
    for relative in changed:
        path = ROOT / relative
        if not path.is_file():
            continue
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            lines.append((relative, line_number, line))
    findings = scan_doc_patterns(lines, base_date=date.fromisoformat(base_date_text))
    if findings:
        raise AuditError("C6 release-doc findings:\n  " + "\n  ".join(findings))
    print(f"C6 release-doc patterns: PASS ({len(changed)} changed docs checked)")


def telemetry_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    """Run the canonical summary SQL against a code-created fixture schema."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(requests)").fetchall()}
    required = {"cache_origin", "attribution_source", "compressed_tokens", "cache_read_tokens"}
    missing = sorted(required - columns)
    if missing:
        raise AuditError("telemetry request schema is missing: " + ", ".join(missing))

    origins = dict(
        connection.execute(
            "SELECT cache_origin, COUNT(*) FROM requests GROUP BY cache_origin"
        ).fetchall()
    )
    null_origins = connection.execute(
        "SELECT COUNT(*) FROM requests WHERE cache_origin IS NULL"
    ).fetchone()[0]
    attribution = dict(
        connection.execute(
            "SELECT attribution_source, COUNT(*) FROM requests GROUP BY attribution_source"
        ).fetchall()
    )
    totals = connection.execute(
        "SELECT COALESCE(SUM(compressed_tokens), 0), "
        "COALESCE(SUM(CASE WHEN cache_origin = 'proxy' THEN cache_read_tokens ELSE 0 END), 0) "
        "FROM requests"
    ).fetchone()
    if null_origins:
        raise AuditError(f"telemetry fixture contains {null_origins} NULL cache_origin row(s)")
    return {
        "cache_origin_counts": origins,
        "attribution_source_counts": attribution,
        "compressed_tokens": totals[0],
        "proxy_cache_read_tokens": totals[1],
    }


def run_telemetry_gate() -> None:
    from tokenpak.proxy.monitor import Monitor

    with tempfile.TemporaryDirectory(prefix="tokenpak-release-audit-") as directory:
        database = Path(directory) / "monitor.db"
        monitor = Monitor(database)
        connection = sqlite3.connect(database)
        try:
            rows = (
                ("2026-01-01T00:00:00", "fixture-a", "proxy", "runtime", 10, 20),
                ("2026-01-01T00:00:01", "fixture-b", "client", "provider", 5, 30),
                ("2026-01-01T00:00:02", "fixture-c", "unknown", "", 0, 40),
            )
            connection.executemany(
                "INSERT INTO requests "
                "(timestamp, model, cache_origin, attribution_source, "
                "compressed_tokens, cache_read_tokens) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            connection.commit()
            summary = telemetry_summary(connection)
        finally:
            connection.close()
            monitor.stop()
    expected_origins = {"client": 1, "proxy": 1, "unknown": 1}
    if summary["cache_origin_counts"] != expected_origins:
        raise AuditError(f"telemetry origin summary drifted: {summary['cache_origin_counts']!r}")
    print("Telemetry summary SQL: PASS " + json.dumps(summary, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    mypy_parser = subparsers.add_parser("mypy", help="run the exact A3 strict-mypy gate")
    mypy_parser.add_argument("--accepted-finding", type=Path)
    mypy_parser.add_argument("--release-version")
    subparsers.add_parser("forbidden", help="run the B3 qualifier scan")
    docs_parser = subparsers.add_parser("docs-patterns", help="run the C6 changed-doc scan")
    docs_parser.add_argument("--base-ref", required=True)
    subparsers.add_parser("telemetry", help="run fixture-backed telemetry summary SQL")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "mypy":
            run_mypy_gate(args.accepted_finding, args.release_version)
        elif args.command == "forbidden":
            run_forbidden_gate()
        elif args.command == "docs-patterns":
            run_docs_pattern_gate(args.base_ref)
        elif args.command == "telemetry":
            run_telemetry_gate()
    except (AuditError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"release audit FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
