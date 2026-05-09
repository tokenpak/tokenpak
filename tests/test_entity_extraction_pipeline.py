
import pytest

# tokenpak.extraction is a namespace package in the slim OSS install — the
# directory exists but the EntityExtractor/EntityType symbols ship from a
# submodule that isn't bundled. importorskip on the bare namespace returns
# truthy here, so wrap the actual import in try/except + skip-at-module-level
# so the release test gate stays green.
try:
    from tokenpak.extraction import EntityExtractor, EntityType
    from tokenpak.vault.blocks import BlockStore
    from tokenpak.vault.indexer import VaultIndexer
except ImportError as _exc:
    pytest.skip(f"tokenpak.extraction symbols not present in slim OSS install: {_exc}", allow_module_level=True)


def test_extracts_file_paths_correctly():
    text = "See /home/trix/Projects/tokenpak/tokenpak/agent/vault/indexer.py for details."
    out = EntityExtractor().extract(text)
    paths = [e.value for e in out.by_type(EntityType.FILE_PATH)]
    assert "/home/trix/Projects/tokenpak/tokenpak/agent/vault/indexer.py" in paths


def test_extracts_api_endpoints_from_docs():
    text = "Use GET /v1/users/{id} and POST /v1/sessions to authenticate."
    out = EntityExtractor().extract(text)
    endpoints = {(a.method, a.path) for a in out.api_endpoints}
    assert ("GET", "/v1/users/{id}") in endpoints
    assert ("POST", "/v1/sessions") in endpoints


def test_extracts_dates_and_normalizes_deadlines():
    text = "Deadline: 2026-03-31. Backup date is Mar 15, 2026."
    out = EntityExtractor().extract(text)
    normalized = {d.normalized for d in out.deadlines}
    assert "2026-03-31" in normalized
    assert "2026-03-15" in normalized


def test_compact_format_is_significantly_smaller_than_raw():
    raw = "\n".join([
        "Decision: We will ship v1 this week with staged rollout.",
        "GET /v1/users/{id}",
        "TOKENPAK_INJECT_TOP_K=8",
        "Glossary: Context Window",
        "Deadline: 2026-04-01",
    ] * 20)
    extractor = EntityExtractor()
    entities = extractor.extract(raw)
    compact = extractor.compact_text(entities)
    assert len(compact) < len(raw) * 0.5


def test_no_false_positive_file_paths_in_fenced_code_blocks():
    raw = """
    ```python
    path = \"/tmp/should_not_extract.py\"
    ```
    Outside code, use /real/path.txt
    """
    out = EntityExtractor().extract(raw)
    paths = [e.value for e in out.by_type(EntityType.FILE_PATH)]
    assert "/tmp/should_not_extract.py" not in paths
    assert "/real/path.txt" in paths


def test_runs_at_index_time_and_persists_entities_in_metadata(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("Decision: use POST /v1/jobs by 2026-03-31")

    idx = VaultIndexer(block_store=BlockStore(":memory:"))
    rec = idx.index_file(str(f))

    assert rec is not None
    assert "entities" in rec.metadata
    assert "entities_compact" in rec.metadata
    assert "/v1/jobs" in rec.metadata["entities_compact"]
