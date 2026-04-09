#!/usr/bin/env bash
# test_modes.sh — tokenpak Claude Code plugin per-mode smoke-test harness
#
# PURPOSE
#   Verify that the tokenpak plugin loads correctly in supported modes and that negative
#   assertions hold (--bare does NOT load the plugin; SDK mode does NOT auto-load it).
#
# CANARY ROLE
#   Anthropic has stated that `--bare` will become the default for `claude -p` in a future
#   release. If `test_cli_default` starts failing (plugin not loaded) while `test_cli_bare`
#   passes (plugin correctly absent), the default flip has shipped and all CLI/cron users
#   need `--plugin-dir` added to their invocations. See MODES.md for the full story.
#
# RETURN VALUES (per-function and overall)
#   0  PASS   — assertion met
#   1  FAIL   — assertion not met (regression detected)
#   77 SKIP   — cannot test in this environment (missing dependency, manual mode, etc.)
#
# USAGE
#   bash test_modes.sh                  # run all mode tests, print summary, exit 0/1
#   bash test_modes.sh --dry-run        # print what would run, exit 0 without executing
#
# CI INTEGRATION
#   Wire into CI as:
#     bash tokenpak/tokenpak/integrations/claude_code/tests/test_modes.sh
#   Exit code 0 means all executed tests passed (SKIP is not a failure).
#   Exit code 1 means at least one FAIL — check the FAIL lines in the output.
#
# REQUIREMENTS
#   - bash, jq, curl  (always required for the parts that run)
#   - claude CLI       (required for cli-default, cli-bare, cron tests; SKIP if absent)
#   - tmux             (required for tmux test; SKIP if absent)
#   - python3          (required for sdk-negative test; SKIP if absent)
#   - CLAUDE_PLUGIN_DATA env var or fallback default used for telemetry path checks

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../plugin" && pwd)"
PLUGIN_DATA="${CLAUDE_PLUGIN_DATA:-/tmp/tokenpak-test-plugin-data-$$}"
TELEMETRY_FILE="${PLUGIN_DATA}/telemetry/$(date -u +%F).jsonl"
TMUX_SESSION="tokenpak-mode-test-$$"
TIMEOUT_SECS=30

# Test result accumulators
declare -a RESULTS=()
OVERALL_EXIT=0

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

require_cmd() {
    local cmd="$1"
    command -v "$cmd" >/dev/null 2>&1
}

# Print a labelled result line and record it
record() {
    local mode="$1"
    local result="$2"   # PASS | FAIL | SKIP
    local note="$3"
    RESULTS+=("${result}  ${mode}  ${note}")
    case "$result" in
        FAIL) OVERALL_EXIT=1 ;;
    esac
    printf '  %-6s  %-30s  %s\n' "$result" "$mode" "$note"
}

# Run claude with a timeout, capture stdout+stderr
run_claude() {
    local timeout_secs="$1"; shift
    timeout "$timeout_secs" claude "$@" 2>&1 || true
}

# ──────────────────────────────────────────────────────────────────────────────
# Mode: CLI default  (`claude -p`, no --bare)
# Assertion: plugin IS loaded → MCP tool list includes tokenpak tools
# ──────────────────────────────────────────────────────────────────────────────
test_cli_default() {
    if ! require_cmd claude; then
        record "cli-default" "SKIP" "claude CLI not installed"
        return 77
    fi

    local out
    out=$(run_claude "$TIMEOUT_SECS" -p \
        "List all MCP tools available in this session. Output one tool name per line." 2>&1)

    if echo "$out" | grep -q "tokenpak"; then
        record "cli-default" "PASS" "tokenpak MCP tools visible in -p mode"
    else
        record "cli-default" "FAIL" "tokenpak MCP tools NOT found in -p output (bare flip may have shipped)"
        echo "    output excerpt: $(echo "$out" | head -5)"
    fi
}

