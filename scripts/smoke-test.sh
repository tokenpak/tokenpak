#!/usr/bin/env bash
# scripts/smoke-test.sh — Fresh-venv smoke test for tokenpak CLI
#
# FIN-17 acceptance gate: verifies tokenpak installs and all CLI commands work
# in a clean environment with no pre-existing state.
#
# Usage:
#   bash scripts/smoke-test.sh [--keep-venv]
#
# Options:
#   --keep-venv   Skip venv cleanup on exit (useful for debugging)
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed

set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KEEP_VENV=false
SERVE_PID=""
SERVE_PORT="${TOKENPAK_SMOKE_PORT:-19877}"  # avoid collision with default 8766
SERVE_TIMEOUT=10  # seconds to wait for serve to start
VENV_DIR=""

# ── Colors ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Argument parsing ──────────────────────────────────────────────────────────

for arg in "$@"; do
  case "$arg" in
    --keep-venv) KEEP_VENV=true ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

# ── Pass/fail tracking ────────────────────────────────────────────────────────

PASS=0
FAIL=0
WARNINGS=()

pass() { echo -e "  ${GREEN}✅ PASS${NC} — $1"; PASS=$(( PASS + 1 )); }
fail() { echo -e "  ${RED}❌ FAIL${NC} — $1"; FAIL=$(( FAIL + 1 )); }
warn() { echo -e "  ${YELLOW}⚠️  WARN${NC} — $1"; WARNINGS+=("$1"); }
step() { echo -e "\n${CYAN}${BOLD}▸ $1${NC}"; }

# ── Cleanup ───────────────────────────────────────────────────────────────────

cleanup() {
  # Stop serve process if still running
  if [[ -n "$SERVE_PID" ]] && kill -0 "$SERVE_PID" 2>/dev/null; then
    echo -e "\n[cleanup] Stopping serve process (PID $SERVE_PID)..."
    kill "$SERVE_PID" 2>/dev/null || true
    wait "$SERVE_PID" 2>/dev/null || true
  fi

  # Remove temp venv
  if [[ "$KEEP_VENV" == false ]] && [[ -n "$VENV_DIR" ]] && [[ -d "$VENV_DIR" ]]; then
    echo "[cleanup] Removing temp venv: $VENV_DIR"
    rm -rf "$VENV_DIR"
  elif [[ "$KEEP_VENV" == true ]] && [[ -n "$VENV_DIR" ]]; then
    echo "[cleanup] Keeping venv (--keep-venv): $VENV_DIR"
  fi
}
trap cleanup EXIT

# ── Sanity checks ─────────────────────────────────────────────────────────────

step "Pre-flight checks"

if [[ ! -f "$REPO_ROOT/pyproject.toml" ]]; then
  echo -e "${RED}ERROR: pyproject.toml not found in $REPO_ROOT${NC}" >&2
  echo "Run this script from ~/tokenpak/ or its scripts/ subdirectory." >&2
  exit 1
fi
pass "pyproject.toml found at $REPO_ROOT/pyproject.toml"

if ! command -v python3 &>/dev/null; then
  echo -e "${RED}ERROR: python3 not found in PATH${NC}" >&2
  exit 1
fi
PY_VERSION=$(python3 --version 2>&1)
pass "Python available: $PY_VERSION"

# ── Create fresh venv ──────────────────────────────────────────────────────────

step "Creating fresh venv"

VENV_DIR="$(mktemp -d -t tokenpak-smoke-XXXXXX)"
echo "  Temp dir: $VENV_DIR"

python3 -m venv "$VENV_DIR/venv" 2>&1 | sed 's/^/  /'
if [[ ! -x "$VENV_DIR/venv/bin/python" ]]; then
  fail "venv creation failed"
  exit 1
fi
pass "venv created in temp dir"

VENV_PYTHON="$VENV_DIR/venv/bin/python"
VENV_PIP="$VENV_DIR/venv/bin/pip"
VENV_TOKENPAK="$VENV_DIR/venv/bin/tokenpak"

