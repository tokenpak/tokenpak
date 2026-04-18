#!/usr/bin/env bash
# scripts/smoketest_integrate_apply.sh — GTM-02: verify `tokenpak integrate --apply`
# on per-client fresh fake configs (tmpdir isolation, never touches real $HOME).
#
# Usage:
#   bash scripts/smoketest_integrate_apply.sh
#
# Exit codes:
#   0 — all assertions passed
#   1 — one or more assertions failed

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REAL_HOME="$HOME"  # preserve for PYTHONUSERBASE — watchdog lives in ~/.local

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

PASSED=0; FAILED=0

pass() { echo -e "    ${GREEN}✅ PASS${NC} $1"; PASSED=$(( PASSED + 1 )); }
fail() { echo -e "    ${RED}❌ FAIL${NC} $1"; FAILED=$(( FAILED + 1 )); }
step() { echo -e "\n${CYAN}${BOLD}▸ $1${NC}"; }

# Run tokenpak integrate <client> --apply with HOME overridden to tmpdir.
# PYTHONUSERBASE is kept pointing at the real home so user site-packages
# (watchdog, etc.) remain importable even while HOME is redirected.
# Default proxy URL (http://localhost:8766) is used — no --proxy-url needed.
tp_apply() {
    local home_dir="$1" client="$2"
    HOME="$home_dir" PYTHONUSERBASE="${REAL_HOME}/.local" python3 -m tokenpak \
        integrate "$client" --apply 2>&1
}

# ---------------------------------------------------------------------------
# CLIENT: claude-code
# ---------------------------------------------------------------------------
step "claude-code — apply + idempotent + rollback"

CC_TMP=$(mktemp -d)
trap 'rm -rf "$CC_TMP"' EXIT

# Pre-create fresh ~/.claude/settings.json
mkdir -p "$CC_TMP/.claude"
printf '{}' > "$CC_TMP/.claude/settings.json"
ORIGINAL_CC=$(cat "$CC_TMP/.claude/settings.json")

# First apply
CC_OUT=$(tp_apply "$CC_TMP" claude-code)
CC_EXIT=$?
echo "$CC_OUT" | sed 's/^/    /'

if [[ $CC_EXIT -eq 0 ]]; then
    pass "claude-code: apply exits 0"
else
    fail "claude-code: apply exited $CC_EXIT"
fi

if echo "$CC_OUT" | grep -q "Applied"; then
    pass "claude-code: output contains 'Applied'"
else
    fail "claude-code: expected 'Applied' in output"
fi

# Backup assertion
CC_BAK="$CC_TMP/.claude/settings.json.bak"
if [[ -f "$CC_BAK" ]]; then
    pass "claude-code: backup exists at settings.json.bak"
else
    fail "claude-code: backup not found at $CC_BAK"
fi

# Save a snapshot of the backup BEFORE idempotent apply overwrites it.
# The idempotent run calls _backup_settings() again, replacing the backup
# with the already-applied content — so we must snapshot now.
CC_BAK_SNAP="$CC_TMP/.claude/settings.json.bak.snap"
cp "$CC_BAK" "$CC_BAK_SNAP" 2>/dev/null || true

# Key assertion — ANTHROPIC_BASE_URL in env block
CC_HAS_KEY=$(HOME="$CC_TMP" python3 - <<'EOF'
import json, pathlib, sys
p = pathlib.Path.home() / ".claude" / "settings.json"
d = json.loads(p.read_text())
print("ok" if d.get("env", {}).get("ANTHROPIC_BASE_URL") else "missing")
EOF
)
if [[ "$CC_HAS_KEY" == "ok" ]]; then
    pass "claude-code: env.ANTHROPIC_BASE_URL written to settings.json"
else
    fail "claude-code: env.ANTHROPIC_BASE_URL not found in settings.json"
fi

# Idempotent apply
CC_OUT2=$(tp_apply "$CC_TMP" claude-code)
CC_EXIT2=$?
echo "$CC_OUT2" | sed 's/^/    /'

if [[ $CC_EXIT2 -eq 0 ]] && echo "$CC_OUT2" | grep -qi "no changes"; then
    pass "claude-code: second apply reports 'no changes'"
