#!/bin/bash
# Memory Promoter Cron Job
# 
# Runs the TokenPak memory promoter to process lesson tier progression.
# Invoked every 30 minutes by OpenClaw cron.
# 
# Logs to: ~/.tokenpak/logs/memory-promoter.log

set -e

# Ensure log directory exists
mkdir -p ~/.tokenpak/logs

# Log file
LOG_FILE="$HOME/.tokenpak/logs/memory-promoter.log"

# Timestamp
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Python entry point: invoke MemoryPromoter.cleanup_expired()
python3 << 'EOF' >> "$LOG_FILE" 2>&1
import logging
from datetime import datetime
from pathlib import Path

# Configure logging
log_file = Path.home() / ".tokenpak" / "logs" / "memory-promoter.log"
logging.basicConfig(
    filename=str(log_file),
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

try:
    from tokenpak.orchestration.memory_promoter import MemoryPromoter
    
    promoter = MemoryPromoter()
    affected = promoter.cleanup_expired()
    stats = promoter.stats()
    
    logger.info(f"Memory promotion cycle completed. Affected: {affected} lessons")
    logger.info(f"Memory stats: {stats['total_lessons']} total lessons, by tier: {stats['by_tier']}")
    
except Exception as e:
    logger.error(f"Memory promotion failed: {e}", exc_info=True)
    exit(1)
EOF

# Exit with appropriate code
if [ $? -eq 0 ]; then
    # Also log to stdout for monitoring
    echo "[$TIMESTAMP] Memory promoter ran successfully. Check $LOG_FILE for details."
    exit 0
else
    echo "[$TIMESTAMP] Memory promoter failed. Check $LOG_FILE for details." >&2
    exit 1
fi
