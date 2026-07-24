# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak/precompute.py — Intent-specific precomputation pipeline."""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.precompute", reason="module not available in current build")
import json

import pytest
from tokenpak.precompute import (
    DocType,
    PrecomputedArtifact,
    PrecomputeStore,
    detect_doc_type,
    generate_error_signature,
    generate_fact_card,
    generate_feature_table,
    generate_project_snapshot,
    get_precomputed_artifact,
    precompute_for_block,
    recompute_all,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_artifacts(tmp_path):
    """Temporary artifacts directory."""
    return tmp_path / "artifacts"


NARRATIVE_CONTENT = """
# Introduction to TokenPak

TokenPak is a context-management proxy that reduces token usage.

## Key Features

- **Budget control**: enforces per-session token limits
- **Compression**: summarizes long documents automatically
- **Retrieval**: vault-based context injection

## How it works

The proxy intercepts LLM requests and applies a budget decision.
"""

ERROR_LOG_CONTENT = """
2026-03-10 08:00:01 ERROR: ConnectionError: failed to reach api.example.com
Caused by: DNS resolution timeout after 30s

Traceback (most recent call last):
  File "proxy.py", line 42, in connect
    socket.connect(addr)
ConnectionError: [Errno 111] Connection refused

2026-03-10 08:01:15 FATAL: OutOfMemoryError: heap exhausted at 2GB
Because: too many concurrent requests without eviction
"""

COMPARISON_CONTENT = """
# Model Comparison

| Model     | Speed  | Cost     | Quality |
|-----------|--------|----------|---------|
| GPT-4o    | Fast   | $5/1M    | High    |
| Sonnet    | Medium | $3/1M    | High    |
| Haiku     | Fast   | $0.25/1M | Medium  |

## Pros and Cons

- GPT-4o: excellent reasoning vs. higher cost
- Sonnet: good balance compared to others
"""

PLAN_CONTENT = """
# Sprint 12 Plan

## Status
On track. 4/7 stories completed.

## Blockers
- Blocked by: auth service deployment
- Issue: rate limit on test environment

## Next Steps
- Complete token eviction PR
- Deploy to staging
- Run load tests
"""


# ---------------------------------------------------------------------------
# Test 1: detect_doc_type
# ---------------------------------------------------------------------------


class TestDetectDocType:
    def test_code_risk_class(self):
        assert detect_doc_type("x = 1", risk_class="code") == DocType.CODE

    def test_config_risk_class(self):
        assert detect_doc_type("key: value", risk_class="config") == DocType.CONFIG

    def test_error_log_detection(self):
        result = detect_doc_type(ERROR_LOG_CONTENT, risk_class="narrative")
        assert result == DocType.ERROR_LOG

    def test_comparison_detection(self):
        result = detect_doc_type(COMPARISON_CONTENT, risk_class="narrative")
        assert result == DocType.COMPARISON

    def test_project_plan_detection(self):
        result = detect_doc_type(PLAN_CONTENT, risk_class="narrative")
        assert result == DocType.PROJECT_PLAN

    def test_changelog_by_filename(self):
        result = detect_doc_type(
            "## v1.2\n- Added feature X\n- Fixed bug Y",
            risk_class="narrative",
            source_path="CHANGELOG.md",
        )
        assert result == DocType.CHANGELOG

    def test_narrative_fallback(self):
        result = detect_doc_type("This is a simple document about things.", risk_class="narrative")
        assert result == DocType.NARRATIVE


# ---------------------------------------------------------------------------
# Test 2: Artifact generators
# ---------------------------------------------------------------------------


class TestGenerators:
    def test_fact_card_has_content(self):
        art = generate_fact_card("blk1", NARRATIVE_CONTENT, DocType.NARRATIVE, "README.md")
        assert art.artifact_type == "fact_card"
        assert art.intent == "query"
        assert "FACT CARD" in art.content
        assert art.token_estimate > 0
        assert art.block_id == "blk1"

    def test_fact_card_extracts_headings(self):
        art = generate_fact_card("blk1", NARRATIVE_CONTENT, DocType.NARRATIVE)
        # Should pick up headings
        assert (
            "Introduction" in art.content or "Features" in art.content or "TokenPak" in art.content
        )

    def test_feature_table_parses_markdown_table(self):
        art = generate_feature_table("blk2", COMPARISON_CONTENT, DocType.COMPARISON, "compare.md")
        assert art.artifact_type == "feature_table"
        assert art.intent == "explain"
        assert "FEATURE TABLE" in art.content
        # Should contain at least one row of data
        assert "GPT-4o" in art.content or "Sonnet" in art.content or "Haiku" in art.content

    def test_feature_table_row_count(self):
        art = generate_feature_table("blk2", COMPARISON_CONTENT, DocType.COMPARISON)
        assert art.metadata["row_count"] > 0

    def test_error_signature_extracts_errors(self):
        art = generate_error_signature("blk3", ERROR_LOG_CONTENT, DocType.ERROR_LOG, "app.log")
        assert art.artifact_type == "error_signature"
        assert art.intent == "debug"
        assert "ERROR SIGNATURES" in art.content
        assert art.metadata["signature_count"] >= 2

    def test_error_signature_includes_cause(self):
        art = generate_error_signature("blk3", ERROR_LOG_CONTENT, DocType.ERROR_LOG)
        # Should link at least one cause
        assert "CAUSE" in art.content or "caused by" in art.content.lower()

    def test_error_signature_no_errors_fallback(self):
        art = generate_error_signature("blk4", "hello world", DocType.NARRATIVE)
        assert "no distinct error signatures" in art.content.lower()

    def test_project_snapshot_extracts_sections(self):
        art = generate_project_snapshot("blk5", PLAN_CONTENT, DocType.PROJECT_PLAN, "sprint.md")
        assert art.artifact_type == "project_snapshot"
        assert art.intent == "plan"
        assert "PROJECT SNAPSHOT" in art.content
        assert "Status" in art.content
        assert "Blockers" in art.content
        assert "Next Steps" in art.content

    def test_project_snapshot_metadata(self):
        art = generate_project_snapshot("blk5", PLAN_CONTENT, DocType.PROJECT_PLAN)
        assert art.metadata["has_status"] is True
        assert art.metadata["blocker_count"] >= 1
        assert art.metadata["next_step_count"] >= 1


# ---------------------------------------------------------------------------
# Test 3: PrecomputeStore
# ---------------------------------------------------------------------------


class TestPrecomputeStore:
    def test_save_and_load(self, tmp_artifacts):
        store = PrecomputeStore(tmp_artifacts)
        art = generate_fact_card("myblock", NARRATIVE_CONTENT, DocType.NARRATIVE)
        store.save(art)
        loaded = store.load("fact_card", "myblock")
        assert loaded is not None
        assert loaded.block_id == "myblock"
        assert loaded.artifact_type == "fact_card"
        assert loaded.content == art.content

    def test_exists_returns_true_after_save(self, tmp_artifacts):
        store = PrecomputeStore(tmp_artifacts)
        art = generate_fact_card("blk_exists", NARRATIVE_CONTENT, DocType.NARRATIVE)
        store.save(art)
        assert store.exists("fact_card", "blk_exists") is True

    def test_exists_false_before_save(self, tmp_artifacts):
        store = PrecomputeStore(tmp_artifacts)
        assert store.exists("fact_card", "ghost_block") is False

    def test_load_missing_returns_none(self, tmp_artifacts):
        store = PrecomputeStore(tmp_artifacts)
        assert store.load("fact_card", "nonexistent") is None

    def test_delete(self, tmp_artifacts):
        store = PrecomputeStore(tmp_artifacts)
        art = generate_fact_card("del_blk", NARRATIVE_CONTENT, DocType.NARRATIVE)
        store.save(art)
        assert store.delete("fact_card", "del_blk") is True
        assert store.exists("fact_card", "del_blk") is False

    def test_list_block_artifacts(self, tmp_artifacts):
        store = PrecomputeStore(tmp_artifacts)
        art1 = generate_fact_card("multi_blk", NARRATIVE_CONTENT, DocType.NARRATIVE)
        art2 = generate_error_signature("multi_blk", ERROR_LOG_CONTENT, DocType.ERROR_LOG)
        store.save(art1)
        store.save(art2)
        types = store.list_block_artifacts("multi_blk")
        assert "fact_card" in types
        assert "error_signature" in types

    def test_json_roundtrip(self, tmp_artifacts):
        store = PrecomputeStore(tmp_artifacts)
        art = generate_project_snapshot("snap_blk", PLAN_CONTENT, DocType.PROJECT_PLAN)
        store.save(art)
        path = store._path("project_snapshot", "snap_blk")
        raw = json.loads(path.read_text())
        assert raw["artifact_type"] == "project_snapshot"
        assert raw["intent"] == "plan"
        assert "block_id" in raw


# ---------------------------------------------------------------------------
# Test 4: precompute_for_block + get_precomputed_artifact (integration)
# ---------------------------------------------------------------------------


class TestPrecomputeIntegration:
    def test_precompute_narrative_generates_fact_card(self, tmp_artifacts):
        arts = precompute_for_block(
            "nav_blk",
            NARRATIVE_CONTENT,
            risk_class="narrative",
            source_path="README.md",
            artifacts_dir=tmp_artifacts,
        )
        types = [a.artifact_type for a in arts]
        assert "fact_card" in types

    def test_precompute_protected_skips(self, tmp_artifacts):
        arts = precompute_for_block(
            "prot_blk", "secret content", risk_class="protected", artifacts_dir=tmp_artifacts
        )
        assert arts == []

    def test_precompute_idempotent(self, tmp_artifacts):
        # First run generates
        arts1 = precompute_for_block(
            "idem_blk", NARRATIVE_CONTENT, risk_class="narrative", artifacts_dir=tmp_artifacts
        )
        # Second run skips (artifacts already exist)
        arts2 = precompute_for_block(
            "idem_blk", NARRATIVE_CONTENT, risk_class="narrative", artifacts_dir=tmp_artifacts
        )
        assert len(arts1) > 0
        assert len(arts2) == 0  # Already existed

    def test_precompute_force_regenerates(self, tmp_artifacts):
        precompute_for_block(
            "force_blk", NARRATIVE_CONTENT, risk_class="narrative", artifacts_dir=tmp_artifacts
        )
        arts = precompute_for_block(
            "force_blk",
            NARRATIVE_CONTENT,
            risk_class="narrative",
            artifacts_dir=tmp_artifacts,
            force=True,
        )
        assert len(arts) > 0

    def test_get_precomputed_artifact_query(self, tmp_artifacts):
        precompute_for_block(
            "qa_blk", NARRATIVE_CONTENT, risk_class="narrative", artifacts_dir=tmp_artifacts
        )
        art = get_precomputed_artifact("qa_blk", "query", artifacts_dir=tmp_artifacts)
        assert art is not None
        assert art.artifact_type == "fact_card"

    def test_get_precomputed_artifact_debug(self, tmp_artifacts):
        precompute_for_block(
            "dbg_blk",
            ERROR_LOG_CONTENT,
            risk_class="narrative",
            source_path="app.log",
            artifacts_dir=tmp_artifacts,
        )
        art = get_precomputed_artifact("dbg_blk", "debug", artifacts_dir=tmp_artifacts)
        assert art is not None
        assert art.artifact_type == "error_signature"

    def test_get_precomputed_artifact_plan(self, tmp_artifacts):
        precompute_for_block(
            "plan_blk",
            PLAN_CONTENT,
            risk_class="narrative",
            source_path="sprint.md",
            artifacts_dir=tmp_artifacts,
        )
        art = get_precomputed_artifact("plan_blk", "plan", artifacts_dir=tmp_artifacts)
        assert art is not None
        assert art.artifact_type == "project_snapshot"

    def test_get_precomputed_artifact_missing_returns_none(self, tmp_artifacts):
        art = get_precomputed_artifact("ghost_blk", "query", artifacts_dir=tmp_artifacts)
        assert art is None

    def test_get_precomputed_artifact_unknown_intent_returns_none(self, tmp_artifacts):
        precompute_for_block(
            "unk_blk", NARRATIVE_CONTENT, risk_class="narrative", artifacts_dir=tmp_artifacts
        )
        art = get_precomputed_artifact(
            "unk_blk", "totally_unknown_intent", artifacts_dir=tmp_artifacts
        )
        assert art is None


# ---------------------------------------------------------------------------
# Test 5: recompute_all
# ---------------------------------------------------------------------------


class TestRecomputeAll:
    def test_recompute_all_processes_blocks(self, tmp_path, tmp_artifacts):
        blocks_dir = tmp_path / "blocks"
        blocks_dir.mkdir()

        # Create fake block files
        (blocks_dir / "readme.md.txt").write_text(NARRATIVE_CONTENT)
        (blocks_dir / "app.log.txt").write_text(ERROR_LOG_CONTENT)

        blocks = {
            "readme.md": {"risk_class": "narrative", "source_path": "README.md"},
            "app.log": {"risk_class": "narrative", "source_path": "app.log"},
        }

        stats = recompute_all(blocks, blocks_dir, artifacts_dir=tmp_artifacts)
        assert stats["generated"] > 0
        assert stats["errors"] == 0

    def test_recompute_all_skips_missing_block_files(self, tmp_path, tmp_artifacts):
        blocks_dir = tmp_path / "blocks"
        blocks_dir.mkdir()

        blocks = {
            "ghost.md": {"risk_class": "narrative", "source_path": "ghost.md"},
        }

        stats = recompute_all(blocks, blocks_dir, artifacts_dir=tmp_artifacts)
        assert stats["skipped"] == 1
        assert stats["generated"] == 0

    def test_recompute_all_force(self, tmp_path, tmp_artifacts):
        blocks_dir = tmp_path / "blocks"
        blocks_dir.mkdir()
        (blocks_dir / "doc.md.txt").write_text(NARRATIVE_CONTENT)

        blocks = {"doc.md": {"risk_class": "narrative", "source_path": "doc.md"}}

        stats1 = recompute_all(blocks, blocks_dir, artifacts_dir=tmp_artifacts)
        stats2 = recompute_all(blocks, blocks_dir, artifacts_dir=tmp_artifacts, force=True)

        assert stats1["generated"] > 0
        # force=True should regenerate all
        assert stats2["generated"] > 0


# ---------------------------------------------------------------------------
# Test 6: PrecomputedArtifact serialization
# ---------------------------------------------------------------------------


class TestArtifactSerialization:
    def test_to_dict_and_from_dict_roundtrip(self):
        art = generate_fact_card("serial_blk", NARRATIVE_CONTENT, DocType.NARRATIVE, "README.md")
        d = art.to_dict()
        restored = PrecomputedArtifact.from_dict(d)
        assert restored.block_id == art.block_id
        assert restored.artifact_type == art.artifact_type
        assert restored.intent == art.intent
        assert restored.content == art.content
        assert restored.token_estimate == art.token_estimate

    def test_to_dict_has_required_keys(self):
        art = generate_error_signature("e_blk", ERROR_LOG_CONTENT, DocType.ERROR_LOG)
        d = art.to_dict()
        for key in (
            "block_id",
            "artifact_type",
            "intent",
            "content",
            "doc_type",
            "created_at",
            "token_estimate",
            "metadata",
        ):
            assert key in d, f"Missing key: {key}"
