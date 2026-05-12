#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Forbidden Implementation Vocabulary check (advisory).

Scans files changed in this pull request for compound-form vocabulary
that exposes implementation strategy hints. The full rule lives in
``01_PROJECTS/tokenpak/standards/08-naming-glossary.md`` → "Forbidden
Implementation Vocabulary" (Std 08 addendum, ratified per Std 32 §13.1
Decision #12).

Detection rule
--------------
**Compound forms only** — standalone ``basis`` is permitted in normal
English (*"on the basis of"*, *"the basis for"*) and is **not** flagged
to avoid false positives. The lint catches:

    low-rank, subspace, quantum-inspired, dequantized,
    matrix factorization, latent context profile,
    latent basis, representative basis, PAKBasis,
    basis_packet, basis packet, basis_cache, basis cache,
    basis_activation, basis activation

Case-insensitive, word-boundary anchored.

Scope rule
----------
**Delta-only** against the PR base ref (default ``origin/main``); legacy
occurrences in untouched files are tolerated as historical debt per the
Std 20 §9 precedent the identity-language-check workflow follows.

Rollout
-------
Shipped advisory by default per maintainer decision (2026-05-11): the
workflow that invokes this script sets ``continue-on-error: true``. The
final enforce flip is an explicit, separate governance review after a
one-week zero-false-positive soak — not automatic.

Exit codes
----------
- ``0``: no hits in the PR delta.
- ``1``: one or more hits (printed as ``::error`` annotations for the
  GitHub Actions UI). When the workflow runs with ``continue-on-error``
  the job stays green; the annotations are surfaced for reviewer
  attention only.

Local invocation
----------------
You can run this from inside the repo to scan a diff against ``main``::

    python3 .github/scripts/check-forbidden-impl-vocab.py

Override the base ref with ``BASE_REF`` (e.g. ``BASE_REF=origin/develop``)
when working on a non-``main`` integration branch.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Files whose extensions are scanned. Mirrors the identity-language-check
# extension list; binary surfaces are skipped (they have no comment or
# prose surface to leak vocabulary through).
_SCANNED_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".py", ".yml", ".yaml", ".json", ".toml", ".sh", ".js", ".ts", ".tsx"}
)

# Paths that are skipped entirely from the scan. The Std 08 section
# *defining* the forbidden vocabulary obviously contains the very strings
# the lint is looking for; the lint itself + its tests are also exempt
# so they can reference their own input without self-triggering.
_SKIP_PATH_SUBSTRINGS: tuple[str, ...] = (
    "01_PROJECTS/tokenpak/standards/08-naming-glossary.md",
    ".github/scripts/check-forbidden-impl-vocab.py",
    ".github/workflows/forbidden-impl-vocab-check.yml",
    "tests/lint/test_forbidden_impl_vocab.py",
)

# Compound-form regex. Word-boundary anchored, case-insensitive.
#
# The ``basis[_ ](packet|cache|activation)`` clause covers both the
# snake_case and the spaced English form ("basis packet", "basis cache",
# "basis activation"). Standalone ``basis`` is *intentionally* NOT in
# the alternation — see module docstring "Detection rule".
_FORBIDDEN_PATTERN: re.Pattern[str] = re.compile(
    r"\b("
    r"low-rank"
    r"|subspace"
    r"|quantum-inspired"
    r"|dequantized"
    r"|matrix\s+factorization"
    r"|latent\s+context\s+profile"
    r"|latent\s+basis"
    r"|representative\s+basis"
    r"|PAKBasis"
    r"|basis[_\s](packet|cache|activation)"
    r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Git delta resolution
# ---------------------------------------------------------------------------


def _resolve_base_ref() -> str:
    """Return the ref to diff against.

    Order of precedence:
    1. ``BASE_REF`` environment variable (explicit override).
    2. ``GITHUB_BASE_REF`` (set on ``pull_request`` workflow runs).
    3. Fallback ``origin/main``.
    """
    explicit = os.environ.get("BASE_REF")
    if explicit:
        return explicit
    gha_base = os.environ.get("GITHUB_BASE_REF")
    if gha_base:
        # GHA gives us a branch name; the actions/checkout step at
        # ``fetch-depth: 0`` puts ``origin/<branch>`` in the local ref set.
        return f"origin/{gha_base}"
    return "origin/main"


def _changed_files(base_ref: str) -> list[Path]:
    """List files added or modified between ``base_ref`` and the working tree."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "--diff-filter=AM", f"{base_ref}...HEAD"],
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"::warning ::Could not compute diff against {base_ref!r}: {exc}",
            file=sys.stderr,
        )
        return []
    return [Path(line) for line in out.splitlines() if line.strip()]


def _should_scan(path: Path) -> bool:
    if path.suffix not in _SCANNED_EXTENSIONS:
        return False
    posix = path.as_posix()
    return not any(skip in posix for skip in _SKIP_PATH_SUBSTRINGS)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return a list of ``(line_number, line_text, matched_term)`` for hits."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits: list[tuple[int, str, str]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        m = _FORBIDDEN_PATTERN.search(line)
        if m:
            hits.append((n, line.rstrip(), m.group(0)))
    return hits


def main() -> int:
    base_ref = _resolve_base_ref()
    files = _changed_files(base_ref)
    if not files:
        print(f"check-forbidden-impl-vocab: no changed files vs {base_ref}; OK.")
        return 0

    targets = [p for p in files if _should_scan(p)]
    if not targets:
        print(
            "check-forbidden-impl-vocab: no scannable files in delta; OK."
        )
        return 0

    total_hits = 0
    for path in targets:
        if not path.exists():
            # Renamed/removed in a later commit; nothing to scan.
            continue
        for ln, line, term in _scan_file(path):
            # GitHub Actions annotation format — the workflow surfaces
            # these in the PR UI even when the job is marked advisory.
            print(
                f"::error file={path.as_posix()},line={ln}::"
                f"Forbidden Implementation Vocabulary: {term!r} "
                f"(see Std 08 → Forbidden Implementation Vocabulary). "
                f"Line: {line.strip()[:200]}"
            )
            total_hits += 1

    if total_hits == 0:
        print(
            f"check-forbidden-impl-vocab: scanned {len(targets)} file(s); no hits."
        )
        return 0

    print(
        f"\ncheck-forbidden-impl-vocab: {total_hits} hit(s) across {len(targets)} "
        f"scanned file(s). Advisory by default; the workflow flag "
        f"``continue-on-error: true`` keeps the job green until the explicit "
        f"enforce flip.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
