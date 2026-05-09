"""test_registry.py — Unit tests for tokenpak/registry.py public API.

Covers: BlockRegistry add/get/list, has_changed, search, get_stats,
        clear, close, batch_transaction, Block dataclass, version bump,
        context manager protocol, duplicate entries, and edge cases.
"""
import threading
import time

import pytest

from tokenpak.core.registry import Block, BlockRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_block(path: str = "test/file.md", content: str = "hello world") -> Block:
    """Create a minimal valid Block for testing."""
    import hashlib
    return Block(
        path=path,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        version=1,
        file_type="md",
        raw_tokens=10,
        compressed_tokens=5,
        compressed_content=content,
        quality_score=0.9,
        importance=5.0,
        processed_at=time.time(),
    )


@pytest.fixture
def registry(tmp_path):
    """Fresh in-memory-like registry using a temp db."""
    db = str(tmp_path / "test_registry.db")
    reg = BlockRegistry(db_path=db)
    yield reg
    reg.close()


# ---------------------------------------------------------------------------
# Block dataclass
# ---------------------------------------------------------------------------

def test_block_slice_id_auto_generated():
    """Block auto-generates slice_id when not provided."""
    b = _make_block()
    assert b.slice_id.startswith("s_")
    assert len(b.slice_id) == 10  # "s_" + 8 hex chars


def test_block_slice_id_custom():
    """Block preserves custom slice_id."""
    b = _make_block()
    b.slice_id = "custom_slice"
    assert b.slice_id == "custom_slice"


def test_block_defaults():
    """Block sets sensible defaults for quality_score and importance."""
    import hashlib
    b = Block(
        path="a.md",
        content_hash=hashlib.sha256(b"x").hexdigest(),
        version=1,
        file_type="md",
        raw_tokens=5,
        compressed_tokens=3,
        compressed_content="x",
    )
    assert b.quality_score == 1.0
    assert b.importance == 5.0


# ---------------------------------------------------------------------------
# add_block / get_block round-trip
# ---------------------------------------------------------------------------

def test_add_and_get_block(registry):
    """add_block stores a block; get_block retrieves it by path."""
    b = _make_block("docs/readme.md", "Some content")
    registry.add_block(b)

    retrieved = registry.get_block("docs/readme.md")
    assert retrieved is not None
    assert retrieved.path == "docs/readme.md"
    assert retrieved.compressed_content == "Some content"
    assert retrieved.file_type == "md"


def test_get_block_missing_returns_none(registry):
    """get_block returns None for unknown paths."""
    result = registry.get_block("nonexistent/path.md")
    assert result is None


def test_add_block_version_bump_on_update(registry):
    """Re-adding a block at the same path increments version."""
    b = _make_block("src/main.py", "v1 content")
    registry.add_block(b)

    b2 = _make_block("src/main.py", "v2 content")
    registry.add_block(b2)

    retrieved = registry.get_block("src/main.py")
    assert retrieved.version == 2
    assert retrieved.compressed_content == "v2 content"


def test_add_block_first_version_is_one(registry):
    """New block starts at version 1."""
    b = _make_block()
    registry.add_block(b)
    retrieved = registry.get_block(b.path)
    assert retrieved.version == 1


# ---------------------------------------------------------------------------
# list_blocks
# ---------------------------------------------------------------------------

def test_list_blocks_empty(registry):
    """list_blocks returns empty list when registry is empty."""
    assert registry.list_blocks() == []


def test_list_blocks_all(registry):
    """list_blocks returns all added blocks."""
    registry.add_block(_make_block("a.md", "aaa"))
    registry.add_block(_make_block("b.md", "bbb"))
    registry.add_block(_make_block("c.py", "ccc"))

    all_blocks = registry.list_blocks()
    paths = {b.path for b in all_blocks}
    assert paths == {"a.md", "b.md", "c.py"}


def test_list_blocks_filtered_by_type(registry):
    """list_blocks(file_type=...) returns only matching type."""
    import hashlib

    def _block(path, ftype):
        return Block(
            path=path,
            content_hash=hashlib.sha256(path.encode()).hexdigest(),
            version=1,
            file_type=ftype,
            raw_tokens=4,
            compressed_tokens=2,
            compressed_content="x",
        )

    registry.add_block(_block("a.md", "md"))
    registry.add_block(_block("b.md", "md"))
    registry.add_block(_block("c.py", "py"))

    md_blocks = registry.list_blocks(file_type="md")
    assert len(md_blocks) == 2
    assert all(b.file_type == "md" for b in md_blocks)

    py_blocks = registry.list_blocks(file_type="py")
    assert len(py_blocks) == 1


# ---------------------------------------------------------------------------
# has_changed
# ---------------------------------------------------------------------------

def test_has_changed_new_file(registry):
    """has_changed returns True for a path not yet in the registry."""
    assert registry.has_changed("new/file.md", "any content") is True


def test_has_changed_same_content(registry):
    """has_changed returns False when content matches stored hash."""
    import hashlib
    content = "stable content"
    b = Block(
        path="stable.md",
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        version=1,
        file_type="md",
        raw_tokens=3,
        compressed_tokens=2,
        compressed_content=content,
    )
    registry.add_block(b)
    assert registry.has_changed("stable.md", content) is False


def test_has_changed_different_content(registry):
    """has_changed returns True when content differs from stored hash."""
    import hashlib
    content = "original content"
    b = Block(
        path="changing.md",
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        version=1,
        file_type="md",
        raw_tokens=3,
        compressed_tokens=2,
        compressed_content=content,
    )
    registry.add_block(b)
    assert registry.has_changed("changing.md", "modified content") is True


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def test_search_finds_matching_content(registry):
    """search returns blocks whose compressed_content contains query terms."""
    registry.add_block(_make_block("a.md", "tokenpak compression algorithm"))
    registry.add_block(_make_block("b.md", "unrelated documentation"))
    registry.add_block(_make_block("c.md", "tokenpak proxy performance"))

    results = registry.search("tokenpak")
    paths = {b.path for b in results}
    assert "a.md" in paths
    assert "c.md" in paths
    assert "b.md" not in paths


