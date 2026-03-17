#!/usr/bin/env python3
"""
Test script for deterministic retrieval injection.

Verifies:
1. sort_retrieval_results produces deterministic ordering
2. inject_retrieved_context respects token cap
3. Identical queries produce identical output
4. Cache-stable format is preserved
"""

import hashlib
from tokenpak.agent.vault.retrieval import (
    sort_retrieval_results,
    inject_retrieved_context,
    measure_injection_consistency,
)


def test_deterministic_sort():
    """Test that results are sorted consistently by score desc, path asc, chunk_id asc."""
    print("\n=== TEST: Deterministic Sort ===")

    # Create test results with equal scores (to test tie-breaking)
    results = [
        ({"source_path": "doc_b.md", "block_id": "chunk_2", "content": "content B"}, 0.90),
        ({"source_path": "doc_a.md", "block_id": "chunk_1", "content": "content A"}, 0.95),
        ({"source_path": "doc_a.md", "block_id": "chunk_3", "content": "content C"}, 0.90),
        ({"source_path": "doc_c.md", "block_id": "chunk_1", "content": "content D"}, 0.85),
    ]

    sorted_results = sort_retrieval_results(results)
    print("Input order:     doc_b(0.90), doc_a(0.95), doc_a(0.90), doc_c(0.85)")
    print("Sorted order:    ", end="")
    for block, score in sorted_results:
        print(f"{block['source_path'].split('/')[-1]}({block['block_id']}, {score:.2f})", end=" ")
    print()

    # Debug: print actual sorted results
    print("\nDebug - sorted results:")
    for i, (block, score) in enumerate(sorted_results):
        print(f"  {i}: {block['source_path']} {block['block_id']} {score:.2f}")

    # Verify sort order: score desc (0.95, 0.90, 0.90, 0.85), then path asc (doc_a before doc_b), then chunk_id asc
    assert sorted_results[0][1] == 0.95, f"First should be 0.95, got {sorted_results[0][1]}"
    assert sorted_results[0][0]["source_path"] == "doc_a.md"

    # At index 1 and 2, we have two 0.90 scores
    # Sort by path: doc_a < doc_b, so doc_a chunk_3 should be at index 1
    assert sorted_results[1][0]["source_path"] == "doc_a.md"
    assert sorted_results[1][0]["block_id"] == "chunk_3"  # chunk_3 for doc_a (0.90)

    # Index 2: doc_b chunk_2 (0.90)
    assert sorted_results[2][0]["source_path"] == "doc_b.md"
    assert sorted_results[2][0]["block_id"] == "chunk_2"

    # Index 3: doc_c (0.85)
    assert sorted_results[3][0]["source_path"] == "doc_c.md"
    assert sorted_results[3][1] == 0.85

    print("✅ Sort order verified: score desc, path asc, chunk_id asc")


