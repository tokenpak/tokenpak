#!/usr/bin/env python3
"""Companion stress test — benchmark subsystems at realistic context sizes.

Tests the companion's performance at 10k, 50k, 100k, and 200k token
transcript sizes to identify latency cliffs and memory issues.

Usage:
    python3 tests/companion_benchmark/stress_test.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Ensure tokenpak is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ["TOKENPAK_NO_THREADS"] = "1"

SIZES = [
    ("10k", 10_000),
    ("50k", 50_000),
    ("100k", 100_000),
    ("200k", 200_000),
]


def generate_synthetic_transcript(target_tokens: int, path: Path) -> int:
    """Generate a realistic Claude Code transcript JSONL at target token size.

    Returns actual char count.
    """
    lines = []
    chars = 0
    turn = 0
    target_chars = target_tokens * 4  # ~4 chars per token

    # System attachment (mimics CLAUDE.md injection)
    system_block = {
        "type": "attachment",
        "content": "You are a helpful assistant. " * 50,
        "timestamp": "2026-04-14T00:00:00Z",
    }
    lines.append(json.dumps(system_block))
    chars += len(lines[-1])

    # Queue operation
    lines.append(json.dumps({
        "type": "queue-operation",
        "operation": "enqueue",
        "timestamp": "2026-04-14T00:00:01Z",
        "sessionId": "stress-test-session",
        "content": "Start the stress test task",
    }))
    chars += len(lines[-1])

    while chars < target_chars:
        turn += 1

        # User message
        user_msg = {
            "type": "user",
            "role": "user",
            "content": f"Turn {turn}: Please analyze the authentication middleware in src/auth/middleware.py and identify any security vulnerabilities. "
                       f"Check for OWASP top 10 issues including SQL injection, XSS, and CSRF. "
                       f"Also review the session token handling at line {turn * 10}. " * (3 + turn % 5),
            "timestamp": f"2026-04-14T00:{turn:02d}:00Z",
        }
        lines.append(json.dumps(user_msg))
        chars += len(lines[-1])

        if chars >= target_chars:
            break

        # Assistant response with tool calls (simulates real Claude Code output)
        tool_output = "def authenticate(request):\n" + "\n".join(
            [f"    # Step {i}: validate {['token', 'session', 'csrf', 'headers', 'origin'][i % 5]}"
             for i in range(20 + turn % 30)]
        )
        assistant_msg = {
            "type": "assistant",
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": f"I'll analyze the middleware. Looking at turn {turn}, I found several issues:\n\n"
                            f"1. The session token is stored in a cookie without the HttpOnly flag\n"
                            f"2. CSRF protection is missing on POST endpoints\n"
                            f"3. The SQL query at line {turn * 10} uses string formatting instead of parameterized queries\n\n"
                            f"Here's the relevant code:\n```python\n{tool_output}\n```\n\n"
                            f"Let me fix these issues. " * (2 + turn % 3),
                }
            ],
            "timestamp": f"2026-04-14T00:{turn:02d}:30Z",
            "model": "claude-sonnet-4-5-20250929",
        }
        lines.append(json.dumps(assistant_msg))
        chars += len(lines[-1])

    path.write_text("\n".join(lines))
    return chars


def bench_transcript_parser(transcript_path: str, label: str) -> dict:
    """Benchmark the transcript parser."""
    from tokenpak.companion.transcript.parser import parse_transcript

    t0 = time.perf_counter()
    summary = parse_transcript(transcript_path)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "test": "transcript_parser",
        "label": label,
        "elapsed_ms": round(elapsed_ms, 1),
        "messages": summary.message_count,
        "tokens_est": summary.tokens_est,
        "chars": summary.total_chars,
        "file_bytes": summary.file_size_bytes,
        "parse_errors": summary.parse_errors,
    }


def bench_token_counter(transcript_path: str, label: str) -> dict:
    """Benchmark tiktoken counting on transcript content."""
    content = Path(transcript_path).read_text()

    # Heuristic
    t0 = time.perf_counter()
    heuristic = len(content) // 4
    heuristic_ms = (time.perf_counter() - t0) * 1000

    # Tiktoken
    try:
        from tokenpak.tokens import count_tokens
        t0 = time.perf_counter()
        # Chunk large texts to avoid LRU cache thrashing
        CHUNK = 100_000
        if len(content) <= CHUNK:
            tiktoken_count = count_tokens(content)
        else:
            tiktoken_count = sum(
                count_tokens(content[i:i + CHUNK])
                for i in range(0, len(content), CHUNK)
            )
        tiktoken_ms = (time.perf_counter() - t0) * 1000
    except Exception as e:
        tiktoken_count = -1
        tiktoken_ms = -1

    return {
        "test": "token_counter",
        "label": label,
        "heuristic_tokens": heuristic,
        "heuristic_ms": round(heuristic_ms, 3),
        "tiktoken_tokens": tiktoken_count,
        "tiktoken_ms": round(tiktoken_ms, 1),
        "accuracy_pct": round(tiktoken_count / max(heuristic, 1) * 100, 1) if tiktoken_count > 0 else -1,
    }


def bench_hook_pipeline(transcript_path: str, label: str) -> dict:
    """Benchmark the hook pre-send pipeline end-to-end.

    Uses the bash hook (pre_send.sh) for production-realistic timing.
    """
    hook_input = json.dumps({
        "session_id": "stress-test",
        "transcript_path": transcript_path,
        "cwd": "/tmp",
        "permission_mode": "default",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "continue analyzing the code",
    })

    hook_script = Path(__file__).parent.parent.parent / "tokenpak" / "companion" / "hooks" / "pre_send.sh"

    t0 = time.perf_counter()
    result = subprocess.run(
        ["bash", str(hook_script)],
        input=hook_input,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(Path(__file__).parent.parent.parent),
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "test": "hook_pipeline",
        "label": label,
        "elapsed_ms": round(elapsed_ms, 1),
        "exit_code": result.returncode,
        "stderr": result.stderr.strip()[:200],
        "stdout": result.stdout.strip()[:200],
    }


def bench_mcp_tool_call(transcript_path: str, label: str) -> dict:
    """Benchmark MCP read_transcript tool on a large file."""
    mcp_input = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "bench", "version": "1.0"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "estimate_tokens",
                                "arguments": {"file_path": transcript_path}}}),
    ])

    t0 = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak.companion.mcp.server"],
        input=mcp_input,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(Path(__file__).parent.parent.parent),
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Parse the tool response
    tool_result = ""
    for line in result.stdout.strip().split("\n"):
        try:
            obj = json.loads(line)
            if obj.get("id") == 2:
                tool_result = obj.get("result", {}).get("content", [{}])[0].get("text", "")
        except (json.JSONDecodeError, IndexError, KeyError):
            pass

    return {
        "test": "mcp_estimate_tokens",
        "label": label,
        "elapsed_ms": round(elapsed_ms, 1),
        "tool_result": tool_result[:300],
    }


def main():
    print("=" * 70)
    print("TOKENPAK COMPANION — STRESS TEST")
    print("=" * 70)
    print()

    results = []

    for label, target_tokens in SIZES:
        print(f"\n{'─' * 50}")
        print(f"  Generating {label} transcript ({target_tokens:,} target tokens)...")

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            transcript_path = f.name

        gen_t0 = time.perf_counter()
        actual_chars = generate_synthetic_transcript(target_tokens, Path(transcript_path))
        gen_ms = (time.perf_counter() - gen_t0) * 1000
        file_size = os.path.getsize(transcript_path)
        print(f"  Generated: {actual_chars:,} chars, {file_size:,} bytes ({gen_ms:.0f}ms)")

        # Run benchmarks
        print(f"\n  Benchmarking {label}...")

        r1 = bench_transcript_parser(transcript_path, label)
        print(f"    parser:     {r1['elapsed_ms']:>8.1f}ms  ({r1['messages']} msgs, ~{r1['tokens_est']:,} tokens)")
        results.append(r1)

        r2 = bench_token_counter(transcript_path, label)
        print(f"    tiktoken:   {r2['tiktoken_ms']:>8.1f}ms  ({r2['tiktoken_tokens']:,} tokens, {r2['accuracy_pct']}% of heuristic)")
        results.append(r2)

        r3 = bench_hook_pipeline(transcript_path, label)
        print(f"    hook e2e:   {r3['elapsed_ms']:>8.1f}ms  (exit={r3['exit_code']}, stderr={r3['stderr'][:80]})")
        results.append(r3)

        r4 = bench_mcp_tool_call(transcript_path, label)
        print(f"    mcp tool:   {r4['elapsed_ms']:>8.1f}ms")
        results.append(r4)

        os.unlink(transcript_path)

    # Summary table
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"\n{'Size':<8} {'Parser':>10} {'Tiktoken':>10} {'Hook E2E':>10} {'MCP Tool':>10}")
    print(f"{'─' * 8} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 10}")
    for label, _ in SIZES:
        parser = next((r for r in results if r["test"] == "transcript_parser" and r["label"] == label), {})
        tiktoken = next((r for r in results if r["test"] == "token_counter" and r["label"] == label), {})
        hook = next((r for r in results if r["test"] == "hook_pipeline" and r["label"] == label), {})
        mcp = next((r for r in results if r["test"] == "mcp_estimate_tokens" and r["label"] == label), {})
        print(f"{label:<8} {parser.get('elapsed_ms', -1):>8.1f}ms {tiktoken.get('tiktoken_ms', -1):>8.1f}ms {hook.get('elapsed_ms', -1):>8.1f}ms {mcp.get('elapsed_ms', -1):>8.1f}ms")

    # Write results JSON
    results_path = Path(__file__).parent / "stress_results.json"
    results_path.write_text(json.dumps(results, indent=2))
    print(f"\nDetailed results: {results_path}")

    # Check for latency issues
    print(f"\n{'=' * 70}")
    print("VERDICT")
    print(f"{'=' * 70}")
    hook_200k = next((r for r in results if r["test"] == "hook_pipeline" and r["label"] == "200k"), {})
    hook_ms = hook_200k.get("elapsed_ms", 0)
    if hook_ms > 1000:
        print(f"  WARNING: Hook pipeline at 200k tokens takes {hook_ms:.0f}ms (> 1s)")
        print("  This will add perceptible delay to every prompt in long sessions.")
    elif hook_ms > 500:
        print(f"  CAUTION: Hook pipeline at 200k tokens takes {hook_ms:.0f}ms (> 500ms)")
        print("  Borderline — may be noticeable on slower machines.")
    else:
        print(f"  OK: Hook pipeline at 200k tokens takes {hook_ms:.0f}ms (< 500ms)")
        print("  Should be imperceptible to users.")


if __name__ == "__main__":
    main()
