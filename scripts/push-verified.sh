#!/usr/bin/env bash
# push-verified.sh — Push to origin + shared, then SSH-verify both landed
# Usage: bash scripts/push-verified.sh [branch]
# Exits non-zero if any push or verification fails.

set -euo pipefail

BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
SHARED_HOST="sue@suewu"
SHARED_REPO="~/tokenpak-origin.git"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

pass() { echo -e "${GREEN}✅ $*${NC}"; }
fail() { echo -e "${RED}❌ $*${NC}"; exit 1; }

# Get local commit hash before pushing
LOCAL_HASH=$(git rev-parse HEAD)
SHORT_HASH=$(git rev-parse --short HEAD)

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  push-verified.sh  |  branch: $BRANCH"
echo "  commit: $SHORT_HASH"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Push to origin ──────────────────────────────────────────────────────────
echo ""
echo "Pushing to origin..."
if git push origin "$BRANCH" 2>&1; then
    ORIGIN_HASH=$(git ls-remote origin "refs/heads/$BRANCH" 2>/dev/null | awk '{print $1}')
    if [ "$ORIGIN_HASH" = "$LOCAL_HASH" ]; then
        pass "origin/$BRANCH @ $SHORT_HASH"
    else
        fail "origin/$BRANCH hash mismatch: expected $SHORT_HASH, got ${ORIGIN_HASH:0:7}"
    fi
else
    fail "Push to origin failed"
fi

# ── Push to shared ──────────────────────────────────────────────────────────
echo ""
echo "Pushing to shared..."
if git push shared "$BRANCH" 2>&1; then
    pass "shared/$BRANCH push completed"
else
    fail "Push to shared failed"
fi

# ── Verify on SueBot via SSH ────────────────────────────────────────────────
echo ""
echo "Verifying on SueBot..."
REMOTE_HASH=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$SHARED_HOST" \
    "git -C $SHARED_REPO rev-parse refs/heads/$BRANCH 2>/dev/null || git -C $SHARED_REPO rev-parse HEAD 2>/dev/null" 2>&1) || \
    fail "SSH to SueBot failed — cannot verify shared remote"

REMOTE_SHORT="${REMOTE_HASH:0:7}"
if [ "$REMOTE_HASH" = "$LOCAL_HASH" ]; then
    pass "SueBot has $SHORT_HASH"
else
    fail "SueBot hash mismatch: expected $SHORT_HASH, got $REMOTE_SHORT"
fi

# ── All good ─────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
pass "All remotes verified — $SHORT_HASH landed everywhere."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
