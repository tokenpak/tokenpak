#!/bin/bash
# merge-resolve.sh — Automated conflict resolution for TokenPak repo divergence

cd ~/tokenpak || exit 1

echo "=== TokenPak Merge Conflict Resolution ==="
echo ""

# 1. Delete rate_limit files (Trix's deletion takes priority — production fix)
echo "Resolving rate_limit conflicts (keeping Trix deletion)..."
git rm tests/test_rate_limit_backoff.py 2>/dev/null || true
git rm tokenpak/handlers/rate_limit.py 2>/dev/null || true
echo "✅ Deleted rate_limit files"

# 2. Use local version for binary files (SQLite)
echo "Using local version for SQLite files..."
git checkout HEAD -- telemetry.db-shm telemetry.db-wal 2>/dev/null || true
echo "✅ SQLite files resolved"

# 3. For test_tool_schema_registry.py — keep Cali version (--ours)
echo "Resolving test_tool_schema_registry.py..."
if git status | grep -q "test_tool_schema_registry.py"; then
    git checkout --ours tests/test_tool_schema_registry.py 2>/dev/null || true
    git add tests/test_tool_schema_registry.py
    echo "✅ Kept Cali version (22 tests)"
fi

# 4. For proxy/server.py — keep Cali version (--ours)
echo "Resolving tokenpak/agent/proxy/server.py..."
if git status | grep -q "tokenpak/agent/proxy/server.py"; then
    git checkout --ours tokenpak/agent/proxy/server.py 2>/dev/null || true
    git add tokenpak/agent/proxy/server.py
    echo "✅ Kept Cali version (with async improvements)"
fi

# 5-6. Manual conflicts require editing
echo ""
echo "⚠️  MANUAL STEPS REQUIRED:"
echo ""
echo "1. TEST_AUDIT.md (if in conflict)"
echo "   - Keep both Trix and Cali entries"
echo "   - Then: git add TEST_AUDIT.md"
echo ""
echo "2. integration/query_briefing.py (if in conflict)"
echo "   - Keep both JSONL fallback (Cali) + heartbeat_ingest (Trix)"
echo "   - Then: git add integration/query_briefing.py"
echo ""

echo "=== Merge Status ==="
git status

echo ""
echo "=== Next Steps ==="
echo "1. Manually resolve files listed above"
echo "2. Run tests: pytest -q --timeout=10"
echo "3. Commit merge:"
echo "   git add -A"
echo "   git commit -m 'merge: resolve SueBot/origin divergence — keep rate_limit revert, integrate type hints'"
echo "4. Push: git push origin master"
