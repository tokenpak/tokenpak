#!/usr/bin/env python3
"""Conservatively classify changed repository surfaces for CI selection.

The classifier is intentionally independent of release version numbers.  It
maps paths and diff size to stable category booleans, then selects the jobs
needed to validate those surfaces.  Missing or unclassifiable input takes the
full-conservative path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Iterable

CATEGORY_NAMES = (
    "python_proxy",
    "packaging_dependencies",
    "migration_storage",
    "pricing_billing",
    "docs_claims",
    "javascript_sdk",
    "workflow_release",
    "unknown",
)

JOB_NAMES = (
    "python_proxy",
    "full_python",
    "integration_chaos",
    "determinism_benchmarks",
    "packaging_dependencies",
    "migration_storage",
    "pricing_billing",
    "docs_claims",
    "javascript_sdk",
    "workflow_release",
)

DEFAULT_LARGE_DIFF_LINES = 500
DEFAULT_LARGE_DIFF_FILES = 50


@dataclass(frozen=True)
class ChangedPath:
    """One changed path reported by Git."""

    status: str
    path: str
    old_path: str | None = None

    def classification_paths(self) -> tuple[str, ...]:
        """Return every path whose former or current surface must be covered."""

        if self.old_path and self.old_path != self.path:
            return (self.old_path, self.path)
        return (self.path,)


def _normalise_path(value: str) -> str:
    normalised = value.strip().replace("\\", "/")
    while normalised.startswith("./"):
        normalised = normalised[2:]
    return normalised.lstrip("/")


def _parts(path: str) -> tuple[str, ...]:
    return tuple(part.lower() for part in PurePosixPath(path).parts)


def _path_tokens(path: str) -> set[str]:
    """Return punctuation-independent tokens from a repository path."""

    tokens: set[str] = set()
    for part in _parts(path):
        tokens.update(token for token in re.split(r"[-_.]+", part) if token)
    return tokens


def _is_python_surface(path: str) -> bool:
    lower = path.lower()
    return (
        (lower.startswith("tokenpak/") and lower.endswith((".py", ".pyi")))
        or (lower.startswith("tests/") and lower.endswith(".py"))
        or (lower.startswith("scripts/") and lower.endswith(".py"))
    )


def _is_packaging_surface(path: str) -> bool:
    lower = path.lower()
    name = PurePosixPath(lower).name
    if lower.startswith(("tokenpak.egg-info/", "dist/", "build/")):
        return True
    if name in {
        "pyproject.toml",
        "uv.lock",
        "setup.py",
        "setup.cfg",
        "manifest.in",
        "tox.ini",
        "constraints.txt",
        "requirements.txt",
    }:
        return True
    if name.startswith(("requirements-", "constraints-")) and name.endswith(".txt"):
        return True
    return lower in {
        "scripts/check-dist-contents.py",
        "scripts/slim-install-smoke.sh",
    }


def _is_migration_storage_surface(path: str) -> bool:
    lower = path.lower()
    tokens = _path_tokens(lower)
    name = PurePosixPath(lower).name
    return (
        bool({"migration", "migrations", "sqlite", "storage"}.intersection(tokens))
        or "migration" in name
        or name.startswith("migrate_")
        or lower == "scripts/migration_registry.json"
        or lower.startswith("tests/migration")
    )


def _is_pricing_billing_surface(path: str) -> bool:
    terms = {"pricing", "billing", "spend", "entitlement", "metering"}
    lower = path.lower()
    tokens = _path_tokens(lower)
    if terms.intersection(tokens):
        return True
    return lower.startswith(("tokenpak/", "tests/", "scripts/")) and bool(
        {"cost", "license", "licensing"}.intersection(tokens)
    )


def _is_docs_claims_surface(path: str) -> bool:
    lower = path.lower()
    name = PurePosixPath(lower).name
    return (
        lower.startswith(("docs/", ".changeset/"))
        or name.startswith(("readme", "changelog", "security", "contributing"))
        or name in {"mkdocs.yml", "license", "notice", "code_of_conduct.md"}
        or lower.startswith("tests/benchmarks/")
        or "claim" in PurePosixPath(lower).stem
    )


def _is_javascript_sdk_surface(path: str) -> bool:
    lower = path.lower()
    if not lower.startswith(("sdk/", "packages/tokenpak-js/")):
        return False
    return PurePosixPath(lower).suffix in {
        ".js",
        ".cjs",
        ".mjs",
        ".jsx",
        ".ts",
        ".tsx",
        ".json",
    } or PurePosixPath(lower).name in {"package.json", "package-lock.json", "npm-shrinkwrap.json"}


def _is_workflow_release_surface(path: str) -> bool:
    lower = path.lower()
    name = PurePosixPath(lower).name
    return (
        lower.startswith(".github/workflows/")
        or lower == ".github/gate-inventory.yml"
        or lower.startswith(("scripts/ci/", "tests/ci/"))
        or lower.startswith("tests/release_gate/")
        or lower.startswith("tests/release_check/")
        or lower.startswith("scripts/release_gate/")
        or lower.startswith("scripts/release_check/")
        or name.startswith(("release", "promote", "push-verified"))
        or lower in {"makefile", "scripts/check-action-pins.sh", "scripts/check-dist-contents.py"}
    )


def categories_for_path(path: str) -> set[str]:
    """Return every material category matched by *path*."""

    path = _normalise_path(path)
    categories: set[str] = set()
    if _is_python_surface(path):
        categories.add("python_proxy")
    if _is_packaging_surface(path):
        categories.add("packaging_dependencies")
    if _is_migration_storage_surface(path):
        categories.add("migration_storage")
    if _is_pricing_billing_surface(path):
        categories.add("pricing_billing")
    if _is_docs_claims_surface(path):
        categories.add("docs_claims")
    if _is_javascript_sdk_surface(path):
        categories.add("javascript_sdk")
    if _is_workflow_release_surface(path):
        categories.add("workflow_release")
    return categories


def is_shared_core(path: str) -> bool:
    """Return whether *path* affects a shared execution backbone."""

    lower = _normalise_path(path).lower()
    return lower in {"tokenpak/__init__.py", "tokenpak/_cli_core.py"} or lower.startswith(
        (
            "tokenpak/cache/",
            "tokenpak/compaction/",
            "tokenpak/compression/",
            "tokenpak/config/",
            "tokenpak/core/",
            "tokenpak/proxy/",
            "tokenpak/routing/",
            "tokenpak/services/",
        )
    )


def _job_selections(categories: dict[str, bool], full_conservative: bool) -> dict[str, bool]:
    selections = {
        "python_proxy": categories["python_proxy"],
        "full_python": (
            categories["packaging_dependencies"]
            or categories["migration_storage"]
            or categories["pricing_billing"]
            or categories["workflow_release"]
        ),
        "integration_chaos": (
            categories["python_proxy"]
            or categories["migration_storage"]
            or categories["pricing_billing"]
        ),
        "determinism_benchmarks": False,
        "packaging_dependencies": categories["packaging_dependencies"],
        "migration_storage": categories["migration_storage"],
        "pricing_billing": categories["pricing_billing"],
        "docs_claims": categories["docs_claims"],
        "javascript_sdk": categories["javascript_sdk"],
        "workflow_release": categories["workflow_release"],
    }
    if full_conservative:
        return {name: True for name in JOB_NAMES}
    return selections


def classify_changes(
    changed: Iterable[ChangedPath],
    *,
    additions: int = 0,
    deletions: int = 0,
    large_diff_lines: int = DEFAULT_LARGE_DIFF_LINES,
    large_diff_files: int = DEFAULT_LARGE_DIFF_FILES,
    base: str = "",
    head: str = "",
    force_full_conservative: bool = False,
) -> dict[str, object]:
    """Build the stable classifier summary for a change set."""

    entries = list(changed)
    categories = {name: False for name in CATEGORY_NAMES}
    unmatched_paths: list[str] = []
    shared_paths: list[str] = []
    classified_paths: dict[str, list[str]] = {}

    for entry in entries:
        for raw_path in entry.classification_paths():
            path = _normalise_path(raw_path)
            matched = categories_for_path(path)
            if not matched:
                unmatched_paths.append(path)
            else:
                classified_paths[path] = sorted(matched)
                for category in matched:
                    categories[category] = True
            if is_shared_core(path):
                shared_paths.append(path)

    reasons: list[str] = []
    if not entries:
        categories["unknown"] = True
        reasons.append("empty_diff")
    if unmatched_paths:
        categories["unknown"] = True
        reasons.append("unclassified_path")

    shared_core = bool(shared_paths)
    changed_lines = max(additions, 0) + max(deletions, 0)
    large_diff = len(entries) >= large_diff_files or changed_lines >= large_diff_lines
    material_count = sum(1 for name in CATEGORY_NAMES if name != "unknown" and categories[name])
    multi_surface = material_count > 1

    if shared_core:
        reasons.append("shared_core")
    if large_diff:
        reasons.append("large_diff")
    if multi_surface:
        reasons.append("multi_surface")
    for category in (
        "packaging_dependencies",
        "migration_storage",
        "pricing_billing",
        "workflow_release",
        "unknown",
    ):
        if categories[category]:
            reasons.append(category)
    if force_full_conservative:
        reasons.append("forced_full_conservative")

    full_conservative = bool(reasons)
    selections = _job_selections(categories, full_conservative)
    return {
        "schema": "tokenpak-ci-selection/v1",
        "classifier_ok": True,
        "base": base,
        "head": head,
        "changes": [asdict(entry) for entry in entries],
        "diff": {
            "files": len(entries),
            "additions": max(additions, 0),
            "deletions": max(deletions, 0),
            "changed_lines": changed_lines,
        },
        "thresholds": {
            "large_diff_lines": large_diff_lines,
            "large_diff_files": large_diff_files,
        },
        "categories": categories,
        "signals": {
            "shared_core": shared_core,
            "large_diff": large_diff,
            "multi_surface": multi_surface,
            "full_conservative": full_conservative,
        },
        "selections": selections,
        "classified_paths": classified_paths,
        "unmatched_paths": sorted(set(unmatched_paths)),
        "shared_core_paths": sorted(set(shared_paths)),
        "reasons": sorted(set(reasons)),
        "error": None,
    }


def conservative_error_summary(error: str, *, base: str = "", head: str = "") -> dict[str, object]:
    """Return an all-selected summary while marking classifier failure."""

    return {
        "schema": "tokenpak-ci-selection/v1",
        "classifier_ok": False,
        "base": base,
        "head": head,
        "changes": [],
        "diff": {"files": 0, "additions": 0, "deletions": 0, "changed_lines": 0},
        "thresholds": {
            "large_diff_lines": DEFAULT_LARGE_DIFF_LINES,
            "large_diff_files": DEFAULT_LARGE_DIFF_FILES,
        },
        "categories": {name: True for name in CATEGORY_NAMES},
        "signals": {
            "shared_core": True,
            "large_diff": True,
            "multi_surface": True,
            "full_conservative": True,
        },
        "selections": {name: True for name in JOB_NAMES},
        "classified_paths": {},
        "unmatched_paths": [],
        "shared_core_paths": [],
        "reasons": ["classifier_error"],
        "error": error,
    }


def parse_name_status(raw: bytes) -> list[ChangedPath]:
    """Parse ``git diff --name-status -z`` output, including renames."""

    fields = raw.decode("utf-8", errors="surrogateescape").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    entries: list[ChangedPath] = []
    index = 0
    while index < len(fields):
        status_field = fields[index]
        index += 1
        if not status_field:
            raise ValueError("git emitted an empty change status")
        status = status_field[0]
        if status in {"R", "C"}:
            if index + 1 >= len(fields):
                raise ValueError(f"git emitted an incomplete {status} record")
            old_path, path = fields[index], fields[index + 1]
            index += 2
            entries.append(ChangedPath(status=status_field, path=path, old_path=old_path))
        else:
            if index >= len(fields):
                raise ValueError(f"git emitted an incomplete {status} record")
            path = fields[index]
            index += 1
            entries.append(ChangedPath(status=status_field, path=path))
    return entries


def _diff_line_counts(base: str, head: str) -> tuple[int, int]:
    completed = subprocess.run(
        ["git", "diff", "--numstat", base, head, "--"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    additions = 0
    deletions = 0
    for line in completed.stdout.splitlines():
        fields = line.split("\t", 2)
        if len(fields) < 2:
            continue
        if fields[0].isdigit():
            additions += int(fields[0])
        if fields[1].isdigit():
            deletions += int(fields[1])
    return additions, deletions


def changes_from_git(base: str, head: str) -> tuple[list[ChangedPath], int, int]:
    """Read changed paths and line totals from an exact Git range."""

    completed = subprocess.run(
        ["git", "diff", "--name-status", "-z", "--find-renames", base, head, "--"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    additions, deletions = _diff_line_counts(base, head)
    return parse_name_status(completed.stdout), additions, deletions


def _as_bool(value: object) -> str:
    return "true" if value is True else "false"


def github_outputs(summary: dict[str, object]) -> dict[str, str]:
    """Flatten the classifier contract for GitHub job outputs."""

    categories = summary["categories"]
    signals = summary["signals"]
    selections = summary["selections"]
    assert isinstance(categories, dict)
    assert isinstance(signals, dict)
    assert isinstance(selections, dict)
    outputs = {"classifier_ok": _as_bool(summary["classifier_ok"])}
    outputs.update({name: _as_bool(categories[name]) for name in CATEGORY_NAMES})
    outputs.update({name: _as_bool(signals[name]) for name in signals})
    outputs.update({f"run_{name}": _as_bool(selections[name]) for name in JOB_NAMES})
    return outputs


def write_summary(
    summary: dict[str, object],
    *,
    output_json: str,
    github_output: str | None = None,
) -> None:
    """Persist the JSON summary and optional GitHub output values."""

    payload = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    with open(output_json, "w", encoding="utf-8") as handle:
        handle.write(payload)
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            for key, value in github_outputs(summary).items():
                handle.write(f"{key}={value}\n")
            handle.write(f"summary_path={output_json}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--output-json", default="ci-selection.json")
    parser.add_argument("--github-output")
    parser.add_argument(
        "--large-diff-lines",
        type=int,
        default=int(os.environ.get("TOKENPAK_CI_LARGE_DIFF_LINES", DEFAULT_LARGE_DIFF_LINES)),
    )
    parser.add_argument(
        "--large-diff-files",
        type=int,
        default=int(os.environ.get("TOKENPAK_CI_LARGE_DIFF_FILES", DEFAULT_LARGE_DIFF_FILES)),
    )
    parser.add_argument(
        "--force-full-conservative",
        action="store_true",
        help="select every job while retaining the exact changed-file range",
    )
    parser.add_argument("--fallback-error", help="emit a failed all-selected contract")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.fallback_error:
        summary = conservative_error_summary(args.fallback_error, base=args.base, head=args.head)
        write_summary(summary, output_json=args.output_json, github_output=args.github_output)
        return 0
    if not args.base or not args.head:
        summary = conservative_error_summary(
            "both --base and --head are required", base=args.base, head=args.head
        )
        write_summary(summary, output_json=args.output_json, github_output=args.github_output)
        return 2
    try:
        changed, additions, deletions = changes_from_git(args.base, args.head)
        summary = classify_changes(
            changed,
            additions=additions,
            deletions=deletions,
            large_diff_lines=args.large_diff_lines,
            large_diff_files=args.large_diff_files,
            base=args.base,
            head=args.head,
            force_full_conservative=args.force_full_conservative,
        )
    except Exception as exc:
        summary = conservative_error_summary(
            f"{type(exc).__name__}: {exc}", base=args.base, head=args.head
        )
        write_summary(summary, output_json=args.output_json, github_output=args.github_output)
        return 2
    write_summary(summary, output_json=args.output_json, github_output=args.github_output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
