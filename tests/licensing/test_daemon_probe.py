# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the bounded local daemon compatibility probe."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from tokenpak import __version__
from tokenpak.licensing import daemon_probe

_VERSION_PARTS = tuple(int(part) for part in __version__.split("."))
_NEXT_PATCH = ".".join(map(str, (*_VERSION_PARTS[:2], _VERSION_PARTS[2] + 1)))


def _declared_health(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "service": "tokenpak-paid-daemon",
        "compatibility_status": "declared",
        "tokenpak_min_version": __version__,
        "tokenpak_max_version": __version__,
        "tip_min_version": "TIP-1.0",
        "tip_max_version": "TIP-1.0",
    }
    payload.update(overrides)
    return payload


def _http_response(
    body: bytes, *, status: str = "200 OK", content_length: int | None = None
) -> bytes:
    length = len(body) if content_length is None else content_length
    return (
        f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {length}\r\nConnection: close\r\n\r\n"
    ).encode("ascii") + body


class _OneShotServer:
    def __init__(self, response: bytes, *, drip_offset: int | None = None) -> None:
        self.response = response
        self.drip_offset = drip_offset
        self.request = b""
        self.connections = 0
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(2.0)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        try:
            client, _address = self._listener.accept()
            self.connections += 1
            with client:
                client.settimeout(1.0)
                while b"\r\n\r\n" not in self.request:
                    chunk = client.recv(4_096)
                    if not chunk:
                        break
                    self.request += chunk

                offset = self.drip_offset
                if offset is None:
                    client.sendall(self.response)
                    return
                if offset:
                    client.sendall(self.response[:offset])
                for byte in self.response[offset:]:
                    client.sendall(bytes((byte,)))
                    time.sleep(0.2)
        except OSError:
            pass

    def __enter__(self) -> "_OneShotServer":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._listener.close()
        self._thread.join(timeout=1.0)


def _write_sock_info(path: Path, port: object) -> None:
    path.write_text(json.dumps({"port": port, "tip_version": "1.0"}), encoding="utf-8")


def _probe_response(tmp_path: Path, response: bytes) -> daemon_probe.DaemonProbeResult:
    info = tmp_path / "daemon.sock-info"
    with _OneShotServer(response) as server:
        _write_sock_info(info, server.port)
        result = daemon_probe.probe_daemon(sock_info_override=info)
        assert server.connections == 1
        assert server.request.startswith(b"GET /v1/health HTTP/1.1\r\n")
    return result


def test_missing_sock_info_short_circuits_before_socket(tmp_path, monkeypatch):
    def fail_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("missing sock-info must not create a socket")

    monkeypatch.setattr(daemon_probe, "_DeadlineSocket", fail_socket)
    result = daemon_probe.probe_daemon(sock_info_override=tmp_path / "missing")
    assert result == ("unavailable", "sock_info_absent")


def test_non_regular_or_oversized_sock_info_is_malformed(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_text('{"port": 1234}', encoding="utf-8")
    symlink = tmp_path / "link"
    symlink.symlink_to(target)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    oversized = tmp_path / "oversized"
    oversized.write_bytes(b"x" * 8_193)

    def fail_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid sock-info must not create a socket")

    monkeypatch.setattr(daemon_probe, "_DeadlineSocket", fail_socket)
    for path in (symlink, fifo, oversized):
        assert daemon_probe.probe_daemon(sock_info_override=path) == (
            "unavailable",
            "sock_info_malformed",
        )


@pytest.mark.parametrize("port", [True, False, 0, -1, 65_536, "1234", None])
def test_invalid_port_is_malformed_without_socket(tmp_path, monkeypatch, port):
    info = tmp_path / "daemon.sock-info"
    _write_sock_info(info, port)

    def fail_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid port must not create a socket")

    monkeypatch.setattr(daemon_probe, "_DeadlineSocket", fail_socket)
    assert daemon_probe.probe_daemon(sock_info_override=info) == (
        "unavailable",
        "sock_info_malformed",
    )


def test_compatible_health_is_active_over_one_http_connection(tmp_path):
    raw = json.dumps(_declared_health()).encode("utf-8")
    assert _probe_response(tmp_path, _http_response(raw)) == ("active", "ok")


def test_connection_refusal_has_specific_reason(tmp_path):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    info = tmp_path / "daemon.sock-info"
    _write_sock_info(info, port)
    assert daemon_probe.probe_daemon(sock_info_override=info) == (
        "unavailable",
        "connect_refused",
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"ok": True}, ("unavailable", "health_malformed")),
        (_declared_health(ok=1), ("unavailable", "health_malformed")),
        (_declared_health(service=7), ("unavailable", "health_malformed")),
        (_declared_health(service="other"), ("unavailable", "service_mismatch")),
        (
            _declared_health(compatibility_status="unconfigured"),
            ("unavailable", "declaration_unconfigured"),
        ),
        (
            _declared_health(compatibility_status="malformed"),
            ("unavailable", "declaration_malformed"),
        ),
        (_declared_health(compatibility_status="unknown"), ("unavailable", "health_malformed")),
        (_declared_health(tokenpak_min_version=None), ("unavailable", "health_malformed")),
        (
            _declared_health(tokenpak_min_version="1.24"),
            ("unavailable", "declaration_malformed"),
        ),
        (
            _declared_health(tip_min_version="TIP-1.x"),
            ("unavailable", "declaration_malformed"),
        ),
        (
            _declared_health(tokenpak_min_version="2.0.0", tokenpak_max_version="1.0.0"),
            ("unavailable", "declaration_malformed"),
        ),
        (
            _declared_health(tip_min_version="TIP-2.0", tip_max_version="TIP-1.0"),
            ("unavailable", "declaration_malformed"),
        ),
        (
            _declared_health(tokenpak_min_version=_NEXT_PATCH, tokenpak_max_version=_NEXT_PATCH),
            ("tip_mismatch", "tokenpak_out_of_range"),
        ),
        (
            _declared_health(tip_min_version="TIP-1.1", tip_max_version="TIP-1.9"),
            ("tip_mismatch", "tip_out_of_range"),
        ),
    ],
)
def test_health_contract_classification(tmp_path, payload, expected):
    raw = json.dumps(payload).encode("utf-8")
    assert _probe_response(tmp_path, _http_response(raw)) == expected


