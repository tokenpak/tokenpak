# SPDX-License-Identifier: Apache-2.0
"""OAS-11 — Path C proxy-side reader tests.

Covers Kevin's six verification gates (2026-04-28) for
``tokenpak.services.routing_service._openclaw_extract``:

* **G1** — User-Agent gate: only ``openclaw*`` UAs trigger an active.json read.
* **G2** — Schema validation: bad ``schema_version`` / missing fields → anonymous.
* **G3** — Stale TTL: ``now - last_event_ts > TTL`` → anonymous; negative age → anonymous.
* **G4** — Malformed/missing/permission-denied/invalid-JSON → anonymous fallback.
* **G5** — ``attribution_source`` is ALWAYS set on every OpenClaw return.
* **G6** — Non-OpenClaw UAs return ``None`` BEFORE any filesystem access.

Plus the canonical 7-case smoke set called out in the OAS-11 task spec.
"""
from __future__ import annotations

import json
import os
import stat as _stat
import time
import uuid as _uuid
from pathlib import Path
from unittest import mock

import pytest

from tokenpak.services.routing_service import platform_bridge
from tokenpak.services.routing_service.platform_bridge import (
    ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY,
    ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE,
    ATTRIBUTION_UNKNOWN,
    PlatformOrigin,
    _openclaw_extract,
    _read_active_json,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_cache():
    """Reset the 1s mtime cache between every test."""
    platform_bridge._reset_cache_for_tests()
    yield
    platform_bridge._reset_cache_for_tests()


@pytest.fixture
def tmp_active_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the extractor at a temp ``active.json`` via env override.

    Returns the path; tests write JSON content to it directly.
    """
    target = tmp_path / "active.json"
    monkeypatch.setenv("OPENCLAW_ACTIVE_FILE", str(target))
    monkeypatch.delenv("OPENCLAW_ACTIVE_TTL_SEC", raising=False)
    return target


def _write_active(path: Path, payload: dict) -> None:
    """Atomically write a JSON payload to the active file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def _fresh_payload(uuid: str | None = None, *, age_sec: float = 1.0) -> dict:
    """Build a v1 active.json payload with last_event_ts = now - age_sec."""
    return {
        "schema_version": "1.0",
        "session_uuid": uuid or str(_uuid.uuid4()),
        "last_event_ts": time.time() - age_sec,
    }


# ---------------------------------------------------------------------------
# Canonical 7-case smoke set (matches the spec's Verification table)
# ---------------------------------------------------------------------------


class TestCanonicalSmokeSet:
    """The 7 cases the task spec calls out by name."""

    def test_happy_path_fresh_active_json(self, tmp_active_file: Path) -> None:
        """Fresh active.json + valid UUID → openclaw_active_session_file."""
        target_uuid = str(_uuid.uuid4())
        _write_active(tmp_active_file, _fresh_payload(target_uuid))

        result = _openclaw_extract({"User-Agent": "openclaw/2026.4.28-1"}, b"")

        assert isinstance(result, PlatformOrigin)
        assert result.platform_name == "openclaw"
        assert result.session_id == target_uuid
        assert result.attribution_source == ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE

    def test_stale_last_event_ts_returns_anonymous(self, tmp_active_file: Path) -> None:
        """last_event_ts older than TTL → anonymous fallback."""
        # Default TTL is 300s; 301s old qualifies as stale.
        _write_active(tmp_active_file, _fresh_payload(age_sec=301))

        result = _openclaw_extract({"User-Agent": "openclaw/2026.4.28-1"}, b"")

        assert result is not None
        assert result.session_id is None
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_missing_file_returns_anonymous(self, tmp_active_file: Path) -> None:
        """active.json doesn't exist → anonymous fallback."""
        # tmp_active_file fixture defines the path but no file is written.
        assert not tmp_active_file.exists()

        result = _openclaw_extract({"User-Agent": "openclaw/2026.4.28-1"}, b"")

        assert result is not None
        assert result.session_id is None
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_malformed_json_returns_anonymous(self, tmp_active_file: Path) -> None:
        """Invalid JSON in active.json → anonymous fallback."""
        tmp_active_file.write_text("{not valid json{", encoding="utf-8")

        result = _openclaw_extract({"User-Agent": "openclaw/2026.4.28-1"}, b"")

        assert result is not None
        assert result.session_id is None
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_schema_version_mismatch_returns_anonymous(self, tmp_active_file: Path) -> None:
        """schema_version != "1.0" → anonymous fallback."""
        payload = _fresh_payload()
        payload["schema_version"] = "0.9"
        _write_active(tmp_active_file, payload)

        result = _openclaw_extract({"User-Agent": "openclaw/2026.4.28-1"}, b"")

        assert result is not None
        assert result.session_id is None
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_non_uuid_session_uuid_returns_anonymous(self, tmp_active_file: Path) -> None:
        """session_uuid that doesn't match UUID regex → anonymous fallback."""
        _write_active(
            tmp_active_file,
            {
                "schema_version": "1.0",
                "session_uuid": "not-a-uuid-12345",
                "last_event_ts": time.time(),
            },
        )

        result = _openclaw_extract({"User-Agent": "openclaw/2026.4.28-1"}, b"")

        assert result is not None
        assert result.session_id is None
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_non_openclaw_user_agent_returns_none(self, tmp_active_file: Path) -> None:
        """Non-OpenClaw UA → returns None (not our traffic)."""
        # Even if a fresh active.json exists, a claude-code UA must return None.
        _write_active(tmp_active_file, _fresh_payload())

        result = _openclaw_extract({"User-Agent": "claude-code/2.1.0"}, b"")

        assert result is None


# ---------------------------------------------------------------------------
# Kevin's verification gates G1–G6
# ---------------------------------------------------------------------------


class TestGateG1_UserAgentGate:
    """Only ``User-Agent: openclaw*`` triggers active.json read."""

    def test_openclaw_ua_does_read_file(self, tmp_active_file: Path) -> None:
        _write_active(tmp_active_file, _fresh_payload())
        with mock.patch.object(
            platform_bridge,
            "_read_active_json",
            wraps=platform_bridge._read_active_json,
        ) as spy:
            _openclaw_extract({"User-Agent": "openclaw/2026.3.23-2"}, b"")
            assert spy.call_count == 1

    def test_claude_code_ua_does_not_read_file(self, tmp_active_file: Path) -> None:
        _write_active(tmp_active_file, _fresh_payload())
        with mock.patch.object(
            platform_bridge,
            "_read_active_json",
            wraps=platform_bridge._read_active_json,
        ) as spy:
            _openclaw_extract({"User-Agent": "claude-code/2.1.0"}, b"")
            assert spy.call_count == 0

    def test_anthropic_python_ua_does_not_read_file(self, tmp_active_file: Path) -> None:
        _write_active(tmp_active_file, _fresh_payload())
        with mock.patch.object(
            platform_bridge,
            "_read_active_json",
            wraps=platform_bridge._read_active_json,
        ) as spy:
            _openclaw_extract({"User-Agent": "anthropic-python/0.42.0"}, b"")
            assert spy.call_count == 0

    def test_empty_ua_does_not_read_file(self, tmp_active_file: Path) -> None:
        _write_active(tmp_active_file, _fresh_payload())
        with mock.patch.object(
            platform_bridge,
            "_read_active_json",
            wraps=platform_bridge._read_active_json,
        ) as spy:
            _openclaw_extract({}, b"")
            assert spy.call_count == 0

    def test_ua_case_insensitive(self, tmp_active_file: Path) -> None:
        """`OpenClaw/` (mixed case) still matches."""
        _write_active(tmp_active_file, _fresh_payload())
        result = _openclaw_extract({"User-Agent": "OpenClaw/2026.3.23-2"}, b"")
        assert result is not None
        assert result.attribution_source == ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE


class TestGateG2_SchemaValidation:
    """schema_version + required-field validation."""

    def test_missing_schema_version(self, tmp_active_file: Path) -> None:
        _write_active(
            tmp_active_file,
            {"session_uuid": str(_uuid.uuid4()), "last_event_ts": time.time()},
        )
        result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY
        assert result.session_id is None

    def test_missing_session_uuid(self, tmp_active_file: Path) -> None:
        _write_active(
            tmp_active_file,
            {"schema_version": "1.0", "last_event_ts": time.time()},
        )
        result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_missing_last_event_ts(self, tmp_active_file: Path) -> None:
        _write_active(
            tmp_active_file,
            {"schema_version": "1.0", "session_uuid": str(_uuid.uuid4())},
        )
        # Defaults `last_event_ts` to 0 → age = now > TTL → anonymous.
        result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_session_uuid_not_a_string(self, tmp_active_file: Path) -> None:
        _write_active(
            tmp_active_file,
            {"schema_version": "1.0", "session_uuid": 12345, "last_event_ts": time.time()},
        )
        result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY


class TestGateG3_StaleTtl:
    """Stale TTL fallback + clock-skew (negative age) defense."""

    def test_stale_just_over_ttl(self, tmp_active_file: Path) -> None:
        _write_active(tmp_active_file, _fresh_payload(age_sec=301))
        result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_within_ttl_window(self, tmp_active_file: Path) -> None:
        _write_active(tmp_active_file, _fresh_payload(age_sec=299))
        result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE

    def test_negative_age_clock_skew(self, tmp_active_file: Path) -> None:
        """last_event_ts in the future (clock skew) → anonymous."""
        future = _fresh_payload(age_sec=-60)  # last_event_ts = now + 60
        _write_active(tmp_active_file, future)
        result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_env_configurable_ttl(self, tmp_active_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OPENCLAW_ACTIVE_TTL_SEC override shrinks the freshness window."""
        monkeypatch.setenv("OPENCLAW_ACTIVE_TTL_SEC", "10")
        # 30s old is within default 300s but stale under 10s override
        _write_active(tmp_active_file, _fresh_payload(age_sec=30))
        result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY


class TestGateG4_MalformedFallback:
    """Filesystem failure modes all degrade to anonymous, never raise."""

    def test_empty_file(self, tmp_active_file: Path) -> None:
        tmp_active_file.write_text("", encoding="utf-8")
        result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_invalid_json(self, tmp_active_file: Path) -> None:
        tmp_active_file.write_text("not json at all{{{", encoding="utf-8")
        result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_payload_is_a_list_not_dict(self, tmp_active_file: Path) -> None:
        """JSON parses but is the wrong shape (list instead of dict)."""
        tmp_active_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY

    def test_permission_denied(self, tmp_active_file: Path) -> None:
        """``stat`` raises ``PermissionError`` → anonymous, never escapes."""
        _write_active(tmp_active_file, _fresh_payload())
        with mock.patch.object(Path, "stat", side_effect=PermissionError("EACCES")):
            result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result.attribution_source == ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY


class TestGateG5_AttributionAlwaysSet:
    """Every OpenClaw return path sets attribution_source non-empty."""

    def test_attribution_source_never_unknown_for_openclaw(
        self, tmp_active_file: Path
    ) -> None:
        """Iterate over every documented failure-mode and the happy path —
        each must produce a PlatformOrigin with attribution_source ∈
        {openclaw_active_session_file, anonymous_user_agent_only}."""

        scenarios: list[tuple[str, dict | None]] = [
            ("happy-path", _fresh_payload()),
            ("stale", _fresh_payload(age_sec=999)),
            ("schema-mismatch", {"schema_version": "0.5", "session_uuid": str(_uuid.uuid4()),
                                 "last_event_ts": time.time()}),
            ("missing-uuid", {"schema_version": "1.0", "last_event_ts": time.time()}),
            ("non-uuid", {"schema_version": "1.0", "session_uuid": "xxx",
                          "last_event_ts": time.time()}),
            ("future-ts", _fresh_payload(age_sec=-1000)),
            ("missing-file", None),
        ]

        valid = {ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE, ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY}

        for label, payload in scenarios:
            platform_bridge._reset_cache_for_tests()
            if payload is None:
                tmp_active_file.unlink(missing_ok=True)
            else:
                _write_active(tmp_active_file, payload)

            result = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
            assert result is not None, f"scenario {label} returned None for openclaw UA"
            assert result.attribution_source in valid, (
                f"scenario {label} attribution_source={result.attribution_source!r}"
            )
            assert result.attribution_source != ATTRIBUTION_UNKNOWN, (
                f"scenario {label} fell back to unknown"
            )


class TestGateG6_NonOpenclawNoFsAccess:
    """Non-OpenClaw UAs must short-circuit BEFORE touching the filesystem."""

    @pytest.mark.parametrize(
        "ua",
        [
            "claude-code/2.1.0",
            "anthropic-python/0.42.0",
            "Mozilla/5.0",
            "",
            "openai/1.0",
            "litellm/1.30.0",
        ],
    )
    def test_no_fs_access_for_non_openclaw_ua(
        self, tmp_active_file: Path, ua: str
    ) -> None:
        _write_active(tmp_active_file, _fresh_payload())
        with mock.patch.object(
            platform_bridge,
            "_read_active_json",
            wraps=platform_bridge._read_active_json,
        ) as read_spy, mock.patch.object(
            Path, "stat", wraps=Path.stat
        ) as stat_spy:
            result = _openclaw_extract({"User-Agent": ua} if ua else {}, b"")

        assert result is None
        assert read_spy.call_count == 0
        # `Path.stat` may be called by other things, but never by our path
        # since `_read_active_json` was never invoked.


# ---------------------------------------------------------------------------
# Cache + perf
# ---------------------------------------------------------------------------


class TestMtimeCache:
    """1-second mtime-keyed cache prevents per-request file thrash."""

    def test_repeated_reads_within_1s_hit_cache(self, tmp_active_file: Path) -> None:
        _write_active(tmp_active_file, _fresh_payload())

        original_open = Path.open
        with mock.patch.object(Path, "open", autospec=True) as open_spy:
            open_spy.side_effect = lambda self, *a, **kw: original_open(self, *a, **kw)
            for _ in range(5):
                _openclaw_extract({"User-Agent": "openclaw/x"}, b"")

            # Only the active.json path counts — the test fixture path
            # equals tmp_active_file. First call opens; the next four hit
            # the in-memory cache.
            opens = [
                c for c in open_spy.mock_calls
                if c.args and isinstance(c.args[0], Path)
                and str(c.args[0]) == str(tmp_active_file)
            ]
            assert len(opens) == 1, f"expected 1 active.json open, got {len(opens)}"

    def test_mtime_change_invalidates_cache(self, tmp_active_file: Path) -> None:
        first = _fresh_payload()
        _write_active(tmp_active_file, first)
        result1 = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result1.session_id == first["session_uuid"]

        # Bump mtime forward and rewrite with a new UUID.
        time.sleep(0.05)
        second = _fresh_payload()
        _write_active(tmp_active_file, second)
        # Force mtime to differ even on coarse-resolution filesystems.
        new_mtime = tmp_active_file.stat().st_mtime + 2.0
        os.utime(tmp_active_file, (new_mtime, new_mtime))

        result2 = _openclaw_extract({"User-Agent": "openclaw/x"}, b"")
        assert result2.session_id == second["session_uuid"]
        assert result2.session_id != first["session_uuid"]


# ---------------------------------------------------------------------------
# PlatformOrigin defaults
# ---------------------------------------------------------------------------


class TestPlatformOrigin:
    def test_defaults_are_backward_compatible(self) -> None:
        """Constructing with platform_name only must work."""
        po = PlatformOrigin(platform_name="openclaw")
        assert po.platform_name == "openclaw"
        assert po.session_id is None
        assert po.attribution_source == ATTRIBUTION_UNKNOWN

    def test_full_construction(self) -> None:
        po = PlatformOrigin(
            platform_name="openclaw",
            session_id="abc",
            attribution_source=ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE,
        )
        assert po.session_id == "abc"
        assert po.attribution_source == ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE


# ---------------------------------------------------------------------------
# Reader unit (smoke)
# ---------------------------------------------------------------------------


class TestReadActiveJson:
    def test_returns_none_when_missing(self, tmp_active_file: Path) -> None:
        assert not tmp_active_file.exists()
        assert _read_active_json() is None

    def test_returns_dict_when_present(self, tmp_active_file: Path) -> None:
        payload = _fresh_payload()
        _write_active(tmp_active_file, payload)
        got = _read_active_json()
        assert got == payload
