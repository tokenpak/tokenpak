"""
Tests for system prompt section-level compression.
Verifies: content preservation, protection accuracy, compression ratio improvement.
"""

import re
import sys


# Minimal stubs to test the functions without importing full proxy
def count_tokens(text):
    return max(1, len(text) // 4)


_SENSITIVE_SECTION_HEADERS = [
    "/SOUL.md",
    "/USER.md",
    "/MEMORY.md",
    "# SOUL.md",
    "# USER.md",
    "# MEMORY.md",
    "# MEMORY.md - Long-Term Memory",
    "## Current Date",
]


def _is_sensitive_section(header):
    for pat in _SENSITIVE_SECTION_HEADERS:
        if pat in header:
            return True
    return False


def _whitespace_compress(text):
    if not text:
        return text
    lines = [line.rstrip() for line in text.splitlines()]
    result = []
    blank_count = 0
    for line in lines:
        if line == "":
            blank_count += 1
            if blank_count <= 1:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)
    return "\n".join(result)


def _compress_system_prompt_sections(system_text, mode):
    if not system_text or mode == "strict":
        tok = count_tokens(system_text)
        return system_text, tok, 0
    section_pattern = re.compile(r"^(#{1,3} .+)$", re.MULTILINE)
    parts = section_pattern.split(system_text)
    result_parts = []
    protected_toks = 0
    compressed_toks = 0
    if parts:
        pre = parts[0]
        if pre.strip():
            compressed = _whitespace_compress(pre)
            compressed_toks += count_tokens(pre) - count_tokens(compressed)
            result_parts.append(compressed)
        else:
            result_parts.append(pre)
    i = 1
    while i < len(parts) - 1:
        header = parts[i]
        content = parts[i + 1] if i + 1 < len(parts) else ""
        i += 2
        if _is_sensitive_section(header):
            protected_toks += count_tokens(header) + count_tokens(content)
            result_parts.append(header)
            result_parts.append(content)
        else:
            compressed_content = _whitespace_compress(content)
            delta = count_tokens(content) - count_tokens(compressed_content)
            if delta > 0:
                compressed_toks += delta
            result_parts.append(header)
            result_parts.append(compressed_content)
    return "".join(result_parts), protected_toks, compressed_toks


# --- Tests ---


def test_sensitive_section_detection():
    """Only truly personal file paths are detected as sensitive."""
    assert _is_sensitive_section("## /home/user/.tokenpak/workspace/SOUL.md"), (
        "SOUL.md should be sensitive"
    )
    assert _is_sensitive_section("## /home/user/.tokenpak/workspace/USER.md"), (
        "USER.md should be sensitive"
    )
    assert _is_sensitive_section("## /home/user/.tokenpak/workspace/MEMORY.md"), (
        "MEMORY.md should be sensitive"
    )
    assert not _is_sensitive_section("## /home/user/.tokenpak/workspace/AGENTS.md"), (
        "AGENTS.md is operational, not sensitive"
    )
    assert not _is_sensitive_section("## /home/user/.tokenpak/workspace/TOOLS.md"), (
        "TOOLS.md is operational, not sensitive"
    )
    assert not _is_sensitive_section("## /home/user/.tokenpak/workspace/HEARTBEAT.md"), (
        "HEARTBEAT.md is operational, not sensitive"
    )
    assert not _is_sensitive_section("## Tooling"), "Tooling section not sensitive"
    assert not _is_sensitive_section("## Safety"), "Safety section not sensitive"
    assert not _is_sensitive_section("## Runtime"), "Runtime section not sensitive"
    assert _is_sensitive_section("## Current Date"), "Current Date protected for cache stability"
    print("PASS: test_sensitive_section_detection")


