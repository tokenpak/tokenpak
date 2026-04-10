#!/bin/bash
# ============================================================
# TokenPak IDE Mode Demo Script
# ============================================================
# Mode:    IDE (claude-code-ide profile — VSCode + Claude Code extension)
# Target:  ~30 seconds SCREEN RECORDING (not asciinema — IDE UI can't be captured by terminal)
# File:    docs/demo/claude-code/ide.sh
#
# WHAT THIS RECORDING SHOULD SHOW:
#   1. Terminal: proxy started + ANTHROPIC_BASE_URL set, then `code .` launched
#   2. IDE: Claude Code extension responding to a prompt in the sidebar
#   3. Terminal (embedded or side-by-side): the tokenpak savings line appearing in the proxy log
#   4. IDE status bar or sidebar showing the profile + cost summary (if CCI-14 UI landed)
#
# HOW TO RECORD (Kevin):
#   This recording requires OBS or equivalent (not asciinema) because the value is
#   in the IDE UI — the Claude Code extension sidebar, the status bar indicator, and
#   the proxy log. Suggested layout:
#     - Split screen: left = VSCode with Claude Code sidebar open, right = terminal showing proxy log
#   Replace docs/demo/claude-code/ide.cast with the screen recording file (mp4/webm)
#   or update the link in docs/claude-code-integration.md to point at a YouTube URL.
#
# REQUIREMENTS BEFORE RECORDING:
#   - VSCode installed with Claude Code extension (v2.1.85+)
#   - tokenpak proxy running: tokenpak serve --port 8766 &
#   - ANTHROPIC_BASE_URL=http://localhost:8766 in shell environment
#   - ANTHROPIC_API_KEY set (or Claude Code OAuth login)
#   - A project open in VSCode with some Python/JS files for the demo prompt
# ============================================================

set -e

clear
echo "# TokenPak IDE mode — VSCode + Claude Code extension"
sleep 1

# Step 1: Start proxy (if not already running)
if ! curl -s http://localhost:8766/health > /dev/null 2>&1; then
    echo "Starting tokenpak proxy..."
    tokenpak serve --port 8766 &
    sleep 1
fi

# Step 2: Set the env var (must be done before launching the IDE)
export ANTHROPIC_BASE_URL=http://localhost:8766
echo "✓ ANTHROPIC_BASE_URL=http://localhost:8766"
sleep 0.5

# Step 3: Confirm proxy is active and will detect IDE mode
tokenpak status
sleep 1

# Step 4: Launch VSCode from this shell (inherits the env var)
echo ""
echo "# Launching VSCode — use Claude Code sidebar to send a prompt"
echo "# The proxy log will show: profile=claude-code-ide vault_blocks=N tokens_in=N saved=\$N"
echo ""
code .

# Kevin: after VSCode opens, switch to screen recording.
# In the Claude Code sidebar, type: "explain what this file does"
# Then switch to the terminal pane to show the proxy log line.
# End recording after the log line appears (~30 seconds from launch).