else
    fail "claude-code: second apply did not report 'no changes' (exit=$CC_EXIT2)"
fi

# Rollback assertion — use snapshot (pre-apply backup) not current .bak
cp "$CC_BAK_SNAP" "$CC_TMP/.claude/settings.json"
RESTORED_CC=$(cat "$CC_TMP/.claude/settings.json")
if [[ "$RESTORED_CC" == "$ORIGINAL_CC" ]]; then
    pass "claude-code: rollback restores original content byte-for-byte"
else
    fail "claude-code: rollback mismatch — original='$ORIGINAL_CC' restored='$RESTORED_CC'"
fi

trap - EXIT
rm -rf "$CC_TMP"

# ---------------------------------------------------------------------------
# CLIENT: cursor
# ---------------------------------------------------------------------------
step "cursor — apply + idempotent + rollback"

CUR_TMP=$(mktemp -d)
trap 'rm -rf "$CUR_TMP"' EXIT

# Pre-create fake Cursor User dir + minimal settings.json
mkdir -p "$CUR_TMP/.config/Cursor/User"
printf '{}' > "$CUR_TMP/.config/Cursor/User/settings.json"
ORIGINAL_CUR=$(cat "$CUR_TMP/.config/Cursor/User/settings.json")

# First apply
CUR_OUT=$(tp_apply "$CUR_TMP" cursor)
CUR_EXIT=$?
echo "$CUR_OUT" | sed 's/^/    /'

if [[ $CUR_EXIT -eq 0 ]]; then
    pass "cursor: apply exits 0"
else
    fail "cursor: apply exited $CUR_EXIT"
fi

if echo "$CUR_OUT" | grep -q "Applied"; then
    pass "cursor: output contains 'Applied'"
else
    fail "cursor: expected 'Applied' in output"
fi

# Backup assertion
CUR_BAK="$CUR_TMP/.config/Cursor/User/settings.json.bak"
if [[ -f "$CUR_BAK" ]]; then
    pass "cursor: backup exists at settings.json.bak"
else
    fail "cursor: backup not found at $CUR_BAK"
fi
CUR_BAK_SNAP="$CUR_TMP/.config/Cursor/User/settings.json.bak.snap"
cp "$CUR_BAK" "$CUR_BAK_SNAP" 2>/dev/null || true

# Key assertion — cursor.general.openaiBaseUrl in settings.json
CUR_HAS_KEY=$(HOME="$CUR_TMP" python3 - <<'EOF'
import json, pathlib
p = pathlib.Path.home() / ".config/Cursor/User/settings.json"
d = json.loads(p.read_text())
print("ok" if d.get("cursor.general.openaiBaseUrl") else "missing")
EOF
)
if [[ "$CUR_HAS_KEY" == "ok" ]]; then
    pass "cursor: cursor.general.openaiBaseUrl written to settings.json"
else
    fail "cursor: cursor.general.openaiBaseUrl not found in settings.json"
fi

# Idempotent apply
CUR_OUT2=$(tp_apply "$CUR_TMP" cursor)
CUR_EXIT2=$?
echo "$CUR_OUT2" | sed 's/^/    /'

if [[ $CUR_EXIT2 -eq 0 ]] && echo "$CUR_OUT2" | grep -qi "no changes"; then
    pass "cursor: second apply reports 'no changes'"
else
    fail "cursor: second apply did not report 'no changes' (exit=$CUR_EXIT2)"
fi

# Rollback assertion — use pre-idempotent snapshot
cp "$CUR_BAK_SNAP" "$CUR_TMP/.config/Cursor/User/settings.json"
RESTORED_CUR=$(cat "$CUR_TMP/.config/Cursor/User/settings.json")
if [[ "$RESTORED_CUR" == "$ORIGINAL_CUR" ]]; then
    pass "cursor: rollback restores original content byte-for-byte"
else
    fail "cursor: rollback mismatch"
fi

trap - EXIT
rm -rf "$CUR_TMP"

# ---------------------------------------------------------------------------
# CLIENT: continue
# ---------------------------------------------------------------------------
step "continue — apply + idempotent + rollback"

