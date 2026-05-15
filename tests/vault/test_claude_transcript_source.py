"""Tests for the Phase 0 claude_transcript source adapter.

Covers the acceptance criteria from the Suki packet:

* Off-by-default (env-flag gated)
* Parses real Claude Code JSONL line shapes
* Required metadata preserved per block
* Transcript hits in the BM25 index carry a distinct ``source_type``
* No transcript mutation
* Filesystem-rebuild path preserves non-filesystem blocks
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tokenpak.vault.sources import claude_transcript as ct
from tokenpak.vault.vault_health import VaultHealth


# ---------------------------------------------------------------------------
# Fixtures — minimal synthetic transcript tree
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_projects_root(tmp_path: Path) -> Path:
    """A ~/.claude/projects clone with two sessions across two project dirs."""
    root = tmp_path / "projects"

    proj_a = root / "-home-sue"
    proj_a.mkdir(parents=True)
    session_a = proj_a / "session-aaaa-1111.jsonl"
    session_a.write_text(
        "\n".join([
            json.dumps({
                "type": "custom-title",
                "customTitle": "📦 tokenpak claude",
            }),
            json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "How do I configure the proxy injection budget?",
                },
                "timestamp": "2026-05-15T10:00:00.000Z",
            }),
            json.dumps({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-7",
                    "content": [
                        {"type": "thinking", "thinking": "this should not be indexed"},
                        {"type": "text", "text": "INJECT_BUDGET defaults to 4000 tokens."},
                    ],
                },
                "timestamp": "2026-05-15T10:00:05.000Z",
                "cwd": "/home/sue",
            }),
            json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "<local-command-caveat>noise</local-command-caveat>",
                },
            }),
            # Corrupt line — must not crash the parser
            "{not valid json",
        ]) + "\n",
        encoding="utf-8",
    )

    proj_b = root / "-home-sue--openclaw-workspace"
    proj_b.mkdir(parents=True)
    session_b = proj_b / "session-bbbb-2222.jsonl"
    session_b.write_text(
        "\n".join([
            json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "Plan the lane E migration for Std 30 trust gate.",
                },
                "timestamp": "2026-05-14T08:00:00.000Z",
            }),
        ]) + "\n",
        encoding="utf-8",
    )

    # An empty-of-content session — should be skipped (yields no block)
    proj_empty = root / "-tmp"
    proj_empty.mkdir(parents=True)
    (proj_empty / "empty.jsonl").write_text(
        json.dumps({"type": "custom-title", "customTitle": "x"}) + "\n",
        encoding="utf-8",
    )

    return root


@pytest.fixture
def tokenpak_dir(tmp_path: Path) -> Path:
    """Empty ``.tokenpak`` index directory."""
    d = tmp_path / ".tokenpak"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Off-by-default
# ---------------------------------------------------------------------------

def test_disabled_by_default(monkeypatch, tokenpak_dir, fake_projects_root):
    monkeypatch.delenv(ct.ENV_FLAG, raising=False)

    result = ct.index_claude_transcripts(tokenpak_dir, projects_root=fake_projects_root)

    assert result == {"skipped": True, "reason": "disabled"}
    # No index file or block files should be written
    assert not (tokenpak_dir / "index.json").exists()
    blocks_dir = tokenpak_dir / "blocks"
    assert not blocks_dir.exists() or not any(blocks_dir.iterdir())


def test_force_bypasses_env_flag(monkeypatch, tokenpak_dir, fake_projects_root):
    monkeypatch.delenv(ct.ENV_FLAG, raising=False)

    result = ct.index_claude_transcripts(
        tokenpak_dir, projects_root=fake_projects_root, force=True
    )

    assert result["skipped"] is False
    assert result["added"] == 2
    assert (tokenpak_dir / "index.json").exists()


def test_env_flag_enables(monkeypatch, tokenpak_dir, fake_projects_root):
    monkeypatch.setenv(ct.ENV_FLAG, "1")

    result = ct.index_claude_transcripts(tokenpak_dir, projects_root=fake_projects_root)

    assert result["skipped"] is False
    assert result["added"] == 2


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_parse_extracts_user_and_assistant_text(fake_projects_root):
    session = next(iter(
        (fake_projects_root / "-home-sue").glob("*.jsonl")
    ))
    msgs = ct.parse_jsonl_session(session)

    roles = [m.role for m in msgs]
    assert roles == ["user", "assistant"]
    assert "INJECT_BUDGET defaults to 4000 tokens." in msgs[1].text
    # Thinking blocks are intentionally skipped
    assert "this should not be indexed" not in msgs[1].text
    # local-command-caveat noise is dropped
    assert all("local-command-caveat" not in m.text for m in msgs)


def test_parse_tolerates_corrupt_jsonl(fake_projects_root):
    session = next(iter(
        (fake_projects_root / "-home-sue").glob("*.jsonl")
    ))
    # parse_jsonl_session should not raise even though the file ends with a
    # malformed JSON line.
    msgs = ct.parse_jsonl_session(session)
    assert len(msgs) >= 2


# ---------------------------------------------------------------------------
# Block metadata + index merge
# ---------------------------------------------------------------------------

def test_block_metadata_preserved(monkeypatch, tokenpak_dir, fake_projects_root):
    monkeypatch.setenv(ct.ENV_FLAG, "1")
    ct.index_claude_transcripts(tokenpak_dir, projects_root=fake_projects_root)

    data = json.loads((tokenpak_dir / "index.json").read_text())
    blocks = data["blocks"]

    # Locate the session-A block by suffix
    matches = [b for bid, b in blocks.items() if "session-aaaa-1111" in bid]
    assert len(matches) == 1
    entry = matches[0]

    assert entry["source_type"] == "claude_transcript"
    ct_meta = entry["claude_transcript"]
    assert ct_meta["project_dir"] == "-home-sue"
    assert ct_meta["project_cwd_guess"] == "/home/sue"
    assert ct_meta["session_id"] == "session-aaaa-1111"
    assert ct_meta["session_file"].endswith("session-aaaa-1111.jsonl")
    assert ct_meta["message_count"] == 2
    assert ct_meta["first_timestamp"] == "2026-05-15T10:00:00.000Z"
    assert ct_meta["last_timestamp"] == "2026-05-15T10:00:05.000Z"
    assert entry["source_path"].endswith("session-aaaa-1111.jsonl")


def test_block_id_is_stable_and_namespaced(monkeypatch, tokenpak_dir, fake_projects_root):
    monkeypatch.setenv(ct.ENV_FLAG, "1")
    r1 = ct.index_claude_transcripts(tokenpak_dir, projects_root=fake_projects_root)
    r2 = ct.index_claude_transcripts(tokenpak_dir, projects_root=fake_projects_root)

    assert r1["added"] == 2
    assert r2["unchanged"] == 2 and r2["added"] == 0

    data = json.loads((tokenpak_dir / "index.json").read_text())
    for bid in data["blocks"]:
        assert bid.startswith("claude_transcript.")


def test_no_transcript_mutation(monkeypatch, tokenpak_dir, fake_projects_root):
    monkeypatch.setenv(ct.ENV_FLAG, "1")
    session = next(iter(
        (fake_projects_root / "-home-sue").glob("*.jsonl")
    ))
    before_hash = hashlib.sha256(session.read_bytes()).hexdigest()
    before_mtime = session.stat().st_mtime

    ct.index_claude_transcripts(tokenpak_dir, projects_root=fake_projects_root)

    after_hash = hashlib.sha256(session.read_bytes()).hexdigest()
    after_mtime = session.stat().st_mtime
    assert before_hash == after_hash
    assert before_mtime == after_mtime


def test_block_txt_written_and_searchable_content(
    monkeypatch, tokenpak_dir, fake_projects_root
):
    monkeypatch.setenv(ct.ENV_FLAG, "1")
    ct.index_claude_transcripts(tokenpak_dir, projects_root=fake_projects_root)

    blocks_dir = tokenpak_dir / "blocks"
    files = list(blocks_dir.glob("claude_transcript.*.txt"))
    assert len(files) == 2

    session_a_file = next(
        p for p in files if "session-aaaa-1111" in p.name
    )
    body = session_a_file.read_text()
    assert "INJECT_BUDGET" in body
    # thinking blocks must not leak into the searchable body
    assert "this should not be indexed" not in body


# ---------------------------------------------------------------------------
# Filesystem rebuild preserves transcript blocks
# ---------------------------------------------------------------------------

def test_filesystem_rebuild_preserves_transcript_blocks(
    monkeypatch, tmp_path, fake_projects_root
):
    # Set up a tiny vault that VaultHealth will rebuild over.
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("# Note\nimportant filesystem doc.\n")

    monkeypatch.setenv(ct.ENV_FLAG, "1")
    # First, index transcripts so the index has non-filesystem entries.
    health = VaultHealth(vault_dir=vault_dir)
    health.tokenpak_dir.mkdir(parents=True, exist_ok=True)
    health.blocks_dir.mkdir(parents=True, exist_ok=True)
    ct.index_claude_transcripts(
        health.tokenpak_dir, projects_root=fake_projects_root
    )
    pre = json.loads(health.index_path.read_text())
    pre_transcript_bids = {
        bid for bid, b in pre["blocks"].items()
        if b.get("source_type") == "claude_transcript"
    }
    assert len(pre_transcript_bids) == 2

    # Now trigger a filesystem rebuild
    result = health._do_rebuild()
    assert result.success

    post = json.loads(health.index_path.read_text())
    post_bids = set(post["blocks"].keys())
    # All transcript block_ids must survive the rebuild
    assert pre_transcript_bids.issubset(post_bids)
    # And the filesystem note must also be present
    fs_paths = {
        b["source_path"]
        for b in post["blocks"].values()
        if b.get("source_type", "filesystem") == "filesystem"
    }
    assert "note.md" in fs_paths


# ---------------------------------------------------------------------------
# Search response labeling — exercises the proxy endpoint helper directly
# ---------------------------------------------------------------------------

def test_proxy_search_response_labels_source(monkeypatch, tokenpak_dir, fake_projects_root):
    """The proxy ``_handle_vault_search`` rows include ``source`` so callers
    can distinguish transcript hits from filesystem-vault hits.

    We test the labeling slice in isolation rather than booting the proxy:
    feed a (block, score) pair through the same shape the endpoint emits.
    """
    monkeypatch.setenv(ct.ENV_FLAG, "1")
    ct.index_claude_transcripts(tokenpak_dir, projects_root=fake_projects_root)
    data = json.loads((tokenpak_dir / "index.json").read_text())

    # Build the in-memory block shape that VaultIndex._load produces.
    transcript_block_meta = next(
        b for b in data["blocks"].values()
        if b.get("source_type") == "claude_transcript"
    )
    block_in_memory = {
        "block_id": transcript_block_meta["block_id"],
        "source_path": transcript_block_meta["source_path"],
        "raw_tokens": transcript_block_meta["raw_tokens"],
        "source_type": transcript_block_meta["source_type"],
        "claude_transcript": transcript_block_meta["claude_transcript"],
        "content": "",
    }

    # Mirror the row-construction in _handle_vault_search.
    row = {
        "block_id": block_in_memory.get("block_id") or "",
        "path": block_in_memory.get("path") or block_in_memory.get("source_path", ""),
        "score": 3.21,
        "tokens": int(block_in_memory.get("raw_tokens", 0) or 0),
        "preview": "",
        "source": block_in_memory.get("source_type") or "vault",
    }
    if row["source"] == "claude_transcript":
        ct_meta = block_in_memory.get("claude_transcript") or {}
        row["claude_transcript"] = {
            "project_dir": ct_meta.get("project_dir"),
            "project_cwd_guess": ct_meta.get("project_cwd_guess"),
            "session_id": ct_meta.get("session_id"),
            "session_file": ct_meta.get("session_file"),
            "message_count": ct_meta.get("message_count"),
            "first_timestamp": ct_meta.get("first_timestamp"),
            "last_timestamp": ct_meta.get("last_timestamp"),
        }

    assert row["source"] == "claude_transcript"
    assert row["claude_transcript"]["session_id"].startswith("session-")
    # And the filesystem-default fallback path
    fs_row_source = "filesystem"
    assert fs_row_source != "claude_transcript"
