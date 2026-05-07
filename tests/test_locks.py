"""Tests for tokenpak.agentic.locks"""

import pytest

pytest.importorskip("tokenpak.agentic.locks", reason="module not available in current build")
import time

import pytest

from tokenpak.agentic.locks import FileLockManager, LockConflictError


@pytest.fixture
def mgr(tmp_path):
    return FileLockManager(agent_id="test-agent", lock_dir=tmp_path / "locks", timeout_s=60)


@pytest.fixture
def mgr2(tmp_path):
    return FileLockManager(agent_id="other-agent", lock_dir=tmp_path / "locks", timeout_s=60)


def test_claim_returns_record(mgr, tmp_path):
    target = tmp_path / "file.txt"
    record = mgr.claim(target)
    assert record["agent"] == "test-agent"
    assert record["path"] == str(target.resolve())
    assert record["expires"] > time.time()


def test_query_live_lock(mgr, tmp_path):
    target = tmp_path / "file.txt"
    mgr.claim(target)
    result = mgr.query(target)
    assert result is not None
    assert result["agent"] == "test-agent"


def test_query_no_lock(mgr, tmp_path):
    assert mgr.query(tmp_path / "nonexistent.txt") is None


def test_release_removes_lock(mgr, tmp_path):
    target = tmp_path / "file.txt"
    mgr.claim(target)
    assert mgr.release(target) is True
    assert mgr.query(target) is None


def test_release_nonexistent_returns_false(mgr, tmp_path):
    assert mgr.release(tmp_path / "ghost.txt") is False


def test_same_agent_can_reacquire(mgr, tmp_path):
    target = tmp_path / "file.txt"
    mgr.claim(target)
    record = mgr.claim(target)  # Should extend, not raise
    assert record["agent"] == "test-agent"


def test_conflict_raises_error(tmp_path):
    """Two different agents — second should get LockConflictError."""
    mgr_a = FileLockManager(agent_id="agent-a", lock_dir=tmp_path / "locks", timeout_s=60)
    mgr_b = FileLockManager(agent_id="agent-b", lock_dir=tmp_path / "locks", timeout_s=60)
    target = tmp_path / "shared.txt"
    mgr_a.claim(target)
    with pytest.raises(LockConflictError) as exc_info:
        mgr_b.claim(target)
    assert "agent-a" in str(exc_info.value)


def test_expired_lock_can_be_stolen(tmp_path):
    mgr_a = FileLockManager(agent_id="agent-a", lock_dir=tmp_path / "locks", timeout_s=0)
    mgr_b = FileLockManager(agent_id="agent-b", lock_dir=tmp_path / "locks", timeout_s=60)
    target = tmp_path / "file.txt"
    mgr_a.claim(target, timeout_s=0)
    time.sleep(0.01)
    # Expired — mgr_b should be able to claim
    record = mgr_b.claim(target)
    assert record["agent"] == "agent-b"


def test_locks_returns_live_only(mgr, tmp_path):
    f1, f2 = tmp_path / "a.txt", tmp_path / "b.txt"
    mgr.claim(f1)
    mgr.claim(f2)
    assert len(mgr.locks()) == 2
    mgr.release(f1)
    assert len(mgr.locks()) == 1


def test_prune_expired(tmp_path):
    mgr = FileLockManager(agent_id="pruner", lock_dir=tmp_path / "locks", timeout_s=0)
    target = tmp_path / "file.txt"
    mgr.claim(target, timeout_s=0)
    time.sleep(0.01)
    removed = mgr.prune_expired()
    assert removed == 1


def test_suggest_alternatives(mgr, tmp_path):
    locked = tmp_path / "locked.txt"
    free1 = tmp_path / "free1.txt"
    free2 = tmp_path / "free2.txt"
    mgr.claim(locked)
    alternatives = mgr.suggest_alternatives(locked, [locked, free1, free2])
    assert str(free1) in alternatives
    assert str(free2) in alternatives
    assert str(locked) not in alternatives


def test_lock_dir_created_automatically(tmp_path):
    new_dir = tmp_path / "deep" / "nested" / "locks"
    mgr = FileLockManager(agent_id="x", lock_dir=new_dir)
    assert new_dir.exists()

# ── renew tests ──────────────────────────────────────────────────────────────

def test_renew_extends_expiry(mgr, tmp_path):
    target = tmp_path / "file.txt"
    record1 = mgr.claim(target, timeout_s=60)
    record2 = mgr.renew(target, timeout_s=120)
    assert record2["expires"] > record1["expires"]
    assert record2["agent"] == "test-agent"


def test_renew_no_lock_raises(mgr, tmp_path):
    from tokenpak.agentic.locks import LockExpiredError
    with pytest.raises(LockExpiredError):
        mgr.renew(tmp_path / "nonexistent.txt")


def test_renew_conflict_raises(tmp_path):
    from tokenpak.agentic.locks import LockConflictError
    mgr_a = FileLockManager(agent_id="agent-a", lock_dir=tmp_path / "locks", timeout_s=60)
    mgr_b = FileLockManager(agent_id="agent-b", lock_dir=tmp_path / "locks", timeout_s=60)
    target = tmp_path / "file.txt"
    mgr_a.claim(target)
    with pytest.raises(LockConflictError):
        mgr_b.renew(target)


def test_renew_expired_raises(tmp_path):
    from tokenpak.agentic.locks import LockExpiredError
    mgr = FileLockManager(agent_id="agent-a", lock_dir=tmp_path / "locks", timeout_s=0)
    target = tmp_path / "file.txt"
    mgr.claim(target, timeout_s=0)
    time.sleep(0.01)
    with pytest.raises(LockExpiredError):
        mgr.renew(target)


def test_renew_sets_renewed_field(mgr, tmp_path):
    target = tmp_path / "file.txt"
    before = time.time()
    mgr.claim(target)
    record = mgr.renew(target)
    assert "renewed" in record
    assert record["renewed"] >= before

