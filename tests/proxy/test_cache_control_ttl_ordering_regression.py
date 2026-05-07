"""
tests/proxy/test_cache_control_ttl_ordering_regression.py

CCG-17: Regression test — cache_control TTL ordering hotfix (v3).

Locks in the 2026-04-08 PM hotfix that prevents Anthropic's
  "A ttl=1h cache_control block must not come after a ttl=5m cache_control block"
error from recurring silently after proxy.py refactors.

Incident history:
  2026-04-07 → 2026-04-08 morning: v1 hotfix lost in refactor → v2 emergency patch
  2026-04-08 morning → 2026-04-08 afternoon: v2 hotfix lost in rewrite → v3 emergency patch
  CCG-17: ships the regression test that catches removal before it reaches production

The fix lives in proxy.py (search "TTL ordering hotfix v3") and is tested here via
a recording stub upstream — no real Anthropic API calls are made.

Cases:
  A — Exact 2026-04-08 incident shape: system[1]={1h}, system[2]={no ttl}, messages[0].content[2]={1h}
      After hotfix: system[2] cache_control stripped
  B — Pure 1h shape: hotfix is a no-op, body unchanged
  C — Pure default/5m shape: no explicit-ttl block, hotfix does not fire
  D — Interleaved with multiple 1h blocks: system[0]={1h}, system[1]={no ttl}, system[2]={1h}
      After hotfix: system[1] cache_control stripped (item before last 1h)
  E — Canary: "TTL ordering hotfix v[2-9]" marker present in proxy.py (catches silent removal)
  F — Log line: the 🧹 TTL ordering hotfix log fires when Case A shape is processed

HARD GATE: failure blocks CI merge (covered by `pytest tests/` glob in .github/workflows/ci.yml).
Both /home/cali/tokenpak/proxy.py and /home/sue/tokenpak/proxy.py are tested when present.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.runtime.providers", reason="module not available in current build")
import io
import json
import os
import re
import socket
import sys
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.needs_cali_env

# ---------------------------------------------------------------------------
# Bootstrap: load the monolith proxy.py via importlib (no sys.path mutation)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SUE_PROJECT_ROOT = Path("/home/sue/tokenpak")

# Set env vars BEFORE importing proxy so module-level _cfg() calls pick them up.
os.environ.setdefault("TOKENPAK_SEMANTIC_CACHE", "0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-sk-ccg17-dummy-not-real")
os.environ.setdefault("TOKENPAK_VAULT_INDEX", "0")

import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("proxy", _PROJECT_ROOT / "proxy.py")
_proxy = _ilu.module_from_spec(_spec)
sys.modules.setdefault("proxy", _proxy)
_spec.loader.exec_module(_proxy)

from tokenpak.proxy.config import SEMANTIC_CACHE_ENABLED  # noqa: E402,F401
from tokenpak.runtime.providers import Provider  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_FAILING_REQ = json.loads((_FIXTURES / "cali_failing_request_2026-04-08.json").read_bytes())

# Proxy.py files to test — primary host is mandatory, secondary host is optional
_PROXY_FILES: list[Path] = [_PROJECT_ROOT / "proxy.py"]
_SUE_PROXY = _SUE_PROJECT_ROOT / "proxy.py"
if _SUE_PROXY.exists():
    _PROXY_FILES.append(_SUE_PROXY)


# ---------------------------------------------------------------------------
# Recording stub upstream
# ---------------------------------------------------------------------------

class _RecordingServer(HTTPServer):
    """HTTPServer that stores the last received request body."""
    request_count: int = 0
    last_body: bytes = b""
    last_json: dict = {}


class _RecordingHandler(BaseHTTPRequestHandler):
    """Stub that records the request body and returns a minimal SSE response."""

    _MINIMAL_SSE = (
        b"data: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_stub\","
        b"\"type\":\"message\",\"role\":\"assistant\",\"content\":[],"
        b"\"model\":\"claude-sonnet-4-6\",\"stop_reason\":null,\"stop_sequence\":null,"
        b"\"usage\":{\"input_tokens\":1,\"output_tokens\":0}}}\n\n"
        b"data: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},"
        b"\"usage\":{\"output_tokens\":1}}\n\n"
        b"data: {\"type\":\"message_stop\"}\n\n"
    )

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # silence test output

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        self.server.request_count += 1  # type: ignore[attr-defined]
        self.server.last_body = raw  # type: ignore[attr-defined]
        try:
            self.server.last_json = json.loads(raw)  # type: ignore[attr-defined]
        except Exception:
            self.server.last_json = {}  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(self._MINIMAL_SSE)))
        self.end_headers()
        self.wfile.write(self._MINIMAL_SSE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _send_via_proxy(proxy_port: int, stub_port: int, body: dict | bytes) -> bytes:
    """
    Send a forward-proxy POST through the proxy to the stub upstream.
    Returns the response body bytes.
    """
    import urllib.error
    import urllib.request

    raw = json.dumps(body).encode() if isinstance(body, dict) else body
    target_url = f"http://127.0.0.1:{stub_port}/v1/messages"
    req = urllib.request.Request(
        target_url,
        data=raw,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
            "X-Claude-Code-Session-Id": "test-ccg17",
            "User-Agent": "claude-code/2.1.99",
        },
    )
    proxy_handler = urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy_port}"})
    opener = urllib.request.build_opener(proxy_handler)
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        return e.read()


@pytest.fixture()
def recording_proxy():
    """
    Start a recording stub upstream + the real proxy (ForwardProxyHandler).

    Yields (proxy_port, stub) where stub.last_json is the body received by upstream.

    detect_provider is patched to return ANTHROPIC so the TTL ordering hotfix fires.
    SEMANTIC_CACHE_ENABLED is disabled to avoid cache interactions.
    """
    stub_port = _free_port()
    proxy_port = _free_port()

    stub = _RecordingServer(("127.0.0.1", stub_port), _RecordingHandler)
    stub_thread = threading.Thread(target=stub.serve_forever, daemon=True)
    stub_thread.start()

    with patch.object(_proxy, "SEMANTIC_CACHE_ENABLED", False), \
         patch.object(_proxy, "detect_provider", return_value=Provider.ANTHROPIC):

        proxy_server = _proxy.ThreadedHTTPServer(
            ("127.0.0.1", proxy_port), _proxy.ForwardProxyHandler
        )
        proxy_thread = threading.Thread(target=proxy_server.serve_forever, daemon=True)
        proxy_thread.start()

        yield proxy_port, stub, stub_port

    proxy_server.shutdown()
    proxy_thread.join(timeout=2)
    stub.shutdown()
    stub_thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Helpers to extract cache_control from received body
# ---------------------------------------------------------------------------

def _system_cc(body: dict, idx: int) -> dict | None:
    """Return cache_control at system[idx], or None if absent."""
    system = body.get("system") or []
    if not isinstance(system, list) or idx >= len(system):
        return None
    block = system[idx]
    return block.get("cache_control") if isinstance(block, dict) else None


def _msg_content_cc(body: dict, msg_idx: int, content_idx: int) -> dict | None:
    """Return cache_control at messages[msg_idx].content[content_idx], or None."""
    messages = body.get("messages") or []
    if not isinstance(messages, list) or msg_idx >= len(messages):
        return None
    content = messages[msg_idx].get("content") if isinstance(messages[msg_idx], dict) else None
    if not isinstance(content, list) or content_idx >= len(content):
        return None
    block = content[content_idx]
    return block.get("cache_control") if isinstance(block, dict) else None


def _has_explicit_ttl(cc: dict | None) -> bool:
    """Return True if the cache_control block has an explicit ttl field."""
    return isinstance(cc, dict) and cc.get("ttl") is not None


# ---------------------------------------------------------------------------
# Case A — Exact 2026-04-08 incident shape
# ---------------------------------------------------------------------------

class TestCaseA_ExactIncidentShape:
    """
    Incident shape: system[1]={ttl:1h}, system[2]={no explicit ttl},
    messages[0].content[2]={ttl:1h}.

    The hotfix must strip system[2]'s cache_control block (it is a default-ttl
    block appearing before the last explicit-ttl block at messages[0].content[2]).
    """

    def test_system2_cache_control_stripped(self, recording_proxy):
        proxy_port, stub, stub_port = recording_proxy

        # Build the exact incident shape
        body = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 64,
            "stream": True,
            "system": [
                {"type": "text", "text": "<TEST: system 0 — no cc>"},
                {"type": "text", "text": "<TEST: system 1 — 1h>",
                 "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                {"type": "text", "text": "<TEST: system 2 — default-ttl>",
                 "cache_control": {"type": "ephemeral"}},
            ],
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "<TEST: content 0>"},
                    {"type": "text", "text": "<TEST: content 1>"},
                    {"type": "text", "text": "<TEST: content 2 — 1h>",
                     "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                ],
            }],
        }

        _send_via_proxy(proxy_port, stub_port, body)

        assert stub.request_count == 1, "Proxy should have forwarded the request to upstream"
        received = stub.last_json
        assert received, "Stub should have received a non-empty JSON body"

        # system[2] must have its cache_control stripped
        cc_s2 = _system_cc(received, 2)
        assert cc_s2 is None, (
            f"CCG-17 FAIL: system[2] still has cache_control={cc_s2!r} after hotfix. "
            "This is the exact shape that caused the 2026-04-08 Cali cycle failure. "
            "The TTL ordering hotfix (v3) was likely removed."
        )

        # system[1] must keep its 1h block (it is the anchor, not stripped)
        cc_s1 = _system_cc(received, 1)
        assert _has_explicit_ttl(cc_s1), (
            f"system[1]'s explicit 1h cache_control should be preserved, got {cc_s1!r}"
        )

        # messages[0].content[2] must keep its 1h block
        cc_c2 = _msg_content_cc(received, 0, 2)
        assert _has_explicit_ttl(cc_c2), (
            f"messages[0].content[2]'s explicit 1h cache_control should be preserved, got {cc_c2!r}"
        )


# ---------------------------------------------------------------------------
# Case B — Pure 1h shape (hotfix is a no-op)
# ---------------------------------------------------------------------------

class TestCaseB_Pure1hShape:
    """
    All cache_control blocks have explicit ttl: 1h.
    No default-ttl blocks exist, so the hotfix must not strip anything.
    """

    def test_pure_1h_body_unchanged(self, recording_proxy):
        proxy_port, stub, stub_port = recording_proxy

        body = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 64,
            "stream": True,
            "system": [
                {"type": "text", "text": "<TEST: system 0>",
                 "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                {"type": "text", "text": "<TEST: system 1>",
                 "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            ],
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "<TEST: content 0>",
                     "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                ],
            }],
        }

        _send_via_proxy(proxy_port, stub_port, body)

        received = stub.last_json

        # All cache_control blocks should survive intact
        cc_s0 = _system_cc(received, 0)
        assert _has_explicit_ttl(cc_s0), (
            f"Pure-1h: system[0] cache_control should be kept, got {cc_s0!r}"
        )
        cc_s1 = _system_cc(received, 1)
        assert _has_explicit_ttl(cc_s1), (
            f"Pure-1h: system[1] cache_control should be kept, got {cc_s1!r}"
        )
        cc_c0 = _msg_content_cc(received, 0, 0)
        assert _has_explicit_ttl(cc_c0), (
            f"Pure-1h: messages[0].content[0] cache_control should be kept, got {cc_c0!r}"
        )


# ---------------------------------------------------------------------------
# Case C — Pure default-ttl shape (hotfix does not fire)
# ---------------------------------------------------------------------------

class TestCaseC_PureDefaultTtlShape:
    """
    All cache_control blocks use the default ttl (no explicit ttl field).
    No explicit-ttl block exists, so _last_ext_ttl stays None and the hotfix
    loop is never entered. Body must be unchanged.
    """

    def test_pure_default_ttl_body_unchanged(self, recording_proxy):
        proxy_port, stub, stub_port = recording_proxy

        body = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 64,
            "stream": True,
            "system": [
                {"type": "text", "text": "<TEST: system 0>",
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "<TEST: system 1>",
                 "cache_control": {"type": "ephemeral"}},
            ],
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "<TEST: content 0>",
                     "cache_control": {"type": "ephemeral"}},
                ],
            }],
        }

        _send_via_proxy(proxy_port, stub_port, body)
        received = stub.last_json

        # Hotfix must not strip any blocks when no explicit-ttl block exists
        cc_s0 = _system_cc(received, 0)
        assert isinstance(cc_s0, dict), (
            f"Pure-5m: system[0] cache_control should be kept, got {cc_s0!r}. "
            "Hotfix should not fire when no explicit-ttl block exists."
        )
        cc_s1 = _system_cc(received, 1)
        assert isinstance(cc_s1, dict), (
            f"Pure-5m: system[1] cache_control should be kept, got {cc_s1!r}"
        )
        cc_c0 = _msg_content_cc(received, 0, 0)
        assert isinstance(cc_c0, dict), (
            f"Pure-5m: messages[0].content[0] cache_control should be kept, got {cc_c0!r}"
        )


# ---------------------------------------------------------------------------
# Case D — Interleaved with multiple 1h blocks
# ---------------------------------------------------------------------------

class TestCaseD_InterleavedMultiple1h:
    """
    system[0]={ttl:1h}, system[1]={no explicit ttl}, system[2]={ttl:1h},
    messages[0].content[0]={no explicit ttl}.

    Flat list order: [system[0], system[1], system[2], messages[0].content[0]]
    Last explicit-ttl block index: 2 (system[2]).
    Hotfix strips items in range(2) with no explicit ttl:
      - system[0]: has explicit ttl → NOT stripped
      - system[1]: no explicit ttl → STRIPPED
    messages[0].content[0] is at flat index 3 (after last_ext_ttl=2) → NOT stripped.
    """

    def test_interleaved_strips_system1_only(self, recording_proxy):
        proxy_port, stub, stub_port = recording_proxy

        body = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 64,
            "stream": True,
            "system": [
                {"type": "text", "text": "<TEST: system 0 — 1h>",
                 "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                {"type": "text", "text": "<TEST: system 1 — default-ttl>",
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "<TEST: system 2 — 1h>",
                 "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            ],
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "<TEST: content 0 — default-ttl>",
                     "cache_control": {"type": "ephemeral"}},
                ],
            }],
        }

        _send_via_proxy(proxy_port, stub_port, body)
        received = stub.last_json

        # system[0]: 1h — must be preserved (it has explicit ttl, hotfix only strips default-ttl)
        cc_s0 = _system_cc(received, 0)
        assert _has_explicit_ttl(cc_s0), (
            f"Interleaved: system[0] 1h block should be preserved, got {cc_s0!r}"
        )

        # system[1]: default-ttl, before last 1h (system[2]) → MUST be stripped
        cc_s1 = _system_cc(received, 1)
        assert cc_s1 is None, (
            f"CCG-17 FAIL: system[1] default-ttl block should be stripped (it precedes "
            f"system[2] which has ttl:1h), got {cc_s1!r}. Hotfix may be absent."
        )

        # system[2]: 1h — must be preserved
        cc_s2 = _system_cc(received, 2)
        assert _has_explicit_ttl(cc_s2), (
            f"Interleaved: system[2] 1h block should be preserved, got {cc_s2!r}"
        )

        # messages[0].content[0]: default-ttl, comes AFTER the last 1h block (system[2])
        # Not a TTL ordering violation (5m after 1h is fine) → hotfix must NOT strip it
        cc_c0 = _msg_content_cc(received, 0, 0)
        assert isinstance(cc_c0, dict), (
            f"Interleaved: messages[0].content[0] is after the last 1h block — "
            f"hotfix must not strip it, got {cc_c0!r}"
        )


# ---------------------------------------------------------------------------
# Case E — Canary: hotfix marker present in proxy.py
# ---------------------------------------------------------------------------

class TestCaseE_CanaryMarkerPresent:
    """
    Grep proxy.py for the TTL ordering hotfix version marker.

    If a refactor accidentally removes the hotfix, the version comment will be gone
    and this test will fail BEFORE the behavioural tests catch it at runtime.

    Tested against all proxy.py files in _PROXY_FILES.
    """

    @pytest.mark.parametrize("proxy_path", _PROXY_FILES)
    def test_canary_marker_present(self, proxy_path: Path):
        source = proxy_path.read_text(encoding="utf-8")
        pattern = re.compile(r"TTL ordering hotfix v[2-9]")
        match = pattern.search(source)
        assert match is not None, (
            f"CCG-17 CANARY FAIL: '{proxy_path}' does not contain the TTL ordering hotfix "
            f"version marker matching /{pattern.pattern}/. "
            "The hotfix was likely removed during a refactor. "
            "Re-apply from /home/cali/tokenpak/proxy.py.bak.v3-20260408114749 and see "
            "feedback_cache_control_ttl_ordering.md for the incident history."
        )


# ---------------------------------------------------------------------------
# Case F — Log line fires when Case A shape is processed
# ---------------------------------------------------------------------------

class TestCaseF_HotfixLogLineFires:
    """
    The hotfix emits '🧹 TTL ordering hotfix' to stdout when it strips blocks.

    Capture stdout during a Case-A-shaped request and assert the log line appears.
    If the hotfix is removed or silenced, this test fails.
    """

    def test_log_line_fires_on_incident_shape(self, recording_proxy):
        proxy_port, stub, stub_port = recording_proxy

        body = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 64,
            "stream": True,
            "system": [
                {"type": "text", "text": "<TEST: system 0>"},
                {"type": "text", "text": "<TEST: system 1 — 1h>",
                 "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                {"type": "text", "text": "<TEST: system 2 — default-ttl>",
                 "cache_control": {"type": "ephemeral"}},
            ],
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "<TEST: content 0>"},
                    {"type": "text", "text": "<TEST: content 1>"},
                    {"type": "text", "text": "<TEST: content 2 — 1h>",
                     "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                ],
            }],
        }

        captured = io.StringIO()
        with redirect_stdout(captured):
            _send_via_proxy(proxy_port, stub_port, body)

        output = captured.getvalue()
        assert "🧹 TTL ordering hotfix" in output, (
            f"CCG-17 FAIL: Expected '🧹 TTL ordering hotfix' in proxy stdout but got:\n{output!r}\n"
            "This means the hotfix did not fire (or was removed / silenced). "
            "The incident shape was not corrected."
        )


# ---------------------------------------------------------------------------
# Case A (fixture variant) — The actual anonymised incident fixture
# ---------------------------------------------------------------------------

class TestCaseA_FixtureVariant:
    """
    Replay the anonymised fixture from tests/fixtures/cali_failing_request_2026-04-08.json
    (the exact TTL ordering shape captured during the 2026-04-08 incident).

    After the hotfix: system[2] must have no cache_control.
    """

    def test_fixture_incident_shape_corrected(self, recording_proxy):
        proxy_port, stub, stub_port = recording_proxy

        assert isinstance(_FAILING_REQ.get("system"), list), (
            "Fixture cali_failing_request_2026-04-08.json must have system as an array — "
            "update the fixture to match the CCG-17 incident shape."
        )

        _send_via_proxy(proxy_port, stub_port, _FAILING_REQ)
        received = stub.last_json

        # After hotfix: system[2]'s default-ttl block must be stripped
        cc_s2 = _system_cc(received, 2)
        assert cc_s2 is None, (
            f"CCG-17 FAIL (fixture): system[2] still has cache_control={cc_s2!r}. "
            "The hotfix did not correct the TTL ordering in the incident fixture."
        )
