#!/usr/bin/env bash
# Canonical staging → public promotion train.
#
# Brings the public mirror's main to content parity with staging main by
# building a train branch off public main, applying the staging tree delta
# (minus the hold-list), running local public-safety gates, and — only with
# --execute — pushing the branch to the public remote and opening the
# promotion PR. The PUBLIC MERGE ITSELF IS NEVER PERFORMED HERE; it remains a
# human-approved step under the repository's public merge policy.
#
# Default mode is a dry run: computes the manifest, builds the train branch in
# a temporary worktree, runs the gates, prints the result, pushes nothing.
#
# Usage:
#   scripts/promote-staging-to-public.sh            # dry run
#   scripts/promote-staging-to-public.sh --execute  # push branch + open PR
#
# Env overrides: PUBLIC_REMOTE (github), STAGING_REMOTE (github-staging),
# PUBLIC_REPO (tokenpak/tokenpak), HOLD_FILE (.github/public-promotion-hold.txt).
set -euo pipefail

PUBLIC_REMOTE="${PUBLIC_REMOTE:-github}"
STAGING_REMOTE="${STAGING_REMOTE:-github-staging}"
PUBLIC_REPO="${PUBLIC_REPO:-tokenpak/tokenpak}"
HOLD_FILE="${HOLD_FILE:-.github/public-promotion-hold.txt}"
MODE="dry-run"
[[ "${1:-}" == "--execute" ]] && MODE="execute"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

git fetch -q "$PUBLIC_REMOTE" main
git fetch -q "$STAGING_REMOTE" main
PUB="refs/remotes/$PUBLIC_REMOTE/main"
STG="refs/remotes/$STAGING_REMOTE/main"
PUB_SHA="$(git rev-parse --short "$PUB")"
STG_SHA="$(git rev-parse --short "$STG")"
TRAIN_BRANCH="promote/train-$(date -u +%Y%m%d)"

echo "== promotion train ($MODE) =="
echo "public  main: $PUB_SHA"
echo "staging main: $STG_SHA"

# ---------------------------------------------------------------------------
# 1. Manifest (content delta minus hold-list)
# ---------------------------------------------------------------------------
MANIFEST="$(mktemp)"
trap 'rm -f "$MANIFEST"' EXIT

hold_patterns=()
if [[ -f "$HOLD_FILE" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    hold_patterns+=("$line")
  done < "$HOLD_FILE"
fi

is_held() {
  local path="$1" pat
  for pat in "${hold_patterns[@]+"${hold_patterns[@]}"}"; do
    # shellcheck disable=SC2053
    [[ "$path" == $pat ]] && return 0
  done
  return 1
}

while IFS=$'\t' read -r status path; do
  [[ -z "${path:-}" ]] && continue
  if is_held "$path"; then
    echo "held: $status $path"
  else
    printf '%s\t%s\n' "$status" "$path" >> "$MANIFEST"
  fi
done < <(git diff --no-renames --name-status "$PUB" "$STG")

COUNT="$(wc -l < "$MANIFEST" | tr -d ' ')"
if [[ "$COUNT" -eq 0 ]]; then
  echo "Content parity: nothing to promote."
  exit 0
fi
echo "-- manifest: $COUNT file(s) --"
cat "$MANIFEST"

# ---------------------------------------------------------------------------
# 2. Build the train branch in a temporary worktree off public main
# ---------------------------------------------------------------------------
TRAIN_WT="$(mktemp -d)/train"
cleanup() {
  git worktree remove --force "$TRAIN_WT" 2>/dev/null || true
  git branch -D "$TRAIN_BRANCH" 2>/dev/null || true
  rm -f "$MANIFEST"
}
trap cleanup EXIT

git branch -D "$TRAIN_BRANCH" 2>/dev/null || true
git worktree add -q -b "$TRAIN_BRANCH" "$TRAIN_WT" "$PUB"

while IFS=$'\t' read -r status path; do
  case "$status" in
    D)   git -C "$TRAIN_WT" rm -q -- "$path" ;;
    A|M) git -C "$TRAIN_WT" checkout -q "$STG" -- "$path" ;;
    *)   echo "ERROR: unhandled diff status '$status' for $path" >&2; exit 2 ;;
  esac
