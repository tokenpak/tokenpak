"""Determinism tests for TokenPak — same input must always produce identical output.

Enforces 100% deterministic compilation: identical packs produce byte-identical
text output regardless of how many times or in which process they are compiled.

Test coverage:
  1. Basic determinism   — 100 consecutive compiles produce identical output
  2. Hash equality       — SHA-256 of compiled text is stable across runs
  3. Cross-process       — separate Python sub-processes produce identical output
  4. Block order         — blocks with equal priority maintain stable insertion order
  5. Compaction          — truncation (extractive) produces identical results
  6. Tiebreaker          — equal-quality/priority blocks always land in same order

Patterns audited as non-deterministic and confirmed absent from compile path:
  ❌ random.choice / random.shuffle  → not present in compile path
  ❌ datetime.now() in output text   → compile_time_ms is in report only (not text)
  ❌ set() / dict iteration order    → sorted() used everywhere ordering matters
  ✅ SHA-256 slice IDs               → deterministic, input-derived
  ✅ sorted() with explicit key      → deterministic ordering
"""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.pack", reason="module not available in current build")
import hashlib
import json
import subprocess
import sys
import textwrap
import unittest

from tokenpak.pack import ContextPack, PackBlock
from tokenpak.wire import pack as wire_pack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pack(budget: int = 8000) -> ContextPack:
    """Create a fully-populated, reproducible ContextPack for testing."""
    cp = ContextPack(budget=budget)
    cp.add(PackBlock(
        id="sys",
        type="instructions",
        content="You are a helpful assistant. Answer questions concisely.",
        priority="critical",
    ))
    cp.add(PackBlock(
        id="docs",
        type="knowledge",
        content="TokenPak is a context management library for LLM applications.\n"
                "It provides budget-aware compilation, quality filtering, and "
                "transparent compile reports.",
        priority="high",
        quality=0.9,
    ))
    cp.add(PackBlock(
        id="evidence_a",
        type="evidence",
        content="Example 1: context compression reduces token cost by 40%.",
        priority="medium",
        quality=0.7,
    ))
    cp.add(PackBlock(
        id="evidence_b",
        type="evidence",
        content="Example 2: deterministic output is required for reproducible evals.",
        priority="medium",
        quality=0.7,
    ))
    cp.add(PackBlock(
        id="history",
        type="conversation",
        content="User: What is TokenPak?\nAssistant: A context management toolkit.",
        priority="low",
    ))
    return cp


def _make_wire_blocks() -> list[dict]:
    """Return a fixed list of wire-format blocks for wire_pack tests."""
    return [
        {
            "ref": "docs/intro.md#v1",
            "type": "knowledge",
            "quality": 0.9,
            "tokens": 50,
            "content": "Introduction to TokenPak context management.",
        },
        {
            "ref": "docs/api.md#v1",
            "type": "knowledge",
            "quality": 0.8,
            "tokens": 40,
            "content": "API reference for ContextPack and PackBlock classes.",
        },
        {
            "ref": "conv/session1.md#v1",
            "type": "conversation",
            "quality": 0.6,
            "tokens": 30,
            "content": "User: How do I use TokenPak?\nAssistant: Import and compile.",
        },
    ]


# ---------------------------------------------------------------------------
# Test 1: Basic Determinism — 100 consecutive compiles
# ---------------------------------------------------------------------------


