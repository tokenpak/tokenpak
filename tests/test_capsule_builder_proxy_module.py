"""
Tests for tokenpak.proxy.capsule_builder — the proxy-layer module
that exposes CapsuleBuilder at the path the proxy pipeline expects.
"""

from __future__ import annotations

import json

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Import checks
# ─────────────────────────────────────────────────────────────────────────────


class TestModuleImports:
    def test_import_module(self):
        """Module must be importable at the proxy-layer path."""
        import tokenpak.proxy.capsule_builder as cb  # noqa: F401

        assert cb is not None

    def test_capsule_builder_class_importable(self):
        from tokenpak.proxy.capsule_builder import CapsuleBuilder  # noqa

        assert CapsuleBuilder is not None

    def test_make_capsule_builder_importable(self):
        from tokenpak.proxy.capsule_builder import make_capsule_builder  # noqa

        assert callable(make_capsule_builder)

    def test_constants_exported(self):
        from tokenpak.proxy.capsule_builder import (
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
        from tokenpak.proxy.capsule_builder import CapsuleBuilder

        b = CapsuleBuilder(enabled=False)
        assert b._enabled is False

    def test_instantiate_enabled(self):
        from tokenpak.proxy.capsule_builder import CapsuleBuilder

        b = CapsuleBuilder(enabled=True)
        assert b._enabled is True

    def test_process_noop_when_disabled(self):
        from tokenpak.proxy.capsule_builder import CapsuleBuilder

        b = CapsuleBuilder(enabled=False)
        body = json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode()
        out, stats = b.process(body)
        assert out == body
        assert stats["blocks_capsulized"] == 0
        assert stats["skip_reason"] == "disabled"

    def test_process_compresses_when_enabled(self):
        from tokenpak.proxy.capsule_builder import CapsuleBuilder

        b = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        long_text = "This is a long message. " * 30
        body = json.dumps({"messages": [{"role": "user", "content": long_text}]}).encode()
        out, stats = b.process(body)
        assert stats["blocks_capsulized"] >= 1
        assert b"[CAPSULE" in out

    def test_never_inflates_structure_heavy_block(self):
        """A block the compressor cannot shrink is left untouched.

        Structure lines (headings, bullets, code fences) pass through the
        compressor verbatim, so the capsule envelope would make the block
        strictly larger. The builder must skip it rather than inflate the
        request.
        """
        from tokenpak.proxy.capsule_builder import CapsuleBuilder

        structure_heavy = "\n".join(
            [
                "# Heading one",
                "- bullet item alpha",
                "- bullet item beta",
                "```",
                "code line that must be preserved verbatim inside the fence",
                "```",
                "## Heading two",
                "1. ordered item one",
                "2. ordered item two",
            ]
            * 8
        )
        assert len(structure_heavy) >= 400
        b = CapsuleBuilder(enabled=True, hot_window=0)
        body = json.dumps({"messages": [{"role": "user", "content": structure_heavy}]}).encode()
        out, stats = b.process(body)
        assert out == body
        assert stats["blocks_capsulized"] == 0
        assert b"[CAPSULE" not in out

    def test_capsulized_output_is_never_larger_than_input(self):
        """When a block IS capsulized, the result must be a net shrink."""
        from tokenpak.proxy.capsule_builder import CapsuleBuilder

        prose = (
            "This is a long prose paragraph that keeps adding descriptive "
            "sentences well past the compressor's paragraph budget so that "
            "deterministic truncation has real material to work with. "
        ) * 10
        b = CapsuleBuilder(enabled=True, hot_window=0)
        body = json.dumps({"messages": [{"role": "user", "content": prose}]}).encode()
        out, stats = b.process(body)
        assert stats["blocks_capsulized"] == 1
        assert len(out) < len(body)
        assert stats["ratio"] < 1.0

    def test_same_class_as_canonical(self):
        """Proxy module re-exports the canonical CapsuleBuilder — same class."""
        # WS-A residual import guard — TSR-01-followup. tokenpak.capsule.builder
        # is the canonical home; on slim [dev] install (without the full
        # capsule namespace) this re-export check can't run.
        pytest.importorskip(
            "tokenpak.capsule.builder",
            reason="tokenpak.capsule.builder absent on slim OSS install",
        )
        from tokenpak.capsule.builder import CapsuleBuilder as CB_canonical

        from tokenpak.proxy.capsule_builder import CapsuleBuilder as CB_proxy

        assert CB_proxy is CB_canonical


# ─────────────────────────────────────────────────────────────────────────────
# make_capsule_builder factory
# ─────────────────────────────────────────────────────────────────────────────


class TestMakeCapsuleBuilderFactory:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_CAPSULE_BUILDER", raising=False)
        from tokenpak.proxy.capsule_builder import make_capsule_builder

        b = make_capsule_builder()
        assert b._enabled is False

    def test_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "1")
        from tokenpak.proxy.capsule_builder import make_capsule_builder

        b = make_capsule_builder()
        assert b._enabled is True

    def test_not_enabled_by_zero(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "0")
        from tokenpak.proxy.capsule_builder import make_capsule_builder

        b = make_capsule_builder()
        assert b._enabled is False

    def test_custom_params(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_CAPSULE_BUILDER", raising=False)
        from tokenpak.proxy.capsule_builder import make_capsule_builder

        b = make_capsule_builder(min_block_chars=100, hot_window=5)
        assert b._min_block_chars == 100
        assert b._hot_window == 5

    def test_returns_capsule_builder_instance(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_CAPSULE_BUILDER", raising=False)
        from tokenpak.proxy.capsule_builder import CapsuleBuilder, make_capsule_builder

        b = make_capsule_builder()
        assert isinstance(b, CapsuleBuilder)


# ─────────────────────────────────────────────────────────────────────────────
# Determinism via proxy module path
# ─────────────────────────────────────────────────────────────────────────────


class TestDeterminismViaProxyModule:
    def test_same_input_same_output(self):
        from tokenpak.proxy.capsule_builder import CapsuleBuilder

        b = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        long_text = "Determinism test. " * 40
        body = json.dumps({"messages": [{"role": "user", "content": long_text}]}).encode()
        out1, _ = b.process(body)
        out2, _ = b.process(body)
        assert out1 == out2