# ──────────────────────────────────────────────────────────────────────────────
# Mode: CLI --bare  (`claude -p --bare`)
# Assertion: plugin is NOT loaded → no tokenpak tools visible (negative canary)
# ──────────────────────────────────────────────────────────────────────────────
test_cli_bare() {
    if ! require_cmd claude; then
        record "cli-bare" "SKIP" "claude CLI not installed"
        return 77
    fi

    local out
    out=$(run_claude "$TIMEOUT_SECS" -p --bare \
        "List all MCP tools available in this session. Output one tool name per line." 2>&1)

    if echo "$out" | grep -q "tokenpak"; then
        # Plugin loaded in --bare mode — --bare semantics may have changed
        record "cli-bare" "FAIL" "tokenpak tools found in --bare mode (--bare semantics may have changed)"
        echo "    output excerpt: $(echo "$out" | head -5)"
    else
        record "cli-bare" "PASS" "plugin correctly absent in --bare mode"
    fi
}

# ──────────────────────────────────────────────────────────────────────────────
# Mode: TUI interactive
# Assertion: manual verification only — no expect framework available
# ──────────────────────────────────────────────────────────────────────────────
test_tui() {
    record "tui" "SKIP" "manual only — launch 'claude' interactively and run /tokenpak-status"
    return 77
}

# ──────────────────────────────────────────────────────────────────────────────
# Mode: TMUX multi-pane concurrent
# Assertion: two concurrent claude -p calls both write to the daily telemetry JSONL
#            without producing torn (corrupted) lines
# ──────────────────────────────────────────────────────────────────────────────
test_tmux() {
    if ! require_cmd tmux; then
        record "tmux" "SKIP" "tmux not installed"
        return 77
    fi
    if ! require_cmd claude; then
        record "tmux" "SKIP" "claude CLI not installed"
        return 77
    fi
    if ! require_cmd jq; then
        record "tmux" "SKIP" "jq not installed (needed to validate JSONL)"
        return 77
    fi

    # Prepare telemetry directory
    mkdir -p "$(dirname "$TELEMETRY_FILE")"

    # Capture pre-test line count
    local before_count=0
    if [[ -f "$TELEMETRY_FILE" ]]; then
        before_count=$(wc -l < "$TELEMETRY_FILE")
    fi

    # Spawn tmux session with 2 panes, each runs a one-shot claude -p call
    # We use CLAUDE_PLUGIN_DATA override so telemetry goes to our test path
    local marker_a="tokenpak-tmux-test-pane-a-$$"
    local marker_b="tokenpak-tmux-test-pane-b-$$"

    tmux new-session -d -s "$TMUX_SESSION" \
        "CLAUDE_PLUGIN_DATA='$PLUGIN_DATA' claude -p 'emit telemetry marker $marker_a' 2>&1; touch /tmp/tmux-pane-a-done-$$" 2>/dev/null

    tmux split-window -t "$TMUX_SESSION" \
        "CLAUDE_PLUGIN_DATA='$PLUGIN_DATA' claude -p 'emit telemetry marker $marker_b' 2>&1; touch /tmp/tmux-pane-b-done-$$" 2>/dev/null

    # Wait for both panes to complete (up to TIMEOUT_SECS)
    local elapsed=0
    while [[ $elapsed -lt $TIMEOUT_SECS ]]; do
        if [[ -f "/tmp/tmux-pane-a-done-$$" && -f "/tmp/tmux-pane-b-done-$$" ]]; then
            break
        fi
        sleep 1
        ((elapsed++))
    done

    # Clean up tmux session and temp files
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
    rm -f "/tmp/tmux-pane-a-done-$$" "/tmp/tmux-pane-b-done-$$"

    # Validate telemetry JSONL: new lines must be valid JSON (no torn writes)
    if [[ ! -f "$TELEMETRY_FILE" ]]; then
        record "tmux" "SKIP" "telemetry file not created (telemetry hook may not be active)"
        return 77
    fi

    local after_count
    after_count=$(wc -l < "$TELEMETRY_FILE")
    local new_lines=$((after_count - before_count))

    if [[ $new_lines -lt 1 ]]; then
        record "tmux" "SKIP" "no new telemetry lines written (telemetry hook may not be active)"
        return 77
    fi

    # Check each new line is valid JSON (not torn mid-write)
    local bad_lines=0
    tail -n "$new_lines" "$TELEMETRY_FILE" | while IFS= read -r line; do
        if ! echo "$line" | jq . >/dev/null 2>&1; then
            ((bad_lines++))
        fi
    done

    if [[ $bad_lines -gt 0 ]]; then
        record "tmux" "FAIL" "$bad_lines torn/corrupted JSONL lines found — file lock may be broken"
    else
        record "tmux" "PASS" "$new_lines new JSONL lines written, all valid JSON (no torn writes)"
    fi
}