class TestBasicDeterminism(unittest.TestCase):
    """Same pack compiles identically across many consecutive runs."""

    def test_compile_determinism_text_100_runs(self):
        """Compiled text must be byte-identical across 100 consecutive compiles."""
        cp = _make_pack()
        results = [cp.compile().text for _ in range(100)]
        first = results[0]
        for i, result in enumerate(results[1:], start=1):
            self.assertEqual(
                result,
                first,
                msg=f"Run {i} produced different text than run 0.\n"
                    f"Expected:\n{first[:200]}\nGot:\n{result[:200]}",
            )

    def test_compile_determinism_wire_pack_100_runs(self):
        """wire_pack() output must be byte-identical across 100 consecutive calls."""
        blocks = _make_wire_blocks()
        results = [wire_pack(blocks, 1000) for _ in range(100)]
        first = results[0]
        for i, result in enumerate(results[1:], start=1):
            self.assertEqual(
                result,
                first,
                msg=f"wire_pack run {i} differs from run 0.",
            )

    def test_compile_determinism_final_order(self):
        """The final_order of blocks must be identical across runs."""
        cp = _make_pack()
        orders = [cp.compile().report.final_order for _ in range(50)]
        first = orders[0]
        for i, order in enumerate(orders[1:], start=1):
            self.assertEqual(order, first, msg=f"final_order differed on run {i}")

    def test_compile_determinism_with_quality_filter(self):
        """Quality-filtered packs must compile identically."""
        cp = ContextPack(budget=8000, quality_threshold=0.8)
        cp.add(PackBlock(id="a", type="knowledge", content="Keep me", priority="high", quality=0.9))
        cp.add(PackBlock(id="b", type="evidence", content="Drop me", priority="medium", quality=0.5))
        cp.add(PackBlock(id="c", type="evidence", content="Also keep", priority="medium", quality=0.85))

        results = [cp.compile().text for _ in range(50)]
        first = results[0]
        for i, result in enumerate(results[1:], start=1):
            self.assertEqual(result, first, msg=f"Quality-filtered run {i} differed")


# ---------------------------------------------------------------------------
# Test 2: Hash Equality
# ---------------------------------------------------------------------------


class TestHashEquality(unittest.TestCase):
    """SHA-256 of compiled text is stable across runs."""

    def test_compile_hash_stability_100_runs(self):
        """SHA-256 of compiled text must be the same across 100 runs."""
        cp = _make_pack()
        hashes = [
            hashlib.sha256(cp.compile().text.encode()).hexdigest()
            for _ in range(100)
        ]
        unique = set(hashes)
        self.assertEqual(
            len(unique),
            1,
            msg=f"Expected exactly 1 unique hash, got {len(unique)}: {unique}",
        )

    def test_wire_pack_hash_stability(self):
        """SHA-256 of wire_pack output is stable across 50 runs."""
        blocks = _make_wire_blocks()
        hashes = [
            hashlib.sha256(wire_pack(blocks, 1000).encode()).hexdigest()
            for _ in range(50)
        ]
        self.assertEqual(len(set(hashes)), 1, msg="wire_pack hashes not stable")

    def test_report_text_fields_stable(self):
        """Text-affecting report fields (final_order, decisions) must be stable."""
        cp = _make_pack()
        first = cp.compile().report
        for _ in range(20):
            r = cp.compile().report
            self.assertEqual(r.final_order, first.final_order)
            self.assertEqual(r.input_blocks, first.input_blocks)
            self.assertEqual(r.output_blocks, first.output_blocks)
            self.assertEqual(r.input_tokens, first.input_tokens)
            self.assertEqual(r.output_tokens, first.output_tokens)
            self.assertEqual(
                [d.to_dict() for d in r.decisions],
                [d.to_dict() for d in first.decisions],
            )


# ---------------------------------------------------------------------------
# Test 3: Cross-Process Determinism
# ---------------------------------------------------------------------------


