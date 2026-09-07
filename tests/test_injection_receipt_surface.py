# SPDX-License-Identifier: Apache-2.0
"""The vault-injection receipt must reach a surface a user can read.

Regression coverage for a defect where the receipt was computed and discarded:
the vault stage recorded ``injected_tokens`` / ``injected_sources`` onto
``StageResult.details``, and nothing consumed them. The monitor row took
hardcoded ``0`` / ``""``, the live-session counters had no writer at all, and
``tokenpak status`` therefore reported "0 across 0 requests" permanently while
injection was working correctly on every eligible request.

The defect class is **"value computed, never surfaced"**, so these tests
assert at the *render* site and at the *persistence* site — and the
persistence site is ``ProxyServer.session``, the dict request accounting and
``GET /stats`` actually read, not the ``tokenpak.proxy.bootstrap.SESSION``
compatibility global that no live request path touches. A test that
monkeypatched that global (an earlier version of this file did) would pass
regardless of which object the writer actually updated — precisely the gap
that let the first fix land silently wired to the wrong object.

These tests also assert the fail-open contract (PR #744 precedent): valid
non-object JSON and an injected exception in the receipt path must never
raise or break the response.
"""

from __future__ import annotations

import json

import pytest

from tokenpak.cli.commands import status as status_mod
from tokenpak.cli.commands.status import _format_injected_sources
from tokenpak.proxy.server import (
    _MAX_SESSION_SOURCES,
    ProxyServer,
    _read_injection_receipt,
    _record_injection_in_session,
)


class _Stage:
    def __init__(self, name, skipped=False, details=None, skip_reason=""):
        self.name = name
        self.skipped = skipped
        self.details = details if details is not None else {}
        self.skip_reason = skip_reason


class _Result:
    def __init__(self, stages):
        self.stages = stages