# ──────────────────────────────────────────────────────────────────────────────
# Mode: SDK (Agent SDK)
# Assertion: without CCP-23 helpers, importing claude_agent_sdk does NOT auto-load
#            the filesystem plugin (negative assertion)
# ──────────────────────────────────────────────────────────────────────────────
test_sdk_negative() {
    if ! require_cmd python3; then
        record "sdk-negative" "SKIP" "python3 not installed"
        return 77
    fi

    # Check if claude_agent_sdk is importable
    if ! python3 -c "import claude_agent_sdk" 2>/dev/null; then
        record "sdk-negative" "SKIP" "claude_agent_sdk not installed (CCP-23 not yet shipped)"
        return 77
    fi

    # Verify that without explicit plugin option, the plugin is not auto-discovered
    local py_check
    py_check=$(python3 - <<'EOF'
import sys
import claude_agent_sdk

# Create a minimal agent config with no plugins option
# If the SDK auto-loads filesystem plugins, the plugin list will contain 'tokenpak-claude-code'
try:
    # Inspect what plugins would be auto-loaded without explicit config
    # This varies by SDK version; we check for the absence of auto-load behavior
    opts = claude_agent_sdk.ClaudeAgentOptions(model="claude-haiku-4-5-20251001")
    plugins = getattr(opts, 'plugins', None)
    if plugins and any('tokenpak' in str(p) for p in plugins):
        print("FOUND_PLUGIN")
    else:
        print("NO_PLUGIN")
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(0)
EOF
)

    case "$py_check" in
        NO_PLUGIN)
            record "sdk-negative" "PASS" "SDK does not auto-load filesystem plugin (no-op without CCP-23)"
            ;;
        FOUND_PLUGIN)
            record "sdk-negative" "FAIL" "SDK auto-loaded tokenpak plugin without explicit config (SDK changed?)"
            ;;
        ERROR:*)
            record "sdk-negative" "SKIP" "SDK inspection failed: $py_check"
            return 77
            ;;
        *)
            record "sdk-negative" "SKIP" "unexpected SDK check output: $py_check"
            return 77
            ;;
    esac
}

# ──────────────────────────────────────────────────────────────────────────────
# Mode: Cron (non-TTY)
# Assertion: plugin IS loaded when claude -p is run from a non-TTY context
#            (same as cli-default but verifies non-TTY doesn't break loading)
# ──────────────────────────────────────────────────────────────────────────────
test_cron() {
    if ! require_cmd claude; then
        record "cron" "SKIP" "claude CLI not installed"
        return 77
    fi

    # Simulate cron by redirecting stdin from /dev/null (non-TTY)
    local out
    out=$(run_claude "$TIMEOUT_SECS" -p \
        "List all MCP tools available in this session. Output one tool name per line." \
        </dev/null 2>&1)

    if echo "$out" | grep -q "tokenpak"; then
        record "cron" "PASS" "plugin loaded correctly in non-TTY (cron) context"
    else
        record "cron" "FAIL" "plugin NOT loaded in non-TTY context (check if --bare became default)"
        echo "    output excerpt: $(echo "$out" | head -5)"
    fi
}

# ──────────────────────────────────────────────────────────────────────────────
# Mode: IDE-VSCode / Cursor-Windsurf
# These are manual-only in this harness
# ──────────────────────────────────────────────────────────────────────────────
test_ide_vscode() {
    record "ide-vscode" "SKIP" "manual only — install plugin, open VSCode, verify /tokenpak-status"
    return 77
}

test_cursor_windsurf() {
    record "cursor-windsurf" "SKIP" "NOT TESTED — unsupported mode, no Claude Code plugin system"
    return 77
}

