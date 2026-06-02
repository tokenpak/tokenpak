"""Regression tests for the skeleton phantom-capability truth patch.

Context: the "code skeleton" feature is wired into config, profiles, the proxy
injection path, and the doctor/status capability surface, and historically
claimed "70-90% reduction on code" — but the core extractor module
(``tokenpak.skeleton_extractor``) does not exist, so at runtime the feature is
a silent no-op. The truth patch makes the capability surface report from a real
import probe (not the intent flag), emits a diagnostic when the extractor is
missing, and removes the unbacked savings claim.

These tests fail against the pre-patch phantom state (feature reported
active/available while the extractor is absent) and pass after it.

When the real extractor is implemented, the ``pytest.skip`` guards below
deactivate the "absent" assertions, and a follow-up change replaces them with
their post-implementation inverse.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest


def _extractor_present() -> bool:
    try:
        from tokenpak.skeleton_extractor import extract_skeleton  # noqa: F401

        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    _extractor_present(),
    reason="skeleton extractor present (implemented); absence-invariant tests N/A",
)


def test_skeleton_available_probe_false_when_extractor_absent():
    """The capability probe must reflect real importability, not a config flag."""
    from tokenpak.proxy.config import skeleton_available

    assert skeleton_available() is False


def test_skeleton_not_reported_active_despite_enabled_flag(monkeypatch):
    """Core anti-phantom invariant: enabled intent must NOT report 'active'
    when the extractor cannot be imported."""
    import tokenpak.proxy.config as cfg

    # Even with the intent flag forced on (its default is True), skeleton must
    # not be reported active because the extractor is absent.
    monkeypatch.setattr(cfg, "SKELETON_ENABLED", True, raising=False)
    assert cfg.skeleton_active() is False


def test_skeletonize_block_is_noop_and_emits_diagnostic(monkeypatch, caplog):
    """A missing extractor must (a) leave the block byte-identical and
    (b) emit a diagnostic signal — never a silent swallow."""
    import tokenpak.proxy.config as cfg
    import tokenpak.vault.chunk_shaping as cs

    monkeypatch.setattr(cfg, "SKELETON_ENABLED", True, raising=False)
    # Reset the one-shot diagnostic latch for a deterministic assertion.
    monkeypatch.setattr(cs, "_SKELETON_EXTRACTOR_MISSING", False, raising=False)

    code = "def foo(x):\n    return x + 1\n"
    with caplog.at_level(logging.WARNING, logger="tokenpak.skeleton"):
        out = cs._skeletonize_block(code, ".py")

    # (a) no-op / no corruption
    assert out == code
    # (b) diagnostic observable via status field ...
    status = cs.skeleton_runtime_status()
    assert status["enabled"] is True
    assert status["available"] is False
    assert status["extractor_missing_observed"] is True
    # ... and via a log line
    assert any("extractor unavailable" in r.message for r in caplog.records)


def test_non_skeleton_injection_path_byte_identical(monkeypatch):
    """Acceptance #4: the non-skeleton injection path (feature disabled) is
    byte-identical — the truth patch must not change it."""
    import tokenpak.proxy.config as cfg
    import tokenpak.vault.chunk_shaping as cs

    monkeypatch.setattr(cfg, "SKELETON_ENABLED", False, raising=False)
    blocks = "Some prose.\n\n```python\ndef foo(x):\n    return x + 1\n```\n"
    assert cs._inject_skeleton_into_blocks(blocks) == blocks


def test_extractor_missing_preserves_code_body(monkeypatch):
    """With skeleton enabled but the extractor absent, the code body must be
    preserved (no skeletonization / no corruption).

    NOTE: the enabled fence-rewrite path normalizes block whitespace (adds a
    trailing newline before the closing fence) independently of this patch — a
    pre-existing cosmetic quirk left for the real extractor work. This test
    asserts content survival, not byte-equality, to avoid coupling to that quirk.
    """
    import tokenpak.proxy.config as cfg
    import tokenpak.vault.chunk_shaping as cs

    monkeypatch.setattr(cfg, "SKELETON_ENABLED", True, raising=False)
    blocks = "Some prose.\n\n```python\ndef foo(x):\n    return x + 1\n```\n"
    out = cs._inject_skeleton_into_blocks(blocks)
    assert "def foo(x):" in out
    assert "return x + 1" in out


def test_no_unbacked_savings_percentage_in_injection_source():
    """No live code may assert a skeleton savings percentage that isn't backed
    by a passing benchmark test (the removed '70-90% reduction' claim)."""
    import tokenpak.proxy.vault_bridge as vb

    src = Path(vb.__file__).read_text(encoding="utf-8")
    assert "70-90% reduction" not in src
    assert "70-90%" not in src
