"""
Unit tests for compression/fingerprinter.py — content fingerprinting for cache keys.

Tests cover:
  - FingerprintGenerator initialization and generate() / generate_from_messages()
  - Fingerprint dataclass and to_dict()
  - PrivacyLevel enum
  - apply_privacy() at all three levels
  - FingerprintSync initialization, dry_run sync, cache helpers
  - Edge cases: empty input, unicode, large input
"""

from __future__ import annotations

from unittest.mock import patch

from tokenpak.compression.fingerprinter import (
    Fingerprint,
    FingerPrinter,
    FingerprintGenerator,
    FingerprintSync,
    PrivacyLevel,
    SyncResult,
    apply_privacy,
)

# ── FingerprintGenerator — initialization ─────────────────────────────────────


class TestFingerprintGeneratorInit:
    def test_default_init(self):
        gen = FingerprintGenerator()
        assert gen.include_hashes is False
        assert gen.model_hint is None

    def test_init_with_hashes(self):
        gen = FingerprintGenerator(include_hashes=True)
        assert gen.include_hashes is True

    def test_init_with_model_hint(self):
        gen = FingerprintGenerator(model_hint="claude-3")
        assert gen.model_hint == "claude-3"

    def test_alias_fingerprinter(self):
        assert FingerPrinter is FingerprintGenerator


# ── FingerprintGenerator.generate() ──────────────────────────────────────────


class TestGenerate:
    def setup_method(self):
        self.gen = FingerprintGenerator()

    def test_returns_fingerprint_instance(self):
        fp = self.gen.generate("Hello world")
        assert isinstance(fp, Fingerprint)

    def test_fingerprint_id_is_set(self):
        fp = self.gen.generate("Hello")
        assert fp.fingerprint_id and len(fp.fingerprint_id) > 0

    def test_unique_fingerprint_ids(self):
        fp1 = self.gen.generate("Hello")
        fp2 = self.gen.generate("Hello")
        assert fp1.fingerprint_id != fp2.fingerprint_id

    def test_total_tokens_positive(self):
        fp = self.gen.generate("This is a moderately long text to fingerprint.")
        assert fp.total_tokens > 0

    def test_segment_count_matches_segments(self):
        fp = self.gen.generate("Para one.\n\nPara two.\n\nPara three.")
        assert fp.segment_count == len(fp.segments)

    def test_empty_string(self):
        fp = self.gen.generate("")
        assert isinstance(fp, Fingerprint)
        assert fp.segment_count == 0
        assert fp.total_tokens == 0

    def test_language_set(self):
        fp = self.gen.generate("Simple English text.")
        assert fp.language in ("en", "non-ascii")

    def test_unicode_text(self):
        fp = self.gen.generate("日本語のテキスト。")
        assert isinstance(fp, Fingerprint)
        assert fp.language == "non-ascii"

    def test_model_hint_propagated(self):
        gen = FingerprintGenerator(model_hint="gpt-4")
        fp = gen.generate("test")
        assert fp.model_hint == "gpt-4"

    def test_include_hashes_adds_content_hash(self):
        gen = FingerprintGenerator(include_hashes=True)
        fp = gen.generate("Hello world paragraph.")
        for seg in fp.segments:
            assert seg.content_hash is not None

    def test_no_hashes_by_default(self):
        fp = self.gen.generate("Hello world paragraph.")
        for seg in fp.segments:
            assert seg.content_hash is None

    def test_large_input(self):
        text = "word " * 5000
        fp = self.gen.generate(text)
        assert fp.total_tokens > 100
        assert fp.segment_count >= 1

    def test_code_segment_classified(self):
        fp = self.gen.generate("```python\nprint('hello')\n```")
        types = [s.type for s in fp.segments]
        assert "code" in types

    def test_multiline_prose_produces_segments(self):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        fp = self.gen.generate(text)
        assert fp.segment_count == 3


# ── FingerprintGenerator.generate_from_messages() ────────────────────────────


