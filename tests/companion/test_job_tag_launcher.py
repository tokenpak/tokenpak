"""Regression: launch-local job attribution preserves client routing and headers."""

import logging

import pytest

from tokenpak.companion import launcher
from tokenpak.companion.config import CompanionConfig


@pytest.mark.parametrize("tag", ["orca:job_example:1", "malformed\nsecret"])
def test_claude_work_tag_is_launch_local(monkeypatch, tmp_path, caplog, tag):
    cfg = CompanionConfig(journal_dir=tmp_path / "journal", proxy_url="http://127.0.0.1:8766")
    monkeypatch.setattr(launcher.CompanionConfig, "from_env", lambda: cfg)
    monkeypatch.setattr(launcher, "_launcher_mode_state", lambda: ("inherit", None))
    monkeypatch.setenv("TOKENPAK_JOB_TAG", tag)
    monkeypatch.setenv(
        "ANTHROPIC_CUSTOM_HEADERS", "Existing: retained\nX-Tokenpak-Tag: job_stale/9"
    )
    captured = []
    monkeypatch.setattr(launcher.os, "execvpe", lambda binary, args, env: captured.append(env))
    with caplog.at_level(logging.WARNING):
        launcher.main(["--status-surface=off"])
    assert len(captured) == 1
    headers = captured[0]["ANTHROPIC_CUSTOM_HEADERS"]
    assert headers == "Existing: retained\nX-Tokenpak-Tag: job_stale/9"
    url = captured[0]["ANTHROPIC_BASE_URL"]
    if tag.startswith("orca:"):
        assert url == "http://127.0.0.1:8766/_tokenpak/t/orca:job_example:1"
    else:
        assert url == "http://127.0.0.1:8766"
        assert len([r for r in caplog.records if "malformed TokenPak job tag" in r.message]) == 1
        assert "secret" not in caplog.text


def test_codex_tag_reuses_existing_base_url_override(monkeypatch):
    from urllib.parse import urlsplit

    from tokenpak.companion.codex import launcher as codex
    from tokenpak.core.work_tag import extract_job_path

    monkeypatch.setattr(codex, "_local_proxy_is_healthy", lambda: True)
    monkeypatch.delenv("TOKENPAK_JOB_TAG", raising=False)
    original, _ = codex._with_tokenpak_proxy_route(["--model", "chosen"])
    monkeypatch.setenv("TOKENPAK_JOB_TAG", "orca:job_example:2")
    tagged, routed = codex._with_tokenpak_proxy_route(["--model", "chosen"])
    assert routed
    assert tagged.count("-c") == original.count("-c")
    changed = [(a, b) for a, b in zip(original, tagged) if a != b]
    assert len(changed) == 1 and ".base_url=" in changed[0][0]
    url = changed[0][1].split("=", 1)[1].strip('"')
    path, tag = extract_job_path(urlsplit(url).path + "/responses")
    assert tag == "orca:job_example:2"
    assert path == urlsplit(codex._TOKENPAK_OPENAI_BASE_URL).path + "/responses"
    override, routed = codex._with_tokenpak_proxy_route(["-c", 'model_provider="external"'])
    assert not routed and override == ["-c", 'model_provider="external"']


def test_malformed_codex_tag_preserves_route(monkeypatch, caplog):
    from tokenpak.companion.codex import launcher as codex

    monkeypatch.setattr(codex, "_local_proxy_is_healthy", lambda: True)
    monkeypatch.delenv("TOKENPAK_JOB_TAG", raising=False)
    original, _ = codex._with_tokenpak_proxy_route([])
    monkeypatch.setenv("TOKENPAK_JOB_TAG", "job_bad/no\nsecret")
    with caplog.at_level(logging.WARNING):
        actual, routed = codex._with_tokenpak_proxy_route([])
    assert actual == original and routed
    assert len(caplog.records) == 1 and "secret" not in caplog.text
