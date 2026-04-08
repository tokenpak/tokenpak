#!/bin/bash
# audit-docs.sh — detect doc drift in tokenpak/docs/
#
# Checks:
#   1. Case-insensitive duplicate filenames in docs/ (top-level .md files)
#   2. Orphaned docs (docs/*.md not referenced in mkdocs.yml nav)
#
# Returns non-zero if any finding is detected.
# Used as a soft CI warning gate (continue-on-error in GitHub Actions).
#
# Usage: bash scripts/audit-docs.sh [docs-dir] [mkdocs-yml]

set -euo pipefail

DOCS_DIR="${1:-docs}"
MKDOCS_FILE="${2:-mkdocs.yml}"
FOUND=0

echo "=== TokenPak Doc Audit ==="
echo "  docs dir : $DOCS_DIR"
echo "  mkdocs   : $MKDOCS_FILE"

# ── 1. Case-insensitive duplicate filenames ───────────────────────────────────
echo ""
echo "--- Case-insensitive duplicate filenames in $DOCS_DIR/ ---"

# Collect all top-level .md filenames, lowercase, find any that collide
LOWER_NAMES=$(find "$DOCS_DIR" -maxdepth 1 -name "*.md" -printf '%f\n' \
    | tr '[:upper:]' '[:lower:]' | sort)
DUP_LOWERS=$(echo "$LOWER_NAMES" | uniq -d || true)

if [[ -n "$DUP_LOWERS" ]]; then
    echo "WARNING: The following base names collide case-insensitively:"
    while IFS= read -r dup; do
        echo "  collision: $dup"
        find "$DOCS_DIR" -maxdepth 1 -iname "${dup%.md}.md" -printf '    %p\n'
    done <<< "$DUP_LOWERS"
    FOUND=$((FOUND + 1))
else
    echo "OK: No case-insensitive duplicate filenames."
fi

# ── 2. Orphaned docs not in mkdocs.yml nav ───────────────────────────────────
echo ""
echo "--- Orphaned docs not referenced in $MKDOCS_FILE nav ---"

# Extract every .md path that appears in the nav section of mkdocs.yml.
# Matches lines like:  - Label: path/to/file.md
NAV_DOCS=$(grep -oP '[\w./-]+\.md' "$MKDOCS_FILE" | sed 's|^|'"$DOCS_DIR"'/|' | sort -u)

# All .md files under docs/ (relative paths from repo root)
ALL_DOCS=$(find "$DOCS_DIR" -name "*.md" -printf '%p\n' | sort)

ORPHAN_LIST=""
while IFS= read -r doc; do
    if ! echo "$NAV_DOCS" | grep -qxF "$doc"; then
        ORPHAN_LIST="${ORPHAN_LIST}  ${doc}\n"
    fi
done <<< "$ALL_DOCS"

if [[ -n "$ORPHAN_LIST" ]]; then
    echo "WARNING: Docs not referenced in mkdocs.yml nav:"
    printf "%b" "$ORPHAN_LIST"
    FOUND=$((FOUND + 1))
else
    echo "OK: All docs are referenced in mkdocs.yml nav."
fi

# ── Result ────────────────────────────────────────────────────────────────────
echo ""
if [[ $FOUND -ne 0 ]]; then
    echo "AUDIT RESULT: $FOUND issue(s) found (see warnings above)."
    exit 1
else
    echo "AUDIT RESULT: PASSED — no issues found."
    exit 0
fi