def test_non_200_and_oversized_body_are_malformed(tmp_path):
    assert _probe_response(tmp_path, _http_response(b"{}", status="503 Busy")) == (
        "unavailable",
        "health_malformed",
    )
    assert _probe_response(tmp_path, _http_response(b"x" * 65_537)) == (
        "unavailable",
        "health_malformed",
    )


def test_non_200_is_classified_before_stalled_body(tmp_path):
    response = _http_response(b"x" * 10, status="503 Busy")
    header_end = response.index(b"\r\n\r\n") + 4
    info = tmp_path / "daemon.sock-info"

    with _OneShotServer(response, drip_offset=header_end) as server:
        _write_sock_info(info, server.port)
        started = time.monotonic()
        result = daemon_probe.probe_daemon(sock_info_override=info)
        elapsed = time.monotonic() - started

    assert result == ("unavailable", "health_malformed")
    assert elapsed < 0.4


@pytest.mark.parametrize("raw", [b"[]", b"not-json", b"[" * 1_100])
def test_invalid_and_hostile_json_is_typed_malformed(tmp_path, raw):
    assert _probe_response(tmp_path, _http_response(raw)) == (
        "unavailable",
        "health_malformed",
    )


@pytest.mark.parametrize(
    "raw",
    [
        (
            b'{"ok":true,"ok":true,"service":"tokenpak-paid-daemon",'
            b'"compatibility_status":"declared","tokenpak_min_version":"1.24.0",'
            b'"tokenpak_max_version":"1.24.0","tip_min_version":"TIP-1.0",'
            b'"tip_max_version":"TIP-1.0"}'
        ),
        (
            b'{"ok":true,"service":"tokenpak-paid-daemon",'
            b'"compatibility_status":"declared","compatibility_status":"declared",'
            b'"tokenpak_min_version":"1.24.0","tokenpak_max_version":"1.24.0",'
            b'"tip_min_version":"TIP-1.0","tip_max_version":"TIP-1.0"}'
        ),
        (
            b'{"ok":true,"service":"tokenpak-paid-daemon",'
            b'"compatibility_status":"declared","tokenpak_min_version":"1.24.0",'
            b'"tokenpak_min_version":"1.24.0","tokenpak_max_version":"1.24.0",'
            b'"tip_min_version":"TIP-1.0","tip_max_version":"TIP-1.0"}'
        ),
        b'{"ok":NaN,"service":"tokenpak-paid-daemon"}',
    ],
)
def test_non_strict_health_json_is_malformed(tmp_path, raw):
    assert _probe_response(tmp_path, _http_response(raw)) == (
        "unavailable",
        "health_malformed",
    )


def test_huge_quoted_version_is_declaration_malformed(tmp_path):
    huge = "1." + ("9" * 5_000) + ".0"
    raw = json.dumps(_declared_health(tokenpak_min_version=huge)).encode("utf-8")
    assert _probe_response(tmp_path, _http_response(raw)) == (
        "unavailable",
        "declaration_malformed",
    )


def test_huge_json_integer_is_typed_malformed(tmp_path):
    raw = b'{"ok":' + (b"9" * 5_000) + b"}"
    assert _probe_response(tmp_path, _http_response(raw)) == (
        "unavailable",
        "health_malformed",
    )


@pytest.mark.parametrize("reason", ["oss_metadata_missing", "oss_metadata_malformed"])
def test_metadata_failure_reason_is_preserved(tmp_path, monkeypatch, reason):
    raw = json.dumps(_declared_health()).encode("utf-8")

    def fail_metadata():
        raise daemon_probe.ReleaseMetadataError(reason)

    monkeypatch.setattr(daemon_probe, "load_release_metadata", fail_metadata)
    assert _probe_response(tmp_path, _http_response(raw)) == ("unavailable", reason)


@pytest.mark.parametrize("drip_body", [False, True], ids=["header", "body"])
def test_total_deadline_cannot_be_reset_by_drip_response(tmp_path, drip_body):
    body = json.dumps(_declared_health()).encode("utf-8")
    response = _http_response(body)
    drip_offset = response.index(b"\r\n\r\n") + 4 if drip_body else 0
    info = tmp_path / "daemon.sock-info"

    with _OneShotServer(response, drip_offset=drip_offset) as server:
        _write_sock_info(info, server.port)
        started = time.monotonic()
        result = daemon_probe.probe_daemon(sock_info_override=info)
        elapsed = time.monotonic() - started

    assert result == ("unavailable", "health_unreachable")
    assert elapsed < 0.6


def test_detect_and_boolean_wrappers_only_accept_active(monkeypatch):
    monkeypatch.setattr(
        daemon_probe,
        "probe_daemon",
        lambda **_kwargs: ("tip_mismatch", "tip_out_of_range"),
    )
    assert daemon_probe.detect_daemon_state() == "tip_mismatch"
    assert daemon_probe.is_daemon_reachable() is False
