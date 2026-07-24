"""
Tests for TokenPak Fingerprint Sync Client.

Run:  pytest tests/test_fingerprint_sync.py -v
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "tokenpak._internal.fingerprint.generator", reason="module not available in current build"
)
import json
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from tokenpak._internal.fingerprint.generator import Fingerprint, FingerprintGenerator
from tokenpak._internal.fingerprint.privacy import PrivacyLevel, apply_privacy
from tokenpak._internal.fingerprint.sync import (
    Directive,
    FingerprintSync,
    _oss_fallback_directives,
    _read_cache,
    _write_cache,
)

# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────


@pytest.fixture
def tmp_cache(tmp_path):
    return tmp_path / "fingerprint_cache"


@pytest.fixture
def sample_fingerprint():
    gen = FingerprintGenerator()
    return gen.generate("You are a helpful assistant.\n\nWhat is the capital of France?")


@pytest.fixture
def sample_directives():
    return [
        Directive(
            directive_id="dir-001",
            action="compress",
            params={"strategy": "advanced", "ratio": 0.7},
            priority=1,
            description="Advanced compression for structured prompts",
        ),
        Directive(
            directive_id="dir-002",
            action="route",
            params={"model": "gpt-4o-mini"},
            priority=0,
            description="Route to cheaper model for simple queries",
        ),
    ]


# ─────────────────────────────────────────────
# Generator tests
# ─────────────────────────────────────────────


class TestFingerprintGenerator:
    def test_generate_returns_fingerprint(self, sample_fingerprint):
        assert isinstance(sample_fingerprint, Fingerprint)
        assert sample_fingerprint.fingerprint_id
        assert sample_fingerprint.total_tokens > 0
        assert sample_fingerprint.segment_count > 0

    def test_generate_no_raw_content(self, sample_fingerprint):
        d = sample_fingerprint.to_dict()
        for seg in d.get("segments", []):
            assert "content" not in seg
            assert "text" not in seg

    def test_generate_from_messages(self):
        gen = FingerprintGenerator()
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
        ]
        fp = gen.generate_from_messages(messages)
        assert fp.segment_count >= 3
        assert fp.total_tokens > 0

    def test_generate_from_messages_with_code(self):
        gen = FingerprintGenerator()
        messages = [
            {"role": "user", "content": "Explain this:\n```python\nprint('hello')\n```"},
        ]
        fp = gen.generate_from_messages(messages)
        types = [s.type for s in fp.segments]
        assert "code" in types

    def test_unique_fingerprint_ids(self):
        gen = FingerprintGenerator()
        fp1 = gen.generate("Same text")
        fp2 = gen.generate("Same text")
        assert fp1.fingerprint_id != fp2.fingerprint_id

    def test_to_dict_schema(self, sample_fingerprint):
        d = sample_fingerprint.to_dict()
        assert "fingerprint_id" in d
        assert "schema_version" in d
        assert "total_tokens" in d
        assert "segment_count" in d
        assert "segments" in d

    def test_include_hashes(self):
        gen = FingerprintGenerator(include_hashes=True)
        fp = gen.generate("Hello world")
        hashes = [s.content_hash for s in fp.segments if s.content_hash]
        assert len(hashes) > 0

    def test_no_hashes_by_default(self, sample_fingerprint):
        for seg in sample_fingerprint.segments:
            assert seg.content_hash is None


# ─────────────────────────────────────────────
# Privacy tests
# ─────────────────────────────────────────────


class TestPrivacy:
    def test_minimal_strips_segments(self, sample_fingerprint):
        d = apply_privacy(sample_fingerprint.to_dict(), PrivacyLevel.MINIMAL)
        assert "segments" not in d
        assert "total_tokens" in d
        assert "segment_count" in d

    def test_standard_includes_type_distribution(self, sample_fingerprint):
        d = apply_privacy(sample_fingerprint.to_dict(), PrivacyLevel.STANDARD)
        assert "segment_type_distribution" in d
        assert "segments" not in d

    def test_full_preserves_all(self, sample_fingerprint):
        d = apply_privacy(sample_fingerprint.to_dict(), PrivacyLevel.FULL)
        assert "segments" in d
        assert len(d["segments"]) == sample_fingerprint.segment_count


# ─────────────────────────────────────────────
# Cache tests
# ─────────────────────────────────────────────


class TestCache:
    def test_write_and_read_cache(self, tmp_cache, sample_directives):
        fp_id = str(uuid.uuid4())
        _write_cache(fp_id, sample_directives, ttl=3600, cache_dir=tmp_cache)
        data = _read_cache(fp_id, tmp_cache)
        assert data is not None
        assert data["fingerprint_id"] == fp_id
        assert len(data["directives"]) == 2

    def test_cache_expiry(self, tmp_cache, sample_directives):
        fp_id = str(uuid.uuid4())
        _write_cache(fp_id, sample_directives, ttl=1, cache_dir=tmp_cache)
        time.sleep(1.1)
        data = _read_cache(fp_id, tmp_cache)
        assert data is None

    def test_cache_miss_returns_none(self, tmp_cache):
        data = _read_cache("nonexistent-id", tmp_cache)
        assert data is None

    def test_clear_all_cache(self, tmp_cache, sample_directives):
        client = FingerprintSync(cache_dir=tmp_cache)
        for _ in range(3):
            _write_cache(str(uuid.uuid4()), sample_directives, 3600, tmp_cache)
        deleted = client.clear_cache()
        assert deleted == 3

    def test_clear_specific_cache_entry(self, tmp_cache, sample_directives):
        client = FingerprintSync(cache_dir=tmp_cache)
        fp_id = str(uuid.uuid4())
        _write_cache(fp_id, sample_directives, 3600, tmp_cache)
        other_id = str(uuid.uuid4())
        _write_cache(other_id, sample_directives, 3600, tmp_cache)

        deleted = client.clear_cache(fingerprint_id=fp_id)
        assert deleted == 1
        assert _read_cache(fp_id, tmp_cache) is None
        assert _read_cache(other_id, tmp_cache) is not None

    def test_cache_status(self, tmp_cache, sample_directives):
        client = FingerprintSync(cache_dir=tmp_cache, ttl=3600)
        _write_cache(str(uuid.uuid4()), sample_directives, 3600, tmp_cache)
        _write_cache(str(uuid.uuid4()), sample_directives, 1, tmp_cache)
        time.sleep(1.1)
        status = client.cache_status()
        assert status["entries"] == 2
        assert status["valid"] == 1
        assert status["expired"] == 1


# ─────────────────────────────────────────────
# Mock server sync tests
# ─────────────────────────────────────────────


class TestMockServerSync:
    def _mock_server_response(self, directives: list[dict]) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"directives": directives}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("tokenpak.infrastructure.license_activation.is_pro", return_value=True)
    @patch("urllib.request.urlopen")
    def test_sync_from_server(self, mock_urlopen, mock_is_pro, tmp_cache, sample_fingerprint):
        server_directives = [
            {
                "directive_id": "srv-001",
                "action": "compress",
                "params": {},
                "priority": 1,
                "description": "",
            }
        ]
        mock_urlopen.return_value = self._mock_server_response(server_directives)

        client = FingerprintSync(cache_dir=tmp_cache, server_url="http://mock-server")
        result = client.sync(sample_fingerprint)

        assert result.success
        assert result.source == "server"
        assert len(result.directives) == 1
        assert result.directives[0].directive_id == "srv-001"

    @patch("tokenpak.infrastructure.license_activation.is_pro", return_value=True)
    @patch("urllib.request.urlopen")
    def test_sync_uses_cache_on_second_call(
        self, mock_urlopen, mock_is_pro, tmp_cache, sample_fingerprint
    ):
        server_directives = [
            {
                "directive_id": "srv-001",
                "action": "compress",
                "params": {},
                "priority": 1,
                "description": "",
            }
        ]
        mock_urlopen.return_value = self._mock_server_response(server_directives)

        client = FingerprintSync(cache_dir=tmp_cache, server_url="http://mock-server")
        result1 = client.sync(sample_fingerprint)
        # Second call — should use cache, NOT call server again
        mock_urlopen.reset_mock()
        result2 = client.sync(sample_fingerprint)

        assert result2.source == "cache"
        mock_urlopen.assert_not_called()

    @patch("tokenpak.infrastructure.license_activation.is_pro", return_value=True)
    @patch("urllib.request.urlopen", side_effect=Exception("connection refused"))
    def test_offline_fallback_to_oss(
        self, mock_urlopen, mock_is_pro, tmp_cache, sample_fingerprint
    ):
        client = FingerprintSync(cache_dir=tmp_cache, server_url="http://mock-server")
        result = client.sync(sample_fingerprint)

        assert not result.success
        assert result.source == "oss_fallback"
        assert len(result.directives) > 0
        assert result.error is not None

    @patch("tokenpak.infrastructure.license_activation.is_pro", return_value=True)
    @patch("urllib.request.urlopen", side_effect=Exception("connection refused"))
    def test_offline_fallback_to_stale_cache(
        self, mock_urlopen, mock_is_pro, tmp_cache, sample_fingerprint, sample_directives
    ):
        # Pre-seed a stale cache entry (expired TTL but file exists)
        _write_cache(
            sample_fingerprint.fingerprint_id, sample_directives, ttl=1, cache_dir=tmp_cache
        )
        time.sleep(1.1)

        client = FingerprintSync(cache_dir=tmp_cache, server_url="http://mock-server")
        result = client.sync(sample_fingerprint)

        assert not result.success
        assert result.source == "cache"
        assert len(result.directives) == len(sample_directives)

    @patch("tokenpak.infrastructure.license_activation.is_pro", return_value=True)
    def test_dry_run_does_not_call_server(self, mock_is_pro, tmp_cache, sample_fingerprint):
        with patch("urllib.request.urlopen") as mock_urlopen:
            client = FingerprintSync(cache_dir=tmp_cache)
            result = client.sync(sample_fingerprint, dry_run=True)

        assert result.dry_run
        assert result.source == "dry_run"
        mock_urlopen.assert_not_called()


# ─────────────────────────────────────────────
# License gate tests
# ─────────────────────────────────────────────


class TestLicenseGate:
    @patch("tokenpak.infrastructure.license_activation.is_pro", return_value=False)
    def test_sync_blocked_for_oss(self, mock_is_pro, tmp_cache, sample_fingerprint):
        client = FingerprintSync(cache_dir=tmp_cache)
        with pytest.raises(PermissionError, match="Pro\\+"):
            client.sync(sample_fingerprint)

    @patch("tokenpak.infrastructure.license_activation.is_pro", return_value=True)
    @patch("urllib.request.urlopen")
    def test_sync_allowed_for_pro(self, mock_urlopen, mock_is_pro, tmp_cache, sample_fingerprint):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"directives": []}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        client = FingerprintSync(cache_dir=tmp_cache, server_url="http://mock-server")
        result = client.sync(sample_fingerprint)
        assert result.success


# ─────────────────────────────────────────────
# OSS fallback directives
# ─────────────────────────────────────────────


class TestOSSFallback:
    def test_oss_fallback_returns_directives(self):
        directives = _oss_fallback_directives()
        assert len(directives) >= 1
        assert all(isinstance(d, Directive) for d in directives)
        assert directives[0].action == "compress"

    def test_cached_directives_empty_when_no_cache(self, tmp_cache):
        client = FingerprintSync(cache_dir=tmp_cache)
        result = client.cached_directives("nonexistent-id")
        assert result == []
