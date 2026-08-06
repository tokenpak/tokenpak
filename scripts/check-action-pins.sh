#!/usr/bin/env bash
# check-action-pins.sh — immutable workflow dependency enforcement.
#
# Scans `.github/workflows/*.{yml,yaml}` for third-party
# `uses: <owner>/<repo>...@<ref>` entries and requires every reference to be
# a full 40-character peeled commit SHA. It also rejects direct GitHub release
# downloads unless the same workflow step declares a literal SHA-256 and checks
# the downloaded file before extraction, installation, or permission changes.
#
# Why this matters:
#
#   • An abbreviated SHA pin (e.g. `@abc1234`) gives weaker security
#     guarantees than a full peeled commit — short refs are vulnerable
#     to SHA collisions and to Git's silent prefix-matching behavior.
#
#   • A pinned action is supposed to be cryptographically immutable.
#     Abbreviating the SHA partly undoes the immutability claim.
#
#   • Version tags and branch refs are mutable. A compromised upstream account
#     can repoint them without changing this repository, so they do not
#     satisfy this project's release-artifact integrity contract.
#
# Exit codes:
#
#   0 — every third-party action ref and GitHub release fetch is immutable.
#   1 — at least one ref or external release fetch is not safely verified.
#   2 — environmental error (no workflow files, bash version, etc.).
#
# Usage:
#
#   bash scripts/check-action-pins.sh
#
# Reference: https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions#using-third-party-actions

set -euo pipefail
shopt -s nullglob

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKFLOWS_DIR="$REPO_ROOT/.github/workflows"

if [ ! -d "$WORKFLOWS_DIR" ]; then
    echo "ERROR: $WORKFLOWS_DIR does not exist." >&2
    exit 2
fi

fail=0
checked=0