class TestCrossProcessDeterminism(unittest.TestCase):
    """Same input produces identical output in separate Python processes."""

    _SUBPROCESS_SCRIPT = textwrap.dedent("""\
        from tokenpak.pack import ContextPack, PackBlock
        cp = ContextPack(budget=8000)
        cp.add(PackBlock(
            id="sys", type="instructions",
            content="You are a helpful assistant.",
            priority="critical",
        ))
        cp.add(PackBlock(
            id="docs", type="knowledge",
            content="TokenPak is a context management library.",
            priority="high", quality=0.9,
        ))
        cp.add(PackBlock(
            id="evidence", type="evidence",
            content="Deterministic output is required for reproducible evals.",
            priority="medium", quality=0.7,
        ))
        cp.add(PackBlock(
            id="history", type="conversation",
            content="User: What is TokenPak?\\nAssistant: A toolkit.",
            priority="low",
        ))
        print(cp.compile().text, end="")
    """)

    def _run_subprocess(self) -> str:
        result = subprocess.run(
            [sys.executable, "-c", self._SUBPROCESS_SCRIPT],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}",
        )
        return result.stdout

    def test_cross_process_determinism_three_runs(self):
        """Three independent subprocess runs must produce identical output."""
        outputs = [self._run_subprocess() for _ in range(3)]
        for i in range(1, len(outputs)):
            self.assertEqual(
                outputs[i],
                outputs[0],
                msg=f"Subprocess run {i} differs from run 0.\n"
                    f"Run 0 hash: {hashlib.sha256(outputs[0].encode()).hexdigest()}\n"
                    f"Run {i} hash: {hashlib.sha256(outputs[i].encode()).hexdigest()}",
            )

    def test_cross_process_hash_match(self):
        """Hash from subprocess matches hash from in-process compile."""
        # In-process
        cp = ContextPack(budget=8000)
        cp.add(PackBlock(
            id="sys", type="instructions",
            content="You are a helpful assistant.",
            priority="critical",
        ))
        cp.add(PackBlock(
            id="docs", type="knowledge",
            content="TokenPak is a context management library.",
            priority="high", quality=0.9,
        ))
        cp.add(PackBlock(
            id="evidence", type="evidence",
            content="Deterministic output is required for reproducible evals.",
            priority="medium", quality=0.7,
        ))
        cp.add(PackBlock(
            id="history", type="conversation",
            content="User: What is TokenPak?\nAssistant: A toolkit.",
            priority="low",
        ))
        in_process_hash = hashlib.sha256(cp.compile().text.encode()).hexdigest()

        subprocess_output = self._run_subprocess()
        subprocess_hash = hashlib.sha256(subprocess_output.encode()).hexdigest()

        self.assertEqual(
            in_process_hash,
            subprocess_hash,
            msg="In-process and subprocess hashes differ — cross-process determinism broken.",
        )


# ---------------------------------------------------------------------------
# Test 4: Block Order Stability
# ---------------------------------------------------------------------------


class TestBlockOrderDeterminism(unittest.TestCase):
    """Blocks with equal priority maintain stable, deterministic order."""

    def test_same_priority_insertion_order_preserved(self):
        """Equal-priority blocks must appear in consistent order across runs."""
        cp = ContextPack(budget=80000)
        block_ids = [f"block_{i:03d}" for i in range(20)]
        for bid in block_ids:
            cp.add(PackBlock(
                id=bid,
                type="evidence",
                content=f"Content for {bid}",
                priority="medium",
                quality=0.7,
            ))

        orders = [cp.compile().report.final_order for _ in range(20)]
        first = orders[0]
        for i, order in enumerate(orders[1:], start=1):
            self.assertEqual(order, first, msg=f"Block order differed on run {i}")

    def test_mixed_priority_order_deterministic(self):
        """Mixed-priority packs produce consistent priority-sorted output order."""
        cp = ContextPack(budget=80000)
        # Add in intentionally mixed order
        cp.add(PackBlock(id="low1",      type="context",      content="Low 1",      priority="low"))
        cp.add(PackBlock(id="critical1", type="instructions", content="Critical 1", priority="critical"))
        cp.add(PackBlock(id="medium1",   type="evidence",     content="Medium 1",   priority="medium", quality=0.7))
        cp.add(PackBlock(id="high1",     type="knowledge",    content="High 1",     priority="high"))
        cp.add(PackBlock(id="medium2",   type="evidence",     content="Medium 2",   priority="medium", quality=0.7))
        cp.add(PackBlock(id="high2",     type="knowledge",    content="High 2",     priority="high"))

        orders = [cp.compile().report.final_order for _ in range(20)]
        first = orders[0]
        # Critical → high → medium → low priority order must be stable
        self.assertIn("critical1", first)
        idx_critical = first.index("critical1")
        idx_high1    = first.index("high1")
        idx_medium1  = first.index("medium1")
        idx_low1     = first.index("low1")
        self.assertLess(idx_critical, idx_high1,    "Critical must precede high")
        self.assertLess(idx_high1,    idx_medium1,  "High must precede medium")
        self.assertLess(idx_medium1,  idx_low1,     "Medium must precede low")
        for i, order in enumerate(orders[1:], start=1):
            self.assertEqual(order, first, msg=f"Mixed-priority order differed on run {i}")

    def test_wire_pack_block_order_stable(self):
        """wire_pack preserves provided block order deterministically."""
        blocks = [
            {"ref": f"doc_{i}.md", "type": "knowledge", "quality": 0.8,
             "tokens": 10, "content": f"Content {i}"}
            for i in range(10)
        ]
        results = [wire_pack(blocks, 5000) for _ in range(30)]
        first = results[0]
        for i, result in enumerate(results[1:], start=1):
            self.assertEqual(result, first, msg=f"wire_pack block order differed on run {i}")