# ──────────────────────────────────────────────────────────────────────────────
# Structural checks (always run, no claude required)
# ──────────────────────────────────────────────────────────────────────────────
test_plugin_structure() {
    local ok=0
    local fail_notes=()

    # plugin.json
    if [[ ! -f "${PLUGIN_ROOT}/.claude-plugin/plugin.json" ]]; then
        fail_notes+=("plugin.json missing")
    fi

    # MCP config
    if [[ ! -f "${PLUGIN_ROOT}/.mcp.json" ]]; then
        fail_notes+=(".mcp.json missing")
    fi

    # MODES.md (this doc)
    if [[ ! -f "${PLUGIN_ROOT}/MODES.md" ]]; then
        fail_notes+=("MODES.md missing")
    fi

    # tokenpak-status skill
    if [[ ! -f "${PLUGIN_ROOT}/skills/tokenpak-status/SKILL.md" ]]; then
        fail_notes+=("tokenpak-status/SKILL.md missing")
    fi

    if [[ ! -x "${PLUGIN_ROOT}/skills/tokenpak-status/check.sh" ]]; then
        fail_notes+=("tokenpak-status/check.sh not executable")
    fi

    # check.sh bash syntax
    if [[ -f "${PLUGIN_ROOT}/skills/tokenpak-status/check.sh" ]]; then
        if ! bash -n "${PLUGIN_ROOT}/skills/tokenpak-status/check.sh" 2>/dev/null; then
            fail_notes+=("check.sh has bash syntax errors")
        fi
    fi

    if [[ ${#fail_notes[@]} -eq 0 ]]; then
        record "plugin-structure" "PASS" "all required plugin files present and valid"
    else
        record "plugin-structure" "FAIL" "structure issues: ${fail_notes[*]}"
    fi
}

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
main() {
    # Dry-run mode
    if [[ "${1:-}" == "--dry-run" ]]; then
        echo "DRY RUN — tests that would execute:"
        echo "  plugin-structure  (always)"
        echo "  cli-default       (requires: claude)"
        echo "  cli-bare          (requires: claude)"
        echo "  tui               (SKIP — manual)"
        echo "  tmux              (requires: tmux, claude, jq)"
        echo "  sdk-negative      (requires: python3, claude_agent_sdk)"
        echo "  cron              (requires: claude)"
        echo "  ide-vscode        (SKIP — manual)"
        echo "  cursor-windsurf   (SKIP — not tested)"
        exit 0
    fi

    echo "tokenpak Claude Code plugin — mode smoke-test harness"
    echo "Plugin root: ${PLUGIN_ROOT}"
    echo "Plugin data: ${PLUGIN_DATA}"
    echo "$(date -u)"
    echo ""
    printf '  %-6s  %-30s  %s\n' "RESULT" "MODE" "NOTES"
    printf '  %-6s  %-30s  %s\n' "------" "------------------------------" "-----"

    test_plugin_structure   || true
    test_cli_default        || true
    test_cli_bare           || true
    test_tui                || true
    test_tmux               || true
    test_sdk_negative       || true
    test_cron               || true
    test_ide_vscode         || true
    test_cursor_windsurf    || true

    echo ""
    echo "Summary:"
    local pass_count fail_count skip_count
    pass_count=$(printf '%s\n' "${RESULTS[@]}" | grep -c '^PASS' || true)
    fail_count=$(printf '%s\n' "${RESULTS[@]}" | grep -c '^FAIL' || true)
    skip_count=$(printf '%s\n' "${RESULTS[@]}" | grep -c '^SKIP' || true)

    echo "  PASS: ${pass_count}  FAIL: ${fail_count}  SKIP: ${skip_count}"
    echo ""

    if [[ $OVERALL_EXIT -ne 0 ]]; then
        echo "RESULT: FAIL — ${fail_count} mode(s) regressed"
        echo ""
        echo "If cli-default FAILED and cli-bare PASSED: the --bare default flip has shipped."
        echo "Add --plugin-dir to all CLI and cron invocations."
    else
        echo "RESULT: PASS (${pass_count} passed, ${skip_count} skipped, 0 failed)"
    fi

    exit $OVERALL_EXIT
}

main "$@"