class TestGenerateFromMessages:
    def setup_method(self):
        self.gen = FingerprintGenerator()

    def test_returns_fingerprint_instance(self):
        msgs = [{"role": "user", "content": "hello"}]
        fp = self.gen.generate_from_messages(msgs)
        assert isinstance(fp, Fingerprint)

    def test_empty_messages(self):
        fp = self.gen.generate_from_messages([])
        assert fp.segment_count == 0
        assert fp.total_tokens == 0

    def test_system_user_assistant(self):
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
        ]
        fp = self.gen.generate_from_messages(msgs)
        assert fp.segment_count >= 3
        types = {s.type for s in fp.segments}
        assert "user" in types or "system" in types

    def test_code_block_inside_message_creates_code_segment(self):
        msgs = [
            {"role": "user", "content": "Here is code:\n```python\nprint('hi')\n```"}
        ]
        fp = self.gen.generate_from_messages(msgs)
        types = [s.type for s in fp.segments]
        assert "code" in types

    def test_content_list_handled(self):
        msgs = [
            {"role": "user", "content": [{"text": "hello from array"}]}
        ]
        fp = self.gen.generate_from_messages(msgs)
        assert isinstance(fp, Fingerprint)

    def test_unicode_messages(self):
        msgs = [{"role": "user", "content": "こんにちは世界"}]
        fp = self.gen.generate_from_messages(msgs)
        assert fp.language == "non-ascii"

    def test_missing_content_key_no_crash(self):
        msgs = [{"role": "user"}]
        fp = self.gen.generate_from_messages(msgs)
        assert isinstance(fp, Fingerprint)

    def test_large_conversation(self):
        msgs = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}" * 50}
            for i in range(40)
        ]
        fp = self.gen.generate_from_messages(msgs)
        assert fp.total_tokens > 0


# ── Fingerprint.to_dict() ─────────────────────────────────────────────────────


class TestFingerprintToDict:
    def test_required_keys_present(self):
        gen = FingerprintGenerator()
        fp = gen.generate("Hello world.")
        d = fp.to_dict()
        assert "fingerprint_id" in d
        assert "schema_version" in d
        assert "total_tokens" in d
        assert "segment_count" in d
        assert "segments" in d

    def test_segments_serialized(self):
        gen = FingerprintGenerator()
        fp = gen.generate("Hello world paragraph.")
        d = fp.to_dict()
        for seg in d["segments"]:
            assert "type" in seg
            assert "token_estimate" in seg

    def test_language_included_when_set(self):
        gen = FingerprintGenerator()
        fp = gen.generate("Hello")
        d = fp.to_dict()
        if fp.language:
            assert "language" in d

    def test_model_hint_included(self):
        gen = FingerprintGenerator(model_hint="claude-3")
        fp = gen.generate("Hello")
        d = fp.to_dict()
        assert d.get("model_hint") == "claude-3"

    def test_no_content_hash_without_include_hashes(self):
        gen = FingerprintGenerator(include_hashes=False)
        fp = gen.generate("A paragraph of text here.")
        d = fp.to_dict()
        for seg in d["segments"]:
            assert "content_hash" not in seg

    def test_content_hash_with_include_hashes(self):
        gen = FingerprintGenerator(include_hashes=True)
        fp = gen.generate("A paragraph of text here.")
        d = fp.to_dict()
        for seg in d["segments"]:
            assert "content_hash" in seg


# ── PrivacyLevel ──────────────────────────────────────────────────────────────


class TestPrivacyLevel:
    def test_enum_values(self):
        assert PrivacyLevel.MINIMAL == "minimal"
        assert PrivacyLevel.STANDARD == "standard"
        assert PrivacyLevel.FULL == "full"

    def test_enum_membership(self):
        levels = list(PrivacyLevel)
        assert PrivacyLevel.MINIMAL in levels
        assert PrivacyLevel.STANDARD in levels
        assert PrivacyLevel.FULL in levels


# ── apply_privacy() ───────────────────────────────────────────────────────────


class TestApplyPrivacy:
    def _sample_dict(self):
        gen = FingerprintGenerator(include_hashes=True)
        fp = gen.generate("Sample text for privacy.\n\nSecond block here.")
        return fp.to_dict()

    def test_full_returns_all_keys(self):
        d = self._sample_dict()
        result = apply_privacy(d, PrivacyLevel.FULL)
        for k in d:
            assert k in result

    def test_minimal_strips_segments(self):
        d = self._sample_dict()
        result = apply_privacy(d, PrivacyLevel.MINIMAL)
        assert "segments" not in result
        assert "total_tokens" in result
        assert "segment_count" in result

    def test_standard_includes_type_distribution(self):
        d = self._sample_dict()
        result = apply_privacy(d, PrivacyLevel.STANDARD)
        assert "segment_type_distribution" in result
        assert "avg_segment_tokens" in result

    def test_standard_no_content_hashes(self):
        d = self._sample_dict()
        result = apply_privacy(d, PrivacyLevel.STANDARD)
        assert "segments" not in result

    def test_minimal_no_segment_type_distribution(self):
        d = self._sample_dict()
        result = apply_privacy(d, PrivacyLevel.MINIMAL)
        assert "segment_type_distribution" not in result

    def test_fingerprint_id_always_present(self):
        d = self._sample_dict()
        for level in PrivacyLevel:
            result = apply_privacy(d, level)
            assert result.get("fingerprint_id") == d["fingerprint_id"]

    def test_returns_new_dict(self):
        d = self._sample_dict()
        result = apply_privacy(d, PrivacyLevel.FULL)
        assert result is not d


