"""Unit tests for tokenpak/vault_health.py — helpers, dataclasses, VaultHealth."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tokenpak"))

from tokenpak.vault_health import (  # type: ignore
    HealthCheckResult,
    IndexStatus,
    RepairResult,
    VaultHealth,
    _make_block_id,
    _parse_frontmatter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_make_block_id_forward_slashes():
    assert _make_block_id("foo/bar/baz.txt") == "foo.bar.baz.txt"


def test_make_block_id_backslashes():
    assert _make_block_id("foo\\bar\\baz.txt") == "foo.bar.baz.txt"


def test_make_block_id_strips_leading_dot():
    result = _make_block_id("/leading/slash.txt")
    assert not result.startswith(".")


def test_parse_frontmatter_valid():
    content = "---\ntitle: Hello\nauthor: Test\n---\nBody text"
    result = _parse_frontmatter(content)
    assert result["title"] == "Hello"
    assert result["author"] == "Test"


def test_parse_frontmatter_no_frontmatter():
    assert _parse_frontmatter("just a normal file") == {}


def test_parse_frontmatter_malformed_no_close():
    assert _parse_frontmatter("---\ntitle: Open\n") == {}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


def test_health_check_result_is_ok():
    r = HealthCheckResult(status=IndexStatus.OK)
    assert r.is_ok() is True


def test_health_check_result_not_ok_when_stale():
    r = HealthCheckResult(status=IndexStatus.STALE)
    assert r.is_ok() is False


def test_health_check_result_to_dict():
    r = HealthCheckResult(status=IndexStatus.OK, block_count=42, age_seconds=10.5)
    d = r.to_dict()
    assert d["status"] == "OK"
    assert d["block_count"] == 42
    assert d["age_seconds"] == 10.5


def test_repair_result_to_dict():
    r = RepairResult(success=True, files_processed=5, index_entries=100)
    d = r.to_dict()
    assert d["success"] is True
    assert d["files_processed"] == 5
    assert d["index_entries"] == 100


# ---------------------------------------------------------------------------
# VaultHealth — using temp directories
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_dir(tmp_path):
    """Minimal vault structure for testing."""
    tokenpak = tmp_path / ".tokenpak"
    tokenpak.mkdir()
    (tokenpak / "blocks").mkdir()
    return tmp_path


def _write_index(vault_dir, blocks=None):
    index = {"blocks": blocks or {}}
    (vault_dir / ".tokenpak" / "index.json").write_text(json.dumps(index))


def test_check_missing_index(tmp_path):
    vh = VaultHealth(vault_dir=tmp_path)
    result = vh.check()
    assert result.status == IndexStatus.MISSING


def test_check_ok_fresh_index(vault_dir):
    _write_index(vault_dir, blocks={"b1": "v1", "b2": "v2"})
    vh = VaultHealth(vault_dir=vault_dir, stale_seconds=3600)
    result = vh.check()
    assert result.status == IndexStatus.OK
    assert result.block_count == 2


def test_check_corrupt_index(vault_dir):
    (vault_dir / ".tokenpak" / "index.json").write_text("not valid json {{")
    vh = VaultHealth(vault_dir=vault_dir)
    result = vh.check()
    assert result.status == IndexStatus.CORRUPT
    assert result.error is not None


def test_check_stale_index(vault_dir):
    _write_index(vault_dir)
    # Touch index with old mtime
    idx = vault_dir / ".tokenpak" / "index.json"
    old_time = time.time() - 7200  # 2 hours ago
    import os
    os.utime(idx, (old_time, old_time))
    vh = VaultHealth(vault_dir=vault_dir, stale_seconds=300)
    result = vh.check()
    assert result.status == IndexStatus.STALE


def test_check_index_staleness_returns_bool(vault_dir):
    _write_index(vault_dir)
    vh = VaultHealth(vault_dir=vault_dir, stale_seconds=3600)
    assert vh.check_index_staleness() is False


def test_get_status_returns_string(vault_dir):
    _write_index(vault_dir)
    vh = VaultHealth(vault_dir=vault_dir, stale_seconds=3600)
    assert vh.get_status() == IndexStatus.OK


def test_repair_ok_index_skips_rebuild(vault_dir):
    _write_index(vault_dir, blocks={"b1": "v"})
    vh = VaultHealth(vault_dir=vault_dir, stale_seconds=3600)
    result = vh.repair()
    assert result.success is True


def test_rebuild_index_on_empty_vault(vault_dir):
    """rebuild_index on empty vault should succeed (0 blocks)."""
    vh = VaultHealth(vault_dir=vault_dir, stale_seconds=3600)
    metrics = vh.rebuild_index()
    assert isinstance(metrics, dict)
    assert metrics.get("success") is True


# ---------------------------------------------------------------------------
# Extended VaultHealth tests — ≥30 total
# ---------------------------------------------------------------------------


def test_parse_frontmatter_multiple_keys():
    content = "---\ntitle: Foo\nstatus: active\ndate: 2026-01-01\n---\nBody"
    result = _parse_frontmatter(content)
    assert result["title"] == "Foo"
    assert result["status"] == "active"
    assert result["date"] == "2026-01-01"


def test_parse_frontmatter_empty_values():
    content = "---\ntitle: \nauthor: Test\n---\nBody"
    result = _parse_frontmatter(content)
    assert "title" in result
    assert result["author"] == "Test"


def test_make_block_id_mixed_slashes():
    result = _make_block_id("a/b\\c/d.md")
    assert "/" not in result
    assert "\\" not in result


def test_make_block_id_extension_preserved():
    result = _make_block_id("docs/note.md")
    assert result.endswith(".md")


def test_health_check_result_stale_not_ok():
    r = HealthCheckResult(status=IndexStatus.STALE)
    assert r.is_ok() is False


def test_health_check_result_missing_not_ok():
    r = HealthCheckResult(status=IndexStatus.MISSING)
    assert r.is_ok() is False


def test_health_check_result_corrupt_not_ok():
    r = HealthCheckResult(status=IndexStatus.CORRUPT, error="bad json")
    assert r.is_ok() is False


def test_health_check_result_to_dict_includes_threshold():
    r = HealthCheckResult(status=IndexStatus.OK, stale_threshold_seconds=7200)
    d = r.to_dict()
    assert d["stale_threshold_seconds"] == 7200


def test_repair_result_to_dict_includes_entries_added():
    r = RepairResult(success=True, entries_added=10, entries_removed=2)
    d = r.to_dict()
    assert d["entries_added"] == 10
    assert d["entries_removed"] == 2


def test_vault_health_default_vault_dir():
    vh = VaultHealth()
    assert "vault" in str(vh.vault_dir)


def test_check_index_staleness_true_when_missing(tmp_path):
    vh = VaultHealth(vault_dir=tmp_path)
    assert vh.check_index_staleness() is True


def test_check_index_staleness_true_when_corrupt(vault_dir):
    (vault_dir / ".tokenpak" / "index.json").write_text("{invalid")
    vh = VaultHealth(vault_dir=vault_dir)
    assert vh.check_index_staleness() is True


def test_get_status_missing(tmp_path):
    vh = VaultHealth(vault_dir=tmp_path)
    assert vh.get_status() == IndexStatus.MISSING


def test_get_status_corrupt(vault_dir):
    (vault_dir / ".tokenpak" / "index.json").write_text("not-json!!")
    vh = VaultHealth(vault_dir=vault_dir)
    assert vh.get_status() == IndexStatus.CORRUPT


def test_rebuild_index_writes_index_json(vault_dir):
    """rebuild_index should create/overwrite index.json."""
    vh = VaultHealth(vault_dir=vault_dir, stale_seconds=3600)
    metrics = vh.rebuild_index()
    assert (vault_dir / ".tokenpak" / "index.json").exists()
    assert metrics["success"] is True


def test_rebuild_index_returns_stats(vault_dir):
    vh = VaultHealth(vault_dir=vault_dir, stale_seconds=3600)
    metrics = vh.rebuild_index()
    for key in ("success", "files_processed", "index_entries"):
        assert key in metrics


def test_repair_stale_triggers_rebuild(vault_dir):
    _write_index(vault_dir, blocks={"b": "v"})
    idx = vault_dir / ".tokenpak" / "index.json"
    import os
    os.utime(idx, (time.time() - 9000, time.time() - 9000))
    vh = VaultHealth(vault_dir=vault_dir, stale_seconds=3600)
    result = vh.repair()
    assert result.success is True


def test_repair_corrupt_triggers_rebuild(vault_dir):
    (vault_dir / ".tokenpak" / "index.json").write_text("{{invalid")
    vh = VaultHealth(vault_dir=vault_dir)
    result = vh.repair()
    assert result.success is True


def test_repair_result_log_entry_set(vault_dir):
    _write_index(vault_dir, blocks={"b": "v"})
    vh = VaultHealth(vault_dir=vault_dir, stale_seconds=3600)
    result = vh.repair()
    assert isinstance(result.log_entry, str) and len(result.log_entry) > 0


def test_index_status_constants():
    assert IndexStatus.OK == "OK"
    assert IndexStatus.STALE == "STALE"
    assert IndexStatus.MISSING == "MISSING"
    assert IndexStatus.CORRUPT == "CORRUPT"
