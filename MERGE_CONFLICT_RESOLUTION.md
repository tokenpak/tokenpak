# TokenPak Repo: Merge Conflict Resolution Guide

## Problem Summary

The `~/tokenpak` repository on SueBot has diverged from `origin/master`:

- **SueBot HEAD:** 9 commits ahead (Trix's rate_limit revert + health/test fixes)
- **origin/master:** 40+ commits ahead (Cali's type hints, tokenpak-local fix, phase3 work)

## Conflict Analysis

### Files with Conflicts

1. **test_rate_limit_backoff.py** ⚠️ CRITICAL
   - Trix deleted (rate_limit handler was causing 70s stalls on 429 responses)
   - Cali modified (added tests)
   - **Resolution: USE TRIX VERSION (delete the file)**
   - This is a production bug fix

2. **tokenpak/handlers/rate_limit.py** ⚠️ CRITICAL
   - Trix deleted (same reason as above)
   - Cali modified (added handler logic)
   - **Resolution: USE TRIX VERSION (delete the file)**
   - This is a production bug fix

3. **TEST_AUDIT.md**
   - Add/add conflict (both sides added content)
   - **Resolution: MERGE BOTH** (Trix's entries + Cali's entries)
   - Use Trix's entries at top, Cali's below

4. **integration/query_briefing.py**
   - Content conflict (JSONL vs heartbeat_ingest)
   - **Resolution: KEEP BOTH** (Cali's JSONL fallback + Trix's heartbeat_ingest)
   - They are complementary, not mutually exclusive

5. **tests/test_tool_schema_registry.py**
   - Add/add conflict
   - **Resolution: KEEP CALI VERSION** (22 comprehensive tests)
   - Cali's version is more complete

6. **tokenpak/agent/proxy/server.py**
   - Content conflict (async changes)
   - **Resolution: KEEP CALI VERSION + Trix's alias**
   - Integrate Trix's `_async_thread` alias if present

7. **telemetry.db-shm, telemetry.db-wal**
   - Binary conflicts (SQLite temporary files)
   - **Resolution: USE LOCAL** (these are ephemeral)
   - `git checkout HEAD -- <file>`

## Merge Resolution Script

Run this on SueBot after `git merge origin/master` creates conflicts:

```bash
#!/bin/bash
# merge-resolve.sh — Automated conflict resolution for TokenPak repo

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

# 3. For TEST_AUDIT.md — merge both
echo "Merging TEST_AUDIT.md..."
if git status | grep -q "TEST_AUDIT.md"; then
    # Keep both sections: Trix's at top, Cali's below
    # Manual: edit the file and remove conflict markers
    echo "⚠️  MANUAL: Edit TEST_AUDIT.md to merge both Trix and Cali sections"
    echo "   Then: git add TEST_AUDIT.md"
fi

# 4. For integration/query_briefing.py — keep both
echo "Resolving integration/query_briefing.py..."
if git status | grep -q "integration/query_briefing.py"; then
    # Keep Cali's JSONL fallback + Trix's heartbeat_ingest
    echo "⚠️  MANUAL: Edit integration/query_briefing.py"
    echo "   Keep both JSONL fallback (Cali) + heartbeat_ingest (Trix)"
    echo "   Then: git add integration/query_briefing.py"
fi

# 5. For test_tool_schema_registry.py — keep Cali version
echo "Resolving test_tool_schema_registry.py..."
if git status | grep -q "test_tool_schema_registry.py"; then
    git checkout --ours tests/test_tool_schema_registry.py
    git add tests/test_tool_schema_registry.py
    echo "✅ Kept Cali version (22 tests)"
fi

# 6. For proxy/server.py — keep Cali version
echo "Resolving tokenpak/agent/proxy/server.py..."
if git status | grep -q "tokenpak/agent/proxy/server.py"; then
    git checkout --ours tokenpak/agent/proxy/server.py
    # TODO: If Trix added _async_thread alias, manually add it
    git add tokenpak/agent/proxy/server.py
    echo "✅ Kept Cali version (with async improvements)"
fi

echo ""
echo "=== Merge Status ==="
git status

echo ""
echo "=== Next Steps ==="
echo "1. Verify all conflicts are resolved: git status"
echo "2. Run tests: pytest -q --timeout=10"
echo "3. Commit merge: git commit -m 'merge: resolve SueBot/origin divergence'"
echo "4. Push: git push origin master"
```

## Manual Resolution Steps

If running the script above, then for manual conflicts:

### TEST_AUDIT.md
```bash
# Open the file and resolve conflict markers:
# Keep both sections:
<<<<<<< HEAD
[Trix's entries]
=======
[Cali's entries]
>>>>>>> origin/master

# Becomes:
[Trix's entries]
[Cali's entries]

# Save and:
git add TEST_AUDIT.md
```

### integration/query_briefing.py
```bash
# Open file and resolve conflict markers:
# Keep both functions/sections
# The JSONL fallback (Cali) and heartbeat_ingest (Trix) should coexist

# Save and:
git add integration/query_briefing.py
```

## Verification

After resolving all conflicts:

```bash
# 1. Check no conflicts remain
git status
# Should show "nothing to commit, working tree clean" or only staged files

# 2. Run tests
cd ~/tokenpak
pytest -q --timeout=10 2>&1 | tail -5

# 3. Verify critical files deleted
test ! -f tests/test_rate_limit_backoff.py && echo "✅ test_rate_limit_backoff.py deleted"
test ! -f tokenpak/handlers/rate_limit.py && echo "✅ rate_limit.py deleted"

# 4. View commit log
git log --oneline -5

# 5. Commit and push
git add -A
git commit -m "merge: resolve SueBot/origin divergence — keep rate_limit revert, integrate type hints"
git push origin master
```

## Why This Approach

1. **Rate limit deletion is priority** — Trix's fix (removing the stalling handler) is critical for production
2. **Type hints integration** — Cali's latest work (type hints phase 3) should be merged
3. **Test completeness** — Cali's test suite is more comprehensive where conflicts exist
4. **Compatibility** — Conflicting code sections were from different phases; Cali's later work supersedes

## Success Criteria

- ✅ No merge conflicts in `git status`
- ✅ `tests/test_rate_limit_backoff.py` DELETED
- ✅ `tokenpak/handlers/rate_limit.py` DELETED
- ✅ Test suite passes (< 5 new failures acceptable)
- ✅ `git log --oneline -1` shows merge commit
- ✅ Pushed to `origin/master`

## Troubleshooting

If merge gets stuck:

```bash
# Abort and start over
git merge --abort

# Fetch fresh
git fetch origin

# Try again
git merge origin/master
```

If tests fail after merge:

```bash
# Check which tests failed
pytest -v 2>&1 | grep FAILED

# If failures are unrelated to merge (e.g., pre-existing):
# Document them and proceed with commit
```

---

*This guide is for manual execution on SueBot to resolve the divergence.*