def test_search_empty_query(registry):
    """search with empty string returns no results."""
    registry.add_block(_make_block("a.md", "some content"))
    results = registry.search("")
    assert results == []


def test_search_no_results(registry):
    """search returns empty list when no blocks match."""
    registry.add_block(_make_block("a.md", "hello world"))
    results = registry.search("zzz_nonexistent_term")
    assert results == []


def test_search_top_k_limit(registry):
    """search respects top_k parameter."""
    for i in range(10):
        registry.add_block(_make_block(f"file{i}.md", f"keyword content {i}"))

    results = registry.search("keyword", top_k=3)
    assert len(results) <= 3


def test_search_path_match(registry):
    """search also matches against block path."""
    registry.add_block(_make_block("special/path/readme.md", "generic text"))
    results = registry.search("special")
    assert any(b.path == "special/path/readme.md" for b in results)


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

def test_get_stats_empty(registry):
    """get_stats on empty registry returns zeros."""
    stats = registry.get_stats()
    assert stats["total_files"] == 0
    assert stats["total_raw_tokens"] == 0
    assert stats["total_compressed_tokens"] == 0
    assert stats["compression_ratio"] == 0


def test_get_stats_with_data(registry):
    """get_stats reflects added blocks correctly."""
    import hashlib

    def _b(path, ftype, raw, comp):
        return Block(
            path=path,
            content_hash=hashlib.sha256(path.encode()).hexdigest(),
            version=1,
            file_type=ftype,
            raw_tokens=raw,
            compressed_tokens=comp,
            compressed_content="x",
        )

    registry.add_block(_b("a.md", "md", 100, 50))
    registry.add_block(_b("b.md", "md", 200, 80))
    registry.add_block(_b("c.py", "py", 150, 60))

    stats = registry.get_stats()
    assert stats["total_files"] == 3
    assert stats["total_raw_tokens"] == 450
    assert stats["total_compressed_tokens"] == 190
    assert stats["compression_ratio"] == round(450 / 190, 2)
    assert "md" in stats["by_type"]
    assert stats["by_type"]["md"]["files"] == 2


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------

def test_clear_removes_all_blocks(registry):
    """clear() deletes all blocks from registry."""
    registry.add_block(_make_block("a.md", "aaa"))
    registry.add_block(_make_block("b.md", "bbb"))
    registry.clear()
    assert registry.list_blocks() == []


def test_clear_then_add(registry):
    """After clear, new blocks can be added normally."""
    registry.add_block(_make_block("a.md", "aaa"))
    registry.clear()
    registry.add_block(_make_block("new.md", "fresh"))
    assert registry.get_block("new.md") is not None


# ---------------------------------------------------------------------------
# batch_transaction
# ---------------------------------------------------------------------------

def test_batch_transaction_commits_all(registry):
    """batch_transaction commits all operations atomically."""
    blocks = [_make_block(f"file{i}.md", f"content {i}") for i in range(5)]
    with registry.batch_transaction() as conn:
        for b in blocks:
            registry.add_block_batch(b, conn)

    all_blocks = registry.list_blocks()
    assert len(all_blocks) == 5


def test_batch_transaction_rollback_on_error(registry, tmp_path):
    """batch_transaction rolls back on exception; no partial writes."""
    registry.add_block(_make_block("existing.md", "safe"))

    try:
        with registry.batch_transaction() as conn:
            registry.add_block_batch(_make_block("new1.md", "x"), conn)
            registry.add_block_batch(_make_block("new2.md", "y"), conn)
            raise ValueError("Simulated failure")
    except ValueError:
        pass

    # Only the pre-batch block should be present
    assert registry.get_block("new1.md") is None
    assert registry.get_block("new2.md") is None
    assert registry.get_block("existing.md") is not None


# ---------------------------------------------------------------------------
# close / context manager
# ---------------------------------------------------------------------------

def test_context_manager(tmp_path):
    """BlockRegistry works as a context manager; closes cleanly."""
    db = str(tmp_path / "ctx_test.db")
    with BlockRegistry(db_path=db) as reg:
        reg.add_block(_make_block("ctx.md", "context manager test"))
        assert reg.get_block("ctx.md") is not None
    # After exit, should be closed
    assert reg._closed is True


def test_close_idempotent(registry):
    """Calling close() multiple times does not raise."""
    registry.close()
    registry.close()  # Should not raise


def test_closed_registry_raises(tmp_path):
    """Operations on a closed registry raise RuntimeError."""
    db = str(tmp_path / "closed.db")
    reg = BlockRegistry(db_path=db)
    reg.close()
    with pytest.raises(RuntimeError, match="closed"):
        reg.get_block("any.md")


# ---------------------------------------------------------------------------
# Thread safety (basic)
# ---------------------------------------------------------------------------

def test_concurrent_adds(tmp_path):
    """Multiple threads can add blocks without corruption."""
    db = str(tmp_path / "thread_test.db")
    reg = BlockRegistry(db_path=db)
    errors = []

    def add_blocks(thread_id):
        try:
            for i in range(5):
                b = _make_block(f"thread{thread_id}/file{i}.md", f"content {thread_id} {i}")
                reg.add_block(b)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=add_blocks, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    reg.close()
    assert errors == [], f"Thread errors: {errors}"