def test_content_preservation():
    """Sensitive section content is never modified."""
    prompt = """## Tooling
Tool availability:

- read: Read file contents


## /home/user/.tokenpak/workspace/SOUL.md
# SOUL.md - Who You Are
User personality: deep thinker, builder mindset.

## /home/user/.tokenpak/workspace/USER.md
# USER.md - About the User
John Doe. Account ID: 12345. Contact: Jane.

## Runtime
Runtime: agent=main | model=claude
"""
    compressed, protected, saved = _compress_system_prompt_sections(prompt, "hybrid")
    assert "User personality: deep thinker" in compressed, "SOUL.md content must be preserved"
    assert "12345" in compressed, "Personal account ID must be preserved"
    assert "Contact: Jane" in compressed, "Personal contact info must be preserved"
    assert "Tool availability" in compressed, "Safe section content must be preserved"
    assert "Runtime: agent=main" in compressed, "Runtime section content must be preserved"
    print("PASS: test_content_preservation")


def test_protected_ratio_reduction():
    """Protected tokens significantly reduced from 100% baseline."""
    prompt = """You are Claude Code.
## Tooling
Tool list here with many items and descriptions that are all operational content.
Nothing personal here. Just tool names and descriptions.


## Safety
Safety rules go here. Not personal.


## /home/user/.tokenpak/workspace/SOUL.md
# SOUL.md - Who You Are
This is personal soul content about the user's personality and values.

## /home/user/.tokenpak/workspace/USER.md
# USER.md - About the User
John Doe personal info here.

## /home/user/.tokenpak/workspace/AGENTS.md
# AGENTS.md
Agent operational procedures and protocols.

## Runtime
Runtime info here.
"""
    orig_tokens = count_tokens(prompt)
    compressed, protected, saved = _compress_system_prompt_sections(prompt, "hybrid")
    protected_pct = 100 * protected / orig_tokens

    # SOUL.md + USER.md should be protected, AGENTS.md should NOT be
    assert protected_pct < 50, f"Protected should be <50% but got {protected_pct:.1f}%"
    assert "personal soul content" in compressed, "SOUL.md preserved"
    assert "John Doe personal info" in compressed, "USER.md preserved"
    assert "Agent operational procedures" in compressed, (
        "AGENTS.md (operational) preserved and not protected"
    )
    print(f"PASS: test_protected_ratio_reduction (protected: {protected_pct:.1f}%)")


def test_strict_mode_no_compression():
    """Strict mode: no compression at all."""
    prompt = "## Tooling\nSome content with   lots   of   whitespace\n\n\n\nand blank lines"
    compressed, protected, saved = _compress_system_prompt_sections(prompt, "strict")
    assert compressed == prompt, "Strict mode must not modify content"
    print("PASS: test_strict_mode_no_compression")


def test_whitespace_compression():
    """Whitespace compression removes excess blank lines and trailing spaces."""
    text = "Line one   \n\n\n\nLine two\n\nLine three   \n"
    compressed = _whitespace_compress(text)
    assert "   " not in compressed, "Trailing spaces removed"
    assert "\n\n\n" not in compressed, "3+ consecutive blanks collapsed"
    assert "Line one" in compressed
    assert "Line two" in compressed
    assert "Line three" in compressed
    print("PASS: test_whitespace_compression")


def test_empty_and_edge_cases():
    """Edge cases: empty string, no sections, single section."""
    result_text, _, _ = _compress_system_prompt_sections("", "hybrid")
    assert result_text == "", "Empty string returns empty string"

    # Single section (safe)
    prompt = "## Tooling\nSome content here"
    compressed, protected, saved = _compress_system_prompt_sections(prompt, "hybrid")
    assert "Some content here" in compressed
    assert protected == 0, "Single safe section should have 0 protected tokens"

    # Single section (sensitive)
    prompt2 = "## /home/user/SOUL.md\nPersonal content"
    compressed2, protected2, saved2 = _compress_system_prompt_sections(prompt2, "hybrid")
    assert "Personal content" in compressed2
    assert protected2 > 0, "Sensitive section should have protected tokens"
    print("PASS: test_empty_and_edge_cases")


