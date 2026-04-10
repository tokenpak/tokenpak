#!/bin/bash
# ============================================================
# TokenPak SDK Mode Demo Script
# ============================================================
# Mode:    SDK / Claude Agent SDK (claude-code-sdk profile)
# Target:  ~30 seconds real-time recording
# File:    docs/demo/claude-code/sdk.sh
#
# WHAT THIS RECORDING SHOULD SHOW:
#   1. A minimal Python script routing the Anthropic SDK through tokenpak
#   2. The response arriving successfully
#   3. Dashboard telemetry confirming the call was tracked (profile, latency, OTLP export)
#
# HOW TO RECORD (Kevin):
#   asciinema rec docs/demo/claude-code/sdk.cast --title "TokenPak SDK mode"
#   # Then run: bash docs/demo/claude-code/sdk.sh
#
# REQUIREMENTS BEFORE RECORDING:
#   - tokenpak proxy running: tokenpak serve --port 8766 &
#   - pip install anthropic
#   - ANTHROPIC_API_KEY set
#   - python3 with json module available
# ============================================================

set -e

clear
echo "# TokenPak SDK mode — Python Anthropic SDK through the proxy"
sleep 1

# Step 1: Write the demo Python script inline
cat > /tmp/tokenpak_sdk_demo.py << 'PYEOF'
import anthropic
import os

# Route through tokenpak proxy — zero other changes needed
client = anthropic.Anthropic(
    base_url="http://localhost:8766",
    api_key=os.environ["ANTHROPIC_API_KEY"],
)

print("Sending request via tokenpak...")
response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=64,
    messages=[{"role": "user", "content": "Say hello in one sentence."}],
)

print(f"Response: {response.content[0].text}")
PYEOF

# Step 2: Show the script
echo ""
echo "--- sdk_demo.py ---"
cat /tmp/tokenpak_sdk_demo.py
echo "-------------------"
sleep 1

# Step 3: Run it
echo ""
echo "$ python3 /tmp/tokenpak_sdk_demo.py"
python3 /tmp/tokenpak_sdk_demo.py
sleep 1

# Step 4: Show the dashboard telemetry (the OTLP export confirmation)
echo ""
echo "# Proxy telemetry for the last call:"
curl -s http://localhost:8766/dashboard/last | python3 -m json.tool

# Clean up
rm -f /tmp/tokenpak_sdk_demo.py