def _injecting_result(tokens=412, sources="decisions/auth.md,notes/api.md"):
    return _Result(
        [
            _Stage("cache_poison_removal"),
            _Stage(
                "vault_injection",
                details={"injected_tokens": tokens, "injected_sources": sources},
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Reading the receipt off the pipeline
# ---------------------------------------------------------------------------


def test_receipt_is_read_from_the_vault_stage():
    tokens, sources, skip_reason = _read_injection_receipt(_injecting_result())
    assert tokens == 412
    assert sources == "decisions/auth.md,notes/api.md"
    assert skip_reason == ""


def test_receipt_accepts_sources_as_a_sequence():
    """``compile_injection`` returns source refs as a sequence, not a string."""
    result = _Result(
        [
            _Stage(
                "vault_injection",
                details={"injected_tokens": 7, "injected_sources": ["a.md", "b.md"]},
            )
        ]
    )
    assert _read_injection_receipt(result) == (7, "a.md,b.md", "")


@pytest.mark.parametrize(
    "result",
    [
        _Result([]),
        _Result([_Stage("compaction")]),
        _Result([_Stage("vault_injection", skipped=True, details={"injected_tokens": 99})]),
    ],
    ids=["no-stages", "no-vault-stage", "vault-stage-skipped"],
)
def test_non_injecting_requests_report_a_measured_zero(result):
    assert _read_injection_receipt(result) == (0, "", "")


@pytest.mark.parametrize("reason", ["retrieval_timeout", "retrieval_backlog"])
def test_bounded_retrieval_skip_reason_is_preserved(reason):
    result = _Result([_Stage("vault_injection", skipped=True, skip_reason=reason)])
    assert _read_injection_receipt(result) == (0, "", reason)


def test_malformed_details_do_not_raise():
    """Telemetry must never break a request, including on garbage input."""
    result = _Result(
        [
            _Stage(
                "vault_injection",
                details={"injected_tokens": "not-a-number", "injected_sources": None},
            )
        ]
    )
    assert _read_injection_receipt(result) == (0, "", "")


# ---------------------------------------------------------------------------
# Fail-open: valid non-object JSON and injected exceptions, receipt side
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "details",
    [["a.md", "b.md"], 42, "raw-string"],
    ids=["list", "int", "str"],
)
def test_receipt_reading_fails_open_on_non_dict_details(details):
    """``details`` deserialized as a list/scalar instead of an object — the
    "valid non-object JSON" shape — must report a measured zero, not raise."""
    result = _Result([_Stage("vault_injection", details=details)])
    assert _read_injection_receipt(result) == (0, "", "")


def test_receipt_reading_fails_open_on_a_raising_stages_property():
    """An exception raised while walking the pipeline result (a corrupted or
    adversarial trace object) must degrade to the measured-zero default,
    never propagate and break the request."""

    class _ExplodingResult:
        @property
        def stages(self):
            raise RuntimeError("synthetic failure reading stages")

    assert _read_injection_receipt(_ExplodingResult()) == (0, "", "")


def test_receipt_reading_fails_open_on_a_raising_stage_attribute():
    class _ExplodingStage:
        name = "vault_injection"

        @property
        def skipped(self):
            raise RuntimeError("synthetic failure reading skipped")

    assert _read_injection_receipt(_Result([_ExplodingStage()])) == (0, "", "")


def test_session_recording_fails_open_on_an_injected_exception():
    """Even if the session mapping itself misbehaves, telemetry must never
    raise into the request path."""

    class _ExplodingSession(dict):
        def get(self, *_a, **_kw):
            raise RuntimeError("synthetic failure reading session")

    _record_injection_in_session(_ExplodingSession(), 100, "a.md")  # must not raise


# ---------------------------------------------------------------------------
# Reaching the live session — the real object `tokenpak status` reads
# ---------------------------------------------------------------------------


@pytest.fixture
def proxy_server() -> ProxyServer:
    """A ``ProxyServer`` with no socket bound — the object real requests
    accumulate onto via ``ps.session`` under ``ps._session_lock``, and that
    ``GET /stats`` serializes verbatim as the ``session`` key."""
    return ProxyServer(host="127.0.0.1", port=0)


def test_injection_accumulates_onto_the_real_session(proxy_server):
    with proxy_server._session_lock:
        _record_injection_in_session(proxy_server.session, 412, "decisions/auth.md,notes/api.md")
        _record_injection_in_session(proxy_server.session, 88, "decisions/auth.md,other.md")

    session = proxy_server.session
    assert session["injected_tokens"] == 500
    assert session["injection_hits"] == 2
    # De-duplicated, insertion-ordered.
    assert session["injected_source_names"] == ["decisions/auth.md", "notes/api.md", "other.md"]


def test_non_injecting_request_does_not_count_as_a_hit(proxy_server):
    with proxy_server._session_lock:
        _record_injection_in_session(proxy_server.session, 0, "")
    assert proxy_server.session["injected_tokens"] == 0
    assert proxy_server.session["injection_hits"] == 0


def test_bounded_retrieval_degradation_reaches_stats(proxy_server):
    with proxy_server._session_lock:
        _record_injection_in_session(proxy_server.session, 0, "", "retrieval_timeout")
        _record_injection_in_session(proxy_server.session, 0, "", "retrieval_timeout")
        _record_injection_in_session(proxy_server.session, 0, "", "retrieval_backlog")

    session = proxy_server.stats()["session"]
    assert session["injection_skips"] == 3
    assert session["injection_skip_reasons"] == {
        "retrieval_timeout": 2,
        "retrieval_backlog": 1,
    }


def test_session_source_list_is_bounded(proxy_server):
    with proxy_server._session_lock:
        for i in range(_MAX_SESSION_SOURCES * 3):
            _record_injection_in_session(proxy_server.session, 1, f"source-{i}.md")
    names = proxy_server.session["injected_source_names"]
    assert len(names) == _MAX_SESSION_SOURCES
    # The cap drops the oldest, so the most recent injection is still nameable.
    assert f"source-{_MAX_SESSION_SOURCES * 3 - 1}.md" in names


def test_stats_reflects_the_session_it_was_copied_from(proxy_server):
    """``GET /stats`` (``ProxyServer.stats()``) must carry the same values
    that landed on ``ps.session`` — this is the field `tokenpak status`
    reads, and the field the earlier fix wrote to a different, unread object."""
    with proxy_server._session_lock:
        _record_injection_in_session(proxy_server.session, 412, "decisions/auth.md")
    payload = proxy_server.stats()
    assert payload["session"]["injected_tokens"] == 412
    assert payload["session"]["injection_hits"] == 1
    assert payload["session"]["injected_source_names"] == ["decisions/auth.md"]


# ---------------------------------------------------------------------------
# Reaching the user — the render site
# ---------------------------------------------------------------------------


def test_sources_are_named_at_the_render_site():
    lines = _format_injected_sources(["decisions/auth.md", "notes/api.md"])
    rendered = "\n".join(lines)
    assert "decisions/auth.md" in rendered
    assert "notes/api.md" in rendered


def test_render_accepts_the_persisted_string_form():
    """The monitor column stores a comma-separated string."""
    rendered = "\n".join(_format_injected_sources("a.md,b.md"))
    assert "a.md" in rendered and "b.md" in rendered


@pytest.mark.parametrize("empty", ["", [], None, 0], ids=["str", "list", "none", "int"])
def test_render_emits_nothing_when_there_is_nothing_to_name(empty):
    assert _format_injected_sources(empty) == []


def test_render_truncates_but_says_so():
    """A silently shortened list reads as a complete one."""
    names = [f"src-{i}.md" for i in range(12)]
    lines = _format_injected_sources(names)
    rendered = "\n".join(lines)

    assert "src-0.md" in rendered
    assert "src-11.md" not in rendered
    assert "+7 more" in rendered


# ---------------------------------------------------------------------------
# The end-to-end property the defect violated: pipeline -> real session ->
# GET /stats -> the status consumer's parsing -> rendered output.
# ---------------------------------------------------------------------------


def test_injecting_request_is_nameable_end_to_end_via_the_real_stats_surface(
    monkeypatch, tmp_path, capsys
):
    """Pipeline result -> ``ProxyServer.session`` -> ``GET /stats`` payload ->
    ``tokenpak status``'s own parsing of that payload -> rendered output.

    This is the assertion whose absence let the defect ship, twice: every
    individual layer can be correct in isolation while the value never
    reaches a human, either because nothing writes it (the original defect)
    or because it writes an object no consumer reads (the first fix).
    """
    ps = ProxyServer(host="127.0.0.1", port=0)
    tokens, sources, skip_reason = _read_injection_receipt(_injecting_result())
    with ps._session_lock:
        # A real request also bumps `requests` in this same locked block —
        # without it `status.run()` takes its "no measurements yet" early
        # exit before ever reaching the injection line.
        ps.session["requests"] += 1
        _record_injection_in_session(ps.session, tokens, sources, skip_reason)

    stats_payload = ps.stats()

    def fetch(url: str, timeout: int = 5):
        del timeout
        if url.endswith("/health"):
            return {"status": "ok"}
        if url.endswith("/stats"):
            return stats_payload
        return None

    monkeypatch.setattr(status_mod, "_fetch", fetch)
    monkeypatch.setattr(status_mod, "_session_economics_enabled", lambda: False)

    status_mod.run(
        proxy_base="http://127.0.0.1:0",
        db_path=str(tmp_path / "no-monitor.db"),
        no_meme=True,
    )
    output = capsys.readouterr().out

    assert "Vault injected" in output
    assert "across 1 requests" in output
    assert "decisions/auth.md" in output
    assert "notes/api.md" in output


# ---------------------------------------------------------------------------
# Fail-open: valid non-object JSON at the status consumer boundary
# ---------------------------------------------------------------------------


class _RawResponse:
    """Minimal context-manager stand-in for ``urllib.request.urlopen``'s return."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self):
        return self._body


@pytest.mark.parametrize(
    "body",
    [b"42", b"[1, 2, 3]", b"null", b'"just a string"'],
    ids=["scalar-int", "list", "null", "string"],
)
def test_fetch_fails_open_on_valid_non_object_json(monkeypatch, body):
    """``/stats`` returning valid-but-non-object JSON (a corrupted proxy build,
    or a fuzzed/hostile response) must resolve to "no data", never raise."""
    monkeypatch.setattr(status_mod.urllib.request, "urlopen", lambda *_a, **_kw: _RawResponse(body))
    assert status_mod._fetch("http://127.0.0.1:0/stats") is None


def test_run_survives_stats_endpoint_returning_valid_non_object_json(monkeypatch, tmp_path, capsys):
    """Exercises the real ``_fetch`` (not a pre-resolved stand-in) so the
    ``/stats`` boundary itself, not just a mock, is what fails open here.
    """
    bodies = {
        "/health": json.dumps({"status": "ok"}).encode(),
        "/stats": b"[1, 2, 3]",  # valid JSON, not an object
        "/cache-stats": b"null",
    }

    def urlopen(url, timeout=5):
        del timeout
        for suffix, body in bodies.items():
            if url.endswith(suffix):
                return _RawResponse(body)
        return _RawResponse(b"null")

    monkeypatch.setattr(status_mod.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(status_mod, "_session_economics_enabled", lambda: False)

    status_mod.run(
        proxy_base="http://127.0.0.1:0",
        db_path=str(tmp_path / "no-monitor.db"),
        no_meme=True,
    )
    output = capsys.readouterr().out

    # No live session data survives a non-object /stats payload — nothing
    # about the vault injection line (which needs `session`) is fabricated.
    assert "Vault injected" not in output