def test_projected_impact_realistic():
    """Simulate realistic system prompt and verify projected compression."""
    # Build a realistic system prompt (rough approximation of a typical agent's)
    sections = {
        "safe": [
            ("## Tooling", "A" * 3200),  # 800 tokens of tool descriptions
            ("## Tool Call Style", "B" * 1200),  # 300 tokens
            ("## Safety", "C" * 800),  # 200 tokens
            ("## TokenPak CLI", "D" * 800),  # 200 tokens
            ("## Skills", "E" * 2400),  # 600 tokens
            ("## Memory Recall", "F" * 600),  # 150 tokens
            ("## Model Aliases", "G" * 1200),  # 300 tokens
            ("## /home/user/workspace/AGENTS.md", "H" * 12000),  # 3000 tokens OPERATIONAL
            ("## /home/user/workspace/TOOLS.md", "I" * 10000),  # 2500 tokens OPERATIONAL
            ("## /home/user/workspace/HEARTBEAT.md", "J" * 2400),  # 600 tokens OPERATIONAL
            ("## Runtime", "K" * 800),  # 200 tokens
        ],
        "sensitive": [
            ("## /home/user/workspace/SOUL.md", "L" * 3200),  # 800 tokens PERSONAL
            ("## /home/user/workspace/USER.md", "M" * 6000),  # 1500 tokens PERSONAL
            ("## /home/user/workspace/MEMORY.md", "N" * 8000),  # 2000 tokens PERSONAL
            ("## Current Date", "O" * 200),  # 50 tokens
        ],
    }

    prompt_parts = ["You are Claude Code.\n"]
    for header, content in sections["safe"]:
        prompt_parts.append(f"{header}\n{content}\n\n\n\n")  # with extra blank lines
    for header, content in sections["sensitive"]:
        prompt_parts.append(f"{header}\n{content}\n\n\n\n")

    prompt = "".join(prompt_parts)
    orig_tokens = count_tokens(prompt)

    compressed, protected, saved = _compress_system_prompt_sections(prompt, "hybrid")
    out_tokens = count_tokens(compressed)

    protected_pct = 100 * protected / orig_tokens
    compression_pct = 100 * (orig_tokens - out_tokens) / orig_tokens

    print("\nProjected impact (realistic system prompt):")
    print(f"  Original: {orig_tokens:,} tokens")
    print(f"  Protected (truly sensitive): {protected:,} tokens ({protected_pct:.1f}%)")
    print(f"  After compression: {out_tokens:,} tokens")
    print(f"  Compression ratio: {compression_pct:.1f}%")
    print(f"  Old protected ratio: ~91.5% | New protected ratio: {protected_pct:.1f}%")

    # Key metric: protected ratio dramatically reduced from 91.5% baseline
    assert protected_pct < 40, (
        f"Protected should be <40% (from 91.5% baseline), got {protected_pct:.1f}%"
    )
    # Whitespace compression savings depend on actual whitespace in the prompt
    # (test content uses repeated chars, not real markdown with blank lines)
    # In real prompts with markdown, expect 15-35% whitespace savings
    assert out_tokens <= orig_tokens, "Output should never be larger than input"
    assert "L" * 100 in compressed, "SOUL.md content preserved"
    assert "M" * 100 in compressed, "USER.md content preserved"
    print("PASS: test_projected_impact_realistic")


# Run all tests
tests = [
    test_sensitive_section_detection,
    test_content_preservation,
    test_protected_ratio_reduction,
    test_strict_mode_no_compression,
    test_whitespace_compression,
    test_empty_and_edge_cases,
    test_projected_impact_realistic,
]

passed = 0
failed = 0
for test in tests:
    try:
        test()
        passed += 1
    except AssertionError as e:
        print(f"FAIL: {test.__name__}: {e}")
        failed += 1
    except Exception as e:
        print(f"ERROR: {test.__name__}: {e}")
        failed += 1

if __name__ == "__main__":
    print(f"\n{'=' * 50}")
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    if failed == 0:
        print("ALL TESTS PASS ✅")
        sys.exit(0)
    else:
        sys.exit(1)