CON_TMP=$(mktemp -d)
trap 'rm -rf "$CON_TMP"' EXIT

# Pre-create fresh ~/.continue/config.json with empty models
mkdir -p "$CON_TMP/.continue"
printf '{"models":[]}' > "$CON_TMP/.continue/config.json"
ORIGINAL_CON=$(cat "$CON_TMP/.continue/config.json")

# First apply
CON_OUT=$(tp_apply "$CON_TMP" continue)
CON_EXIT=$?
echo "$CON_OUT" | sed 's/^/    /'

if [[ $CON_EXIT -eq 0 ]]; then
    pass "continue: apply exits 0"
else
    fail "continue: apply exited $CON_EXIT"
fi

if echo "$CON_OUT" | grep -q "Applied"; then
    pass "continue: output contains 'Applied'"
else
    fail "continue: expected 'Applied' in output"
fi

# Backup assertion
CON_BAK="$CON_TMP/.continue/config.json.bak"
if [[ -f "$CON_BAK" ]]; then
    pass "continue: backup exists at config.json.bak"
else
    fail "continue: backup not found at $CON_BAK"
fi
CON_BAK_SNAP="$CON_TMP/.continue/config.json.bak.snap"
cp "$CON_BAK" "$CON_BAK_SNAP" 2>/dev/null || true

# Key assertion — tokenpak-sonnet model entry present
CON_HAS_KEY=$(HOME="$CON_TMP" python3 - <<'EOF'
import json, pathlib
p = pathlib.Path.home() / ".continue/config.json"
d = json.loads(p.read_text())
titles = [m.get("title") for m in d.get("models", [])]
print("ok" if "tokenpak-sonnet" in titles else "missing")
EOF
)
if [[ "$CON_HAS_KEY" == "ok" ]]; then
    pass "continue: tokenpak-sonnet model entry written to config.json"
else
    fail "continue: tokenpak-sonnet model entry not found in config.json"
fi

# Idempotent apply
CON_OUT2=$(tp_apply "$CON_TMP" continue)
CON_EXIT2=$?
echo "$CON_OUT2" | sed 's/^/    /'

if [[ $CON_EXIT2 -eq 0 ]] && echo "$CON_OUT2" | grep -qi "no changes"; then
    pass "continue: second apply reports 'no changes'"
else
    fail "continue: second apply did not report 'no changes' (exit=$CON_EXIT2)"
fi

# Rollback assertion — use pre-idempotent snapshot
cp "$CON_BAK_SNAP" "$CON_TMP/.continue/config.json"
RESTORED_CON=$(cat "$CON_TMP/.continue/config.json")
if [[ "$RESTORED_CON" == "$ORIGINAL_CON" ]]; then
    pass "continue: rollback restores original content byte-for-byte"
else
    fail "continue: rollback mismatch"
fi

trap - EXIT
rm -rf "$CON_TMP"

# ---------------------------------------------------------------------------
# CLIENT: aider (with pre-existing config)
# ---------------------------------------------------------------------------
step "aider — apply + idempotent + rollback"

AID_TMP=$(mktemp -d)
trap 'rm -rf "$AID_TMP"' EXIT

# Pre-create minimal ~/.aider.conf.yml with unrelated key
printf 'model: gpt-4o\n' > "$AID_TMP/.aider.conf.yml"
ORIGINAL_AID=$(cat "$AID_TMP/.aider.conf.yml")

# First apply
AID_OUT=$(tp_apply "$AID_TMP" aider)
AID_EXIT=$?
echo "$AID_OUT" | sed 's/^/    /'

if [[ $AID_EXIT -eq 0 ]]; then
    pass "aider: apply exits 0"
else
    fail "aider: apply exited $AID_EXIT"
fi

if echo "$AID_OUT" | grep -q "Applied"; then
    pass "aider: output contains 'Applied'"
else
    fail "aider: expected 'Applied' in output"
fi

# Backup assertion
AID_BAK="$AID_TMP/.aider.conf.yml.bak"
if [[ -f "$AID_BAK" ]]; then
    pass "aider: backup exists at .aider.conf.yml.bak"
else
    fail "aider: backup not found at $AID_BAK"
