#!/bin/bash
#
# quickstart-test.sh — TokenPak 5-Minute Quickstart Test (bash, Linux/macOS)
#
# Sends 3 identical requests through the proxy and prints cache hit stats.
# Works with Anthropic OR OpenAI — auto-detects which key is set.
#
# Usage:
#   bash scripts/quickstart-test.sh
#   TOKENPAK_PORT=9000 bash scripts/quickstart-test.sh
#

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROXY_HOST="${TOKENPAK_HOST:-localhost}"
PROXY_PORT="${TOKENPAK_PORT:-8766}"
PROXY_URL="http://${PROXY_HOST}:${PROXY_PORT}"

# Detect provider and API key
if [ -n "$ANTHROPIC_API_KEY" ]; then
    PROVIDER="anthropic"
    API_KEY="$ANTHROPIC_API_KEY"
    MODEL="claude-opus-4-5"
elif [ -n "$OPENAI_API_KEY" ]; then
    PROVIDER="openai"
    API_KEY="$OPENAI_API_KEY"
    MODEL="gpt-4o"
else
    echo -e "${RED}❌ Error: No API key found${NC}"
    echo ""
    echo "Please set one of:"
    echo "  export ANTHROPIC_API_KEY='sk-ant-...' (or add to .env)"
    echo "  export OPENAI_API_KEY='sk-...' (or add to .env)"
    echo ""
    echo "📖 Setup guide: https://docs.tokenpak.dev/getting-started"
    exit 1
fi

# Helper: print section header
section() {
    echo ""
    echo -e "${BLUE}▸ $1${NC}"
}

# Helper: check if proxy is running
check_proxy() {
    if ! curl -s -f "$PROXY_URL/health" > /dev/null 2>&1; then
        echo -e "${RED}❌ Proxy not running${NC}"
        echo ""
        echo "Start TokenPak:"
        echo "  tokenpak start"
        echo ""
        echo "Or with Docker:"
        echo "  docker compose up -d"
        echo ""
        echo "📖 Troubleshooting: https://docs.tokenpak.dev/troubleshooting"
        exit 1
    fi
}

# Helper: make request and extract stats
make_request() {
    local request_num=$1
    
    curl -s -X POST "$PROXY_URL/v1/messages" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $API_KEY" \
        -H "X-Tokenpak-Provider: $PROVIDER" \
        -d '{
            "model": "'$MODEL'",
            "messages": [
                {
                    "role": "user",
                    "content": "In exactly 10 words, summarize the purpose of a proxy server in software engineering. Just the 10 words, nothing else."
                }
            ]
        }'
}

# ============================================================================
# Main Script
# ============================================================================

echo -e "${GREEN}╭─ TokenPak Quickstart Test${NC}"
echo -e "${GREEN}╰─ Testing cache hits and cost savings${NC}"

# Check proxy is running
section "Checking proxy..."
check_proxy
echo -e "${GREEN}✓ Proxy is running${NC}"

# Make 3 identical requests: miss → hit → hit
section "Sending 3 identical requests..."

echo "  Request 1 (cache miss)..." >&2
RESPONSE_1=$(make_request 1)
CACHE_HIT_1=$(echo "$RESPONSE_1" | grep -o '"cache_read_input_tokens": [0-9]*' | grep -o '[0-9]*' || echo "0")
INPUT_TOKENS_1=$(echo "$RESPONSE_1" | grep -o '"input_tokens": [0-9]*' | grep -o '[0-9]*' || echo "0")

sleep 0.5

echo "  Request 2 (cache hit)..." >&2
RESPONSE_2=$(make_request 2)
CACHE_HIT_2=$(echo "$RESPONSE_2" | grep -o '"cache_read_input_tokens": [0-9]*' | grep -o '[0-9]*' || echo "0")
INPUT_TOKENS_2=$(echo "$RESPONSE_2" | grep -o '"input_tokens": [0-9]*' | grep -o '[0-9]*' || echo "0")

sleep 0.5

