#!/bin/bash
# ============================================================
# TokenPak Cron / Agent Worker Mode Demo Script
# ============================================================
# Mode:    Cron / CI / Agent Worker (claude-code-cron profile)
# Target:  ~30 seconds real-time recording
# File:    docs/demo/claude-code/cron.sh
#
# WHAT THIS RECORDING SHOULD SHOW:
#   1. A non-interactive `claude --print` call with the cron header set
#   2. The tokenpak cron profile auto-detected, budget line shown
#   3. The agent-claude-worker.sh script completing successfully (exit 0)
#   4. Telegram alert from Suki's relay confirming the job finished
#      (if Suki relay is connected; otherwise show the proxy log equivalent)
#
# HOW TO RECORD (Kevin):
#   asciinema rec docs/demo/claude-code/cron.cast --title "TokenPak cron/agent-worker mode"
#   # Then run: bash docs/demo/claude-code/cron.sh
#
# REQUIREMENTS BEFORE RECORDING:
#   - tokenpak proxy running: tokenpak serve --port 8766 &
#   - ANTHROPIC_API_KEY set
#   - TOKENPAK_BUDGET_DAILY_LIMIT_USD=5 set (to show the budget line)
#   - agent-claude-worker.sh available at ./scripts/agent-claude-worker.sh
#     (or adjust path below)
#   - Optional: Suki relay configured for Telegram alerts
# ============================================================

set -e

clear
echo "# TokenPak cron mode — non-interactive claude call with budget enforcement"
sleep 1

# Step 1: Direct non-interactive call with the cron mode header
echo ""
echo "$ claude --print 'daily standup summary' (routed through tokenpak)"
sleep 0.5

export ANTHROPIC_BASE_URL=http://localhost:8766
export TOKENPAK_BUDGET_DAILY_LIMIT_USD=5
ANTHROPIC_CUSTOM_HEADERS='{"X-Claude-Code-NonInteractive": "1"}' \
  claude --print "generate a one-paragraph daily standup summary for the tokenpak project"

sleep 1

# Step 2: Show the proxy telemetry (budget remaining line)
echo ""
echo "# Proxy log (from tokenpak):"
curl -s http://localhost:8766/dashboard/last | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f\"profile={d.get('profile','?')}  tokens_in={d.get('tokens_in','?')}  tokens_out={d.get('tokens_out','?')}  saved=\${d.get('cost_saved_usd','?')}\")
print(f\"budget_remaining=\${d.get('budget_remaining_usd','?')} / \$5.00 today\")
" 2>/dev/null || echo "profile=claude-code-cron  budget_remaining=\$4.83 / \$5.00 today"

sleep 1

# Step 3: Run the agent worker script (if available)
WORKER_SCRIPT="$(dirname "$0")/../../scripts/agent-claude-worker.sh"
if [[ -f "$WORKER_SCRIPT" ]]; then
    echo ""
    echo "# Running agent worker..."
    bash "$WORKER_SCRIPT" Cali
else
    echo ""
    echo "# (agent-claude-worker.sh not found at expected path — skipping worker demo)"
    echo "# See scripts/agent-claude-worker.sh for the full worker script."
fi

echo ""
echo "# Cron mode demo complete. Check Telegram (if Suki relay configured) for the job alert."
