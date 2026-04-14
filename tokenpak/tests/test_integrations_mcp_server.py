"""Unit tests for tokenpak.sdk.integrations.claude_code.mcp_server."""

from __future__ import annotations

import json
import os
import tempfile
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.sdk.integrations.claude_code.mcp_server import (
    HANDLERS,
    TOOLS,
    _build_summary,
    _dispatch,
    _handle_extract_structured_fields,
    _handle_search_corpus,
    _handle_summarize_related_issues,
    _handle_build_context_pack,
    _handle_prepare_review_packet,
    _no_corpus_response,
    _resolve_vault_root,
    _shared_index_lock,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_index_mock(available: bool = True, search_results=None):
    """Return a VaultIndex-like mock."""
    m = MagicMock()
    m.available = available
    m.maybe_reload.return_value = None
    m.search.return_value = search_results or []
    m._get_content.return_value = "mock snippet content"
    return m


def _make_block(block_id="blk1", source_path="docs/file.md"):
    return {"block_id": block_id, "source_path": source_path}


# ---------------------------------------------------------------------------
# _resolve_vault_root
# ---------------------------------------------------------------------------

class TestResolveVaultRoot:
    def test_returns_none_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_VAULT_ROOT", raising=False)
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        assert _resolve_vault_root() is None

    def test_returns_path_when_env_valid(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        result = _resolve_vault_root()
        assert result == str(tmp_path)

    def test_returns_none_when_env_path_nonexistent(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", "/nonexistent/path/xyz")
        assert _resolve_vault_root() is None

    def test_returns_none_when_env_empty_string(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", "   ")
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        assert _resolve_vault_root() is None

    def test_plugin_config_path(self, monkeypatch, tmp_path):
        # Build a settings.json at the expected path:
        # plugin_root/../../../settings.json
        # i.e. tmp_path/a/b/c is plugin_root, settings.json at tmp_path/a/settings.json
        plugin_root = tmp_path / "a" / "b" / "c"
        plugin_root.mkdir(parents=True)
        vault_dir = tmp_path / "myvault"
        vault_dir.mkdir()
        settings = plugin_root.parent.parent.parent / "settings.json"
        settings.write_text(json.dumps({
            "pluginConfigs": {
                "tokenpak-claude-code": {
                    "vault_root": str(vault_dir)
                }
            }
        }))
        monkeypatch.delenv("TOKENPAK_VAULT_ROOT", raising=False)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        result = _resolve_vault_root()
        assert result == str(vault_dir)

    def test_plugin_config_invalid_vault_path(self, monkeypatch, tmp_path):
        plugin_root = tmp_path / "a" / "b" / "c"
        plugin_root.mkdir(parents=True)
        settings = plugin_root.parent.parent.parent / "settings.json"
        settings.write_text(json.dumps({
            "pluginConfigs": {
                "tokenpak-claude-code": {"vault_root": "/does/not/exist"}
            }
        }))
        monkeypatch.delenv("TOKENPAK_VAULT_ROOT", raising=False)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        assert _resolve_vault_root() is None


# ---------------------------------------------------------------------------
# _no_corpus_response
# ---------------------------------------------------------------------------

class TestNoCorpusResponse:
    def test_structure(self):
        resp = _no_corpus_response("search_corpus")
        assert resp["status"] == "no-corpus"
        assert resp["tool"] == "search_corpus"
        assert "hint" in resp

    def test_tool_name_propagated(self):
        resp = _no_corpus_response("build_context_pack")
        assert resp["tool"] == "build_context_pack"


# ---------------------------------------------------------------------------
# _shared_index_lock
# ---------------------------------------------------------------------------

class TestSharedIndexLock:
    def test_yields_without_error(self, tmp_path):
        with _shared_index_lock(str(tmp_path)) as result:
            assert result is None

    def test_yields_when_dir_missing(self):
        """Should not raise even if dir doesn't exist."""
        with _shared_index_lock("/nonexistent/path/abc"):
            pass  # must not raise


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    def test_five_tools_defined(self):
        assert len(TOOLS) == 5

    def test_tool_names(self):
        names = [t["name"] for t in TOOLS]
        assert names == [
            "search_corpus",
            "extract_structured_fields",
            "summarize_related_issues",
            "build_context_pack",
            "prepare_review_packet",
        ]

    def test_each_tool_has_required_fields(self):
        for tool in TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool

    def test_handlers_match_tools(self):
        tool_names = {t["name"] for t in TOOLS}
        handler_names = set(HANDLERS.keys())
        assert tool_names == handler_names


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_tools_list(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        resp = json.loads(_dispatch(req))
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        tools = resp["result"]["tools"]
        assert len(tools) == 5

    def test_initialize(self):
        req = {"jsonrpc": "2.0", "id": 99, "method": "initialize"}
        resp = json.loads(_dispatch(req))
        result = resp["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert "capabilities" in result
        assert "serverInfo" in result

    def test_unknown_method(self):
        req = {"jsonrpc": "2.0", "id": 5, "method": "bogus/method"}
        resp = json.loads(_dispatch(req))
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_tools_call_unknown_tool(self):
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "input": {}},
        }
        resp = json.loads(_dispatch(req))
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_tools_call_extract_no_vault(self):
        """extract_structured_fields works without vault; test via dispatch."""
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "extract_structured_fields",
                "input": {"text": "Decided: use BM25. GET /api/v1/search"},
            },
        }
        resp = json.loads(_dispatch(req))
        content_text = resp["result"]["content"][0]["text"]
        result = json.loads(content_text)
        assert result["status"] == "ok"

    def test_tools_call_search_no_corpus(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_VAULT_ROOT", raising=False)
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "search_corpus", "input": {"query": "test"}},
        }
        resp = json.loads(_dispatch(req))
        content_text = resp["result"]["content"][0]["text"]
        result = json.loads(content_text)
        assert result["status"] == "no-corpus"

    def test_tools_call_handler_exception_returns_error(self, monkeypatch):
        """If a handler raises, dispatch returns a JSON-RPC error."""
        def boom(_params):
            raise RuntimeError("handler exploded")

        with patch.dict(HANDLERS, {"search_corpus": boom}):
            req = {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "search_corpus", "input": {"query": "q"}},
            }
            resp = json.loads(_dispatch(req))
        assert "error" in resp
        assert resp["error"]["code"] == -32000
        assert "handler exploded" in resp["error"]["message"]

    def test_tools_call_uses_arguments_key(self):
        """params.arguments is accepted as fallback for params.input."""
        req = {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "extract_structured_fields",
                "arguments": {"text": "Hello world"},
            },
        }
        resp = json.loads(_dispatch(req))
        result = json.loads(resp["result"]["content"][0]["text"])
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# _handle_search_corpus
# ---------------------------------------------------------------------------