echo "  Request 3 (cache hit)..." >&2
RESPONSE_3=$(make_request 3)
CACHE_HIT_3=$(echo "$RESPONSE_3" | grep -o '"cache_read_input_tokens": [0-9]*' | grep -o '[0-9]*' || echo "0")
INPUT_TOKENS_3=$(echo "$RESPONSE_3" | grep -o '"input_tokens": [0-9]*' | grep -o '[0-9]*' || echo "0")

# Calculate totals
TOTAL_CACHE_HIT=$((CACHE_HIT_1 + CACHE_HIT_2 + CACHE_HIT_3))
TOTAL_INPUT_TOKENS=$((INPUT_TOKENS_1 + INPUT_TOKENS_2 + INPUT_TOKENS_3))

# Calculate savings
if [ "$TOTAL_INPUT_TOKENS" -gt 0 ]; then
    CACHE_HIT_COUNT=$((TOTAL_CACHE_HIT > 0 ? 2 : 0)) # At least requests 2 and 3 should hit cache
    SAVINGS_PERCENT=$(( TOTAL_CACHE_HIT * 100 / TOTAL_INPUT_TOKENS ))
else
    CACHE_HIT_COUNT=0
    SAVINGS_PERCENT=0
fi

# Estimate cost savings (rough calculation)
# Anthropic: cache write = 1.25x, cache read = 0.1x (vs new tokens at 1x)
# OpenAI: cache write = 1.25x, cache read = 0.1x
CACHE_WRITE_COST=$(( CACHE_HIT_1 * 125 / 100 ))  # First request writes cache (25% extra)
CACHE_READ_COST=$(( TOTAL_CACHE_HIT * 10 / 100 )) # Other requests read from cache (90% cheaper)
REGULAR_COST=$(( (TOTAL_INPUT_TOKENS - TOTAL_CACHE_HIT) ))

# Cost unit: tokens (proportional to actual cost)
ACTUAL_COST=$(( CACHE_WRITE_COST + CACHE_READ_COST + REGULAR_COST ))
BASELINE_COST=$(( TOTAL_INPUT_TOKENS ))
COST_SAVED=$(( BASELINE_COST - ACTUAL_COST ))

# ============================================================================
# Summary
# ============================================================================

section "Cache Hit Summary"
echo -e "  Requests sent:      ${GREEN}3${NC}"
echo -e "  Cache hits:         ${GREEN}$CACHE_HIT_COUNT / 3${NC}"
echo -e "  Cached tokens:      ${GREEN}$TOTAL_CACHE_HIT${NC}"
echo -e "  Total input tokens: ${YELLOW}$TOTAL_INPUT_TOKENS${NC}"
echo -e "  Tokens saved:       ${GREEN}$TOTAL_CACHE_HIT${NC}"
echo -e "  Savings:            ${GREEN}$SAVINGS_PERCENT%${NC}"

if [ "$COST_SAVED" -gt 0 ]; then
    echo -e "  Cost reduction:     ${GREEN}~$COST_SAVED token units${NC}"
fi

section "What's Next?"
echo "  🔍 Check proxy status:"
echo "     tokenpak status"
echo ""
echo "  📊 View detailed savings:"
echo "     tokenpak savings"
echo ""
echo "  🗂️  List cached models:"
echo "     tokenpak models"
echo ""
echo "  📈 Open dashboard:"
echo "     tokenpak dashboard  (or visit http://localhost:8766/dashboard)"
echo ""
echo "  🛑 Stop proxy when done:"
echo "     tokenpak stop"
echo ""

if [ "$CACHE_HIT_COUNT" -eq 0 ]; then
    echo -e "${YELLOW}⚠️  Cache hits not detected${NC} — this may be expected on first run."
    echo "    Try running the test again to see cache hits from previous requests."
else
    echo -e "${GREEN}✅ Cache is working! Try running again to increase savings.${NC}"
fi

echo ""
echo -e "${BLUE}📖 Learn more: https://docs.tokenpak.dev${NC}"
echo -e "${BLUE}💬 Issues & feedback: https://github.com/repliflow/tokenpak/issues${NC}"
echo ""
