# SPDX-License-Identifier: Apache-2.0
"""Tests for configurable generic memory-source ingestion.

Covers the "bring your own knowledge base" path added so a fresh (solo) user
can point the companion at any Markdown notes directory — without the vault
``03_AGENT_PACKS/<agent>/memory/`` schema.

Maps to the feature acceptance criteria:
  AC#1 vault-schema default path still works (regression guard)
  AC#2 custom memory dir ingests
  AC#3 Markdown supported (.md / .markdown)
  AC#4 missing / empty dirs fail gracefully (no crash) with distinct reasons
  AC#5 status surfaces configured source(s) — see test_status_reports_sources
  AC#6 from_env parses TOKENPAK_COMPANION_MEMORY_DIRS (pathsep/comma/~/empties)
"""

import os
import tempfile
from pathlib import Path

import pytest

from tokenpak.companion.config import CompanionConfig, _path_list
from tokenpak.companion.memory.decision_memory import DecisionMemoryDB
from tokenpak.companion.memory.lesson_ingest import (
    ingest_from_dir,
    ingest_from_vault,
    ingest_sources,
)


@pytest.fixture
def db():
    """A throwaway on-disk DecisionMemoryDB (SQLite needs a real path)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        yield DecisionMemoryDB(path)
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------- #
# AC#6 — from_env parsing of TOKENPAK_COMPANION_MEMORY_DIRS
# --------------------------------------------------------------------------- #


class TestFromEnvMemoryDirs:
    def test_default_empty(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_COMPANION_MEMORY_DIRS", raising=False)
        cfg = CompanionConfig.from_env()
        assert cfg.memory_dirs == []

    def test_pathsep_form(self, monkeypatch):
        monkeypatch.setenv(
            "TOKENPAK_COMPANION_MEMORY_DIRS",
            os.pathsep.join(["/tmp/a", "/tmp/b"]),
        )
        cfg = CompanionConfig.from_env()
        assert cfg.memory_dirs == [Path("/tmp/a"), Path("/tmp/b")]

    def test_comma_form(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_COMPANION_MEMORY_DIRS", "/tmp/a,/tmp/b")
        cfg = CompanionConfig.from_env()
        assert cfg.memory_dirs == [Path("/tmp/a"), Path("/tmp/b")]

    def test_tilde_expansion_and_empties(self, monkeypatch):
        monkeypatch.setenv(
            "TOKENPAK_COMPANION_MEMORY_DIRS", "~/notes,, ,~/work/journal,"
        )
        cfg = CompanionConfig.from_env()
        home = os.path.expanduser("~")
        assert cfg.memory_dirs == [
            Path(home) / "notes",
            Path(home) / "work" / "journal",
        ]

    def test_path_list_unset_is_empty(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_COMPANION_MEMORY_DIRS", raising=False)
        assert _path_list("TOKENPAK_COMPANION_MEMORY_DIRS") == []


# --------------------------------------------------------------------------- #
# AC#1 — vault-schema default path still works (regression guard)
# --------------------------------------------------------------------------- #


def test_vault_schema_still_works(db):
    with tempfile.TemporaryDirectory() as tmp:
        mem = Path(tmp) / "03_AGENT_PACKS" / "TestAgent" / "memory"
        mem.mkdir(parents=True)
        (mem / "2026-03-27.md").write_text(
            "# Daily Log\n\n## Lessons Learned\n- Lesson A\n- Lesson B\n"
        )
        assert ingest_from_vault(tmp, db) == 2
        assert db.count() == 2


# --------------------------------------------------------------------------- #
# AC#2 / AC#3 — custom memory dir ingests Markdown (.md and .markdown)
# --------------------------------------------------------------------------- #


def test_custom_memory_dir_ingests(db):
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "notes.md").write_text(
            "# My Notes\n\n## Lessons Learned\n- Keep a changelog\n- Write tests first\n"
        )
        # nested + .markdown extension also picked up
        sub = Path(tmp) / "sub"
        sub.mkdir()
        (sub / "more.markdown").write_text(
            "## Lessons Learned\n- Back up before migrations\n"
        )
        assert ingest_from_dir(tmp, db) == 3
        assert db.count() == 3


def test_non_markdown_ignored(db):
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "keep.md").write_text("## Lessons Learned\n- Real lesson\n")
        (Path(tmp) / "skip.txt").write_text("## Lessons Learned\n- ignored\n")
        (Path(tmp) / "skip.json").write_text('{"lesson": "ignored"}')
        assert ingest_from_dir(tmp, db) == 1


# --------------------------------------------------------------------------- #
# AC#4 — missing / empty directories fail gracefully (no crash)
# --------------------------------------------------------------------------- #


def test_missing_dir_no_crash(db):
    assert ingest_from_dir("/nonexistent/path/xyz", db) == 0


def test_empty_dir_no_crash(db):
    with tempfile.TemporaryDirectory() as tmp:
        assert ingest_from_dir(tmp, db) == 0


# --------------------------------------------------------------------------- #
# ingest_sources orchestrator — structured per-source reasons (AC#4/#5 support)
# --------------------------------------------------------------------------- #


class TestIngestSources:
    def test_no_source_configured(self, db):
        res = ingest_sources(db)
        assert res["total"] == 0
        assert res["sources"][0]["reason"] == "no-source-configured"

    def test_missing_memory_dir_reason(self, db):
        res = ingest_sources(db, memory_dirs=["/nope/missing"])
        assert res["total"] == 0
        assert res["sources"][0]["reason"] == "missing"

    def test_empty_memory_dir_reason(self, db):
        with tempfile.TemporaryDirectory() as tmp:
            res = ingest_sources(db, memory_dirs=[tmp])
            assert res["sources"][0]["reason"] == "present-but-no-matching-files"

    def test_mixed_vault_and_memory_dirs(self, db):
        with tempfile.TemporaryDirectory() as vault, \
             tempfile.TemporaryDirectory() as notes:
            mem = Path(vault) / "03_AGENT_PACKS" / "A" / "memory"
            mem.mkdir(parents=True)
            (mem / "2026-03-27.md").write_text("## Lessons Learned\n- vault lesson\n")
            (Path(notes) / "n.md").write_text("## Lessons Learned\n- byo lesson\n")
            res = ingest_sources(db, vault_dir=vault, memory_dirs=[notes])
            assert res["total"] == 2
            kinds = {s["kind"] for s in res["sources"]}
            assert kinds == {"vault", "memory-dir"}
            assert all(s["reason"] == "ok" for s in res["sources"])

    def test_rerun_matches_vault_path_behavior(self, db):
        """Generic path has the SAME re-run semantics as the vault path.

        ``DecisionMemoryDB.record`` always inserts (no upsert on the query
        hash), so both ``ingest_from_dir`` and ``ingest_from_vault`` re-insert
        on a second run.  This test documents that parity — the generic path
        does not introduce a new behavior, and changing the DB to dedupe is
        out of scope for this change.
        """
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "n.md").write_text(
                "## Lessons Learned\n- one\n- two\n"
            )
            assert ingest_from_dir(tmp, db) == 2
            first = db.count()
            assert ingest_from_dir(tmp, db) == 2
            assert db.count() == first + 2  # re-insert, same as vault path


def test_status_reports_sources(monkeypatch):
    """AC#5: ``session_info`` surfaces configured memory sources + a hint.

    The MCP ``session_info`` tool only *reports* ``memory_dirs`` (and, when none
    are set, a hint on how to configure them) — it never ingests. This guards
    that the hint points at the shipped surface (the env var + the library API)
    and NOT at the deliberately deferred ``tokenpak companion ingest`` CLI verb.
    """
    import json

    from tokenpak.companion.mcp import tools as mcp_tools

    # Exercise only the local-config surface; never touch a real proxy.
    monkeypatch.setattr(
        mcp_tools, "_proxy_get",
        lambda *a, **k: (0, {"detail": "proxy down (test)"}),
    )

    # No memory dirs configured -> a self-explaining hint is reported.
    cfg_empty = CompanionConfig.from_env()
    cfg_empty.memory_dirs = []
    out = json.loads(mcp_tools._handle_session_info(
        mcp_tools.CompanionState(config=cfg_empty), {}))
    assert out["config"]["memory_dirs"] == []
    hint = out["config"]["memory_source_hint"]
    assert "TOKENPAK_COMPANION_MEMORY_DIRS" in hint
    assert ("ingest_from_dir" in hint) or ("ingest_sources" in hint)
    # Must NOT advertise the deferred CLI flag / verb.
    assert "--memory-dir" not in hint
    assert "companion ingest" not in hint

    # Memory dirs configured -> reported, and no "configure me" hint.
    cfg_set = CompanionConfig.from_env()
    cfg_set.memory_dirs = [Path("/tmp/notes"), Path("/tmp/journal")]
    out2 = json.loads(mcp_tools._handle_session_info(
        mcp_tools.CompanionState(config=cfg_set), {}))
    assert out2["config"]["memory_dirs"] == ["/tmp/notes", "/tmp/journal"]
    assert "memory_source_hint" not in out2["config"]
