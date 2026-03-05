"""Tests for full vault indexer — file types, incremental indexing, symbol extraction.

Covers acceptance criteria from p1-tokenpak-vault-indexer-full-2026-03-05.md
"""

from __future__ import annotations

import hashlib
import os
import textwrap
from pathlib import Path

import pytest

from tokenpak.walker import detect_file_type, walk_directory, FILE_TYPES
from tokenpak.agent.vault import VaultIndexer
from tokenpak.agent.vault.blocks import BlockStore, BlockRecord
from tokenpak.agent.vault.symbols import SymbolTable
from tokenpak.agent.vault.ast_parser import ASTParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_indexer():
    """Return an indexer backed by an in-memory block store."""
    store = BlockStore(":memory:")
    return VaultIndexer(block_store=store, symbol_table=SymbolTable())


# ===========================================================================
# 1. File type coverage — walker.py
# ===========================================================================

class TestFileTypeCoverage:

    @pytest.mark.parametrize("ext", [
        ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs",
        ".java", ".rb", ".php", ".c", ".cpp", ".h", ".cs",
        ".swift", ".kt",
    ])
    def test_code_extensions_recognized(self, ext, tmp_path):
        f = tmp_path / f"file{ext}"
        f.write_text("hello code")
        assert detect_file_type(str(f)) == "code", f"{ext} should be 'code'"

    @pytest.mark.parametrize("ext", [".md", ".txt", ".rst", ".adoc", ".org"])
    def test_text_extensions_recognized(self, ext, tmp_path):
        f = tmp_path / f"file{ext}"
        f.write_text("# hello")
        assert detect_file_type(str(f)) == "text", f"{ext} should be 'text'"

    @pytest.mark.parametrize("ext", [
        ".json", ".yaml", ".yml", ".toml", ".ini", ".xml", ".csv",
    ])
    def test_config_and_data_extensions_recognized(self, ext, tmp_path):
        f = tmp_path / f"file{ext}"
        f.write_text("key: value")
        result = detect_file_type(str(f))
        assert result in ("data", "code"), f"{ext} should be 'data' or 'code', got {result}"

    def test_env_file_recognized(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("API_KEY=abc")
        assert detect_file_type(str(f)) == "data"

    def test_unknown_extension_returns_none(self, tmp_path):
        f = tmp_path / "file.xyz123"
        f.write_text("content")
        assert detect_file_type(str(f)) is None

    def test_walk_directory_finds_all_supported_types(self, tmp_path):
        exts = [".py", ".ts", ".md", ".json", ".yaml", ".go", ".php", ".cs",
                ".swift", ".kt", ".org", ".xml", ".rst"]
        for ext in exts:
            (tmp_path / f"file{ext}").write_text(f"content for {ext}")
        # also a binary that should be skipped
        (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")

        results = walk_directory(str(tmp_path))
        found_exts = {Path(p).suffix for p, _, _ in results}
        for ext in exts:
            assert ext in found_exts, f"{ext} not found by walk_directory"
        # .png is recognized by walker but indexer will skip it (not code/text/data)
        # Here we just verify all the target exts are found
        assert len(found_exts) >= len(exts)


# ===========================================================================
# 2. VaultIndexer — basic indexing
# ===========================================================================

class TestVaultIndexerBasic:

    def test_index_python_file(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("def hello():\n    return 42\n")
        indexer = make_indexer()
        record = indexer.index_file(str(f))
        assert record is not None
        assert record.file_type == "code"
        assert record.path == str(f)

    def test_index_markdown_file(self, tmp_path):
        f = tmp_path / "README.md"
        f.write_text("# Title\n\nSome content.\n")
        indexer = make_indexer()
        record = indexer.index_file(str(f))
        assert record is not None
        assert record.file_type == "text"

    def test_index_json_file(self, tmp_path):
        f = tmp_path / "config.json"
        f.write_text('{"key": "value", "num": 42}')
        indexer = make_indexer()
        record = indexer.index_file(str(f))
        assert record is not None
        assert record.file_type == "data"

    def test_index_unsupported_file_returns_none(self, tmp_path):
        f = tmp_path / "image.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n")
        indexer = make_indexer()
        record = indexer.index_file(str(f))
        assert record is None

    def test_index_nonexistent_file_returns_none(self):
        indexer = make_indexer()
        record = indexer.index_file("/nonexistent/file.py")
        assert record is None

    def test_index_directory_summary(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.md").write_text("# Hello")
        (tmp_path / "c.json").write_text('{"k": 1}')
        (tmp_path / "skip.bin").write_bytes(b"\x00\x01")

        indexer = make_indexer()
        result = indexer.index_directory(str(tmp_path))
        assert result["files_found"] >= 3
        assert result["files_indexed"] >= 3
        assert result["duration_ms"] >= 0


# ===========================================================================
# 3. Incremental re-indexing
# ===========================================================================

class TestIncrementalIndexing:

    def test_unchanged_file_not_reprocessed(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        indexer = make_indexer()

        record1 = indexer.index_file(str(f))
        assert record1 is not None
        block_id1 = record1.block_id

        # Index again without change — should return cached record
        record2 = indexer.index_file(str(f))
        assert record2 is not None
        assert record2.block_id == block_id1, "Should return same block for unchanged file"

    def test_changed_file_gets_reindexed(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        indexer = make_indexer()
        record1 = indexer.index_file(str(f))

        f.write_text("x = 2  # changed\n")
        record2 = indexer.index_file(str(f))
        assert record2 is not None
        assert record2.block_id != record1.block_id, "New content → new block_id"

    def test_incremental_skips_reported_in_directory_index(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.py").write_text("y = 2")

        indexer = make_indexer()
        result1 = indexer.index_directory(str(tmp_path))
        assert result1["files_indexed"] >= 2

        # Second run without changes — files_indexed should equal 2 (cached) or skipped 0
        result2 = indexer.index_directory(str(tmp_path))
        # All files should still be "indexed" (returned from cache)
        assert result2["files_indexed"] >= 2


# ===========================================================================
# 4. Symbol extraction
# ===========================================================================

class TestSymbolExtraction:

    def test_python_functions_extracted(self):
        code = textwrap.dedent("""\
            def add(a, b):
                return a + b

            def subtract(a, b):
                return a - b
        """)
        parser = ASTParser()
        nodes = parser.parse_file("mod.py", code)
        names = [n.name for n in nodes]
        assert "add" in names
        assert "subtract" in names

    def test_python_class_extracted(self):
        code = "class MyClass:\n    def method(self): pass\n"
        parser = ASTParser()
        nodes = parser.parse_file("mod.py", code)
        kinds = {n.kind for n in nodes}
        names = {n.name for n in nodes}
        assert "class" in kinds
        assert "MyClass" in names

    def test_javascript_functions_extracted(self):
        code = "function greet(name) { return 'hi ' + name; }\n"
        parser = ASTParser()
        nodes = parser.parse_file("app.js", code)
        names = [n.name for n in nodes]
        assert "greet" in names

    def test_markdown_headers_extracted(self):
        table = SymbolTable()
        content = "# Title\n\n## Section One\n\n### Subsection\n"
        syms = table.index_file("readme.md", content)
        names = [s.name for s in syms]
        assert "Title" in names
        assert "Section One" in names

    def test_symbol_lookup_by_name(self):
        table = SymbolTable()
        table.index_file("mod.py", "def my_func(): pass\n")
        results = table.lookup("my_func")
        assert len(results) >= 1
        assert results[0].kind in ("function", "method", "definition")

    def test_symbol_search_case_insensitive(self):
        table = SymbolTable()
        table.index_file("mod.py", "def MyHelper(): pass\n")
        results = table.search("myhelper")
        assert len(results) >= 1


# ===========================================================================
# 5. stats_by_type
# ===========================================================================

class TestStatsByType:

    def test_stats_by_type_empty(self):
        # Use a brand-new in-memory store to avoid cross-test state
        from tokenpak.agent.vault.blocks import BlockStore
        from tokenpak.agent.vault.symbols import SymbolTable
        fresh_store = BlockStore(":memory:")
        indexer = VaultIndexer(block_store=fresh_store, symbol_table=SymbolTable())
        stats = indexer.stats_by_type()
        assert stats["total_files"] == 0
        assert stats["by_type"] == {}

    def test_stats_by_type_after_indexing(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.py").write_text("y = 2")
        (tmp_path / "c.md").write_text("# Hello")
        (tmp_path / "d.json").write_text('{"k": 1}')

        indexer = make_indexer()
        indexer.index_directory(str(tmp_path))
        stats = indexer.stats_by_type()
        assert stats["total_files"] >= 4
        assert stats["by_type"].get("code", 0) >= 2
        assert stats["by_type"].get("text", 0) >= 1
        assert stats["by_type"].get("data", 0) >= 1
        assert ".py" in stats["by_extension"]
