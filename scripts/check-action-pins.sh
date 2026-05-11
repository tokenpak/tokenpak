#!/usr/bin/env bash
# check-action-pins.sh — peeled-commit pin enforcement.
#
# Scans `.github/workflows/*.yml` for `uses: <owner>/<repo>...@<ref>` entries
# and asserts that any SHA-pinned reference is a full 40-character hex
# commit SHA. Floating refs (version tags like `v4`, branch names like
# `main`, slashed refs like `release/v1`) are allowed.
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
#   • Version-tag pins (`@v4`) are mutable by the upstream maintainer,
#     but at least they're unambiguous about the trust model: you're
#     trusting the maintainer to not point the tag at a malicious sha.
#     A 7-char hex pin trusts neither — it's just careless.
#
# Exit codes:
#
#   0 — all action refs are either full SHAs (40 hex chars) or
#       non-hex floating refs (allowed).
#   1 — at least one ref is hex but not 40 chars (abbreviated SHA).
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
        # Non-hex (contains a non-[0-9a-f] character) → floating ref, allowed.
        if [[ "$ref" =~ [^0-9a-f] ]]; then
            continue
        fi
        # Pure-hex ref: must be exactly 40 chars (full peeled commit).
        if [ "${#ref}" -ne 40 ]; then
            echo "::error file=$wf::Abbreviated SHA pin '@$ref' (length ${#ref}, required 40). Use the full 40-char commit SHA or a floating version-tag ref."
            fail=1
        fi
    done < <(grep -nE '^[[:space:]]*-?[[:space:]]*uses:[[:space:]]+[A-Za-z0-9_.-]+/[^@]+@' "$wf" || true)
done

if [ "$fail" -ne 0 ]; then
    echo
    echo "FAIL: one or more action references are pinned to an abbreviated SHA." >&2
    echo "      Replace each abbreviated pin with the full 40-character commit SHA," >&2
    echo "      or use a floating version-tag ref (e.g. @v4) if the maintainer's" >&2
    echo "      release policy is acceptable for this workflow." >&2
    exit 1
fi

echo "Action pin check OK: $checked uses-ref(s) verified across workflows."
