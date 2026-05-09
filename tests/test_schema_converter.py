from __future__ import annotations

import pytest

pytest.importorskip("tokenpak._internal.ingest.schema_converter", reason="module not available in current build")
from tokenpak._internal.ingest.schema_converter import (
    SCHEMAS,
    convert_document,
    detect_document_type,
    extract_schema,
    should_serve_schema,
)

from tokenpak.vault.blocks import BlockStore
from tokenpak.vault.indexer import VaultIndexer
from tokenpak.vault.retrieval import inject_retrieved_context
from tokenpak.vault.symbols import SymbolTable


def test_schemas_include_required_document_types() -> None:
    expected = {
        "contract",
        "research_paper",
        "proposal",
        "design_doc",
        "meeting_notes",
        "bug_report",
        "changelog",
    }
    assert expected.issubset(SCHEMAS.keys())


def test_detect_document_type_uses_filename_and_content() -> None:
    content = """
    Scope: migrate billing API
    Timeline: 6 weeks
    Price: $14,000
    """
    assert detect_document_type(content, filename="acme_proposal.md") == "proposal"


def test_extract_schema_contract_fields() -> None:
    content = """
    Parties: Alpha LLC and Beta Inc
    Dates: Effective 2026-01-01
    Payment Terms: Net 30
    Termination: 30-day written notice
    Obligations: Maintain SLA
    """
    out = extract_schema(content, "contract")
    assert out is not None
    assert out["parties"].startswith("Alpha LLC")
    assert out["payment_terms"] == "Net 30"
    assert out["termination"].startswith("30-day")


def test_convert_document_fallback_for_unknown_type() -> None:
    content = "shopping list\n- apples\n- oranges"
    out = convert_document(content, filename="random.bin")
    assert out == {"doc_type": None, "schema": None}


def test_indexer_stores_doc_type_and_schema_metadata() -> None:
    store = BlockStore(":memory:")
    indexer = VaultIndexer(block_store=store, symbol_table=SymbolTable())

    content = """
    Changelog
    Version: 1.4.0
    Date: 2026-03-01
    Added: budget telemetry
    Changed: retry behavior
    Fixed: stale cache key
    Removed: legacy endpoint
    """
    record = indexer.index_file("/tmp/changelog.md", content=content)
    assert record is not None
    assert record.metadata["doc_type"] == "changelog"
    assert isinstance(record.metadata["schema"], dict)
    assert record.metadata["schema"]["version"] == "1.4.0"


def test_retrieval_prefers_schema_when_intent_allows() -> None:
    results = [
        (
            {
                "source_path": "docs/proposal.md",
                "content": "raw compressed text",
                "metadata": {
                    "doc_type": "proposal",
                    "schema": {
                        "client": "ACME",
                        "scope": "Data migration",
                    },
                },
            },
            5.0,
        )
    ]

    text, _tokens, _refs = inject_retrieved_context(results, intent="summarize this file")
    assert '"doc_type": "proposal"' in text
    assert '"client": "ACME"' in text
    assert "raw compressed text" not in text


def test_retrieval_uses_raw_when_verbatim_intent_requested() -> None:
    results = [
        (
            {
                "source_path": "docs/proposal.md",
                "content": "raw compressed text",
                "metadata": {"doc_type": "proposal", "schema": {"client": "ACME"}},
            },
            5.0,
        )
    ]

    text, _tokens, _refs = inject_retrieved_context(results, intent="quote exact wording")
    assert "raw compressed text" in text


def test_should_serve_schema_blocks_exact_quote_requests() -> None:
    assert should_serve_schema("give me a summary") is True
    assert should_serve_schema("quote exact text") is False
