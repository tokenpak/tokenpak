#!/bin/bash
# ============================================================
# TokenPak CLI Mode Demo Script
# ============================================================
# Mode:    CLI (claude-code-cli profile)
# Target:  ~30 seconds real-time recording
# File:    docs/demo/claude-code/cli.sh
#
# WHAT THIS RECORDING SHOULD SHOW:
#   1. One-command install that sets up the proxy + env var
#   2. A real `claude --print` call showing the inline savings line
#   3. `tokenpak status` confirming the proxy is up + today's savings
#
# HOW TO RECORD (Kevin):
#   asciinema rec docs/demo/claude-code/cli.cast --title "TokenPak CLI mode"
#   # Then run the commands below interactively (or paste block-by-block)
#   # Target: finish within 30 seconds. Keep typing speed natural.
#
# REQUIREMENTS BEFORE RECORDING:
#   - tokenpak installed: pip install tokenpak
#   - ANTHROPIC_API_KEY set in environment
#   - Vault has at least 1 document (run: tokenpak vault add README.md)
#   - No proxy already running on :8766
# ============================================================

clear
echo "# TokenPak CLI mode — one-command setup"
sleep 1

# Step 1: One-command install (installs proxy, sets ANTHROPIC_BASE_URL in ~/.bashrc)
tokenpak install --claude-code
sleep 1

# Step 2: Verify the proxy is running
tokenpak status
sleep 1

# Step 3: Run a real claude --print call through the proxy
# Expected output: tokenpak inline savings line in stderr + Claude's answer in stdout
echo ""
echo "# Asking Claude about this repo..."
claude --print "what does this repo do?" < README.md
sleep 2

# Step 4: Show updated savings counter
echo ""
tokenpak status
