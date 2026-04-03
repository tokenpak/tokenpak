"""Unit tests for tokenpak.registry and tokenpak.pro.feature_matrix."""
import os
import tempfile
import pytest

# ── Registry ──────────────────────────────────────────────────────────────────
from tokenpak.registry import Block, BlockRegistry, _cleanup_all_registries


def _reg(tmp_path):
    return BlockRegistry(str(tmp_path / "test_registry.db"))


def _block(**kwargs):
    defaults = dict(
        path="src/foo.py",
        content_hash="abc123",
        version=1,
        file_type="python",
        raw_tokens=100,
        compressed_tokens=50,
        compressed_content="# compressed",
        quality_score=0.9,
        importance=5.0,
    )
    defaults.update(kwargs)
    return Block(**defaults)


# 1. BlockRegistry initializes without error
def test_registry_init(tmp_path):
    reg = _reg(tmp_path)
    assert reg is not None
    reg.close()


# 2. add_block returns a Block with version=1 for new entry
def test_add_block_new(tmp_path):
    reg = _reg(tmp_path)
    b = reg.add_block(_block())
    assert b.version == 1
    reg.close()


# 3. add_block increments version on update
def test_add_block_increments_version(tmp_path):
    reg = _reg(tmp_path)
    reg.add_block(_block())
    b2 = reg.add_block(_block(content_hash="newHash"))
    assert b2.version == 2
    reg.close()


# 4. get_block retrieves the stored block
def test_get_block(tmp_path):
    reg = _reg(tmp_path)
    reg.add_block(_block(path="a.py", content_hash="h1"))
    b = reg.get_block("a.py")
    assert b is not None
    assert b.path == "a.py"
    reg.close()


# 5. get_block returns None for unknown path
def test_get_block_missing(tmp_path):
    reg = _reg(tmp_path)
    assert reg.get_block("nope.py") is None
    reg.close()


# 6. list_blocks returns all stored blocks
def test_list_blocks(tmp_path):
    reg = _reg(tmp_path)
    reg.add_block(_block(path="a.py"))
    reg.add_block(_block(path="b.py"))
    blocks = reg.list_blocks()
    assert len(blocks) == 2
    reg.close()


# 7. list_blocks with file_type filter
def test_list_blocks_filter(tmp_path):
    reg = _reg(tmp_path)
    reg.add_block(_block(path="a.py", file_type="python"))
    reg.add_block(_block(path="b.md", file_type="markdown"))
    py = reg.list_blocks(file_type="python")
    assert len(py) == 1
    assert py[0].path == "a.py"
    reg.close()


# 8. has_changed detects new/changed content
def test_has_changed(tmp_path):
    reg = _reg(tmp_path)
    assert reg.has_changed("x.py", "hello") is True  # new
    reg.add_block(_block(path="x.py", content_hash=__import__("hashlib").sha256(b"hello").hexdigest()))
    assert reg.has_changed("x.py", "hello") is False  # unchanged
    assert reg.has_changed("x.py", "world") is True   # changed
    reg.close()


# 9. search returns matching blocks
def test_search(tmp_path):
    reg = _reg(tmp_path)
    reg.add_block(_block(path="a.py", compressed_content="def hello_world():"))
    reg.add_block(_block(path="b.py", compressed_content="import os"))
    results = reg.search("hello")
    assert len(results) == 1
    assert results[0].path == "a.py"
    reg.close()


# 10. clear removes all blocks
def test_clear(tmp_path):
    reg = _reg(tmp_path)
    reg.add_block(_block(path="a.py"))
    reg.clear()
    assert reg.list_blocks() == []
    reg.close()


# 11. get_stats returns correct totals
def test_get_stats(tmp_path):
    reg = _reg(tmp_path)
    reg.add_block(_block(path="a.py", raw_tokens=100, compressed_tokens=50))
    stats = reg.get_stats()
    assert stats["total_files"] == 1
    assert stats["total_raw_tokens"] == 100
    assert stats["total_compressed_tokens"] == 50
    reg.close()


# 12. batch_transaction commits multiple blocks atomically
def test_batch_transaction(tmp_path):
    reg = _reg(tmp_path)
    with reg.batch_transaction() as conn:
        reg.add_block_batch(_block(path="a.py"), conn)
        reg.add_block_batch(_block(path="b.py"), conn)
    assert len(reg.list_blocks()) == 2
    reg.close()


# 13. Block.slice_id auto-generated when not provided
def test_block_slice_id_autogen():
    b = _block()
    assert b.slice_id.startswith("s_")


# ── FeatureMatrix ─────────────────────────────────────────────────────────────
from tokenpak.pro.feature_matrix import FeatureMatrix, FEATURES, ADAPTERS


FM = FeatureMatrix()


# 14. anthropic supports function_calling and streaming
def test_anthropic_core_features():
    assert FM.is_supported("anthropic", "function_calling") is True
    assert FM.is_supported("anthropic", "streaming") is True


# 15. google does NOT support function_calling (base matrix)
def test_google_no_function_calling():
    assert FM.is_supported("google", "function_calling") is False


# 16. tokenpak-anthropic supports workflow (extra)
def test_tokenpak_anthropic_workflow():
    assert FM.is_supported("tokenpak-anthropic", "workflow") is True


# 17. plain anthropic does NOT support workflow
def test_anthropic_no_workflow():
    assert FM.is_supported("anthropic", "workflow") is False


# 18. unknown adapter returns False
def test_unknown_adapter():
    assert FM.is_supported("fakeprovider", "streaming") is False


# 19. unknown feature returns False
def test_unknown_feature():
    assert FM.is_supported("anthropic", "teleportation") is False


# 20. get_fallback returns non-empty string for known feature
def test_get_fallback_known():
    fb = FM.get_fallback("anthropic", "workflow")
    assert isinstance(fb, str) and len(fb) > 0


# 21. get_fallback returns fallback string for unknown feature
def test_get_fallback_unknown():
    fb = FM.get_fallback("anthropic", "fakefeat")
    assert "No fallback" in fb


# 22. get_matrix returns all adapters and features
def test_get_matrix_shape():
    matrix = FM.get_matrix()
    for adapter in ADAPTERS:
        assert adapter in matrix
        for feature in FEATURES:
            assert feature in matrix[adapter]
            assert isinstance(matrix[adapter][feature], bool)


# 23. tokenpak-google inherits google base + workflow=True
def test_tokenpak_google_workflow_true():
    assert FM.is_supported("tokenpak-google", "workflow") is True


# 24. tokenpak-google streaming still True (inherited)
def test_tokenpak_google_streaming_inherited():
    assert FM.is_supported("tokenpak-google", "streaming") is True
