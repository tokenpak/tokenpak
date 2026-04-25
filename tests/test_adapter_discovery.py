# SPDX-License-Identifier: Apache-2.0
"""Plugin discovery — entry points + filesystem drop-in for FormatAdapter."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from tokenpak.proxy.adapters import (
    build_default_registry,
    build_registry,
    discover_filesystem_adapters,
    register_discovered,
)
from tokenpak.proxy.adapters.discovery import (
    _validate_adapter_class,
    discovery_enabled,
)

# ── Filesystem drop-in path ──────────────────────────────────────────


VALID_PLUGIN_SRC = dedent('''
    """Minimal plugin adapter for testing the filesystem drop-in path."""
    from tokenpak.proxy.adapters.base import FormatAdapter
    from tokenpak.proxy.adapters.canonical import CanonicalRequest


    class MyTestAdapter(FormatAdapter):
        source_format = "my-test"
        capabilities = frozenset({"tip.byte-preserved-passthrough"})

        def detect(self, path, headers, body):
            return "/my-test/" in path

        def normalize(self, body):
            return CanonicalRequest(
                model="my-test-model",
                system="",
                messages=[],
                tools=None,
                generation={},
                stream=False,
                raw_extra={"raw": body},
                source_format=self.source_format,
            )

        def denormalize(self, canonical):
            return canonical.raw_extra.get("raw", b"") or b""

        def get_default_upstream(self):
            return "https://my-test.example.com"
''').lstrip()


class TestFilesystemDiscovery:
    def test_loads_a_valid_plugin_file(self, tmp_path: Path):
        (tmp_path / "my_test.py").write_text(VALID_PLUGIN_SRC)
        found = discover_filesystem_adapters(tmp_path)
        assert len(found) == 1
        adapter, priority, source = found[0]
        assert adapter.source_format == "my-test"
        assert priority == 100  # default plugin priority
        assert "my_test.py" in source

    def test_skips_files_starting_with_underscore(self, tmp_path: Path):
        (tmp_path / "_disabled.py").write_text(VALID_PLUGIN_SRC)
        assert discover_filesystem_adapters(tmp_path) == []

    def test_missing_directory_returns_empty(self, tmp_path: Path):
        assert discover_filesystem_adapters(tmp_path / "no-such-dir") == []

    def test_skips_non_format_adapter_classes(self, tmp_path: Path):
        # Random class that ISN'T a FormatAdapter — should be skipped
        # without crashing the discovery pass.
        (tmp_path / "junk.py").write_text(
            "class NotAnAdapter:\n    pass\n"
        )
        assert discover_filesystem_adapters(tmp_path) == []

    def test_skips_module_with_syntax_error(self, tmp_path: Path, caplog):
        (tmp_path / "broken.py").write_text("class :::\n")
        # Discovery should log + skip, not crash.
        result = discover_filesystem_adapters(tmp_path)
        assert result == []

    def test_skips_imported_FormatAdapter_itself(self, tmp_path: Path):
        # ``from .base import FormatAdapter`` brings the abstract base
        # class into the module's namespace. Discovery must NOT try to
        # instantiate it (it's abstract; would fail) — only classes
        # actually defined in the dropin module count.
        src = (
            "from tokenpak.proxy.adapters.base import FormatAdapter\n"
            "# Just imports — no subclass declared in this file.\n"
        )
        (tmp_path / "imports_only.py").write_text(src)
        assert discover_filesystem_adapters(tmp_path) == []

    def test_skips_adapter_with_invalid_capability_label(self, tmp_path: Path):
        bad_src = VALID_PLUGIN_SRC.replace(
            'capabilities = frozenset({"tip.byte-preserved-passthrough"})',
            'capabilities = frozenset({"NOT_A_TIP_LABEL"})',
        )
        (tmp_path / "bad_caps.py").write_text(bad_src)
        assert discover_filesystem_adapters(tmp_path) == []

    def test_loads_plugin_with_explicit_priority(self, tmp_path: Path):
        custom_src = VALID_PLUGIN_SRC.replace(
            '    source_format = "my-test"\n',
            '    source_format = "my-test"\n    priority = 275\n',
        )
        (tmp_path / "with_pri.py").write_text(custom_src)
        found = discover_filesystem_adapters(tmp_path)
        assert len(found) == 1
        _, priority, _ = found[0]
        assert priority == 275


# ── Validation rules ─────────────────────────────────────────────────


class TestValidation:
    def test_rejects_non_class(self):
        assert _validate_adapter_class(lambda: None, "test") is False

    def test_rejects_non_subclass(self):
        class NotAdapter:
            pass

        assert _validate_adapter_class(NotAdapter, "test") is False

    def test_rejects_FormatAdapter_itself(self):
        from tokenpak.proxy.adapters.base import FormatAdapter

        assert _validate_adapter_class(FormatAdapter, "test") is False

    def test_rejects_empty_source_format(self):
        from tokenpak.proxy.adapters.base import FormatAdapter

        class NoFormat(FormatAdapter):
            source_format = ""
            def detect(self, path, headers, body): return False
            def normalize(self, body): return None
            def denormalize(self, canonical): return b""
            def get_default_upstream(self): return ""

        assert _validate_adapter_class(NoFormat, "test") is False

    def test_rejects_default_unknown_source_format(self):
        from tokenpak.proxy.adapters.base import FormatAdapter

        class UnknownFormat(FormatAdapter):
            # Doesn't override default ``source_format = "unknown"``.
            def detect(self, path, headers, body): return False
            def normalize(self, body): return None
            def denormalize(self, canonical): return b""
            def get_default_upstream(self): return ""

        assert _validate_adapter_class(UnknownFormat, "test") is False

    def test_accepts_well_formed_subclass(self):
        from tokenpak.proxy.adapters.base import FormatAdapter

        class Good(FormatAdapter):
            source_format = "good"
            capabilities = frozenset({"tip.compression.v1"})
            def detect(self, path, headers, body): return False
            def normalize(self, body): return None
            def denormalize(self, canonical): return b""
            def get_default_upstream(self): return ""

        assert _validate_adapter_class(Good, "test") is True

    def test_accepts_ext_namespace_capability(self):
        # ``ext.<vendor>.<feature>`` is the third-party namespace per
        # the registry schema. Should validate.
        from tokenpak.proxy.adapters.base import FormatAdapter

        class WithExt(FormatAdapter):
            source_format = "ext-good"
            capabilities = frozenset({"ext.acme.proprietary-feature"})
            def detect(self, path, headers, body): return False
            def normalize(self, body): return None
            def denormalize(self, canonical): return b""
            def get_default_upstream(self): return ""

        assert _validate_adapter_class(WithExt, "test") is True


# ── Registration into a registry ──────────────────────────────────────


class TestRegisterDiscovered:
    def test_registers_filesystem_adapter_into_registry(self, tmp_path: Path):
        (tmp_path / "p1.py").write_text(VALID_PLUGIN_SRC)
        registry = build_default_registry()
        before = len(registry.adapters())
        count = register_discovered(
            registry,
            include_entry_points=False,
            filesystem_dir=tmp_path,
        )
        assert count == 1
        assert len(registry.adapters()) == before + 1
        formats = {a.source_format for a in registry.adapters()}
        assert "my-test" in formats

    def test_builtin_wins_on_format_collision(self, tmp_path: Path):
        # A drop-in adapter declaring source_format = "anthropic-messages"
        # should be rejected — the built-in AnthropicAdapter wins.
        colliding = VALID_PLUGIN_SRC.replace(
            'source_format = "my-test"',
            'source_format = "anthropic-messages"',
        )
        (tmp_path / "collide.py").write_text(colliding)
        registry = build_default_registry()
        count = register_discovered(
            registry,
            include_entry_points=False,
            filesystem_dir=tmp_path,
        )
        assert count == 0

    def test_disable_env_short_circuits_discovery(self, tmp_path: Path, monkeypatch):
        (tmp_path / "p1.py").write_text(VALID_PLUGIN_SRC)
        monkeypatch.setenv("TOKENPAK_DISABLE_ADAPTER_PLUGINS", "1")
        registry = build_default_registry()
        count = register_discovered(
            registry,
            include_entry_points=False,
            filesystem_dir=tmp_path,
        )
        assert count == 0

    def test_discovery_enabled_default(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_DISABLE_ADAPTER_PLUGINS", raising=False)
        assert discovery_enabled() is True


# ── End-to-end via build_registry ─────────────────────────────────────


class TestBuildRegistry:
    def test_build_registry_includes_builtins_and_filesystem_adapters(
        self, tmp_path: Path, monkeypatch
    ):
        # Point the dropin discovery at our tmp dir for this test.
        monkeypatch.setattr(
            "tokenpak.proxy.adapters.discovery._DEFAULT_DROPIN_DIR", tmp_path
        )
        (tmp_path / "p1.py").write_text(VALID_PLUGIN_SRC)
        registry = build_registry()
        formats = {a.source_format for a in registry.adapters()}
        # Built-ins still present
        assert "anthropic-messages" in formats
        assert "passthrough" in formats
        # Plugin discovered
        assert "my-test" in formats

    def test_build_registry_with_no_plugins_matches_default(self, monkeypatch):
        # Empty dropin dir + no entry points → identical to default.
        monkeypatch.setattr(
            "tokenpak.proxy.adapters.discovery._DEFAULT_DROPIN_DIR",
            Path("/nonexistent/path/for/test"),
        )
        default = {a.source_format for a in build_default_registry().adapters()}
        full = {a.source_format for a in build_registry().adapters()}
        # Full may have entry-point plugins from the host environment;
        # but it must contain everything default has + nothing fewer.
        assert default <= full