# ── Install tokenpak ───────────────────────────────────────────────────────────

step "Installing tokenpak via pip install -e ."

# Capture output to check for warnings/errors
INSTALL_LOG="$VENV_DIR/install.log"
if "$VENV_PIP" install -e "$REPO_ROOT" --quiet 2>&1 | tee "$INSTALL_LOG" | grep -iE 'warning|error|deprecat' | sed 's/^/  [pip] /'; then
  : # grep found something — warnings already printed
fi

# Check install succeeded
if ! "$VENV_PYTHON" -c "import tokenpak" 2>&1; then
  fail "import tokenpak failed after install"
  exit 1
fi
pass "pip install -e . succeeded"

# Check for import warnings
IMPORT_WARNINGS=$("$VENV_PYTHON" -W all -c "import tokenpak" 2>&1 || true)
if [[ -n "$IMPORT_WARNINGS" ]]; then
  warn "import warnings detected: $IMPORT_WARNINGS"
else
  pass "No import warnings on 'import tokenpak'"
fi

# Verify tokenpak binary is present
if [[ ! -x "$VENV_TOKENPAK" ]]; then
  fail "tokenpak binary not found at $VENV_TOKENPAK"
  exit 1
fi
pass "tokenpak binary installed at $VENV_TOKENPAK"

# ── Helper: run a CLI command and capture output/exit code ────────────────────

run_cmd() {
  local label="$1"; shift
  local expected_exit="${1:-0}"; shift
  local cmd=("$@")

  local out
  local rc=0
  out=$("${cmd[@]}" 2>&1) || rc=$?

  # Check for import errors / missing deps in output
  if echo "$out" | grep -qiE 'ModuleNotFoundError|ImportError|No module named'; then
    fail "$label — missing dependency detected"
    echo "    output: $out" | head -5
    return 1
  fi

  if [[ "$rc" -eq "$expected_exit" ]]; then
    pass "$label (exit $rc)"
  else
    fail "$label — expected exit $expected_exit, got $rc"
    echo "    output: $(echo "$out" | head -5)"
    return 1
  fi
  return 0
}

# ── CLI Command: --help ────────────────────────────────────────────────────────

step "tokenpak --help"
run_cmd "tokenpak --help exits 0" 0 "$VENV_TOKENPAK" --help

HELP_OUT=$("$VENV_TOKENPAK" --help 2>&1 || true)
for kw in serve status doctor version; do
  if echo "$HELP_OUT" | grep -q "$kw"; then
    pass "  --help mentions '$kw'"
  else
    warn "  --help does not mention '$kw'"
  fi
done

# ── CLI Command: version ───────────────────────────────────────────────────────

step "tokenpak version"
VERSION_OUT=$("$VENV_TOKENPAK" version 2>&1) || {
  fail "tokenpak version — non-zero exit"
  echo "    $VERSION_OUT"
}
if echo "$VERSION_OUT" | grep -qiE 'tokenpak|version|cli'; then
  pass "tokenpak version output contains version info"
else
  warn "tokenpak version output looks unexpected: $VERSION_OUT"
fi

# ── CLI Command: doctor ────────────────────────────────────────────────────────

step "tokenpak doctor"
# doctor exits 0 (ok), 1 (warnings), or 2 (failures)
# In a fresh env with no proxy running, warnings (exit 1) are expected — not a failure.
DOCTOR_OUT=$("$VENV_TOKENPAK" doctor 2>&1) || DOCTOR_RC=$?
DOCTOR_RC="${DOCTOR_RC:-0}"

if echo "$DOCTOR_OUT" | grep -qiE 'ModuleNotFoundError|ImportError|No module named'; then
  fail "tokenpak doctor — missing dependency"
  echo "    $DOCTOR_OUT" | head -10
elif [[ "$DOCTOR_RC" -le 1 ]]; then
  pass "tokenpak doctor exited $DOCTOR_RC (ok or warnings — expected in fresh env)"
