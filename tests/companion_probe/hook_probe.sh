#!/usr/bin/env bash
# -------------------------------------------------------------------
# Companion validation probe — UserPromptSubmit hook
#
# Tests:
#   1. What data does the hook receive on stdin?
#   2. Is transcript_path present and readable mid-session?
#   3. Does stderr output appear in the TUI?
#   4. Does exit code 2 block the send?
#
# Usage: configured as UserPromptSubmit hook in settings.json
# Output: logs everything to /tmp/tp-companion-probe.log
# -------------------------------------------------------------------

LOG="/tmp/tp-companion-probe.log"
STDIN_DUMP="/tmp/tp-companion-probe-stdin.json"

# Read all of stdin (hook input is JSON)
INPUT=$(cat)

# Log raw input
echo "=== $(date -Iseconds) ===" >> "$LOG"
echo "HOOK: UserPromptSubmit fired" >> "$LOG"
echo "RAW_INPUT:" >> "$LOG"
echo "$INPUT" >> "$LOG"

# Save full stdin for inspection
echo "$INPUT" > "$STDIN_DUMP"

# Parse key fields (best-effort, no jq dependency assumed)
TRANSCRIPT_PATH=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('transcript_path','MISSING'))" 2>/dev/null)
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('session_id','MISSING'))" 2>/dev/null)
HOOK_EVENT=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('hook_event_name','MISSING'))" 2>/dev/null)

echo "PARSED:" >> "$LOG"
echo "  session_id:      $SESSION_ID" >> "$LOG"
echo "  hook_event_name: $HOOK_EVENT" >> "$LOG"
echo "  transcript_path: $TRANSCRIPT_PATH" >> "$LOG"

# Test 2: Can we read the transcript mid-session?
if [ "$TRANSCRIPT_PATH" != "MISSING" ] && [ -f "$TRANSCRIPT_PATH" ]; then
    LINE_COUNT=$(wc -l < "$TRANSCRIPT_PATH")
    BYTE_COUNT=$(wc -c < "$TRANSCRIPT_PATH")
    echo "  transcript readable: YES ($LINE_COUNT lines, $BYTE_COUNT bytes)" >> "$LOG"

    # Try to parse it and estimate tokens
    TOKEN_EST=$(python3 -c "
import json, sys
total_chars = 0
msg_count = 0
try:
    with open('$TRANSCRIPT_PATH') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                msg_count += 1
                # Rough char count as proxy for tokens
                total_chars += len(json.dumps(obj))
            except json.JSONDecodeError:
                pass
    # Rough estimate: 1 token ~ 4 chars for English
    est_tokens = total_chars // 4
    print(f'{msg_count} messages, ~{est_tokens} est tokens')
except Exception as e:
    print(f'parse error: {e}')
" 2>/dev/null)
    echo "  transcript parse: $TOKEN_EST" >> "$LOG"
else
    echo "  transcript readable: NO (path=$TRANSCRIPT_PATH)" >> "$LOG"
fi

# Test 3: Write to stderr — does it show in TUI?
echo "  [tokenpak probe] hook fired, session=$SESSION_ID" >&2

# Test 4: Budget gate test — check for magic phrase to test blocking
BLOCK_TEST=$(echo "$INPUT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
# Check if user typed 'BLOCK_TEST' — we'll exit 2 to test blocking
msg = d.get('message', d.get('content', ''))
if isinstance(msg, dict):
    msg = json.dumps(msg)
print('YES' if 'BLOCK_TEST' in str(msg).upper() else 'NO')
" 2>/dev/null)

if [ "$BLOCK_TEST" = "YES" ]; then
    echo "  BLOCK TEST: exit 2 (should prevent send)" >> "$LOG"
    echo '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","decision":"block","reason":"tokenpak companion budget gate test"}}'
    exit 2
fi

echo "  exit 0 (allow send)" >> "$LOG"
echo "" >> "$LOG"

# Normal exit — allow the prompt through
exit 0