for wf in "$WORKFLOWS_DIR"/*.yml "$WORKFLOWS_DIR"/*.yaml; do
    [ -f "$wf" ] || continue
    # Match: any line with "uses: <owner>/<name>...@<ref>"
    # We rely on a tolerant regex; YAML inline-mapping forms are uncommon
    # in GitHub Actions workflows and not worth special-casing here.
    while IFS= read -r line; do
        # Strip everything up to and including the first `@` after `uses:`.
        ref="${line#*@}"
        # Trim any trailing whitespace / comment.
        ref="${ref%%[[:space:]]*}"
        ref="${ref%%#*}"
        [ -n "$ref" ] || continue
        checked=$((checked + 1))
        if [[ ! "$ref" =~ ^[0-9a-f]{40}$ ]]; then
            echo "::error file=$wf::Unpinned third-party action reference '@$ref'. Use the full 40-character peeled commit SHA."
            fail=1
        fi
    done < <(grep -nE '^[[:space:]]*-?[[:space:]]*uses:[[:space:]]+[A-Za-z0-9_.-]+/[^@]+@' "$wf" || true)
done

# A full action SHA is only half of the build-time fetch contract. Workflows
# occasionally download release archives directly with curl/wget; those bytes
# must be digest-pinned and verified before they can be unpacked or installed.
# Parse step boundaries from the workflow text with the Python standard library
# so this check does not depend on PyYAML being installed.
python3 - "$WORKFLOWS_DIR" <<'PY' || fail=1
from __future__ import annotations

import re
import sys
from pathlib import Path


workflows_dir = Path(sys.argv[1])
release_url = re.compile(
    r"https://github\.com/[^\s\"']+/[^\s\"']+/releases/download/"
)
step_item = re.compile(r"^(?P<indent>\s*)-\s+\S")
digest_decl = re.compile(
    r"^\s+(?P<name>[A-Z][A-Z0-9_]*_SHA256):\s*"
    r"[\"']?(?P<digest>[0-9a-f]{64})[\"']?\s*(?:#.*)?$"
)
fetch_command = re.compile(r"^\s*(?:curl|wget)\b")
checksum_command = re.compile(r"\bsha256sum\s+(?:--check|-c)\b")
consumer_command = re.compile(
    r"^\s*(?:sudo\s+)?(?:tar|unzip|install|chmod)\b"
)
output_arg = re.compile(
    r"(?:^|\s)(?:-o|--output)\s+(?P<path>\"[^\"]+\"|'[^']+'|\S+)"
)
output_equals = re.compile(
    r"(?:^|\s)--output=(?P<path>\"[^\"]+\"|'[^']+'|\S+)"
)

failures: list[tuple[Path, int, str]] = []
checked = 0

for workflow in sorted((*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml"))):
    lines = workflow.read_text(encoding="utf-8").splitlines()
    for url_index, line in enumerate(lines):
        if not release_url.search(line):
            continue
        checked += 1
        url_indent = len(line) - len(line.lstrip())

        step_start = None
        step_indent = None
        for index in range(url_index, -1, -1):
            match = step_item.match(lines[index])
            if match is None:
                continue
            indent = len(match.group("indent"))
            if indent < url_indent:
                step_start = index
                step_indent = indent
                break
        if step_start is None or step_indent is None:
            failures.append((workflow, url_index + 1, "download is not inside a recognizable workflow step"))
            continue

        step_end = len(lines)
        for index in range(step_start + 1, len(lines)):
            match = step_item.match(lines[index])
            if match is not None and len(match.group("indent")) == step_indent:
                step_end = index
                break

        block = lines[step_start:step_end]
        relative_url = url_index - step_start
        digest_names = {
            match.group("name")
            for block_line in block
            if (match := digest_decl.match(block_line)) is not None
        }
        if not digest_names:
            failures.append((workflow, url_index + 1, "step has no literal 64-character *_SHA256 declaration"))
            continue

        fetch_indexes = [
            index
            for index, block_line in enumerate(block[relative_url:], start=relative_url)
            if fetch_command.search(block_line)
        ]
        if not fetch_indexes:
            failures.append((workflow, url_index + 1, "step has no curl/wget command for the release URL"))
            continue
        fetch_index = fetch_indexes[0]
        fetch_line = block[fetch_index]
        output_match = output_arg.search(fetch_line) or output_equals.search(fetch_line)
        if output_match is None:
            failures.append((workflow, step_start + fetch_index + 1, "download must name an explicit output file"))
            continue
        output_path = output_match.group("path").strip("\"'")

        checksum_indexes = [
            index
            for index, block_line in enumerate(block[fetch_index + 1 :], start=fetch_index + 1)
            if checksum_command.search(block_line)
        ]
        if not checksum_indexes:
            failures.append((workflow, step_start + fetch_index + 1, "download is not followed by sha256sum --check"))
            continue
        checksum_index = checksum_indexes[0]
        checksum_line = block[checksum_index]
        first_consumer = next(
            (
                index
                for index, block_line in enumerate(block[fetch_index + 1 :], start=fetch_index + 1)
                if consumer_command.search(block_line)
            ),
            None,
        )
        if first_consumer is not None and checksum_index >= first_consumer:
            failures.append((workflow, step_start + checksum_index + 1, "digest check occurs after archive use"))
        if output_path not in checksum_line:
            failures.append((workflow, step_start + checksum_index + 1, "digest check does not name the downloaded output file"))
        if not any(f"${name}" in checksum_line or f"${{{name}}}" in checksum_line for name in digest_names):
            failures.append((workflow, step_start + checksum_index + 1, "digest check does not consume the declared *_SHA256 value"))

if failures:
    for workflow, line_number, message in failures:
        print(f"::error file={workflow},line={line_number}::{message}", file=sys.stderr)
    print(
        "FAIL: one or more external GitHub release downloads are not checksum-verified before use.",
        file=sys.stderr,
    )
    raise SystemExit(1)

print(f"External release fetch check OK: {checked} checksum-verified download(s).")
PY

if [ "$fail" -ne 0 ]; then
    echo
    echo "FAIL: one or more workflow dependencies are not immutable." >&2
    echo "      Pin action refs to full peeled SHAs and verify release downloads by digest before use." >&2
    exit 1
fi

echo "Action pin check OK: $checked third-party uses-ref(s) pinned to full SHAs."
