"""Tests for Script-Aware Semantic Slicing (p1-tokenpak-script-aware-semantic-slicing-2026-03-10).

Acceptance criteria:
    1. Long structured content files split into deterministic semantic sub-blocks.
    2. Sub-block IDs are stable across re-index runs when content unchanged.
    3. Retrieval precision improves for section-specific queries.
    4. Provenance links parent file to child slices.
    5. Tests cover splitting, stable IDs, and retrieval selectivity.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from tokenpak.vault.blocks import BlockStore, SliceStore
from tokenpak.vault.indexer import VaultIndexer
from tokenpak.vault.slicer import (
    SliceRecord,
    detect_split_strategy,
    should_slice,
    slice_content,
)
from tokenpak.vault.symbols import SymbolTable

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SCRIPT_BATCH = textwrap.dedent("""\
    # YouTube Script Batch — Head2Head Matches

    These scripts cover multiple match-up episodes.

    ## Script 1: Ronaldo vs Messi

    Welcome back to Head2Head Matches! Today we're comparing the two greatest
    footballers of all time. Ronaldo has scored over 800 career goals while
    Messi has won 8 Ballon d'Or awards. Both players have dominated their
    respective leagues and international competitions.

    The data shows Ronaldo edges Messi in raw goal count but Messi leads in
    assists and dribbles. Who do you think wins? Let us know in the comments.

    ## Script 2: LeBron vs Jordan

    The basketball GOAT debate is eternal. LeBron James has played in 10 NBA
    Finals while Michael Jordan went 6-0. LeBron's longevity is unmatched
    with 20+ seasons at an elite level. Jordan's peak dominance was
    historically unprecedented.

    In terms of points per game, Jordan leads slightly at 30.1 vs LeBron's
    27.1. However, LeBron's all-around stats — rebounds, assists — put him
    in a different category. Ultimately this is a subjective debate.

    ## Script 3: Federer vs Nadal

    Tennis legends Federer and Nadal have played 40 matches over their careers.
    Nadal leads the head-to-head 24-16, though Federer dominated on grass.
    Federer holds 20 Grand Slam titles while Nadal has 22.

    Their styles are complementary opposites: Federer's finesse vs Nadal's
    topspin power. Federer fans argue his peak was untouchable; Nadal fans
    point to clay court dominance. Both are top-3 all time by most metrics.
""")

PREAMBLE_ONLY = textwrap.dedent("""\
    This is a short document.

    It has no headings at the ## level, only plain paragraphs.
    The content is intentionally brief.
""")

GENERIC_LONG = textwrap.dedent("""\
    ## Section Alpha

    This section covers the first major topic in our analysis.
    We explore various dimensions including performance metrics,
    user engagement rates, and competitive positioning across markets.
    The alpha section sets the baseline for our downstream comparisons.

    ## Section Beta

    Beta section dives into the secondary analysis layer. This includes
    cohort breakdowns, retention curves, and churn attribution models.
    All figures are normalized against Q4 baseline numbers.

    ## Section Gamma

    Gamma wraps up with forward-looking projections and risk analysis.
    Monte Carlo simulations indicate 85% probability of target attainment
    given current trajectory. Confidence intervals are at the 95% level.
