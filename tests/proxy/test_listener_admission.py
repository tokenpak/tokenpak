import json
import socket
import threading

import pytest

from tokenpak.proxy.server import _ADMISSION_REJECT_RESPONSE, _ThreadedHTTPServer


class _Proxy:
    def __init__(self):
        self._admission = threading.BoundedSemaphore(1)
        self._admission_rejected = 0


def _server(proxy):
    server = _ThreadedHTTPServer.__new__(_ThreadedHTTPServer)
    server.proxy_server = proxy
    server.shutdown_request = lambda request: request.close()
    return server


class _ScriptedRequest:
    """Deterministic socket stand-in.

    ``recv(..., MSG_PEEK)`` returns the scripted snapshots in order,
    repeating the final snapshot once the script is exhausted — exactly the
    observable behavior of a peeking recv on a socket whose bytes arrive in
    fragments. No real sockets, timers, or sleeps are involved.
    """

    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self.sent = b""
        self.closed = False

    def settimeout(self, value):  # accepted and ignored — deterministic stand-in
        pass

    def recv(self, bufsize, flags=0):
        assert flags == socket.MSG_PEEK
        if len(self._snapshots) > 1:
            return self._snapshots.pop(0)
        return self._snapshots[0]

    def sendall(self, data):
        self.sent += data

    def close(self):
        self.closed = True


def test_model_admission_rejects_before_worker_creation():
    proxy = _Proxy()
    assert proxy._admission.acquire(False)
    server = _server(proxy)
    client, request = socket.socketpair()
    try:
        client.sendall(b"POST /v1/messages HTTP/1.1\r\nX-Tokenpak-Managed: 1\r\n\r\n")
        server.process_request(request, ("local", 0))
        response = client.recv(512)
        assert b"503 Service Unavailable" in response
        assert b"managed_admission_capacity" in response
        assert proxy._admission_rejected == 1
    finally:
        client.close()
        proxy._admission.release()


def test_overload_response_wire_framing_is_exact():
    """The complete wire response must parse, and the declared Content-Length
    must equal the actual body byte count."""
    proxy = _Proxy()
    assert proxy._admission.acquire(False)
    server = _server(proxy)
    client, request = socket.socketpair()
    try:
        client.sendall(b"POST /v1/messages HTTP/1.1\r\nX-Tokenpak-Managed: 1\r\n\r\n")
        server.process_request(request, ("local", 0))
        chunks = []
        while True:
            try:
                chunk = client.recv(4096)
            except (ConnectionResetError, OSError):
                # The server closes its side without consuming the peeked
                # request bytes, which surfaces as a reset once the response
                # has been read — treat it as end-of-stream.
                break
            if not chunk:
                break
            chunks.append(chunk)
        response = b"".join(chunks)
        # Complete response bytes, exactly as built.
        assert response == _ADMISSION_REJECT_RESPONSE
        head, sep, body = response.partition(b"\r\n\r\n")
        assert sep == b"\r\n\r\n"
        status_line, *header_lines = head.split(b"\r\n")
        assert status_line == b"HTTP/1.1 503 Service Unavailable"
        headers = {}
        for line in header_lines:
            name, _, value = line.partition(b":")
            headers[name.strip().lower()] = value.strip()
        # Declared framing matches actual body bytes.
        assert int(headers[b"content-length"]) == len(body)
        assert headers[b"content-type"] == b"application/json"
        assert headers[b"connection"] == b"close"
        # Body parses to the expected JSON error.
        assert json.loads(body.decode("utf-8")) == {"error": "managed_admission_capacity"}
    finally:
        client.close()
        proxy._admission.release()


def test_fragmented_managed_marker_is_classified_before_worker_creation(monkeypatch):
    """A managed marker arriving in a later fragment than the request line
    must still be admission-gated: at saturated capacity the request is
    rejected with zero worker threads created and exactly one rejection."""
    fragment_one = b"POST /v1/messages HTTP/1.1\r\nHost: local\r\n"
    full_head = fragment_one + b"X-Tokenpak-Managed: 1\r\n\r\n"
    request = _ScriptedRequest([fragment_one, full_head])

    proxy = _Proxy()
    assert proxy._admission.acquire(False)  # saturate managed capacity
    server = _server(proxy)

    created_threads = []

    def _spy_thread(*args, **kwargs):
        created_threads.append((args, kwargs))
        raise AssertionError("no worker thread may be created for a rejected request")

    monkeypatch.setattr(threading, "Thread", _spy_thread)
    try:
        server.process_request(request, ("local", 0))
        assert created_threads == []  # zero worker threads
        assert proxy._admission_rejected == 1  # exactly one rejection
        assert request.sent == _ADMISSION_REJECT_RESPONSE
        assert request.closed
    finally:
        proxy._admission.release()


def test_lease_released_and_socket_closed_when_thread_construction_fails(monkeypatch):
    proxy = _Proxy()
    server = _server(proxy)
    client, request = socket.socketpair()

    def _construction_fails(*args, **kwargs):
        raise RuntimeError("thread construction failed")

    monkeypatch.setattr(threading, "Thread", _construction_fails)
    try:
        client.sendall(b"POST /v1/messages HTTP/1.1\r\nX-Tokenpak-Managed: 1\r\n\r\n")
        with pytest.raises(RuntimeError, match="thread construction failed"):
            server.process_request(request, ("local", 0))
        # The admission lease must have been released...
        assert proxy._admission.acquire(False)
        proxy._admission.release()
        # ...and the accepted socket closed.
        assert request.fileno() == -1
    finally:
        client.close()


def test_lease_released_and_socket_closed_when_thread_start_fails(monkeypatch):
    proxy = _Proxy()
    server = _server(proxy)
    client, request = socket.socketpair()

    class _StartFails:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread start failed")

    monkeypatch.setattr(threading, "Thread", _StartFails)
    try:
        client.sendall(b"POST /v1/messages HTTP/1.1\r\nX-Tokenpak-Managed: 1\r\n\r\n")
        with pytest.raises(RuntimeError, match="thread start failed"):
            server.process_request(request, ("local", 0))
        assert proxy._admission.acquire(False)
        proxy._admission.release()
        assert request.fileno() == -1
    finally:
        client.close()


def test_control_plane_is_not_admission_gated():
    proxy = _Proxy()
    assert proxy._admission.acquire(False)
    server = _server(proxy)
    client, request = socket.socketpair()
    try:
        # The worker will fail harmlessly because this is only an admission test;
        # importantly, it is not rejected with the overload response.
        client.sendall(b"GET /health HTTP/1.1\r\nHost: local\r\n\r\n")
        server.process_request(request, ("local", 0))
        client.settimeout(0.5)
        try:
            client.recv(512)
        except (ConnectionResetError, OSError):
            pass
        assert proxy._admission_rejected == 0
    finally:
        client.close()
        proxy._admission.release()


def test_admission_recovers_after_capacity_is_released():
    proxy = _Proxy()
    assert proxy._admission.acquire(False)
    assert not proxy._admission.acquire(False)
    proxy._admission.release()
    assert proxy._admission.acquire(False)
    proxy._admission.release()
