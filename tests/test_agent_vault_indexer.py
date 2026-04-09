"""Tests for tokenpak.agent.vault.indexer (VaultIndexer)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tokenpak.agent.vault.blocks import BlockRecord, BlockStore, SliceStore
from tokenpak.agent.vault.indexer import VaultIndexer
from tokenpak.agent.vault.symbols import SymbolTable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_indexer() -> VaultIndexer:
    """Create an in-memory VaultIndexer for isolated testing."""
    return VaultIndexer(
        block_store=BlockStore(":memory:"),
        symbol_table=SymbolTable(),
        slice_store=SliceStore(":memory:"),
    )


# ---------------------------------------------------------------------------
# 1. Import smoke test
# ---------------------------------------------------------------------------

def test_import_ok():
    """VaultIndexer can be imported and instantiated."""
    vi = _make_indexer()
    assert vi is not None


# ---------------------------------------------------------------------------
# 2. Index a document by content (no disk read)
# ---------------------------------------------------------------------------

def test_index_file_with_content():
    """index_file with explicit content returns a BlockRecord."""
    vi = _make_indexer()
    record = vi.index_file("/fake/path/module.py", content="def foo():\n    return 42\n")
    assert record is not None
    assert isinstance(record, BlockRecord)
    assert record.path == "/fake/path/module.py"


# ---------------------------------------------------------------------------
# 3. Retrieve by id via blocks store
# ---------------------------------------------------------------------------

def test_retrieve_by_block_id():
    """Indexed document can be fetched by block_id via the block store."""
    vi = _make_indexer()
    record = vi.index_file("/fake/path/helper.py", content="X = 1\n")
    assert record is not None
    fetched = vi.blocks.get(record.block_id)
    assert fetched is not None
    assert fetched.block_id == record.block_id


# ---------------------------------------------------------------------------
# 4. List indexed documents via blocks.all()
# ---------------------------------------------------------------------------

def test_list_indexed_documents():
    """After indexing multiple files, all() returns all indexed records."""
    vi = _make_indexer()
    files = {
        "/proj/a.py": "def a(): pass\n",
        "/proj/b.py": "def b(): pass\n",
        "/proj/c.py": "def c(): pass\n",
    }
    for path, content in files.items():
        vi.index_file(path, content=content)

    all_blocks = vi.blocks.all()
    indexed_paths = {b.path for b in all_blocks}
    assert "/proj/a.py" in indexed_paths
    assert "/proj/b.py" in indexed_paths
    assert "/proj/c.py" in indexed_paths


# ---------------------------------------------------------------------------
# 5. Remove / delete a document
# ---------------------------------------------------------------------------

def test_remove_indexed_document():
    """Deleting a block_id removes it from the store."""
    vi = _make_indexer()
    record = vi.index_file("/fake/del.py", content="x = 99\n")
    assert record is not None
    deleted = vi.blocks.delete(record.block_id)
    assert deleted is True
    assert vi.blocks.get(record.block_id) is None


# ---------------------------------------------------------------------------
# 6. Search by keyword — results returned
# ---------------------------------------------------------------------------

def test_search_by_keyword_returns_results():
    """search() returns blocks containing the queried keyword."""
    vi = _make_indexer()
    vi.index_file("/proj/auth.py", content="def authenticate_user(token): return True\n")
    vi.index_file("/proj/util.py", content="def helper(): pass\n")
    results = vi.search("authenticate")
    paths = [r.path for r in results]
    assert "/proj/auth.py" in paths


# ---------------------------------------------------------------------------
# 7. Search — empty query returns results or empty list gracefully
# ---------------------------------------------------------------------------

def test_search_empty_query_handled():
    """search('') does not raise; returns a list."""
    vi = _make_indexer()
    vi.index_file("/proj/x.py", content="def x(): pass\n")
    result = vi.search("")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 8. Incremental indexing — same content returns same block (no duplicate)
# ---------------------------------------------------------------------------

def test_incremental_index_no_duplicate():
    """Re-indexing unchanged content returns the existing record, not a new one."""
    vi = _make_indexer()
    content = "def stable(): return 1\n"
    r1 = vi.index_file("/proj/stable.py", content=content)
    r2 = vi.index_file("/proj/stable.py", content=content)
    assert r1 is not None and r2 is not None
    assert r1.block_id == r2.block_id
    # Only one block in the store
    blocks = vi.blocks.get_by_path("/proj/stable.py")
    assert len(blocks) == 1


# ---------------------------------------------------------------------------
# 9. index_directory — real temp dir
# ---------------------------------------------------------------------------

def test_index_directory_returns_summary(tmp_path: Path):
    """index_directory walks a real dir and returns a well-formed summary dict."""
    (tmp_path / "main.py").write_text("def main(): pass\n")
    (tmp_path / "utils.py").write_text("def util(): pass\n")
    (tmp_path / "README.md").write_text("# Project\nSome docs.\n")

    vi = _make_indexer()
    summary = vi.index_directory(str(tmp_path))

    assert isinstance(summary, dict)
    assert "files_found" in summary
    assert "files_indexed" in summary
    assert "tokens_raw" in summary
    assert "tokens_saved" in summary
    assert "duration_ms" in summary
    assert summary["files_indexed"] >= 1


# ---------------------------------------------------------------------------
# 10. stats() returns expected keys
# ---------------------------------------------------------------------------

def test_stats_returns_expected_keys():
    """stats() returns a dict with at least total_indexed and total_symbols."""
    vi = _make_indexer()
    vi.index_file("/proj/stats_test.py", content="def check(): pass\n")
    s = vi.stats()
    assert isinstance(s, dict)
    # Must include symbol count key
    assert "total_symbols" in s


# ---------------------------------------------------------------------------
# 11. Non-existent file without content returns None
# ---------------------------------------------------------------------------

def test_index_nonexistent_file_returns_none():
    """index_file on a missing path with no content returns None gracefully."""
    vi = _make_indexer()
    result = vi.index_file("/no/such/path/ghost.py")
    assert result is None


# ---------------------------------------------------------------------------
# 12. search with top_k limit
# ---------------------------------------------------------------------------

def test_search_top_k_limits_results():
    """search respects top_k parameter."""
    vi = _make_indexer()
    for i in range(10):
        vi.index_file(f"/proj/mod{i}.py", content=f"def func_{i}(): pass  # common\n")
    results = vi.search("common", top_k=3)
    assert len(results) <= 3


# ---------------------------------------------------------------------------
# 13. stats_by_type breakdown
# ---------------------------------------------------------------------------

def test_stats_by_type_breakdown(tmp_path: Path):
    """stats_by_type returns total_files and by_type dict."""
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.md").write_text("# Title\n")

    vi = _make_indexer()
    vi.index_directory(str(tmp_path))
    sbt = vi.stats_by_type()
    assert "total_files" in sbt
    assert "by_type" in sbt
    assert isinstance(sbt["by_type"], dict)


# ---------------------------------------------------------------------------
# 14. get_by_path on blocks store
# ---------------------------------------------------------------------------

def test_get_by_path():
    """blocks.get_by_path returns the indexed record for a given path."""
    vi = _make_indexer()
    vi.index_file("/proj/lookup.py", content="LOOKUP = True\n")
    results = vi.blocks.get_by_path("/proj/lookup.py")
    assert len(results) >= 1
    assert results[0].path == "/proj/lookup.py"
