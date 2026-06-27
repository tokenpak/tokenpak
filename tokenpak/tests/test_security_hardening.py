# SPDX-License-Identifier: Apache-2.0
"""Focused regressions for security hardening around local caches, URL fetches, and hooks."""

from __future__ import annotations

import json
import sys

import pytest

from tokenpak.orchestration.macros.hooks import TriggerRegistry
from tokenpak.sources.base_source import SourceFetchError
from tokenpak.sources.url_adapter import URLAdapter, _SafeRedirectHandler, _validate_url_safe
from tokenpak.vault.retrieval.vault_index import VaultIndex


def test_vault_bm25_cache_uses_json_not_pickle(tmp_path):
    tokenpak_dir = tmp_path / ".tokenpak"
    blocks_dir = tokenpak_dir / "blocks"
    blocks_dir.mkdir(parents=True)
    index_path = tokenpak_dir / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "blocks": {
                    "b1": {
                        "source_path": "docs/a.md",
                        "risk_class": "narrative",
                        "must_keep": False,
                        "raw_tokens": 3,
                    }
                }
            }
        )
    )
    (blocks_dir / "b1.txt").write_text("alpha beta alpha")

    first = VaultIndex(str(tokenpak_dir))
    first._load(index_path, index_path.stat().st_mtime)

    cache_path = tokenpak_dir / ".bm25_cache.json"
    assert cache_path.exists()
    assert not (tokenpak_dir / ".bm25_cache.pkl").exists()

    second = VaultIndex(str(tokenpak_dir))
    assert second._try_load_bm25_cache(index_path, index_path.stat().st_mtime) is True
    assert second._inverted["alpha"] == {"b1"}


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_url_adapter_rejects_unsafe_url_before_fetch(url, monkeypatch):
    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("fetch should not run for unsafe URL")

    monkeypatch.setattr("tokenpak.sources.url_adapter._urlopen_checked", fail_fetch)
    with pytest.raises(SourceFetchError):
        URLAdapter().ingest(url)


def test_url_adapter_rejects_hostname_resolving_to_private(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("10.0.0.5", 80))],
    )
    with pytest.raises(SourceFetchError, match="Blocked local or private"):
        _validate_url_safe("https://example.com/path")


def test_url_adapter_rejects_invalid_port():
    with pytest.raises(SourceFetchError, match="Invalid URL port"):
        _validate_url_safe("https://example.com:99999/path")


def test_url_has_changed_rejects_unsafe_url_without_fetch(monkeypatch):
    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("fetch should not run for unsafe URL")

    monkeypatch.setattr("tokenpak.sources.url_adapter._urlopen_checked", fail_fetch)
    assert URLAdapter().has_changed("http://127.0.0.1/admin", "cached-version") is False


def test_url_redirect_handler_rejects_metadata_redirect():
    handler = _SafeRedirectHandler()
    with pytest.raises(SourceFetchError, match="Blocked local or private"):
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "http://169.254.169.254/latest/meta-data",
        )


def test_trigger_event_data_is_argv_not_shell(tmp_path):
    marker = tmp_path / "shell-ran"
    registry = TriggerRegistry(
        triggers_path=tmp_path / "triggers.json",
        log_path=tmp_path / "trigger-log.json",
    )
    registry.add(
        event_type="file:changed",
        pattern="*",
        action=f"{sys.executable} -c \"import sys; print(sys.argv[1])\" $EVENT_DATA",
    )

    payload = f"changed.txt; touch {marker}"
    entries = registry.fire("file:changed", payload)

    assert len(entries) == 1
    assert entries[0].success is True
    assert payload in entries[0].output
    assert not marker.exists()