# ---------------------------------------------------------------------------
# Test 5: Compaction / Truncation Determinism
# ---------------------------------------------------------------------------


class TestCompactionDeterminism(unittest.TestCase):
    """Truncation (extractive compaction) produces identical results."""

    def test_max_tokens_truncation_deterministic(self):
        """Block-level max_tokens truncation must produce identical output."""
        long_content = "The quick brown fox jumps over the lazy dog. " * 500
        cp = ContextPack(budget=80000)
        cp.add(PackBlock(
            id="big_doc",
            type="knowledge",
            content=long_content,
            priority="high",
            max_tokens=50,
        ))
        cp.add(PackBlock(
            id="small",
            type="instructions",
            content="Short system prompt.",
            priority="critical",
        ))

        results = [cp.compile().text for _ in range(20)]
        first = results[0]
        for i, result in enumerate(results[1:], start=1):
            self.assertEqual(result, first, msg=f"Truncation run {i} differs from run 0")

    def test_budget_overflow_truncation_deterministic(self):
        """Budget-overflow truncation of critical/high blocks must be deterministic."""
        long_content = "A" * 10000  # Large block to force truncation
        cp = ContextPack(budget=100)  # Very tight budget
        cp.add(PackBlock(
            id="big_critical",
            type="instructions",
            content=long_content,
            priority="critical",
        ))

        results = [cp.compile().text for _ in range(20)]
        first = results[0]
        for i, result in enumerate(results[1:], start=1):
            self.assertEqual(result, first, msg=f"Budget-overflow truncation run {i} differs")

    def test_quality_filter_deterministic(self):
        """Quality-based removal is deterministic."""
        cp = ContextPack(budget=8000, quality_threshold=0.6)
        for i in range(10):
            # Alternate above/below threshold
            q = 0.3 if i % 2 == 0 else 0.8
            cp.add(PackBlock(
                id=f"block_{i}",
                type="evidence",
                content=f"Block {i} content with quality {q}.",
                priority="medium",
                quality=q,
            ))

        results = [cp.compile().text for _ in range(20)]
        first = results[0]
        for i, result in enumerate(results[1:], start=1):
            self.assertEqual(result, first, msg=f"Quality-filter run {i} differs")


# ---------------------------------------------------------------------------
# Test 6: Tiebreaker / Equal-Priority Determinism
# ---------------------------------------------------------------------------


class TestTiebreakerDeterminism(unittest.TestCase):
    """When priorities and quality are equal, selection is deterministic."""

    def test_equal_priority_equal_quality_deterministic(self):
        """Blocks with identical priority + quality land in consistent order."""
        cp = ContextPack(budget=80000)
        for i in range(15):
            cp.add(PackBlock(
                id=f"tie_{i:02d}",
                type="evidence",
                content=f"Evidence block {i} with identical metadata.",
                priority="medium",
                quality=0.7,
            ))

        orders = [cp.compile().report.final_order for _ in range(30)]
        first = orders[0]
        for i, order in enumerate(orders[1:], start=1):
            self.assertEqual(order, first, msg=f"Equal-metadata order differed on run {i}")

    def test_budget_limited_tie_selection_deterministic(self):
        """When budget forces dropping tied blocks, dropped set is consistent."""
        # Give tiny budget — only some of the tied blocks will fit
        cp = ContextPack(budget=200)
        for i in range(20):
            cp.add(PackBlock(
                id=f"tie_{i:02d}",
                type="evidence",
                content=f"Evidence block number {i}. " * 5,  # ~25 tokens each
                priority="medium",
                quality=0.7,
            ))

        orders = [cp.compile().report.final_order for _ in range(30)]
        first = orders[0]
        for i, order in enumerate(orders[1:], start=1):
            self.assertEqual(order, first, msg=f"Budget-limited tie selection differed on run {i}")


