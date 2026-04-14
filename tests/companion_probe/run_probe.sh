#!/usr/bin/env bash
# -------------------------------------------------------------------
# tokenpak companion — validation probe launcher
#
# Tests all 7 critical assumptions in one interactive session:
#   1. UserPromptSubmit hook data (transcript_path, session_id)
#   2. Hook stderr visibility in TUI
#   3. Hook exit code 2 blocking (type "BLOCK_TEST" to trigger)
#   4. MCP server startup time
#   5. MCP state persistence + tool calls (call probe_status twice)
#   6. Transcript file readability from MCP (call read_transcript)
#   7. System prompt survival through compaction
#
# Usage:
#   bash /home/sue/tokenpak/tests/companion_probe/run_probe.sh
#
# After launching, try these in the Claude Code TUI:
#   1. "run the probe"           → tests MCP tools (#4, #5)
#   2. "run probe_status again"  → tests state persistence (#5)
#   3. "BLOCK_TEST"              → tests hook blocking (#3)
#   4. Check /tmp/tp-companion-probe.log for hook data (#1, #2)
#   5. Check /tmp/tp-mcp-probe.log for MCP startup time (#4)
# -------------------------------------------------------------------

set -euo pipefail

PROBE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="/tmp/tp-companion-probe.log"
MCP_LOG="/tmp/tp-mcp-probe.log"

# Clean prior probe logs
: > "$LOG"
: > "$MCP_LOG"

echo "=== tokenpak companion probe ==="
echo ""
echo "Probe dir:    $PROBE_DIR"
echo "Hook log:     $LOG"
echo "MCP log:      $MCP_LOG"
echo ""
echo "After Claude Code starts, try:"
echo "  1. Type anything     → hook fires, check $LOG"
echo "  2. 'run the probe'   → tests MCP server"
echo "  3. 'run it again'    → tests state persistence"
echo "  4. 'BLOCK_TEST'      → tests hook blocking (exit 2)"
echo "  5. Check logs after"
echo ""
echo "Launching Claude Code with companion probe..."
echo ""

chmod +x "$PROBE_DIR/hook_probe.sh"

# Pre-flight: verify MCP server responds to initialize
echo -n "Pre-flight: MCP server... "
MCP_TEST=$(echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"preflight","version":"1.0"}}}' \
  | timeout 3 python3 "$PROBE_DIR/mcp_probe_server.py" 2>/dev/null | head -1)
if echo "$MCP_TEST" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['serverInfo']['name']" 2>/dev/null; then
    STARTUP_MS=$(cat "$MCP_LOG" | grep "startup took" | grep -oP '\d+ms' || echo "?ms")
    echo "OK ($STARTUP_MS)"
else
    echo "FAILED"
    echo "MCP server did not respond correctly. Aborting."
    exit 1
fi

# Reset logs for the real session
: > "$LOG"
: > "$MCP_LOG"

exec claude \
    --mcp-config "$PROBE_DIR/mcp_config.json" \
    --append-system-prompt-file "$PROBE_DIR/companion_prompt.md" \
    --settings "$PROBE_DIR/probe_settings.json" \
    "$@"
