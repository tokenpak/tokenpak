"""Regression guards for the legacy compression-flag compatibility boundary."""

from __future__ import annotations

import ast
import http.client
from pathlib import Path

import pytest

from tests.proxy._proxy_subprocess import ProxyProc

ROOT = Path(__file__).resolve().parents[2]


def _tree(relative_path: str) -> ast.Module:
    return ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))


def _package_sources() -> list[Path]:
    package_root = ROOT / "tokenpak"
    return [
        path
        for path in package_root.rglob("*.py")
        if "tests" not in path.relative_to(package_root).parts
    ]


def _loaded_name_paths(target: str) -> set[str]:
    paths: set[str] = set()
    for path in _package_sources():
        source = path.read_text(encoding="utf-8")
        if target not in source:
            continue
        tree = ast.parse(source)
        if any(
            isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == target
            for node in ast.walk(tree)
        ):
            paths.add(path.relative_to(ROOT).as_posix())
    return paths


def test_default_http_modules_do_not_call_legacy_compactor() -> None:
    """The built-in HTTP path must not acquire a direct helper call site."""
    for relative_path in (
        "tokenpak/proxy/server.py",
        "tokenpak/proxy/pipeline.py",
        "tokenpak/proxy/request_pipeline.py",
    ):
        tree = _tree(relative_path)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        called = {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        assert "compact_request_body" not in imported
        assert "compact_request_body" not in called


def test_enable_compaction_has_only_compatibility_export_locations() -> None:
    """The legacy flag must not gain a behavioral consumer silently."""
    containing_paths = {
        path.relative_to(ROOT).as_posix()
        for path in _package_sources()
        if "ENABLE_COMPACTION" in path.read_text(encoding="utf-8")
    }
    assert containing_paths == {
        "tokenpak/proxy/__init__.py",
        "tokenpak/proxy/config.py",
    }
    assert _loaded_name_paths("ENABLE_COMPACTION") == {"tokenpak/proxy/config.py"}


def test_threshold_is_loaded_only_by_the_explicit_helper() -> None:
    """The threshold may tune an explicit helper but must not activate HTTP."""
    assert _loaded_name_paths("COMPACT_THRESHOLD_TOKENS") == {"tokenpak/compression/pipeline.py"}


@pytest.mark.needs_proxy
@pytest.mark.timeout(120)
@pytest.mark.parametrize("legacy_flag", ["0", "1"])
def test_default_http_body_is_byte_identical_for_legacy_flag_values(
    stub_upstream, legacy_flag: str
) -> None:
    """A real default HTTP exchange forwards the original request bytes."""
    history = b"alpha beta gamma delta " * 700
    payload = (
        b'{\n  "model" : "claude-sonnet-4-5",\n  "max_tokens" : 32,\n'
        b'  "messages" : [{"role":"user","content":"'
        + history
        + b'"},{"role":"user","content":"keep this turn exact"}]\n}'
    )
    assert len(payload) > 10_000

    proxy = ProxyProc(
        f"http://127.0.0.1:{stub_upstream.server_port}",
        extra_env={
            "TOKENPAK_COMPACT": legacy_flag,
            "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "1",
            "TOKENPAK_CAPSULE_BUILDER": "0",
            "TOKENPAK_VAULT_INJECTION": "0",
        },
    )
    try:
        proxy.wait_ready()
        conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=60)
        try:
            conn.putrequest("POST", "/v1/messages")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("x-api-key", "test-key")
            conn.putheader("Content-Length", str(len(payload)))
            conn.endheaders(payload)
            response = conn.getresponse()
            response.read()
        finally:
            conn.close()

        assert response.status == 200
        assert stub_upstream.last_request_body == payload
    finally:
        proxy.cleanup()


def test_public_and_setup_surfaces_state_the_compatibility_boundary() -> None:
    required_phrases = {
        "docs/DEPLOYMENT.md": (
            "no consumer in the default http request path",
            "explicitly call `compact_request_body`",
        ),
        "docs/compression.md": (
            "compatibility-only settings",
            "neither the helper nor the default http path",
        ),
        "docs/guides/proxy-setup.md": (
            "do not toggle proxy body compaction",
            "explicitly calls `compact_request_body`",
        ),
        "docs/cli-reference.md": (
            "does not toggle default http body compaction",
            "default http proxy does not invoke this body-compaction path",
        ),
        "tokenpak/proxy/config.py": (
            "legacy compatibility surface",
            "the default http proxy path does not read",
        ),
        "tokenpak/proxy/server.py": (
            "compatibility-only; no default-http consumer",
            "explicit compact-helper threshold",
        ),
        "tokenpak/_cli_core.py": (
            "does not toggle default http body compaction",
            "default http proxy does not invoke this body-compaction path",
        ),
    }
    for relative_path, phrases in required_phrases.items():
        surface = " ".join((ROOT / relative_path).read_text(encoding="utf-8").lower().split())
        for phrase in phrases:
            assert phrase in surface, f"{relative_path} is missing truth marker: {phrase}"