# ── FingerprintSync — initialization ─────────────────────────────────────────


class TestFingerprintSyncInit:
    def test_default_init(self):
        sync = FingerprintSync()
        assert sync.ttl > 0
        assert sync.privacy_level == PrivacyLevel.STANDARD

    def test_custom_cache_dir(self, tmp_path):
        sync = FingerprintSync(cache_dir=tmp_path)
        assert sync.cache_dir == tmp_path

    def test_custom_ttl(self):
        sync = FingerprintSync(ttl=7200)
        assert sync.ttl == 7200

    def test_custom_privacy_level(self):
        sync = FingerprintSync(privacy_level=PrivacyLevel.MINIMAL)
        assert sync.privacy_level == PrivacyLevel.MINIMAL

    def test_custom_server_url(self):
        sync = FingerprintSync(server_url="http://localhost:9999")
        assert "localhost:9999" in sync.server_url


# ── FingerprintSync.sync() — dry run (no network) ─────────────────────────────


class TestFingerprintSyncDryRun:
    def test_dry_run_returns_sync_result(self, tmp_path):
        gen = FingerprintGenerator()
        fp = gen.generate("test text")
        sync = FingerprintSync(cache_dir=tmp_path)
        result = sync.sync(fp, dry_run=True)
        assert isinstance(result, SyncResult)

    def test_dry_run_success(self, tmp_path):
        gen = FingerprintGenerator()
        fp = gen.generate("test text")
        sync = FingerprintSync(cache_dir=tmp_path)
        result = sync.sync(fp, dry_run=True)
        assert result.success is True

    def test_dry_run_flag_set(self, tmp_path):
        gen = FingerprintGenerator()
        fp = gen.generate("test text")
        sync = FingerprintSync(cache_dir=tmp_path)
        result = sync.sync(fp, dry_run=True)
        assert result.dry_run is True

    def test_dry_run_source(self, tmp_path):
        gen = FingerprintGenerator()
        fp = gen.generate("test text")
        sync = FingerprintSync(cache_dir=tmp_path)
        result = sync.sync(fp, dry_run=True)
        assert result.source == "dry_run"

    def test_dry_run_no_network_call(self, tmp_path):
        gen = FingerprintGenerator()
        fp = gen.generate("test text")
        sync = FingerprintSync(cache_dir=tmp_path)
        with patch("urllib.request.urlopen") as mock_urlopen:
            sync.sync(fp, dry_run=True)
            mock_urlopen.assert_not_called()


# ── FingerprintSync — cache helpers ──────────────────────────────────────────


class TestFingerprintSyncCache:
    def test_cached_directives_empty_when_no_cache(self, tmp_path):
        sync = FingerprintSync(cache_dir=tmp_path)
        result = sync.cached_directives("nonexistent-id")
        assert result == []

    def test_cache_status_when_empty(self, tmp_path):
        sync = FingerprintSync(cache_dir=tmp_path)
        status = sync.cache_status()
        assert status["entries"] == 0
        assert status["valid"] == 0
        assert "cache_dir" in status

    def test_clear_cache_nonexistent_dir(self, tmp_path):
        nonexistent = tmp_path / "nope"
        sync = FingerprintSync(cache_dir=nonexistent)
        assert sync.clear_cache() == 0

    def test_clear_cache_specific_id_not_present(self, tmp_path):
        sync = FingerprintSync(cache_dir=tmp_path)
        assert sync.clear_cache("fake-id") == 0

    def test_oss_fallback_when_server_unreachable(self, tmp_path):
        gen = FingerprintGenerator()
        fp = gen.generate("fallback test")
        sync = FingerprintSync(
            server_url="http://127.0.0.1:19999",  # nothing listening
            cache_dir=tmp_path,
            timeout=1,
        )
        result = sync.sync(fp)
        assert isinstance(result, SyncResult)
        assert not result.success or result.source in ("server", "cache", "oss_fallback")
        # In offline/unreachable case it should fall back
        if not result.success:
            assert result.source in ("cache", "oss_fallback")

    def test_sync_result_properties(self):
        result = SyncResult(success=True, source="cache")
        assert result.from_cache is True
        assert result.is_fallback is False

        result2 = SyncResult(success=False, source="oss_fallback")
        assert result2.is_fallback is True
        assert result2.from_cache is False