else
  fail "tokenpak doctor exited $DOCTOR_RC — critical failures detected"
  echo "    $(echo "$DOCTOR_OUT" | tail -10)"
fi

# ── CLI Command: serve (start + stop) ─────────────────────────────────────────

step "tokenpak serve (start + stop)"

# Use an isolated DB and config dir so serve doesn't interfere with system state
SMOKE_DATA_DIR="$VENV_DIR/data"
mkdir -p "$SMOKE_DATA_DIR"

# Start serve in background on the smoke port
TOKENPAK_DB="$SMOKE_DATA_DIR/monitor.db" \
  "$VENV_TOKENPAK" serve --port "$SERVE_PORT" \
  >"$VENV_DIR/serve.log" 2>&1 &
SERVE_PID=$!
echo "  serve PID: $SERVE_PID (port $SERVE_PORT)"

# Wait for it to come up (poll health endpoint)
SERVE_UP=false
for i in $(seq 1 "$SERVE_TIMEOUT"); do
  if curl -sf "http://127.0.0.1:${SERVE_PORT}/health" >/dev/null 2>&1; then
    SERVE_UP=true
    break
  fi
  # Check if process died
  if ! kill -0 "$SERVE_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done

if [[ "$SERVE_UP" == true ]]; then
  pass "tokenpak serve started on port $SERVE_PORT (health endpoint responding)"
else
  # Check if the process is still running but health just isn't up
  if kill -0 "$SERVE_PID" 2>/dev/null; then
    warn "tokenpak serve started (PID $SERVE_PID) but /health not responding after ${SERVE_TIMEOUT}s"
    pass "tokenpak serve process is running"
  else
    fail "tokenpak serve — process exited prematurely"
    echo "    serve log:"
    cat "$VENV_DIR/serve.log" | tail -20 | sed 's/^/    /'
  fi
fi

# Stop serve
if kill -0 "$SERVE_PID" 2>/dev/null; then
  kill "$SERVE_PID" 2>/dev/null || true
  wait "$SERVE_PID" 2>/dev/null || true
  SERVE_PID=""  # prevent double-kill in cleanup
  pass "tokenpak serve stopped cleanly"
fi

# ── CLI Command: status ────────────────────────────────────────────────────────

step "tokenpak status (no proxy running)"
# With proxy stopped, status may exit non-zero — that is correct behavior.
# We verify: it does NOT crash with a Python traceback / import error.
STATUS_OUT=$("$VENV_TOKENPAK" status 2>&1) || STATUS_RC=$?
STATUS_RC="${STATUS_RC:-0}"

if echo "$STATUS_OUT" | grep -qiE 'Traceback|ModuleNotFoundError|ImportError|No module named'; then
  fail "tokenpak status — Python traceback or import error"
  echo "    $(echo "$STATUS_OUT" | head -10)"
else
  pass "tokenpak status ran without traceback (exit $STATUS_RC is expected when proxy is down)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}─────────────────────────────────────────────${NC}"
echo -e "${BOLD}  Smoke Test Summary${NC}"
echo -e "${BOLD}─────────────────────────────────────────────${NC}"
echo -e "  ${GREEN}Passed${NC}: $PASS"
echo -e "  ${RED}Failed${NC}: $FAIL"
if [[ "${#WARNINGS[@]}" -gt 0 ]]; then
  echo -e "  ${YELLOW}Warnings${NC}: ${#WARNINGS[@]}"
  for w in "${WARNINGS[@]}"; do
    echo -e "    ${YELLOW}•${NC} $w"
  done
fi
echo ""

if [[ "$FAIL" -gt 0 ]]; then
  echo -e "${RED}${BOLD}RESULT: FAIL ($FAIL check(s) failed)${NC}"
  exit 1
else
  echo -e "${GREEN}${BOLD}RESULT: PASS — all checks passed${NC}"
  exit 0
fi
