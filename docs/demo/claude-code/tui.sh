#!/bin/bash
# ============================================================
# TokenPak TUI Mode Demo Script
# ============================================================
# Mode:    TUI (claude-code-tui profile, interactive REPL)
# Target:  ~30 seconds real-time recording
# File:    docs/demo/claude-code/tui.sh
#
# WHAT THIS RECORDING SHOULD SHOW:
#   1. Starting `claude` in interactive mode with the proxy active
#   2. Sending one prompt and receiving a response
#   3. The per-turn savings indicator in the TUI ([tokenpak] ✓ cache hit)
#   4. Session cost summary when the session ends (Ctrl+C or /exit)
#
# HOW TO RECORD (Kevin):
#   asciinema rec docs/demo/claude-code/tui.cast --title "TokenPak TUI mode"
#   # Run steps manually — the interactive session won't automate.
#   # For the prompt, type: "explain the auth flow in this repo"
#   # Exit with /exit or Ctrl+C after the response completes.
#
# REQUIREMENTS BEFORE RECORDING:
#   - tokenpak proxy running: tokenpak serve --port 8766 &
#   - export ANTHROPIC_BASE_URL=http://localhost:8766
#   - ANTHROPIC_API_KEY set
#   - Claude Code ≥ 2.0 installed
#   - CCI-14 (savings tape) shipped; otherwise the per-turn line shows at session end only
# ============================================================

clear

# Step 1: Confirm proxy is up and the env var is set
export ANTHROPIC_BASE_URL=http://localhost:8766
tokenpak status

sleep 1

# Step 2: Start the interactive claude session
# Kevin: type your prompt naturally after the session opens.
# Suggested prompt: "explain the auth flow in this repo"
echo ""
echo "# Starting interactive claude session..."
sleep 1
claude

# After the session closes, the script ends.
# The recording should capture the session-end cost summary line printed by tokenpak.