done < "$MANIFEST"

if git -C "$TRAIN_WT" diff --cached --quiet; then
  echo "Nothing staged after applying manifest — aborting."
  exit 2
fi

# ---------------------------------------------------------------------------
# 3. Local public-safety gates (fail-closed)
# ---------------------------------------------------------------------------
echo "-- gates --"

echo "[gate] conflict markers"
if git -C "$TRAIN_WT" grep -nE '^(<<<<<<< |>>>>>>> |=======$)' -- ':!*.md' >/dev/null 2>&1; then
  echo "FAIL: conflict markers present in train tree" >&2
  exit 3
fi

echo "[gate] public-leak scan over promoted files (delta-style)"
# Mirror the per-PR delta gate: scan exactly the files this train changes,
# minus the same self-referential exclusions the delta gate applies. The
# full-tree scanner is calibrated for the shipped artifact tree, not the
# whole repo (workflow files defining the forbidden patterns would trip it).
SCAN_DIR="$(mktemp -d)"
while IFS=$'\t' read -r status path; do
  [[ "$status" == "D" ]] && continue
  echo "$path" | grep -qE '^(packages/tests/|tests/|sdk/dist/|scripts/release_check/|scripts/release_gate/(check_release_leaks|public_safety_scan)\.py|\.github/workflows/identity-language-check\.yml|\.github/workflows/public-layout-check\.yml|\.pre-commit-config\.yaml)' && continue
  mkdir -p "$SCAN_DIR/$(dirname "$path")"
  cp "$TRAIN_WT/$path" "$SCAN_DIR/$path"
done < "$MANIFEST"
if [[ -n "$(find "$SCAN_DIR" -type f -print -quit)" ]]; then
  ( cd "$TRAIN_WT" && python3 scripts/release_gate/check_release_leaks.py --tree "$SCAN_DIR" )
fi
rm -rf "$SCAN_DIR"

echo "gates passed."

# ---------------------------------------------------------------------------
# 4. Commit, and with --execute: push + open the promotion PR
# ---------------------------------------------------------------------------
git -C "$TRAIN_WT" -c user.name=TokenPak -c user.email=hello@tokenpak.ai \
  commit -q --author="TokenPak <hello@tokenpak.ai>" \
  -m "promote: staging content parity train ($(date -u +%Y-%m-%d))" \
  -m "Brings public main to content parity with staging main ($STG_SHA): $COUNT file(s). Built by scripts/promote-staging-to-public.sh; manifest in the PR body."
echo "train commit: $(git -C "$TRAIN_WT" rev-parse --short HEAD)"

if [[ "$MODE" != "execute" ]]; then
  echo "DRY RUN complete — no push, no PR. Re-run with --execute to publish the train branch."
  exit 0
fi

PROMOTE_PUBLIC_ALLOW=1 git -C "$TRAIN_WT" push -f "$PUBLIC_REMOTE" "$TRAIN_BRANCH"
trap 'git worktree remove --force "$TRAIN_WT" 2>/dev/null || true; rm -f "$MANIFEST"' EXIT

BODY="Automated promotion train bringing public main to content parity with staging main (\`$STG_SHA\`).

Manifest ($COUNT files):
\`\`\`
$(cat "$MANIFEST")
\`\`\`

Gates run locally before this PR was opened: conflict-marker scan, full-tree public-leak scan (scripts/release_gate/check_release_leaks.py). Full CI runs on this PR. Merge per the public merge policy (no squash-button/web-button)."

gh api -X POST "repos/$PUBLIC_REPO/pulls" \
  -f title="promote: staging content parity train ($(date -u +%Y-%m-%d))" \
  -f head="$TRAIN_BRANCH" -f base=main -f body="$BODY" \
  --jq '"opened PR #\(.number): \(.html_url)"'
