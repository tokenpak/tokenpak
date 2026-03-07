"""
Tests for tokenpak.agent.proxy.capsule_builder — the proxy-layer module
that exposes CapsuleBuilder at the path the proxy pipeline expects.
"""

from __future__ import annotations

import json
import os
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Import checks
# ─────────────────────────────────────────────────────────────────────────────

class TestModuleImports:
    def test_import_module(self):
        """Module must be importable at the proxy-layer path."""
        import tokenpak.agent.proxy.capsule_builder as cb  # noqa: F401
        assert cb is not None

    def test_capsule_builder_class_importable(self):
        from tokenpak.agent.proxy.capsule_builder import CapsuleBuilder  # noqa
        assert CapsuleBuilder is not None

    def test_make_capsule_builder_importable(self):
        from tokenpak.agent.proxy.capsule_builder import make_capsule_builder  # noqa
        assert callable(make_capsule_builder)

    def test_constants_exported(self):
        from tokenpak.agent.proxy.capsule_builder import (
            DEFAULT_HOT_WINDOW,
            DEFAULT_MIN_BLOCK_CHARS,
        )
        assert isinstance(DEFAULT_HOT_WINDOW, int)
        assert isinstance(DEFAULT_MIN_BLOCK_CHARS, int)


# ─────────────────────────────────────────────────────────────────────────────
# CapsuleBuilder via proxy module path
# ─────────────────────────────────────────────────────────────────────────────

class TestCapsuleBuilderViaProxyModule:
    def test_instantiate_disabled(self):
        from tokenpak.agent.proxy.capsule_builder import CapsuleBuilder
        b = CapsuleBuilder(enabled=False)
        assert b._enabled is False

    def test_instantiate_enabled(self):
        from tokenpak.agent.proxy.capsule_builder import CapsuleBuilder
        b = CapsuleBuilder(enabled=True)
        assert b._enabled is True

    def test_process_noop_when_disabled(self):
        from tokenpak.agent.proxy.capsule_builder import CapsuleBuilder
        b = CapsuleBuilder(enabled=False)
        body = json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode()
        out, stats = b.process(body)
        assert out == body
        assert stats["blocks_capsulized"] == 0
        assert stats["skip_reason"] == "disabled"

    def test_process_compresses_when_enabled(self):
        from tokenpak.agent.proxy.capsule_builder import CapsuleBuilder
        b = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        long_text = "This is a long message. " * 30
        body = json.dumps({
            "messages": [{"role": "user", "content": long_text}]
        }).encode()
        out, stats = b.process(body)
        assert stats["blocks_capsulized"] >= 1
        assert b"[CAPSULE" in out

    def test_same_class_as_canonical(self):
        """Proxy module re-exports the canonical CapsuleBuilder — same class."""
        from tokenpak.agent.proxy.capsule_builder import CapsuleBuilder as CB_proxy
        from tokenpak.capsule.builder import CapsuleBuilder as CB_canonical
        assert CB_proxy is CB_canonical


# ─────────────────────────────────────────────────────────────────────────────
# make_capsule_builder factory
# ─────────────────────────────────────────────────────────────────────────────

class TestMakeCapsuleBuilderFactory:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_CAPSULE_BUILDER", raising=False)
        from tokenpak.agent.proxy.capsule_builder import make_capsule_builder
        b = make_capsule_builder()
        assert b._enabled is False

    def test_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "1")
        from tokenpak.agent.proxy.capsule_builder import make_capsule_builder
        b = make_capsule_builder()
        assert b._enabled is True

    def test_not_enabled_by_zero(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "0")
        from tokenpak.agent.proxy.capsule_builder import make_capsule_builder
        b = make_capsule_builder()
        assert b._enabled is False

    def test_custom_params(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_CAPSULE_BUILDER", raising=False)
        from tokenpak.agent.proxy.capsule_builder import make_capsule_builder
        b = make_capsule_builder(min_block_chars=100, hot_window=5)
        assert b._min_block_chars == 100
        assert b._hot_window == 5

    def test_returns_capsule_builder_instance(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_CAPSULE_BUILDER", raising=False)
        from tokenpak.agent.proxy.capsule_builder import make_capsule_builder, CapsuleBuilder
        b = make_capsule_builder()
        assert isinstance(b, CapsuleBuilder)


# ─────────────────────────────────────────────────────────────────────────────
# Determinism via proxy module path
# ─────────────────────────────────────────────────────────────────────────────

class TestDeterminismViaProxyModule:
    def test_same_input_same_output(self):
        from tokenpak.agent.proxy.capsule_builder import CapsuleBuilder
        b = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        long_text = "Determinism test. " * 40
        body = json.dumps({
            "messages": [{"role": "user", "content": long_text}]
        }).encode()
        out1, _ = b.process(body)
        out2, _ = b.process(body)
        assert out1 == out2