# ---------------------------------------------------------------------------
# Test 7: No Non-Deterministic Imports in Compile Path (Static Audit)
# ---------------------------------------------------------------------------


class TestCompilePathAudit(unittest.TestCase):
    """Verify the compile path does not import or use non-deterministic APIs."""

    def test_pack_module_no_random_import(self):
        """tokenpak.pack must not import the random module."""
        import tokenpak.pack as pack_mod
        self.assertFalse(
            hasattr(pack_mod, "random"),
            "tokenpak.pack imports 'random' — non-deterministic!",
        )

    def test_wire_module_no_random_import(self):
        """tokenpak.wire must not import the random module."""
        import tokenpak.wire as wire_mod
        self.assertFalse(
            hasattr(wire_mod, "random"),
            "tokenpak.wire imports 'random' — non-deterministic!",
        )

    def test_pack_compile_text_not_contain_timing(self):
        """Compile timing (compile_time_ms) must NOT appear in compiled text output."""
        cp = _make_pack()
        result = cp.compile()
        # compile_time_ms should only be in report, never in text
        self.assertNotIn(
            "compile_time_ms",
            result.text,
            "compile_time_ms leaked into compiled text output — breaks determinism",
        )

    def test_output_text_does_not_contain_timestamps(self):
        """Compiled text must not contain wall-clock timestamps."""
        import re
        cp = _make_pack()
        text = cp.compile().text
        # Look for patterns like 2024-01-01 or ISO-8601 in output text
        timestamp_pattern = re.compile(
            r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
        )
        self.assertIsNone(
            timestamp_pattern.search(text),
            f"Wall-clock timestamp found in compiled text: {timestamp_pattern.search(text)}",
        )

    def test_slice_ids_are_deterministic(self):
        """Slice IDs generated by wire.make_slice_id are content-derived (deterministic)."""
        from tokenpak.wire import make_slice_id
        content = "This is some test content for slice ID generation."
        ref = "test/doc.md#v1"
        ids = [make_slice_id(content, ref) for _ in range(50)]
        self.assertEqual(len(set(ids)), 1, "make_slice_id is non-deterministic!")
        # Also verify the ID is SHA-256 derived
        expected_hash = hashlib.sha256(f"{ref}:{content}".encode()).hexdigest()[:8]
        self.assertEqual(ids[0], f"s_{expected_hash}")


# ---------------------------------------------------------------------------
# Test 8: Idempotent Re-Add (same pack, same compile)
# ---------------------------------------------------------------------------


class TestIdempotentCompile(unittest.TestCase):
    """compile() called multiple times on the same object produces identical results."""

    def test_repeated_compile_same_object(self):
        """Calling compile() 50 times on same ContextPack gives identical text."""
        cp = _make_pack()
        first_text = cp.compile().text
        for i in range(49):
            self.assertEqual(
                cp.compile().text,
                first_text,
                msg=f"compile() call {i + 2} on same object differed",
            )

    def test_to_json_text_field_deterministic(self):
        """compiled.to_json()['text'] is deterministic (compile_time_ms excluded from check)."""
        cp = _make_pack()
        results = [cp.compile().to_json() for _ in range(20)]
        first_text = results[0]["text"]
        for i, r in enumerate(results[1:], start=1):
            self.assertEqual(
                r["text"],
                first_text,
                msg=f"to_json()['text'] differed on run {i}",
            )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main(verbosity=2)
