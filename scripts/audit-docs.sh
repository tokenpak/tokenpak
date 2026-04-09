#!/bin/bash
# audit-docs.sh — detect doc drift in tokenpak/docs/
#
# Checks:
#   1. Case-insensitive duplicate filenames in docs/ (top-level .md files)
#      Redirect stubs (single-line "> This file has" pattern) are intentional
#      and exempt from this check.
#   2. Orphaned docs (docs/*.md not referenced in mkdocs.yml nav)
#      Redirect stubs are exempt from this check.
#   3. Root-level doc files that should be redirects:
#      TROUBLESHOOTING.md must not exist as a full doc (only as a redirect stub).
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

# ── Helper: is a file a redirect stub? ───────────────────────────────────────
# Redirect stubs are single-line files starting with "> This file has"
# (the CALI-03 canonical redirect pattern).
is_redirect_stub() {
    local f="$1"
    local line_count
    line_count=$(wc -l < "$f")
    # Allow 1-2 lines (trailing newline counts as a line)
    if [[ "$line_count" -le 2 ]]; then
        local first_line
        first_line=$(head -n1 "$f")
        if [[ "$first_line" == \>\ This\ file\ has* ]]; then
            return 0
        fi
    fi
    return 1
}

# ── 1. Case-insensitive duplicate filenames ───────────────────────────────────
echo ""
echo "--- Case-insensitive duplicate filenames in $DOCS_DIR/ ---"

# Collect all top-level .md filenames, lowercase, find any that collide
LOWER_NAMES=$(find "$DOCS_DIR" -maxdepth 1 -name "*.md" -printf '%f\n' \
    | tr '[:upper:]' '[:lower:]' | sort)
DUP_LOWERS=$(echo "$LOWER_NAMES" | uniq -d || true)

COLLISION_FOUND=0
if [[ -n "$DUP_LOWERS" ]]; then
    while IFS= read -r dup; do
        # Gather all files that match this name case-insensitively
        mapfile -t colliders < <(find "$DOCS_DIR" -maxdepth 1 -iname "${dup%.md}.md")
        # Check if all colliders except one are redirect stubs
        real_count=0
        for f in "${colliders[@]}"; do
            if ! is_redirect_stub "$f"; then
                real_count=$((real_count + 1))
            fi
        done
        if [[ "$real_count" -gt 1 ]]; then
            echo "WARNING: Non-redirect collision for base name: $dup"
            for f in "${colliders[@]}"; do
                echo "    $f"
            done
            COLLISION_FOUND=$((COLLISION_FOUND + 1))
        fi
    done <<< "$DUP_LOWERS"
fi

if [[ "$COLLISION_FOUND" -eq 0 ]]; then
    echo "OK: No unresolved case-insensitive duplicate filenames."
else
    FOUND=$((FOUND + COLLISION_FOUND))
fi

# ── 2. Orphaned docs not in mkdocs.yml nav ───────────────────────────────────
echo ""
echo "--- Orphaned docs not referenced in $MKDOCS_FILE nav ---"

# Extract every .md path that appears in the nav section of mkdocs.yml.
# Matches lines like:  - Label: path/to/file.md
NAV_DOCS=$(grep -oP '[\w./-]+\.md' "$MKDOCS_FILE" | sed 's|^|'"$DOCS_DIR"'/|' | sort -u)

# All .md files under docs/ (relative paths from repo root), excluding redirect stubs
ORPHAN_LIST=""
while IFS= read -r doc; do
    # Skip redirect stubs — they are intentional and don't need nav entries
    if is_redirect_stub "$doc"; then
        continue
    fi
    if ! echo "$NAV_DOCS" | grep -qxF "$doc"; then
        ORPHAN_LIST="${ORPHAN_LIST}  ${doc}\n"
    fi
done < <(find "$DOCS_DIR" -name "*.md" -printf '%p\n' | sort)

if [[ -n "$ORPHAN_LIST" ]]; then
    echo "WARNING: Docs not referenced in mkdocs.yml nav:"
    printf "%b" "$ORPHAN_LIST"
    FOUND=$((FOUND + 1))
else
    echo "OK: All non-redirect docs are referenced in mkdocs.yml nav."
fi

# ── 3. Root-level docs that must be redirect stubs ───────────────────────────
echo ""
echo "--- Root-level docs that must be redirect stubs ---"

ROOT_MUST_REDIRECT=(
    "TROUBLESHOOTING.md"
)

ROOT_FOUND=0
for fname in "${ROOT_MUST_REDIRECT[@]}"; do
    if [[ -f "$fname" ]]; then
        if ! is_redirect_stub "$fname"; then
            echo "WARNING: $fname exists as a full doc — it should be a redirect stub pointing to docs/"
            ROOT_FOUND=$((ROOT_FOUND + 1))
        else
            echo "OK: $fname is a redirect stub."
        fi
    else
        echo "OK: $fname does not exist at root (no duplicate risk)."
    fi
done

if [[ "$ROOT_FOUND" -gt 0 ]]; then
    FOUND=$((FOUND + ROOT_FOUND))
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
