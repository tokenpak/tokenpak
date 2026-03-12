#!/bin/bash
# TokenPak Phase 3 Stage 1 — Baseline Deployment Script
# Usage: bash deploy-stage1-baseline.sh [sue|trixbot|cali|all]
# Run as: sue@sue-machine, trix@trixbot, cali@cali-machine

set -e

MACHINE=${1:-"all"}
PROXY_FILE="proxy_v4.py"
BASELINE_CHECKPOINT="proxy_v4-checkpoint-phase3-baseline.py"
LOGDIR="logs"
PORT=8766

# Environment: All toggles OFF (baseline)
export TOKENPAK_SEMANTIC_CACHE=0
export TOKENPAK_TRACE=0
export TOKENPAK_REQUEST_LOGGER=0

echo "🚀 TokenPak Phase 3 Stage 1 Baseline Deployment"
echo "=================================================="

# Create logs directory
mkdir -p "$LOGDIR"

# Function: Deploy on current machine
deploy_local() {
    echo ""
    echo "📝 Pre-deployment checks..."
    
    # Verify proxy_v4.py exists
    if [ ! -f "$PROXY_FILE" ]; then
        echo "❌ ERROR: $PROXY_FILE not found in $(pwd)"
        exit 1
    fi
    
    # Verify checkpoint
    if [ ! -f "$BASELINE_CHECKPOINT" ]; then
        echo "⚠️  WARNING: $BASELINE_CHECKPOINT not found, creating..."
        cp "$PROXY_FILE" "$BASELINE_CHECKPOINT"
    fi
    
    # Verify Python syntax
    echo "   Checking Python syntax..."
    python3 -m py_compile "$PROXY_FILE" || { echo "❌ Syntax error in $PROXY_FILE"; exit 1; }
    
    # Stop existing proxy
    echo "   Stopping existing proxy..."
    pkill -f "python.*proxy_v4" || true
    sleep 2
    
    # Remove stale PID files
    rm -f /tmp/proxy_v4.pid 2>/dev/null || true
    
    # Start proxy
    echo "   Starting proxy_v4.py..."
    nohup python3 "$PROXY_FILE" > "$LOGDIR/proxy.log" 2>&1 &
    PROXY_PID=$!
    echo "   Proxy started (PID: $PROXY_PID)"
    
    # Wait for startup
    sleep 3
    
    # Verify health
    echo ""
    echo "✅ Baseline deployment complete!"
    echo "   Logs: tail -f $LOGDIR/proxy.log"
    echo ""
    
    # Collect initial baseline snapshot
    if [ -f "baseline-week1-collection-2026-03-11-to-2026-03-18.py" ]; then
        echo "📊 Collecting initial baseline snapshot..."
        python3 baseline-week1-collection-2026-03-11-to-2026-03-18.py > "$LOGDIR/baseline-$(date +%Y-%m-%d-%H%M%S).json" 2>&1
        echo "   Baseline snapshot saved"
    fi
}

# Main
case "$MACHINE" in
    sue)
        echo "🖥️  Deploying to Sue's machine..."
        deploy_local
        ;;
    trixbot)
        echo "🖥️  Deploying to TrixBot..."
        deploy_local
        ;;
    cali)
        echo "🖥️  Deploying to Cali's machine..."
        deploy_local
        ;;
    all)
        echo "🌍 Deploying to ALL machines..."
        echo ""
        echo "⚠️  NOTE: This script must be run on each machine individually:"
        echo "   1. bash deploy-stage1-baseline.sh sue"
        echo "   2. ssh trix@trixbot 'cd ~/Projects/tokenpak && bash deploy-stage1-baseline.sh trixbot'"
        echo "   3. ssh cali@cali 'cd ~/Projects/tokenpak && bash deploy-stage1-baseline.sh cali'"
        echo ""
        echo "   Or run this script locally and it will deploy locally."
        deploy_local
        ;;
    *)
        echo "Usage: bash deploy-stage1-baseline.sh [sue|trixbot|cali|all]"
        exit 1
        ;;
esac

echo ""
echo "ℹ️  Next: Monitor baseline metrics for 7 days (2026-03-11 to 2026-03-18)"
echo "         Run daily: python3 baseline-week1-collection-2026-03-11-to-2026-03-18.py"
