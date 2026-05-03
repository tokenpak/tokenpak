# SPDX-License-Identifier: Apache-2.0
"""OAS-11 — Path C ``_openclaw_extract`` reads ``active.json``.

Per ``initiative 2026-04-28-openclaw-adapter-session-binding`` and
``feedback_status_attribution_contract`` (never over-claim). These tests
pin Kevin's six verification gates from the OAS-11 task spec:

  G1 — User-Agent gate: only ``openclaw*`` UAs trigger an active.json read.
  G2 — Schema validation: ``schema_version`` and ``session_uuid`` must match.
  G3 — Stale TTL: age > ``OPENCLAW_ACTIVE_TTL_SEC`` (default 300) → anonymous.
  G4 — Malformed/missing falls back to anonymous.
  G5 — ``attribution_source`` always set on OpenClaw-UA returns.
  G6 — No FS access for non-OpenClaw UAs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpak.services.routing_service import platform_bridge as pb

_FRESH_UUID = "11111111-2222-3333-4444-555555555555"


def _write_active(tmp_active: Path, **fields) -> None:
    payload = {
        "schema_version": "1.0",
        "session_uuid": _FRESH_UUID,
        "last_event": "message:received",
        "last_event_ts": int(time.time()),
        "event_count": 1,
        "agent": "trix-test",
    }
    payload.update(fields)
    tmp_active.parent.mkdir(parents=True, exist_ok=True)
    tmp_active.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def isolated_active(tmp_path, monkeypatch):
    """Redirect ``_ACTIVE_FILE`` into tmp + clear the 1s mtime cache."""
    target = tmp_path / "active.json"
    monkeypatch.setattr(pb, "_ACTIVE_FILE", target)
    pb._active_cache.update({"mtime": 0.0, "payload": None, "read_ts": 0.0})
    yield target
    pb._active_cache.update({"mtime": 0.0, "payload": None, "read_ts": 0.0})


# ── G1: User-Agent gate ──────────────────────────────────────────────


def test_g1_openclaw_ua_triggers_file_read(isolated_active):
    _write_active(isolated_active)
    with patch.object(pb, "_read_active_json", wraps=pb._read_active_json) as m:
        origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.platform_name == "openclaw"
    assert origin.session_id == _FRESH_UUID
    assert m.call_count == 1


def test_g1_openclaw_gateway_ua_triggers_file_read(isolated_active):
    _write_active(isolated_active)
    origin = pb._openclaw_extract({"user-agent": "OpenClaw-Gateway/1.0"})
    assert origin is not None
    assert origin.session_id == _FRESH_UUID


# ── G6: No FS access for non-OpenClaw traffic ───────────────────────


def test_g6_non_openclaw_ua_skips_fs(isolated_active):
    _write_active(isolated_active)
    with patch.object(pb, "_read_active_json") as m:
        for ua in ("claude-cli/1.0", "python-requests/2.31", "curl/8", ""):
            origin = pb._openclaw_extract({"user-agent": ua})
            assert origin is None, f"non-openclaw UA {ua!r} matched"
    assert m.call_count == 0, "non-openclaw UA must not trigger active.json read"


def test_g6_explicit_session_header_does_not_read_file(isolated_active):
    """X-OpenClaw-Session is the explicit-session bypass; no FS read."""
    _write_active(isolated_active)
    with patch.object(pb, "_read_active_json") as m:
        origin = pb._openclaw_extract({"x-openclaw-session": "sess-abc"})
    assert origin is not None
    assert origin.session_id == "sess-abc"
    assert m.call_count == 0


# ── G2: Schema validation ───────────────────────────────────────────


def test_g2_wrong_schema_version_falls_back(isolated_active):
    _write_active(isolated_active, schema_version="2.0")
    origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.session_id is None
    assert origin.attribution_source == "anonymous_user_agent_only"


def test_g2_missing_session_uuid_falls_back(isolated_active):
    payload = {
        "schema_version": "1.0",
        "last_event": "message:received",
        "last_event_ts": int(time.time()),
    }
    isolated_active.parent.mkdir(parents=True, exist_ok=True)
    isolated_active.write_text(json.dumps(payload), encoding="utf-8")
    origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.session_id is None
    assert origin.attribution_source == "anonymous_user_agent_only"


def test_g2_non_uuid_session_uuid_falls_back(isolated_active):
    _write_active(isolated_active, session_uuid="not-a-uuid")
    origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.session_id is None
    assert origin.attribution_source == "anonymous_user_agent_only"


# ── G3: Stale TTL ───────────────────────────────────────────────────


def test_g3_stale_active_falls_back(isolated_active, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ACTIVE_TTL_SEC", "300")
    _write_active(isolated_active, last_event_ts=int(time.time()) - 301)
    origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.session_id is None
    assert origin.attribution_source == "anonymous_user_agent_only"


def test_g3_negative_age_falls_back(isolated_active):
    """Clock skew (last_event_ts > now) → anonymous (suspicious)."""
    _write_active(isolated_active, last_event_ts=int(time.time()) + 60)
    origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.session_id is None
    assert origin.attribution_source == "anonymous_user_agent_only"


def test_g3_env_override_extends_ttl(isolated_active, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ACTIVE_TTL_SEC", "600")
    _write_active(isolated_active, last_event_ts=int(time.time()) - 400)
    origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.session_id == _FRESH_UUID
    assert origin.attribution_source == "openclaw_active_session_file"


# ── G4: Malformed/missing fallback ──────────────────────────────────


def test_g4_missing_file_falls_back(isolated_active):
    assert not isolated_active.exists()
    origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.session_id is None
    assert origin.attribution_source == "anonymous_user_agent_only"


def test_g4_empty_file_falls_back(isolated_active):
    isolated_active.parent.mkdir(parents=True, exist_ok=True)
    isolated_active.write_text("", encoding="utf-8")
    origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.session_id is None
    assert origin.attribution_source == "anonymous_user_agent_only"


def test_g4_invalid_json_falls_back(isolated_active):
    isolated_active.parent.mkdir(parents=True, exist_ok=True)
    isolated_active.write_text("{not valid json", encoding="utf-8")
    origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.session_id is None
    assert origin.attribution_source == "anonymous_user_agent_only"


def test_g4_payload_not_dict_falls_back(isolated_active):
    isolated_active.parent.mkdir(parents=True, exist_ok=True)
    isolated_active.write_text("[1, 2, 3]", encoding="utf-8")
    origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.session_id is None
    assert origin.attribution_source == "anonymous_user_agent_only"


# ── G5: attribution_source always set ───────────────────────────────


def test_g5_happy_path_sets_active_session_file(isolated_active):
    _write_active(isolated_active)
    origin = pb._openclaw_extract({"user-agent": "openclaw"})
    assert origin is not None
    assert origin.attribution_source == "openclaw_active_session_file"


def test_g5_every_openclaw_ua_return_has_attribution(isolated_active, monkeypatch):
    """Enumerate every fall-through and assert attribution_source is one
    of the two ratified values — never None, never 'unknown'."""
    monkeypatch.setenv("OPENCLAW_ACTIVE_TTL_SEC", "300")
    cases = [
        # missing
        lambda: None,
        # malformed
        lambda: isolated_active.write_text("{bad", encoding="utf-8"),
        # wrong schema
        lambda: _write_active(isolated_active, schema_version="0.9"),
        # bad uuid
        lambda: _write_active(isolated_active, session_uuid="x"),
        # stale
        lambda: _write_active(isolated_active, last_event_ts=int(time.time()) - 9999),
        # happy
        lambda: _write_active(isolated_active),
    ]
    for setup in cases:
        # reset cache + state for each iteration
        if isolated_active.exists():
            isolated_active.unlink()
        pb._active_cache.update({"mtime": 0.0, "payload": None, "read_ts": 0.0})
        if setup is not None:
            setup()
        origin = pb._openclaw_extract({"user-agent": "openclaw"})
        assert origin is not None
        assert origin.attribution_source in (
            "openclaw_active_session_file",
            "anonymous_user_agent_only",
        ), f"unexpected attribution_source={origin.attribution_source!r}"


# ── Cache ───────────────────────────────────────────────────────────


def test_mtime_cache_avoids_thrash(isolated_active):
    _write_active(isolated_active)
    real_open = Path.open
    call_count = {"n": 0}

    def counting_open(self, *args, **kwargs):
        if self == isolated_active:
            call_count["n"] += 1
        return real_open(self, *args, **kwargs)

    with patch.object(Path, "open", counting_open):
        pb._openclaw_extract({"user-agent": "openclaw"})
        pb._openclaw_extract({"user-agent": "openclaw"})
        pb._openclaw_extract({"user-agent": "openclaw"})
    # First call opens the file; subsequent calls within 1s + same mtime
    # serve from cache.
    assert call_count["n"] == 1, f"opened {call_count['n']}× — cache leaks"


# ── Backward compat: existing dataclass + UA tests still hold ───────


def test_platform_origin_default_attribution_is_unknown():
    o = pb.PlatformOrigin(platform_name="x")
    assert o.attribution_source == "unknown"


def test_codex_extract_unaffected():
    origin = pb.detect_origin({"Authorization": "Bearer eyJtest"})
    assert origin is not None
    assert origin.platform_name == "codex"
    assert origin.attribution_source == "unknown"