fi
AID_BAK_SNAP="$AID_TMP/.aider.conf.yml.bak.snap"
cp "$AID_BAK" "$AID_BAK_SNAP" 2>/dev/null || true

# Key assertion — openai-api-base present
AID_HAS_KEY=$(HOME="$AID_TMP" python3 - <<'EOF'
import pathlib
text = (pathlib.Path.home() / ".aider.conf.yml").read_text()
print("ok" if "openai-api-base:" in text else "missing")
EOF
)
if [[ "$AID_HAS_KEY" == "ok" ]]; then
    pass "aider: openai-api-base written to .aider.conf.yml"
else
    fail "aider: openai-api-base not found in .aider.conf.yml"
fi

# Idempotent apply
AID_OUT2=$(tp_apply "$AID_TMP" aider)
AID_EXIT2=$?
echo "$AID_OUT2" | sed 's/^/    /'

if [[ $AID_EXIT2 -eq 0 ]] && echo "$AID_OUT2" | grep -qi "no changes"; then
    pass "aider: second apply reports 'no changes'"
else
    fail "aider: second apply did not report 'no changes' (exit=$AID_EXIT2)"
fi

# Rollback assertion — use pre-idempotent snapshot
cp "$AID_BAK_SNAP" "$AID_TMP/.aider.conf.yml"
RESTORED_AID=$(cat "$AID_TMP/.aider.conf.yml")
if [[ "$RESTORED_AID" == "$ORIGINAL_AID" ]]; then
    pass "aider: rollback restores original content byte-for-byte"
else
    fail "aider: rollback mismatch"
fi

trap - EXIT
rm -rf "$AID_TMP"

# ---------------------------------------------------------------------------
# CLIENT: aider (fresh — no prior config file)
# ---------------------------------------------------------------------------
step "aider-fresh — create on first apply + idempotent (no backup expected)"

AIDF_TMP=$(mktemp -d)
trap 'rm -rf "$AIDF_TMP"' EXIT

# No pre-existing ~/.aider.conf.yml
AIDF_OUT=$(tp_apply "$AIDF_TMP" aider)
AIDF_EXIT=$?
echo "$AIDF_OUT" | sed 's/^/    /'

if [[ $AIDF_EXIT -eq 0 ]]; then
    pass "aider-fresh: apply exits 0"
else
    fail "aider-fresh: apply exited $AIDF_EXIT"
fi

AIDF_CONF="$AIDF_TMP/.aider.conf.yml"
if [[ -f "$AIDF_CONF" ]]; then
    pass "aider-fresh: .aider.conf.yml created on first apply"
else
    fail "aider-fresh: .aider.conf.yml not created"
fi

# Idempotent
AIDF_OUT2=$(tp_apply "$AIDF_TMP" aider)
AIDF_EXIT2=$?

if [[ $AIDF_EXIT2 -eq 0 ]] && echo "$AIDF_OUT2" | grep -qi "no changes"; then
    pass "aider-fresh: second apply reports 'no changes'"
else
    fail "aider-fresh: second apply did not report 'no changes' (exit=$AIDF_EXIT2)"
fi

trap - EXIT
rm -rf "$AIDF_TMP"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
TOTAL=$(( PASSED + FAILED ))
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}RESULT: PASS — all $TOTAL assertions passed${NC}"
    echo "  PASSED: $PASSED assertions"
    echo "  FAILED: $FAILED assertions"
    echo ""
    echo "  ▸ claude-code  ✅ PASS (apply, backup, key, idempotent, rollback)"
    echo "  ▸ cursor       ✅ PASS (apply, backup, key, idempotent, rollback)"
    echo "  ▸ continue     ✅ PASS (apply, backup, key, idempotent, rollback)"
    echo "  ▸ aider        ✅ PASS (apply, backup, key, idempotent, rollback)"
    echo "  ▸ aider-fresh  ✅ PASS (create, idempotent; no backup — no prior file)"
    exit 0
else
    echo -e "${RED}${BOLD}RESULT: FAIL — $FAILED of $TOTAL assertions failed${NC}"
    echo "  PASSED: $PASSED assertions"
    echo "  FAILED: $FAILED assertions"
    exit 1
fi
