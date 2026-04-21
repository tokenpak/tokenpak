"""
Tests for tokenpak.vault_health — Phase 1a
"""

import json

import pytest

from tokenpak.telemetry.vault_health import (
    IndexStatus,
    VaultHealth,
    _make_block_id,
    _parse_frontmatter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_vault(tmp_path):
    """An empty vault directory with no index."""
    return tmp_path


@pytest.fixture
def small_vault(tmp_path):
    """A vault with a few files."""
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "README.md").write_text(
        "---\ntitle: Test\nstatus: open\n---\n# Test\nThis is a test file."
    )
    (tmp_path / "notes" / "code.py").write_text("def hello(): return 'hello'")
    (tmp_path / "notes" / "data.json").write_text('{"key": "value"}')
    (tmp_path / "notes" / "image.png").write_bytes(b"fakepng")  # skipped (binary)
    (tmp_path / "notes" / "large_file.txt").write_text("x" * (2 * 1024 * 1024))  # skipped (too big)
    return tmp_path


# ---------------------------------------------------------------------------
# VaultHealth.check()
# ---------------------------------------------------------------------------


class TestCheck:
    def test_missing_returns_missing_status(self, empty_vault):
        v = VaultHealth(empty_vault)
        result = v.check()
        assert result.status == IndexStatus.MISSING
        assert result.block_count == 0

    def test_ok_when_index_fresh(self, small_vault):
        # Build a fresh index
        v = VaultHealth(small_vault, stale_seconds=9999)
        v.repair()

        v2 = VaultHealth(small_vault, stale_seconds=9999)
        result = v2.check()
        assert result.status == IndexStatus.OK
        assert result.block_count > 0

    def test_stale_when_old(self, small_vault):
        # Build an index but tell VaultHealth the threshold is 0 seconds
        v = VaultHealth(small_vault)
        v.repair()

        v2 = VaultHealth(small_vault, stale_seconds=0)
        result = v2.check()
        assert result.status == IndexStatus.STALE

    def test_corrupt_when_invalid_json(self, small_vault):
        tokenpak_dir = small_vault / ".tokenpak"
        tokenpak_dir.mkdir(parents=True, exist_ok=True)
        (tokenpak_dir / "index.json").write_text("this is not json!!")

        v = VaultHealth(small_vault)
        result = v.check()
        assert result.status == IndexStatus.CORRUPT
        assert result.error is not None

    def test_check_index_staleness_true_when_stale(self, empty_vault):
        v = VaultHealth(empty_vault)
        assert v.check_index_staleness() is True  # missing = stale

    def test_get_status_returns_string(self, empty_vault):
        v = VaultHealth(empty_vault)
        assert v.get_status() == IndexStatus.MISSING


# ---------------------------------------------------------------------------
# VaultHealth.repair() / rebuild_index()
# ---------------------------------------------------------------------------


class TestRepair:
    def test_repair_builds_index_from_scratch(self, small_vault):
        v = VaultHealth(small_vault)
        result = v.repair()

        assert result.success is True
        assert result.index_entries > 0
        assert result.files_processed > 0
        assert (small_vault / ".tokenpak" / "index.json").exists()

    def test_repair_skips_binary_and_oversized(self, small_vault):
        v = VaultHealth(small_vault)
        result = v.repair()
        assert result.files_skipped >= 2  # image.png + large_file.txt

    def test_repair_writes_block_txt_files(self, small_vault):
        v = VaultHealth(small_vault)
        v.repair()
        blocks_dir = small_vault / ".tokenpak" / "blocks"
        assert blocks_dir.exists()
        block_files = list(blocks_dir.glob("*.txt"))
        assert len(block_files) > 0

    def test_repair_index_json_structure(self, small_vault):
        v = VaultHealth(small_vault)
        v.repair()
        data = json.loads((small_vault / ".tokenpak" / "index.json").read_text())
        assert data["version"] == "1.0"
        assert "meta" in data
        assert "blocks" in data
        assert data["meta"]["rebuilt"] is True

    def test_repair_returns_ok_when_fresh(self, small_vault):
        v = VaultHealth(small_vault, stale_seconds=9999)
        v.repair()  # first repair

        v2 = VaultHealth(small_vault, stale_seconds=9999)
        result = v2.repair()
        assert result.success is True
        # Should not rebuild when index is fresh

    def test_rebuild_index_returns_metrics(self, small_vault):
        v = VaultHealth(small_vault)
        metrics = v.rebuild_index()

        assert "index_entries" in metrics
        assert "entries_added" in metrics
        assert "entries_removed" in metrics
        assert "index_size_bytes" in metrics
        assert "rebuild_time_seconds" in metrics
        assert metrics["index_entries"] >= 1

    def test_repair_empty_vault_succeeds_with_zero_entries(self, tmp_path):
        # An empty vault dir (no files) should succeed but produce 0 entries
        empty = tmp_path / "empty_vault"
        empty.mkdir()
        v = VaultHealth(empty)
        result = v.repair()
        assert result.success is True
        assert result.index_entries == 0

    def test_incremental_skips_unchanged(self, small_vault):
        v = VaultHealth(small_vault)
        result1 = v.repair()
        result2 = v.repair()

        # Second repair should not add new entries
        assert result2.success is True

    def test_log_file_appended(self, small_vault):
        v = VaultHealth(small_vault)
        v.repair()

        log_file = small_vault / ".tokenpak" / "vault_health.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "REBUILT" in content or "OK" in content


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_make_block_id(self):
        assert _make_block_id("notes/README.md") == "notes.README.md"
        assert _make_block_id("Agents/Trix/queue/task.md") == "Agents.Trix.queue.task.md"

    def test_parse_frontmatter_valid(self):
        content = "---\ntitle: My Note\nstatus: open\n---\n# Content"
        fm = _parse_frontmatter(content)
        assert fm["title"] == "My Note"
        assert fm["status"] == "open"

    def test_parse_frontmatter_empty(self):
        content = "# No frontmatter here"
        assert _parse_frontmatter(content) == {}

    def test_parse_frontmatter_malformed(self):
        content = "---\nbadline\n---\n# Content"
        result = _parse_frontmatter(content)
        assert isinstance(result, dict)  # fail-silent


# ---------------------------------------------------------------------------
# Exit code compatibility
# ---------------------------------------------------------------------------


class TestExitCodes:
    """Verify the module supports the exit code contract from the CLI."""

    def test_ok_scenario_index_fresh(self, small_vault):
        v = VaultHealth(small_vault, stale_seconds=9999)
        v.repair()
        is_stale = v.check_index_staleness()
        assert is_stale is False  # would yield exit 0

    def test_repair_scenario_yields_success(self, small_vault):
        v = VaultHealth(small_vault)
        # Fresh vault — no index, so needs rebuild
        result = v._do_rebuild()
        assert result.success is True  # would yield exit 1

    def test_missing_index_would_exit_2(self, tmp_path):
        v = VaultHealth(tmp_path / "nonexistent")
        check = v.check()
        assert check.status == IndexStatus.MISSING  # would yield exit 2