""")


def make_indexer():
    store = BlockStore(":memory:")
    slice_store = SliceStore(":memory:")
    return VaultIndexer(block_store=store, symbol_table=SymbolTable(), slice_store=slice_store)


# ===========================================================================
# 1. Splitting — deterministic semantic sub-blocks
# ===========================================================================


class TestSplitting:
    def test_script_batch_produces_three_slices(self):
        slices = slice_content(SCRIPT_BATCH, "doc.md#aabbccdd", "doc.md")
        # Expect at least Script 1, Script 2, Script 3 (plus optional preamble)
        script_slices = [s for s in slices if "Script" in s.heading or "Script" in s.content[:30]]
        assert len(script_slices) >= 3, f"Expected ≥3 script slices, got {len(script_slices)}"

    def test_each_slice_contains_relevant_content(self):
        slices = slice_content(SCRIPT_BATCH, "doc.md#aabbccdd", "doc.md")
        texts = {s.heading: s.content for s in slices}
        # Find Ronaldo slice
        ronaldo_slice = next((s for s in slices if "Ronaldo" in s.content), None)
        assert ronaldo_slice is not None, "Should find a slice with Ronaldo content"
        assert "LeBron" not in ronaldo_slice.content, (
            "Ronaldo slice should not bleed into LeBron slice"
        )

    def test_slices_cover_entire_content(self):
        slices = slice_content(SCRIPT_BATCH, "doc.md#aabbccdd", "doc.md")
        all_text = "\n".join(s.content for s in slices)
        # Each distinct section heading should appear somewhere in slice content
        assert "Ronaldo" in all_text
        assert "LeBron" in all_text
        assert "Federer" in all_text

    def test_generic_heading_split(self):
        slices = slice_content(GENERIC_LONG, "doc.md#x1y2z3w4", "doc.md")
        headings = [s.heading for s in slices]
        assert any("Alpha" in h for h in headings)
        assert any("Beta" in h for h in headings)
        assert any("Gamma" in h for h in headings)

    def test_short_content_returns_empty_or_minimal(self):
        short = "# Heading\n\nShort."
        slices = slice_content(short, "doc.md#00000000", "doc.md")
        # Should produce 0 slices (too short to be worth slicing)
        assert len(slices) == 0

    def test_slice_strategy_set_on_records(self):
        slices = slice_content(SCRIPT_BATCH, "doc.md#aabbccdd", "doc.md")
        assert all(s.strategy in ("script", "heading", "section") for s in slices)

    def test_heading_captured_in_slice(self):
        slices = slice_content(GENERIC_LONG, "doc.md#x1y2z3w4", "doc.md")
        for s in slices:
            if s.heading:
                assert s.heading.startswith("#"), f"Heading should start with #: {s.heading!r}"


# ===========================================================================
# 2. Stable IDs — same across re-index runs when content unchanged
# ===========================================================================


class TestStableIDs:
    def test_slice_ids_stable_across_calls(self):
        slices_1 = slice_content(SCRIPT_BATCH, "doc.md#aabbccdd", "doc.md")
        slices_2 = slice_content(SCRIPT_BATCH, "doc.md#aabbccdd", "doc.md")
        ids_1 = [s.slice_id for s in slices_1]
        ids_2 = [s.slice_id for s in slices_2]
        assert ids_1 == ids_2, "Slice IDs must be deterministic across runs"

    def test_slice_id_changes_when_content_changes(self):
        slices_before = slice_content(SCRIPT_BATCH, "doc.md#aabbccdd", "doc.md")
        modified = SCRIPT_BATCH.replace("Ronaldo vs Messi", "Ronaldo vs Messi REVISED")
        slices_after = slice_content(modified, "doc.md#aabbccdd", "doc.md")

        # The slice that was changed should have a different ID
        ids_before = {s.slice_id for s in slices_before}
        ids_after = {s.slice_id for s in slices_after}
        changed = ids_before.symmetric_difference(ids_after)
        assert len(changed) > 0, "Changing content should produce at least one new slice ID"

    def test_unchanged_sibling_slices_have_stable_ids(self):
        """Modifying Script 1 should not change Script 2 or Script 3 IDs."""
        modified = SCRIPT_BATCH.replace(
            "## Script 1: Ronaldo vs Messi", "## Script 1: Ronaldo vs Messi — UPDATED"
        )
        slices_orig = slice_content(SCRIPT_BATCH, "doc.md#aabbccdd", "doc.md")
        slices_mod = slice_content(modified, "doc.md#aabbccdd", "doc.md")

        # Script 2 and 3 content is unchanged → their slice IDs should match
        orig_by_heading = {s.heading: s.slice_id for s in slices_orig}
        mod_by_heading = {s.heading: s.slice_id for s in slices_mod}

        for heading in orig_by_heading:
            if "Script 2" in heading or "Script 3" in heading:
                assert orig_by_heading[heading] == mod_by_heading.get(heading), (
                    f"Slice ID for '{heading}' changed despite content being unchanged"
                )

    def test_slice_ids_contain_parent_block_id(self):
        parent_id = "mypath/doc.md#aabbccdd"
        slices = slice_content(SCRIPT_BATCH, parent_id, "mypath/doc.md")
        for s in slices:
            assert s.slice_id.startswith(parent_id), (
                f"Slice ID {s.slice_id!r} should start with parent block ID"
            )


# ===========================================================================
# 3. Retrieval precision — section-specific queries avoid sibling content
# ===========================================================================


class TestRetrievalPrecision:
    def _make_indexed_doc(self, tmp_path: Path) -> tuple:
        """Write SCRIPT_BATCH to a temp file, index it, return (indexer, path)."""
        doc = tmp_path / "scripts.md"
        doc.write_text(SCRIPT_BATCH, encoding="utf-8")
        indexer = make_indexer()
        record = indexer.index_file(str(doc))
        return indexer, str(doc), record

    def test_ronaldo_query_returns_ronaldo_slice(self, tmp_path):
        indexer, path, _ = self._make_indexed_doc(tmp_path)
        results = indexer.search_slices("Ronaldo Messi football goals", top_k=3)
        assert len(results) > 0, "Should find at least one slice for Ronaldo query"
        top = results[0]
        assert "Ronaldo" in top.content, "Top result should be the Ronaldo/Messi script"

    def test_ronaldo_query_does_not_include_lebron_as_top(self, tmp_path):
        indexer, path, _ = self._make_indexed_doc(tmp_path)
        results = indexer.search_slices("Ronaldo Messi football goals", top_k=1)
        assert len(results) > 0
        assert "LeBron" not in results[0].content, (
            "LeBron content should not be top result for Ronaldo query"
        )

    def test_lebron_query_returns_basketball_slice(self, tmp_path):
        indexer, path, _ = self._make_indexed_doc(tmp_path)
        results = indexer.search_slices("LeBron Jordan NBA Finals basketball", top_k=3)
        assert any("LeBron" in r.content for r in results), (
            "At least one result should mention LeBron"
        )

    def test_federer_query_does_not_top_with_football(self, tmp_path):
        indexer, path, _ = self._make_indexed_doc(tmp_path)
        results = indexer.search_slices("Federer Nadal tennis Grand Slam", top_k=1)
        assert len(results) > 0
        assert "Ronaldo" not in results[0].content, (
            "Federer query top result should not be the football script"
        )

    def test_search_slices_returns_slice_records(self, tmp_path):
        indexer, path, _ = self._make_indexed_doc(tmp_path)
        results = indexer.search_slices("script", top_k=5)
        for r in results:
            assert isinstance(r, SliceRecord)

    def test_get_slices_for_file_returns_all_slices(self, tmp_path):
        indexer, path, _ = self._make_indexed_doc(tmp_path)
        slices = indexer.get_slices_for_file(path)
        assert len(slices) >= 3, f"Expected ≥3 slices, got {len(slices)}"


# ===========================================================================
# 4. Provenance — parent → child links
# ===========================================================================


class TestProvenance:
    def test_slice_parent_block_id_matches_indexer_record(self, tmp_path):
        doc = tmp_path / "scripts.md"
        doc.write_text(SCRIPT_BATCH, encoding="utf-8")
        indexer = make_indexer()
        record = indexer.index_file(str(doc))
        assert record is not None

        slices = indexer.get_slices_for_file(str(doc))
        for s in slices:
            assert s.parent_block_id == record.block_id, (
                f"Slice {s.slice_id!r} parent_block_id mismatch"
            )

    def test_slice_parent_path_matches_file_path(self, tmp_path):
        doc = tmp_path / "scripts.md"
        doc.write_text(SCRIPT_BATCH, encoding="utf-8")
        indexer = make_indexer()
        indexer.index_file(str(doc))
        slices = indexer.get_slices_for_file(str(doc))
        for s in slices:
            assert s.parent_path == str(doc), (
                f"Slice {s.slice_id!r} parent_path mismatch: {s.parent_path!r}"
            )

    def test_slice_store_by_parent_returns_same_children(self, tmp_path):
        doc = tmp_path / "scripts.md"
        doc.write_text(SCRIPT_BATCH, encoding="utf-8")
        indexer = make_indexer()
        record = indexer.index_file(str(doc))
        slices_via_parent = indexer.slices.get_by_parent(record.block_id)
        slices_via_path = indexer.get_slices_for_file(str(doc))
        assert {s.slice_id for s in slices_via_parent} == {s.slice_id for s in slices_via_path}

    def test_slice_index_is_sequential(self, tmp_path):
        doc = tmp_path / "scripts.md"
        doc.write_text(SCRIPT_BATCH, encoding="utf-8")
        indexer = make_indexer()
        indexer.index_file(str(doc))
        slices = indexer.get_slices_for_file(str(doc))
        indices = [s.slice_index for s in slices]
        # Indices should be in ascending order (get_slices_for_file sorts by slice_index)
        assert indices == sorted(indices), "Slices should be returned in document order"

    def test_re_index_refreshes_slices(self, tmp_path):
        """After re-indexing with new content, stale slices are replaced."""
        doc = tmp_path / "scripts.md"
        doc.write_text(SCRIPT_BATCH, encoding="utf-8")
        indexer = make_indexer()
        record1 = indexer.index_file(str(doc))
        slices_v1 = indexer.get_slices_for_file(str(doc))

        # Add a 4th script
        new_content = SCRIPT_BATCH + textwrap.dedent("""\

            ## Script 4: Ali vs Tyson

            The boxing debate between Muhammad Ali and Mike Tyson has fascinated
            fans for decades. Ali's footwork and ring IQ vs Tyson's explosive
            power in the early rounds. Most analysts favor Ali on points.
        """)
        doc.write_text(new_content, encoding="utf-8")
        record2 = indexer.index_file(str(doc))
        slices_v2 = indexer.get_slices_for_file(str(doc))

        assert len(slices_v2) > len(slices_v1), (
            "Re-index should produce more slices for extended doc"
        )
        assert any("Ali" in s.content for s in slices_v2), "New script content should appear"


# ===========================================================================
# 5. should_slice / detect_split_strategy helpers
# ===========================================================================


class TestHelpers:
    def test_should_slice_long_multiheading_md(self, tmp_path):
        path = str(tmp_path / "long.md")
        assert should_slice(SCRIPT_BATCH, path) is True

    def test_should_not_slice_short_file(self, tmp_path):
        path = str(tmp_path / "short.md")
        assert should_slice("# Hello\n\nShort.", path) is False

    def test_should_not_slice_code_file(self, tmp_path):
        path = str(tmp_path / "main.py")
        assert should_slice(SCRIPT_BATCH, path) is False

    def test_detect_script_strategy(self):
        assert detect_split_strategy(SCRIPT_BATCH) == "script"

    def test_detect_heading_strategy(self):
        assert detect_split_strategy(GENERIC_LONG) in ("heading", "script")

    def test_detect_section_strategy_for_headingless_text(self):
        text = "Para one.\n\nPara two.\n\nPara three.\n\nPara four. " * 5
        assert detect_split_strategy(text) == "section"


# ===========================================================================
# 6. SliceStore persistence
# ===========================================================================


class TestSliceStorePersistence:
    def test_in_memory_store_holds_slices(self):
        store = SliceStore(":memory:")
        slices = slice_content(SCRIPT_BATCH, "doc.md#aabbccdd", "doc.md")
        for s in slices:
            store.save(s)
        assert len(store) == len(slices)

    def test_get_by_parent_returns_correct_children(self):
        store = SliceStore(":memory:")
        slices = slice_content(SCRIPT_BATCH, "doc.md#aabbccdd", "doc.md")
        for s in slices:
            store.save(s)
        children = store.get_by_parent("doc.md#aabbccdd")
        assert len(children) == len(slices)
        assert all(c.parent_block_id == "doc.md#aabbccdd" for c in children)

    def test_delete_by_parent_clears_children(self):
        store = SliceStore(":memory:")
        slices = slice_content(SCRIPT_BATCH, "doc.md#aabbccdd", "doc.md")
        for s in slices:
            store.save(s)
        removed = store.delete_by_parent("doc.md#aabbccdd")
        assert removed == len(slices)
        assert len(store.get_by_parent("doc.md#aabbccdd")) == 0

    def test_json_roundtrip(self, tmp_path):
        store_path = str(tmp_path / "slices.json")
        store1 = SliceStore(store_path)
        slices = slice_content(GENERIC_LONG, "doc.md#xyz12345", "doc.md")
        for s in slices:
            store1.save(s)
        original_ids = {s.slice_id for s in slices}

        # Load fresh from disk
        store2 = SliceStore(store_path)
        loaded_ids = {r.slice_id for r in store2.all()}
        assert original_ids == loaded_ids, "All slice IDs should survive JSON round-trip"