class TestHandleSearchCorpus:
    def test_no_corpus_when_vault_unset(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_VAULT_ROOT", raising=False)
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        result = _handle_search_corpus({"query": "hello"})
        assert result["status"] == "no-corpus"
        assert result["tool"] == "search_corpus"

    def test_error_on_empty_query(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        result = _handle_search_corpus({"query": ""})
        assert result["status"] == "error"
        assert "query" in result["error"].lower()

    def test_no_index_when_index_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        mock_index = _make_index_mock(available=False)
        with patch("tokenpak.vault.retrieval.vault_index.VaultIndex", return_value=mock_index):
            result = _handle_search_corpus({"query": "test"})
        assert result["status"] == "no-index"
        assert result["results"] == []

    def test_returns_results_when_index_available(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        block = _make_block()
        mock_index = _make_index_mock(search_results=[(block, 0.95)])
        with patch("tokenpak.vault.retrieval.vault_index.VaultIndex", return_value=mock_index):
            result = _handle_search_corpus({"query": "search term", "top_k": 3})
        assert result["status"] == "ok"
        assert result["query"] == "search term"
        assert result["top_k"] == 3
        assert len(result["results"]) == 1
        hit = result["results"][0]
        assert hit["block_id"] == "blk1"
        assert hit["source_path"] == "docs/file.md"
        assert hit["score"] == 0.95
        assert "snippet" in hit

    def test_default_top_k_is_five(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        mock_index = _make_index_mock(search_results=[])
        with patch("tokenpak.vault.retrieval.vault_index.VaultIndex", return_value=mock_index):
            result = _handle_search_corpus({"query": "test"})
        assert result["top_k"] == 5


# ---------------------------------------------------------------------------
# _handle_extract_structured_fields
# ---------------------------------------------------------------------------

class TestHandleExtractStructuredFields:
    def test_error_on_empty_text(self):
        result = _handle_extract_structured_fields({"text": ""})
        assert result["status"] == "error"

    def test_returns_ok_with_entities(self):
        text = "Decided: use BM25. Deadline: 2026-04-15. GET /api/v1/search"
        result = _handle_extract_structured_fields({"text": text})
        assert result["status"] == "ok"
        assert "entities" in result
        assert result["source_len"] == len(text)

    def test_type_filter_limits_entity_keys(self):
        text = "Decided: use BM25. GET /api/v1/search"
        result = _handle_extract_structured_fields(
            {"text": text, "types": ["decision"]}
        )
        assert result["status"] == "ok"
        entities = result["entities"]
        # Only "decisions" key should survive
        for key in entities:
            assert key == "decisions", f"Unexpected key: {key}"

    def test_type_filter_api_endpoint(self):
        text = "GET /api/v1/search POST /api/v1/ingest"
        result = _handle_extract_structured_fields(
            {"text": text, "types": ["api_endpoint"]}
        )
        assert result["status"] == "ok"
        assert "api_endpoints" in result["entities"]
        # No other keys
        for k in result["entities"]:
            assert k == "api_endpoints"

    def test_source_len_matches_input(self):
        text = "Hello world"
        result = _handle_extract_structured_fields({"text": text})
        assert result["source_len"] == len(text)


# ---------------------------------------------------------------------------
# _handle_summarize_related_issues
# ---------------------------------------------------------------------------

class TestHandleSummarizeRelatedIssues:
    def test_no_corpus_when_vault_unset(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_VAULT_ROOT", raising=False)
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        result = _handle_summarize_related_issues({"query": "test"})
        assert result["status"] == "no-corpus"

    def test_error_on_empty_query(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        result = _handle_summarize_related_issues({"query": ""})
        assert result["status"] == "error"

    def test_no_index_returns_empty_related(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        mock_index = _make_index_mock(available=False)
        with patch("tokenpak.vault.retrieval.vault_index.VaultIndex", return_value=mock_index):
            with patch("tokenpak.vault.search.extract_must_hit_terms", return_value=[]):
                result = _handle_summarize_related_issues({"query": "test"})
        assert result["status"] == "no-index"
        assert result["related"] == []

    def test_returns_related_with_symbols(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        block = _make_block(block_id="b1", source_path="issues/TICKET-1.md")
        mock_index = _make_index_mock(search_results=[(block, 0.8)])
        mock_index._get_content.return_value = "BM25 retrieval decision"

        with patch("tokenpak.vault.retrieval.vault_index.VaultIndex", return_value=mock_index):
            with patch(
                "tokenpak.vault.search.extract_must_hit_terms",
                return_value=["BM25"],
            ):
                result = _handle_summarize_related_issues({"query": "BM25 search"})

        assert result["status"] == "ok"
        assert len(result["related"]) == 1
        hit = result["related"][0]
        assert hit["source_path"] == "issues/TICKET-1.md"
        assert hit["score"] == 0.8
        assert "BM25" in hit["symbols"]


# ---------------------------------------------------------------------------
# _build_summary
# ---------------------------------------------------------------------------

class TestBuildSummary:
    def test_empty_inputs_return_empty_lists(self):
        summary = _build_summary([], {}, [])
        assert summary["key_facts"] == []
        assert summary["risks"] == []
        assert summary["constraints"] == []
        assert summary["links"] == []
        assert summary["next_actions"] == []

    def test_decisions_appear_in_key_facts(self):
        entities = {"decisions": ["Use BM25", "Keep it simple"]}
        summary = _build_summary([], entities, [])
        assert "Use BM25" in summary["key_facts"]

    def test_deadlines_appear_in_risks(self):
        entities = {"deadlines": ["2026-04-15"]}
        summary = _build_summary([], entities, [])
        assert any("2026-04-15" in r for r in summary["risks"])

    def test_api_endpoints_appear_in_links(self):
        entities = {"api_endpoints": ["GET /api/v1/search"]}
        summary = _build_summary([], entities, [])
        assert "GET /api/v1/search" in summary["links"]

    def test_file_paths_appear_in_constraints(self):
        entities = {"file_paths": ["/etc/config.json"]}
        summary = _build_summary([], entities, [])
        assert "/etc/config.json" in summary["constraints"]

    def test_related_source_paths_in_next_actions(self):
        related = [{"source_path": "docs/ticket.md", "score": 0.9, "snippet": "abc", "symbols": []}]
        summary = _build_summary([], {}, related)
        assert any("docs/ticket.md" in a for a in summary["next_actions"])

    def test_corpus_snippets_in_key_facts(self):
        hits = [{"snippet": "First line.\nSecond line.", "source_path": "docs/a.md", "score": 0.9}]
        summary = _build_summary(hits, {}, [])
        assert any("First line." in kf for kf in summary["key_facts"])

    def test_capped_at_limits(self):
        entities = {"decisions": [f"decision-{i}" for i in range(20)]}
        summary = _build_summary([], entities, [])
        assert len(summary["key_facts"]) <= 8

    def test_risks_capped(self):
        entities = {"deadlines": [f"2026-04-{i:02d}" for i in range(1, 15)]}
        summary = _build_summary([], entities, [])
        assert len(summary["risks"]) <= 5


# ---------------------------------------------------------------------------
# _handle_build_context_pack
# ---------------------------------------------------------------------------

class TestHandleBuildContextPack:
    def test_no_corpus_when_vault_unset(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_VAULT_ROOT", raising=False)
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        result = _handle_build_context_pack({"query": "test"})
        assert result["status"] == "no-corpus"

    def test_error_on_empty_query(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        result = _handle_build_context_pack({"query": ""})
        assert result["status"] == "error"

    def test_returns_full_structure(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        block = _make_block()
        mock_index = _make_index_mock(search_results=[(block, 0.7)])
        mock_index._get_content.return_value = "Some relevant snippet"

        with patch("tokenpak.vault.retrieval.vault_index.VaultIndex", return_value=mock_index):
            with patch(
                "tokenpak.vault.search.extract_must_hit_terms",
                return_value=[],
            ):
                result = _handle_build_context_pack({"query": "vault search"})

        assert result["status"] == "ok"
        assert result["query"] == "vault search"
        assert "corpus_hits" in result
        assert "entities" in result
        assert "related_issues" in result
        assert "summary" in result

    def test_include_related_false_skips_related(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        mock_index = _make_index_mock(search_results=[])
        with patch("tokenpak.vault.retrieval.vault_index.VaultIndex", return_value=mock_index):
            result = _handle_build_context_pack(
                {"query": "vault search", "include_related": False}
            )
        assert result.get("related_issues") == []


# ---------------------------------------------------------------------------
# _handle_prepare_review_packet
# ---------------------------------------------------------------------------

class TestHandlePrepareReviewPacket:
    def test_no_corpus_when_vault_unset(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_VAULT_ROOT", raising=False)
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        result = _handle_prepare_review_packet({"branch": "main"})
        assert result["status"] == "no-corpus"

    def test_returns_full_structure(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        block = _make_block()
        mock_index = _make_index_mock(search_results=[(block, 0.6)])
        mock_index._get_content.return_value = "Corpus block snippet"

        mock_policy = MagicMock()
        mock_policy.compact_block.return_value = "compacted text"
        mock_policy.to_dict.return_value = {"compaction": {"mode": "balanced"}}

        with patch("tokenpak.vault.retrieval.vault_index.VaultIndex", return_value=mock_index):
            with patch(
                "tokenpak.vault.search.extract_must_hit_terms",
                return_value=[],
            ):
                with patch(
                    "tokenpak.compression.budgets.policy.CompactionPolicy"
                ) as MockCP:
                    MockCP.default.return_value = mock_policy
                    result = _handle_prepare_review_packet({"branch": "feature/abc"})

        assert result["status"] == "ok"
        assert result["branch"] == "feature/abc"
        assert "corpus_hits" in result
        assert "entities" in result
        assert "related_issues" in result
        assert "compacted_context" in result
        assert "summary" in result
        assert "policy" in result

    def test_diff_summary_computed_from_diff(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        mock_index = _make_index_mock(search_results=[])
        mock_policy = MagicMock()
        mock_policy.compact_block.return_value = ""
        mock_policy.to_dict.return_value = {"compaction": {}}
        diff_text = "+added line\n-removed line\n--- old\n+++ new"

        with patch("tokenpak.vault.retrieval.vault_index.VaultIndex", return_value=mock_index):
            with patch(
                "tokenpak.vault.search.extract_must_hit_terms",
                return_value=[],
            ):
                with patch(
                    "tokenpak.compression.budgets.policy.CompactionPolicy"
                ) as MockCP:
                    MockCP.default.return_value = mock_policy
                    result = _handle_prepare_review_packet({"diff": diff_text})

        assert "+1/-1" in result["diff_summary"]

    def test_empty_params_default_branch_head(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        mock_index = _make_index_mock(search_results=[])
        mock_policy = MagicMock()
        mock_policy.compact_block.return_value = ""
        mock_policy.to_dict.return_value = {"compaction": {}}

        with patch("tokenpak.vault.retrieval.vault_index.VaultIndex", return_value=mock_index):
            with patch(
                "tokenpak.vault.search.extract_must_hit_terms",
                return_value=[],
            ):
                with patch(
                    "tokenpak.compression.budgets.policy.CompactionPolicy"
                ) as MockCP:
                    MockCP.default.return_value = mock_policy
                    result = _handle_prepare_review_packet({})

        assert result["branch"] == "HEAD"

    def test_file_param_used_as_query(self, monkeypatch, tmp_path):
        """When only file= is given, it becomes the BM25 query."""
        monkeypatch.setenv("TOKENPAK_VAULT_ROOT", str(tmp_path))
        mock_index = _make_index_mock(search_results=[])
        mock_policy = MagicMock()
        mock_policy.compact_block.return_value = ""
        mock_policy.to_dict.return_value = {"compaction": {}}

        with patch("tokenpak.vault.retrieval.vault_index.VaultIndex", return_value=mock_index):
            with patch(
                "tokenpak.vault.search.extract_must_hit_terms",
                return_value=[],
            ):
                with patch(
                    "tokenpak.compression.budgets.policy.CompactionPolicy"
                ) as MockCP:
                    MockCP.default.return_value = mock_policy
                    result = _handle_prepare_review_packet({"file": "src/core.py"})

        assert result["file"] == "src/core.py"
        assert result["status"] == "ok"
