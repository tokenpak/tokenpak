# SPDX-License-Identifier: Apache-2.0
"""Ground truth: does vault content reach the provider?

Every other test of vault injection asserts on an intermediate structure — that
a function is callable, that a stage populated a dict, that a value was written
somewhere. Three separate review rounds each verified one of those layers, and
each was correct, and the capability still did not work.

This suite asserts the only thing that actually matters: **the bytes the
upstream provider receives**. A mock upstream records the exact body it was
sent, and the assertions run against that recording.

Retrieval itself is stubbed — the vault index is not under test here. Everything
downstream of retrieval is real: adapter resolution, ``inject_system_context``,
the byte-splice restore path, and the live ``ProxyServer``. That is deliberate,
because that is precisely where the defects live.

If injection works, the marker appears in what the provider received. If it does
not, no amount of correct-looking intermediate state can hide it.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytestmark = pytest.mark.needs_proxy

from tokenpak.proxy.server import ProxyServer

# The string retrieval will "find". If injection works, the provider sees it.
VAULT_MARKER = "VAULT_GROUND_TRUTH_MARKER_8f3a1c"

# Eligibility floor is ~1000 prompt tokens (vault.inject_min_prompt_tokens), so
# the request must be genuinely large or the stage correctly skips it and the
# test would prove nothing.
_FILLER = "the quick brown fox jumps over the lazy dog. " * 400

_UPSTREAM_RESPONSE = json.dumps(
    {
        "id": "msg_ground_truth",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "ok"}],
        "model": "claude-sonnet-4-6",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }
).encode()


def _wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection((host, port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"port {host}:{port} did not open within {timeout}s")


class _RecordingUpstream(BaseHTTPRequestHandler):
    """Mock provider that records the exact body it received.

    The existing e2e harness reads and discards the body. Keeping it is the
    entire point of this suite.
    """

    received_bodies: list[bytes] = []

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        type(self).received_bodies.append(self.rfile.read(length))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_UPSTREAM_RESPONSE)))
        self.end_headers()
        self.wfile.write(_UPSTREAM_RESPONSE)


@pytest.fixture
def upstream():
    _RecordingUpstream.received_bodies = []
    server = HTTPServer(("127.0.0.1", 0), _RecordingUpstream)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", _RecordingUpstream
    server.shutdown()


@pytest.fixture(autouse=True)
def injection_switch_on(monkeypatch):
    """Enable the vault-injection master switch for this suite.

    PR647 adds `VAULT_INJECTION_ENABLED`, default OFF, guarding the top of
    `stage_vault_injection`. Without this fixture, once that lands the stage
    returns at the guard and the provider-bound assertion can never exercise
    the injection path this suite is meant to prove.

    `raising=False` so the suite works on trees where the flag does not exist yet.
    """
    from tokenpak.proxy import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "VAULT_INJECTION_ENABLED", True, raising=False)


@pytest.fixture
def vault_with_content(monkeypatch):
    """Stub retrieval only. Adapter + splice + server stay real."""
    import tokenpak.proxy.vault_bridge as vb

    class _Idx:
        available = True

        def compile_injection(self, *a, **kw):
            # (injection_text, tokens_used, source_refs)
            return (VAULT_MARKER, 412, ["decisions/auth.md"])

        def search(self, *a, **kw):
            return []

    monkeypatch.setattr(vb, "get_vault_index", lambda *a, **kw: _Idx(), raising=False)
    return _Idx


@pytest.fixture
def injection_stage_spy(monkeypatch):
    """Record whether the vault stage actually executed.

    The instrument's first version asserted only that the upstream received a
    request. That is necessary and NOT sufficient: a request can arrive having
    skipped the pipeline entirely, in which case "the marker is absent" says
    nothing about injection.

    An instrument must prove the code under test ran.
    """
    import tokenpak.proxy.pipeline as pl

    calls: list[bool] = []
    real = pl.stage_vault_injection

    def _spy(request, policy, **kw):
        calls.append(True)
        return real(request, policy, **kw)

    monkeypatch.setattr(pl, "stage_vault_injection", _spy, raising=True)
    return calls


PROXY_PORT = 18873


@pytest.fixture
def intercepted_localhost(monkeypatch):
    """Make the mock upstream look like a provider worth intercepting.

    ``_proxy_to_inner`` gates most of the pipeline on
    ``should_log = any(h in target_url for h in INTERCEPT_HOSTS)``. A localhost
    mock matches nothing, so without this the request is forwarded untouched and
    the suite would report "no injection" for the wrong reason entirely.

    Custom providers already extend this set at runtime (``config.py``), so this
    is the supported seam rather than a test-only hack.
    """
    from tokenpak.proxy import router, server

    patched = set(router.INTERCEPT_HOSTS) | {"127.0.0.1"}

    # `server.py` does `from .router import INTERCEPT_HOSTS` — it binds the set
    # BY VALUE at import time. Patching only `router.INTERCEPT_HOSTS` rebinds a
    # name `server` never reads, so `should_log` at server.py stays False, the
    # pipeline never runs, and this suite would report "no injection" for a
    # request that was never even eligible. Patch the binding that is actually
    # read.
    monkeypatch.setattr(server, "INTERCEPT_HOSTS", patched, raising=False)
    monkeypatch.setattr(router, "INTERCEPT_HOSTS", patched, raising=False)
    try:
        from tokenpak.proxy import fallback

        monkeypatch.setattr(
            fallback,
            "INTERCEPT_HOSTS",
            set(fallback.INTERCEPT_HOSTS) | {"127.0.0.1"},
            raising=False,
        )
    except Exception:
        pass
    return patched


@pytest.fixture
def proxy(upstream, intercepted_localhost):
    upstream_url, _ = upstream
    server = ProxyServer(host="127.0.0.1", port=PROXY_PORT)
    server.start(blocking=False)
    _wait_for_port(PROXY_PORT)
    yield server, upstream_url
    server.stop()
    time.sleep(0.2)


def _send(body: dict, upstream_url: str) -> None:
    """Send in absolute-URI proxy form so the target is the mock upstream."""
    req = urllib.request.Request(
        f"{upstream_url}/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": "test-key",
            "anthropic-version": "2023-06-01",
            # Classifies the request onto the claude-code route, which is the
            # only route whose policy is byte-preserved — and the byte-preserved
            # branch is the ONLY place the request pipeline is invoked. Without
            # this header the request takes the full_pipeline branch, which never
            # calls the pipeline at all, and the suite would report "no injection"
            # for a request that never reached the injector.
            "X-Claude-Code-Session-Id": "ground-truth-e2e",
        },
        method="POST",
    )
    # Route it through the proxy rather than straight to the mock.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{PROXY_PORT}"})
    )
    try:
        opener.open(req, timeout=10).read()
    except Exception:
        # Upstream shape errors are irrelevant — the recording is what matters.
        pass


def _eligible_request() -> dict:
    """A request the byte-splice route can actually act on.

    The `system` array is REQUIRED, not decoration. `_byte_inject_system_block`
    (`proxy/request.py:153-155`) locates the closing bracket of the system array
    and **fail-opens if there is none**:

        close_pos = _find_system_array_close(body)
        if close_pos < 0:
            return body  # fail-open: no system array found

    Without it the splice target does not exist, so the body comes back unchanged
    however correct injection becomes, producing a false failure against a
    request shape the byte-splice path cannot modify.

    Real Claude Code requests always carry a system array, so this is also the
    representative payload for the route the session header forces.
    """
    return {
        "model": "claude-sonnet-4-6",
        "max_tokens": 64,
        "system": [{"type": "text", "text": "You are a helpful assistant."}],
        "messages": [{"role": "user", "content": _FILLER + " what did we decide about auth?"}],
    }


# ---------------------------------------------------------------------------


def test_the_harness_records_what_upstream_receives(proxy, upstream):
    """Guard the instrument itself before trusting anything it reports."""
    _, upstream_url = proxy
    _, recorder = upstream

    _send(_eligible_request(), upstream_url)

    assert recorder.received_bodies, (
        "the mock upstream recorded nothing — the request never arrived, so any "
        "conclusion about injection drawn from this suite would be meaningless"
    )


def test_the_request_actually_reaches_the_injection_stage(
    proxy, upstream, vault_with_content, injection_stage_spy
):
    """The stage under test must RUN, or this suite proves nothing.

    Without this, a closed eligibility gate produces exactly the same observable
    result as a broken injector: no marker upstream. The first version of this
    suite had that flaw — it patched a rebound copy of INTERCEPT_HOSTS, so
    `should_log` stayed False and the pipeline never ran.
    """
    _, upstream_url = proxy

    _send(_eligible_request(), upstream_url)

    assert injection_stage_spy, (
        "stage_vault_injection was never invoked — the request did not reach the "
        "pipeline, so any conclusion about injection from this suite is invalid"
    )


def test_vault_content_reaches_the_provider(
    proxy, upstream, vault_with_content, injection_stage_spy
):
    """Retrieved vault content reaches the provider on the byte-splice route."""
    _, upstream_url = proxy
    _, recorder = upstream

    _send(_eligible_request(), upstream_url)

    assert recorder.received_bodies, "nothing reached the upstream"
    assert injection_stage_spy, "the injection stage never ran — result would be meaningless"
    sent = b"".join(recorder.received_bodies).decode("utf-8", "replace")

    assert VAULT_MARKER in sent, (
        "vault content did not reach the provider — retrieval reported content, "
        "and the request went upstream without it"
    )


def test_request_still_reaches_upstream_intact_when_vault_has_content(
    proxy, upstream, vault_with_content
):
    """Injection must never cost the user their request.

    Whatever injection does or fails to do, the original prompt has to arrive.
    This is the invariant that must hold both before and after the P0 fix, so it
    is not marked xfail.
    """
    _, upstream_url = proxy
    _, recorder = upstream

    _send(_eligible_request(), upstream_url)

    assert recorder.received_bodies, "nothing reached the upstream"
    sent = b"".join(recorder.received_bodies).decode("utf-8", "replace")
    assert "what did we decide about auth?" in sent, "the user's own prompt was lost"
