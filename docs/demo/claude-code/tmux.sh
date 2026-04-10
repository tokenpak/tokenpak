#!/bin/bash
# ============================================================
# TokenPak tmux Multi-Instance Demo Script
# ============================================================
# Mode:    tmux (claude-code-tmux profile)
# Target:  ~30 seconds real-time recording
# File:    docs/demo/claude-code/tmux.sh
#
# WHAT THIS RECORDING SHOULD SHOW:
#   1. Three tmux sessions launched in the background (project-a, project-b, research)
#   2. tokenpak auto-detecting multi-session mode
#   3. Dashboard output showing per-session cost isolation + cross-session cache savings
#
# HOW TO RECORD (Kevin):
#   # In a fresh tmux window, start recording:
#   asciinema rec docs/demo/claude-code/tmux.cast --title "TokenPak tmux multi-instance mode"
#   # Then paste the script below. The dashboard curl is the money shot.
#
# REQUIREMENTS BEFORE RECORDING:
#   - tmux installed
#   - tokenpak proxy running: tokenpak serve --port 8766 &
#   - export ANTHROPIC_BASE_URL=http://localhost:8766
#   - ANTHROPIC_API_KEY set
#   - At least a few prior requests in each session (for cache hits to show)
#   - python3 available for json.tool formatting
# ============================================================

set -e

clear
echo "# TokenPak tmux mode — 3 parallel Claude Code sessions"
sleep 1

# Step 1: Launch three background tmux sessions (each runs claude interactively)
export ANTHROPIC_BASE_URL=http://localhost:8766

tmux new-session -d -s project-a -x 200 -y 50 \
  "ANTHROPIC_BASE_URL=http://localhost:8766 claude --print 'summarize the auth module'"
tmux new-session -d -s project-b -x 200 -y 50 \
  "ANTHROPIC_BASE_URL=http://localhost:8766 claude --print 'what tests exist in this repo?'"
tmux new-session -d -s research  -x 200 -y 50 \
  "ANTHROPIC_BASE_URL=http://localhost:8766 claude --print 'list external dependencies'"

echo ""
echo "✓ project-a session started"
echo "✓ project-b session started"
echo "✓ research  session started"
sleep 2

# Step 2: Poll until sessions have made at least one request
echo ""
echo "# Waiting for sessions to complete their requests..."
sleep 5

# Step 3: Show dashboard (the money shot — per-session spend + cross-session cache)
echo ""
echo "# Dashboard: per-session cost breakdown"
curl -s http://localhost:8766/dashboard/sessions | python3 -m json.tool

sleep 2
echo ""
echo "# Cross-session cache hits visible above — duplicate context sent once across all 3 panes."
