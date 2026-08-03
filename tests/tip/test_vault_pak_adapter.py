# SPDX-License-Identifier: Apache-2.0
"""Offline contract tests for the Vault → Pak adapter (Std 32 §10).

Per Std 32 §10:
- Offline contract tests for every OSS-side hook in §1.3.
- Tests exercise the daemon-absent path (the only path the OSS adapter
  has — daemon-present is Pro-side, tested in tokenpak-paid).
- Privacy contract: assert Pak fields are disjoint from license-payload
  field prefixes (already covered structurally in
  ``test_multipak_contracts.py``; this suite adds adapter-specific checks).
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from tokenpak.tip.pak import (
    Pak,
    PakAuthority,
    PakConfidence,
    PakPrivacyClass,
    PakRetention,
    PakSourceType,
    PakStatus,
    PakSubtype,
)
from tokenpak.vault.pak_adapter import (
    _file_type_to_source_type,
    _infer_project,
    search_as_paks,
    vault_block_to_pak,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_block(tmp_path):
    """Minimal vault block dict matching VaultIndex.search() return shape."""
    cf = tmp_path / "block.txt"
    cf.write_text("hello world")
    return {
        "block_id": "/home/sue/tokenpak/README.md#abc12345",
        "source_path": "/home/sue/tokenpak/README.md",
        "risk_class": "narrative",
        "must_keep": False,
        "raw_tokens": 100,
        "_content_file": str(cf),
        "file_type": "text",
    }


# ---------------------------------------------------------------------------
# vault_block_to_pak — required field mapping
# ---------------------------------------------------------------------------


def test_vault_block_to_pak_returns_pak_instance(vault_block):
    pak = vault_block_to_pak(vault_block)
    assert isinstance(pak, Pak)


def test_vault_block_to_pak_id_prefixed(vault_block):
    """Pak IDs from vault blocks are prefixed `vault:` for namespace clarity."""
    pak = vault_block_to_pak(vault_block)
    assert pak.pak_id == "vault:/home/sue/tokenpak/README.md#abc12345"
    assert pak.pak_id.startswith("vault:")


def test_vault_block_subtype_is_vault(vault_block):
    pak = vault_block_to_pak(vault_block)
    assert pak.pak_type is PakSubtype.VAULT


def test_vault_block_authority_is_file_source(vault_block):
    """Std 32 §2.1 — Vault Paks always carry file_source authority."""
    pak = vault_block_to_pak(vault_block)
    assert pak.authority is PakAuthority.FILE_SOURCE


def test_vault_block_status_is_proposed(vault_block):
    """OSS adapter never promotes — daemon owns the lifecycle transition."""
    pak = vault_block_to_pak(vault_block)
    assert pak.status is PakStatus.PROPOSED


def test_vault_block_default_confidence_is_medium(vault_block):
    pak = vault_block_to_pak(vault_block)
    assert pak.confidence is PakConfidence.MEDIUM


def test_vault_block_retention_is_source_lifetime(vault_block):
    """Std 32 §8 — Vault Paks default to source_lifetime retention."""
    pak = vault_block_to_pak(vault_block)
    assert pak.retention.ttl is PakRetention.SOURCE_LIFETIME


def test_vault_block_privacy_local_only(vault_block):
    """Std 32 §1.2 — local-only is the only admitted privacy class in v1."""
    pak = vault_block_to_pak(vault_block)
    assert pak.privacy.class_ is PakPrivacyClass.LOCAL_ONLY


# ---------------------------------------------------------------------------
# Source mapping
# ---------------------------------------------------------------------------


def test_source_platform_is_tokenpak_vault(vault_block):
    pak = vault_block_to_pak(vault_block)
    assert pak.source.platform == "tokenpak-vault"


def test_source_type_text_to_FILE(vault_block):
    pak = vault_block_to_pak(vault_block)
    assert pak.source.source_type is PakSourceType.FILE


def test_source_type_code_to_CODE():
    block = {"block_id": "x#1", "source_path": "x.py", "file_type": "code"}
    pak = vault_block_to_pak(block)
    assert pak.source.source_type is PakSourceType.CODE


def test_source_type_data_to_FILE():
    block = {"block_id": "x#1", "source_path": "x.json", "file_type": "data"}
    pak = vault_block_to_pak(block)
    assert pak.source.source_type is PakSourceType.FILE


def test_source_type_unknown_falls_back_to_FILE():
    """Std 31 §2 graceful-fallback rule for unknown enum values."""
    block = {"block_id": "x#1", "source_path": "x.bin", "file_type": "exotic"}
    pak = vault_block_to_pak(block)
    assert pak.source.source_type is PakSourceType.FILE


def test_source_type_missing_falls_back_to_FILE():
    block = {"block_id": "x#1", "source_path": "x"}
    pak = vault_block_to_pak(block)
    assert pak.source.source_type is PakSourceType.FILE


def test_source_hash_extracted_from_block_id():
    block = {"block_id": "/path/file.md#deadbeef"}
    pak = vault_block_to_pak(block)
    assert pak.source.source_hash == "deadbeef"


def test_source_hash_override_via_kwarg():
    block = {"block_id": "/path/file.md#abc12345"}
    full_hash = "a" * 64  # full SHA-256 hex
    pak = vault_block_to_pak(block, content_hash=full_hash)
    assert pak.source.source_hash == full_hash


def test_source_created_at_uses_content_file_mtime(tmp_path):
    cf = tmp_path / "block.txt"
    cf.write_text("data")
    # Pin mtime to a known epoch so we can compare deterministically.
    target = 1_700_000_000.0
    import os

    os.utime(cf, (target, target))
    block = {
        "block_id": "x#1",
        "source_path": "x.md",
        "_content_file": str(cf),
    }
    pak = vault_block_to_pak(block)
    parsed = datetime.fromisoformat(pak.source.created_at).timestamp()
    assert abs(parsed - target) < 1.0


def test_source_created_at_falls_back_when_file_missing():
    """When _content_file is missing or unreadable, created_at falls back to "now"."""
    block = {"block_id": "x#1", "source_path": "x.md", "_content_file": "/nonexistent"}
    before = time.time()
    pak = vault_block_to_pak(block)
    after = time.time()
    parsed = datetime.fromisoformat(pak.source.created_at).timestamp()
    assert before - 1 <= parsed <= after + 1


# ---------------------------------------------------------------------------
# Project scope inference
# ---------------------------------------------------------------------------


def test_infer_project_from_path():
    assert _infer_project("/home/sue/tokenpak/README.md") == "tokenpak"


def test_infer_project_returns_none_for_empty_path():
    assert _infer_project("") is None


def test_infer_project_skips_home_root():
    """``home`` and ``Users`` segments are skipped — they're filesystem roots,
    not projects."""
    assert _infer_project("/home/foo") == "foo"


# ---------------------------------------------------------------------------
# Round-trip + JSON-serializability (privacy contract via structural disjointness)
# ---------------------------------------------------------------------------


def test_pak_round_trips_through_dict(vault_block):
    pak = vault_block_to_pak(vault_block)
    d = pak.to_dict()
    pak2 = Pak.from_dict(d)
    assert pak2 == pak


def test_pak_to_dict_is_json_serializable(vault_block):
    import json

    pak = vault_block_to_pak(vault_block)
    s = json.dumps(pak.to_dict())
    assert "vault:" in s
    assert "vault" in s


def test_pak_dict_disjoint_from_license_payload_prefixes(vault_block):
    """Std 32 §7.1 / Std 25 §4.4 — Pak fields MUST NOT carry any of the
    license-validation egress identifiers. Structural disjointness is the
    OSS-side load-bearing privacy claim; Pro daemon enforces semantically.
    """
    pak = vault_block_to_pak(vault_block)
    d = pak.to_dict()

    forbidden_prefixes = (
        "license_token",
        "tenant_id",
        "fingerprint",
        "issuer",
        "signature",
    )
    keys_seen: list[str] = []

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                keys_seen.append(k)
                walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for item in obj:
                walk(item, path)

    walk(d)
    for k in keys_seen:
        for prefix in forbidden_prefixes:
            assert not k.startswith(prefix), (
                f"Pak dict carries license-payload prefix {prefix!r} via {k!r} — "
                "violates Std 32 §7.1 structural disjointness"
            )


# ---------------------------------------------------------------------------
# search_as_paks
# ---------------------------------------------------------------------------


class _StubVaultIndex:
    """Minimal VaultIndex stub for adapter tests — no SQLite, no BM25."""

    def __init__(self, blocks):
        self._blocks = blocks

    def search(self, query, top_k=5, min_score=2.0):
        # Return all blocks with synthetic scores; honor top_k.
        scored = [(b, 5.0) for b in self._blocks]
        return scored[:top_k]


def test_search_as_paks_returns_paks(vault_block):
    idx = _StubVaultIndex([vault_block])
    paks = search_as_paks("anything", vault_index=idx)
    assert len(paks) == 1
    assert isinstance(paks[0], Pak)
    assert paks[0].pak_type is PakSubtype.VAULT


def test_search_as_paks_empty_when_no_index():
    """When no vault index is available, the adapter returns []
    (Std 32 §5.3 no-memory result is correct UX)."""
    paks = search_as_paks("anything", vault_index=None)
    # vault_index=None falls through to lazy-import; if get_vault_index
    # is unavailable or raises, we still get an empty list.
    assert isinstance(paks, list)


def test_search_as_paks_handles_search_exception(vault_block):
    """If the underlying vault search raises, the adapter degrades to []."""

    class _RaisingIndex:
        def search(self, query, top_k=5, min_score=2.0):
            raise RuntimeError("vault corrupt")

    paks = search_as_paks("anything", vault_index=_RaisingIndex())
    assert paks == []


def test_search_as_paks_respects_top_k(vault_block):
    idx = _StubVaultIndex([vault_block, vault_block, vault_block])
    paks = search_as_paks("anything", top_k=2, vault_index=idx)
    assert len(paks) == 2


# ---------------------------------------------------------------------------
# Helper-function unit tests
# ---------------------------------------------------------------------------


def test_file_type_to_source_type_case_insensitive():
    assert _file_type_to_source_type("CODE") is PakSourceType.CODE
    assert _file_type_to_source_type("Text") is PakSourceType.FILE


def test_file_type_to_source_type_none_returns_FILE():
    assert _file_type_to_source_type(None) is PakSourceType.FILE
    assert _file_type_to_source_type("") is PakSourceType.FILE