def test_token_cap():
    """Test that injection respects the token cap."""
    print("\n=== TEST: Token Cap Enforcement ===")

    # Create results that exceed the cap
    results = [
        (
            {
                "source_path": "doc_a.md",
                "block_id": "chunk_1",
                "content": "A" * 2000,
            },
            0.95,
        ),
        (
            {
                "source_path": "doc_b.md",
                "block_id": "chunk_1",
                "content": "B" * 2000,
            },
            0.90,
        ),
        (
            {
                "source_path": "doc_c.md",
                "block_id": "chunk_1",
                "content": "C" * 2000,
            },
            0.85,
        ),
    ]

    max_tokens = 1000

    def dummy_count(text):
        # Rough: 4 chars = 1 token
        return max(1, len(text) // 4)

    injection, tokens_used, refs = inject_retrieved_context(
        results, max_tokens=max_tokens, count_tokens_fn=dummy_count
    )

    print(f"Max tokens:      {max_tokens}")
    print(f"Tokens used:     {tokens_used}")
    print(f"References:      {refs}")
    print(f"Injection length: {len(injection)} chars")

    assert tokens_used <= max_tokens, f"Cap violated: {tokens_used} > {max_tokens}"
    print(f"✅ Token cap enforced: {tokens_used} ≤ {max_tokens}")


def test_deterministic_output():
    """Test that identical queries produce identical output."""
    print("\n=== TEST: Deterministic Output (3 runs) ===")

    # Create a query function that uses deterministic results
    def query_injection():
        results = [
            (
                {
                    "source_path": "doc_a.md",
                    "block_id": "chunk_1",
                    "content": "Machine learning is a subset of AI.",
                },
                0.95,
            ),
            (
                {
                    "source_path": "doc_b.md",
                    "block_id": "chunk_1",
                    "content": "AI stands for Artificial Intelligence.",
                },
                0.85,
            ),
        ]
        injection, _, _ = inject_retrieved_context(results)
        return injection

    results_list = []
    hashes = []
    for i in range(3):
        result = query_injection()
        results_list.append(result)
        h = hashlib.sha256(result.encode()).hexdigest()[:16]
        hashes.append(h)
        print(f"Run {i + 1} hash: {h}")

    # Verify all outputs are identical
    assert len(set(results_list)) == 1, "Outputs differ across runs!"
    assert len(set(hashes)) == 1, "Hashes differ across runs!"
    print("✅ All 3 runs produced identical output")


def test_fixed_section_placement():
    """Test that retrieval context is in a fixed section."""
    print("\n=== TEST: Fixed Section Placement ===")

    results = [
        (
            {"source_path": "doc.md", "block_id": "chunk_1", "content": "Test content"},
            0.90,
        ),
    ]

    injection, _, _ = inject_retrieved_context(results)

    # Verify the fixed header is present
    assert "## Retrieved Context" in injection, "Fixed header missing!"
    print("✅ Fixed section header '## Retrieved Context' present")

    # Verify the section structure
    lines = injection.strip().split("\n")
    assert "## Retrieved Context" in lines[0], "Header not at start!"
    print("✅ Header at fixed position")


def test_cache_stability():
    """Test that the output is stable for caching."""
    print("\n=== TEST: Cache Stability ===")

    # Create two identical request scenarios
    def build_injection(query_param):
        results = [
            (
                {"source_path": f"file_{query_param}.md", "block_id": "c1", "content": "Result 1"},
                0.95,
            ),
            (
                {"source_path": f"file_{query_param}.md", "block_id": "c2", "content": "Result 2"},
                0.85,
            ),
        ]
        injection, tokens, _ = inject_retrieved_context(results)
        return injection, tokens

    # Same query parameter should yield identical output
    inj1, tok1 = build_injection("A")
    inj2, tok2 = build_injection("A")

    assert inj1 == inj2, "Identical input produced different injection!"
    assert tok1 == tok2, "Token count differs!"

    # Different query parameter should yield different output
    inj3, _ = build_injection("B")
    assert inj1 != inj3, "Different input produced same injection!"

    print("✅ Cache stability verified: identical input → identical output")


def test_source_refs_returned():
    """Test that source references are correctly returned."""
    print("\n=== TEST: Source References ===")

    results = [
        ({"source_path": "file_a.md", "block_id": "c1", "content": "Content A"}, 0.95),
        ({"source_path": "file_b.md", "block_id": "c1", "content": "Content B"}, 0.85),
    ]

    _, _, refs = inject_retrieved_context(results)

    print(f"Source references: {refs}")
    assert len(refs) == 2, f"Expected 2 refs, got {len(refs)}"
    assert refs[0] == "file_a.md", f"First ref should be file_a.md, got {refs[0]}"
    assert refs[1] == "file_b.md", f"Second ref should be file_b.md, got {refs[1]}"

    print("✅ Source references correctly ordered and returned")


if __name__ == "__main__":
    print("=" * 60)
    print("DETERMINISTIC RETRIEVAL INJECTION TESTS")
    print("=" * 60)

    try:
        test_deterministic_sort()
        test_token_cap()
        test_deterministic_output()
        test_fixed_section_placement()
        test_cache_stability()
        test_source_refs_returned()

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
        exit(1)
