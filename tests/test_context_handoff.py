"""Tests for tokenpak.agentic.handoff — Context Handoff System.

Covers:
  - Lifecycle: create → receive → apply
  - Missing refs (invalid state)
  - Auto-expiry
  - Registered-agents-only enforcement
  - List/filter
  - Idempotent operations
"""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.agentic.handoff", reason="module not available in current build")
import json
import time
from pathlib import Path

import pytest

from tokenpak.agentic.handoff import (
    ContextRef,
    Handoff,
    HandoffManager,
    HandoffStatus,
    REGISTERED_AGENTS,
    _generate_summary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_manager(tmp_path):
    """Return a HandoffManager backed by a temp dir."""
    return HandoffManager(handoff_dir=tmp_path / "handoffs")


@pytest.fixture
def sample_file(tmp_path):
    """A real file that can be used as a context ref."""
    p = tmp_path / "context.md"
    p.write_text("# Context\nSome content")
    return str(p)


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

def test_generate_summary_all_fields():
    s = _generate_summary("Built X", "Review Y", ["a.py", "b.py"])
    assert "Done: Built X" in s
    assert "Next: Review Y" in s
    assert "a.py" in s


def test_generate_summary_empty():
    s = _generate_summary("", "", [])
    assert s == "(no summary)"


def test_generate_summary_many_files():
    files = [f"file{i}.py" for i in range(10)]
    s = _generate_summary("", "", files)
    assert "+5 more" in s


# ---------------------------------------------------------------------------
# Registered-agents-only enforcement
# ---------------------------------------------------------------------------

def test_create_unknown_from_agent(tmp_manager):
    with pytest.raises(ValueError, match="Unknown from_agent"):
        tmp_manager.create_handoff(from_agent="unknown_bot", to_agent="sue")


def test_create_unknown_to_agent(tmp_manager):
    with pytest.raises(ValueError, match="Unknown to_agent"):
        tmp_manager.create_handoff(from_agent="cali", to_agent="mystery_agent")


def test_all_registered_agents_allowed(tmp_manager):
    """All agents in REGISTERED_AGENTS can create handoffs to each other."""
    for agent in REGISTERED_AGENTS:
        others = [a for a in REGISTERED_AGENTS if a != agent]
        if others:
            h = tmp_manager.create_handoff(from_agent=agent, to_agent=others[0])
            assert h.id


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def test_create_handoff_basic(tmp_manager):
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    assert h.id
    assert h.from_agent == "cali"
    assert h.to_agent == "sue"
    assert h.status == HandoffStatus.PENDING
    assert h.expires_at > h.created_at


def test_create_handoff_with_refs(tmp_manager, sample_file):
    refs = [ContextRef(type="file", path=sample_file, description="Context doc")]
    h = tmp_manager.create_handoff(
        from_agent="cali",
        to_agent="sue",
        context_refs=refs,
        what_was_done="Implemented feature A",
        whats_next="Review PR",
        relevant_files=[sample_file],
    )
    assert len(h.context_refs) == 1
    assert h.context_refs[0].type == "file"
    assert "Implemented feature A" in h.summary
    assert "Review PR" in h.summary


def test_create_handoff_persisted(tmp_manager):
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    loaded = tmp_manager.get_handoff(h.id)
    assert loaded is not None
    assert loaded.id == h.id
    assert loaded.status == HandoffStatus.PENDING


def test_create_handoff_custom_ttl(tmp_manager):
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue", ttl_hours=2.0)
    assert abs(h.expires_at - h.created_at - 7200) < 5


# ---------------------------------------------------------------------------
# Receive
# ---------------------------------------------------------------------------

def test_receive_nonexistent(tmp_manager):
    with pytest.raises(FileNotFoundError):
        tmp_manager.receive_handoff("nonexistent-id")


def test_receive_valid_refs(tmp_manager, sample_file):
    refs = [ContextRef(type="file", path=sample_file)]
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue", context_refs=refs)
    received = tmp_manager.receive_handoff(h.id)
    assert received.status == HandoffStatus.RECEIVED
    assert received.received_at is not None
    assert all(r.valid for r in received.context_refs)


def test_receive_missing_refs(tmp_manager):
    refs = [ContextRef(type="file", path="/nonexistent/file.md")]
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue", context_refs=refs)
    received = tmp_manager.receive_handoff(h.id)
    assert received.status == HandoffStatus.INVALID
    assert received.context_refs[0].valid is False


def test_receive_non_file_refs_always_valid(tmp_manager):
    refs = [
        ContextRef(type="note", path="some_note_id"),
        ContextRef(type="url", path="https://example.com"),
    ]
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue", context_refs=refs)
    received = tmp_manager.receive_handoff(h.id)
    assert received.status == HandoffStatus.RECEIVED
    assert all(r.valid for r in received.context_refs)


def test_receive_is_idempotent(tmp_manager):
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    h1 = tmp_manager.receive_handoff(h.id)
    assert h1.status == HandoffStatus.RECEIVED
    h2 = tmp_manager.receive_handoff(h.id)
    # applied is idempotent too
    assert h2.status in (HandoffStatus.RECEIVED, HandoffStatus.APPLIED)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def test_apply_full_lifecycle(tmp_manager, sample_file):
    refs = [ContextRef(type="file", path=sample_file)]
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue", context_refs=refs)
    
    # receive first
    received = tmp_manager.receive_handoff(h.id)
    assert received.status == HandoffStatus.RECEIVED

    # now apply
    applied = tmp_manager.apply_handoff(h.id)
    assert applied.status == HandoffStatus.APPLIED
    assert applied.applied_at is not None


def test_apply_from_pending(tmp_manager):
    """apply_handoff should auto-receive if still pending."""
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    applied = tmp_manager.apply_handoff(h.id)
    assert applied.status == HandoffStatus.APPLIED


def test_apply_nonexistent(tmp_manager):
    with pytest.raises(FileNotFoundError):
        tmp_manager.apply_handoff("nonexistent-id")


def test_apply_invalid_refs_blocked(tmp_manager):
    refs = [ContextRef(type="file", path="/no/such/file.md")]
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue", context_refs=refs)
    tmp_manager.receive_handoff(h.id)  # marks as INVALID
    with pytest.raises(ValueError, match="invalid refs"):
        tmp_manager.apply_handoff(h.id)


def test_apply_is_idempotent(tmp_manager):
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    a1 = tmp_manager.apply_handoff(h.id)
    a2 = tmp_manager.apply_handoff(h.id)
    assert a1.status == HandoffStatus.APPLIED
    assert a2.status == HandoffStatus.APPLIED


# ---------------------------------------------------------------------------
# Auto-expiry
# ---------------------------------------------------------------------------

def test_receive_expired_handoff(tmp_manager):
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue", ttl_hours=0.0)
    # Force expiry
    h.expires_at = time.time() - 1
    (tmp_manager.handoff_dir / f"{h.id}.json").write_text(json.dumps(h.to_dict()))

    with pytest.raises(ValueError, match="expired"):
        tmp_manager.receive_handoff(h.id)


def test_apply_expired_handoff(tmp_manager):
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue", ttl_hours=0.0)
    h.expires_at = time.time() - 1
    (tmp_manager.handoff_dir / f"{h.id}.json").write_text(json.dumps(h.to_dict()))

    with pytest.raises(ValueError, match="expired"):
        tmp_manager.apply_handoff(h.id)


def test_expire_stale(tmp_manager):
    # Create 2 pending handoffs that should expire + 1 already applied
    h1 = tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    h2 = tmp_manager.create_handoff(from_agent="sue", to_agent="cali")
    h3 = tmp_manager.create_handoff(from_agent="cali", to_agent="trix")
    
    # Expire h1 and h2 manually
    for hid in [h1.id, h2.id]:
        h = tmp_manager.get_handoff(hid)
        h.expires_at = time.time() - 1
        (tmp_manager.handoff_dir / f"{hid}.json").write_text(json.dumps(h.to_dict()))
    
    # Apply h3 (should not be expired)
    tmp_manager.apply_handoff(h3.id)

    count = tmp_manager.expire_stale()
    assert count == 2

    assert tmp_manager.get_handoff(h1.id).status == HandoffStatus.EXPIRED
    assert tmp_manager.get_handoff(h2.id).status == HandoffStatus.EXPIRED
    assert tmp_manager.get_handoff(h3.id).status == HandoffStatus.APPLIED


def test_expire_stale_does_not_re_expire(tmp_manager):
    h = tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    h.expires_at = time.time() - 1
    (tmp_manager.handoff_dir / f"{h.id}.json").write_text(json.dumps(h.to_dict()))
    
    c1 = tmp_manager.expire_stale()
    c2 = tmp_manager.expire_stale()
    assert c1 == 1
    assert c2 == 0  # already expired


# ---------------------------------------------------------------------------
# List / filter
# ---------------------------------------------------------------------------

def test_list_all(tmp_manager):
    tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    tmp_manager.create_handoff(from_agent="sue", to_agent="cali")
    assert len(tmp_manager.list_handoffs()) == 2


def test_list_filter_to_agent(tmp_manager):
    tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    tmp_manager.create_handoff(from_agent="cali", to_agent="trix")
    result = tmp_manager.list_handoffs(to_agent="sue")
    assert len(result) == 1
    assert result[0].to_agent == "sue"


def test_list_filter_from_agent(tmp_manager):
    tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    tmp_manager.create_handoff(from_agent="sue", to_agent="cali")
    result = tmp_manager.list_handoffs(from_agent="cali")
    assert len(result) == 1
    assert result[0].from_agent == "cali"


def test_list_filter_status(tmp_manager):
    h1 = tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    h2 = tmp_manager.create_handoff(from_agent="cali", to_agent="sue")
    tmp_manager.apply_handoff(h2.id)
    
    pending = tmp_manager.list_handoffs(status=HandoffStatus.PENDING)
    applied = tmp_manager.list_handoffs(status=HandoffStatus.APPLIED)
    assert len(pending) == 1
    assert len(applied) == 1


def test_list_empty(tmp_manager):
    assert tmp_manager.list_handoffs() == []


# ---------------------------------------------------------------------------
# ContextRef serialization
# ---------------------------------------------------------------------------

def test_context_ref_round_trip():
    ref = ContextRef(type="file", path="/some/path.md", description="A doc", valid=True)
    d = ref.to_dict()
    restored = ContextRef.from_dict(d)
    assert restored.type == ref.type
    assert restored.path == ref.path
    assert restored.description == ref.description
    assert restored.valid == ref.valid


# ---------------------------------------------------------------------------
# Handoff serialization
# ---------------------------------------------------------------------------

def test_handoff_round_trip(tmp_manager):
    refs = [ContextRef(type="note", path="note123", description="Context note")]
    h = tmp_manager.create_handoff(
        from_agent="cali",
        to_agent="sue",
        context_refs=refs,
        what_was_done="Built feature",
        whats_next="QA review",
        relevant_files=["a.py", "b.py"],
        metadata={"sprint": 42},
    )
    loaded = tmp_manager.get_handoff(h.id)
    assert loaded.what_was_done == "Built feature"
    assert loaded.whats_next == "QA review"
    assert loaded.relevant_files == ["a.py", "b.py"]
    assert loaded.metadata == {"sprint": 42}
    assert loaded.context_refs[0].description == "Context note"
