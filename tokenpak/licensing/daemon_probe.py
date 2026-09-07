# SPDX-License-Identifier: Apache-2.0
"""Bounded compatibility probe for the optional local Pro daemon."""

from __future__ import annotations

import http.client
import os
import socket
import stat
import time
from pathlib import Path
from typing import Any, Literal, Optional

from tokenpak.release_metadata import (
    ReleaseMetadataError,
    _parse_semver,
    _parse_tip_version,
    _strict_json_loads,
    load_release_metadata,
)

DaemonState = Literal["active", "unavailable", "tip_mismatch"]
DaemonStateReason = Literal[
    "sock_info_absent",
    "sock_info_malformed",
    "connect_refused",
    "health_unreachable",
    "health_malformed",
    "service_mismatch",
    "declaration_unconfigured",
    "declaration_malformed",
    "oss_metadata_missing",
    "oss_metadata_malformed",
    "tokenpak_out_of_range",
    "tip_out_of_range",
    "ok",
]
DaemonProbeResult = tuple[DaemonState, DaemonStateReason]

_SOCK_INFO_PATH = Path.home() / ".tokenpak" / "pro" / "daemon.sock-info"
_PROBE_TIMEOUT_SEC = 0.5
_MAX_HEALTH_BODY_BYTES = 65_536
_MAX_SOCK_INFO_BYTES = 8_192
_SERVICE_IDENTITY = "tokenpak-paid-daemon"


class _DeadlineSocket(socket.socket):
    """Socket that reapplies one absolute deadline to every blocking I/O."""

    def __init__(self, *args: Any, deadline: float, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._deadline = deadline

    def _apply_remaining_timeout(self) -> None:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("daemon health deadline expired")
        self.settimeout(min(remaining, _PROBE_TIMEOUT_SEC))

    def sendall(self, data: bytes, flags: int = 0) -> None:
        self._apply_remaining_timeout()
        super().sendall(data, flags)

    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        self._apply_remaining_timeout()
        return super().recv(bufsize, flags)

    def recv_into(self, buffer: Any, nbytes: int = 0, flags: int = 0) -> int:
        self._apply_remaining_timeout()
        return super().recv_into(buffer, nbytes, flags)


def sock_info_path() -> Path:
    """Return the canonical daemon sock-info path."""

    return _SOCK_INFO_PATH


def _read_sock_info(path: Path) -> dict[str, Any] | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        path_stat = path.lstat()
        if not stat.S_ISREG(path_stat.st_mode):
            return None
        descriptor = os.open(path, flags)
        file_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino)
            or file_stat.st_size > _MAX_SOCK_INFO_BYTES
        ):
            return None
        raw = os.read(descriptor, _MAX_SOCK_INFO_BYTES + 1)
        if len(raw) > _MAX_SOCK_INFO_BYTES:
            return None
        data = _strict_json_loads(raw)
    except Exception:
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if not isinstance(data, dict):
        return None
    return data


def _inside(value: tuple[int, ...], lower: tuple[int, ...], upper: tuple[int, ...]) -> bool:
    return lower <= value <= upper


def _classify_health(status: int, raw: bytes) -> DaemonProbeResult:
    if status != 200 or len(raw) > _MAX_HEALTH_BODY_BYTES:
        return ("unavailable", "health_malformed")

    try:
        payload = _strict_json_loads(raw)
    except Exception:
        return ("unavailable", "health_malformed")
    if not isinstance(payload, dict):
        return ("unavailable", "health_malformed")

    if payload.get("ok") is not True:
        return ("unavailable", "health_malformed")
    service = payload.get("service")
    if not isinstance(service, str):
        return ("unavailable", "health_malformed")
    if service != _SERVICE_IDENTITY:
        return ("unavailable", "service_mismatch")

    compatibility_status = payload.get("compatibility_status")
    if compatibility_status == "unconfigured":
        return ("unavailable", "declaration_unconfigured")
    if compatibility_status == "malformed":
        return ("unavailable", "declaration_malformed")
    if compatibility_status != "declared":
        return ("unavailable", "health_malformed")

    field_names = (
        "tokenpak_min_version",
        "tokenpak_max_version",
        "tip_min_version",
        "tip_max_version",
    )
    if any(not isinstance(payload.get(name), str) for name in field_names):
        return ("unavailable", "health_malformed")

    tokenpak_min = _parse_semver(payload["tokenpak_min_version"])
    tokenpak_max = _parse_semver(payload["tokenpak_max_version"])
    tip_min = _parse_tip_version(payload["tip_min_version"])
    tip_max = _parse_tip_version(payload["tip_max_version"])
    if (
        tokenpak_min is None
        or tokenpak_max is None
        or tip_min is None
        or tip_max is None
        or tokenpak_min > tokenpak_max
        or tip_min > tip_max
    ):
        return ("unavailable", "declaration_malformed")

    try:
        metadata = load_release_metadata()
    except ReleaseMetadataError as exc:
        return ("unavailable", exc.reason)
    except Exception:
        return ("unavailable", "oss_metadata_malformed")

    if not _inside(metadata.tokenpak_version, tokenpak_min, tokenpak_max):
        return ("tip_mismatch", "tokenpak_out_of_range")
    if not _inside(metadata.asserted_tip_version, tip_min, tip_max):
        return ("tip_mismatch", "tip_out_of_range")
    return ("active", "ok")


def _fetch_health(port: int) -> DaemonProbeResult:
    deadline = time.monotonic() + _PROBE_TIMEOUT_SEC
    sock: _DeadlineSocket | None = None
    conn: http.client.HTTPConnection | None = None
    try:
        sock = _DeadlineSocket(socket.AF_INET, socket.SOCK_STREAM, deadline=deadline)
        sock._apply_remaining_timeout()
        sock.connect(("127.0.0.1", port))

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=_PROBE_TIMEOUT_SEC)
        conn.sock = sock
        conn.request("GET", "/v1/health")
        response = conn.getresponse()
        if response.status != 200:
            return ("unavailable", "health_malformed")
        raw = response.read(_MAX_HEALTH_BODY_BYTES + 1)
    except ConnectionRefusedError:
        return ("unavailable", "connect_refused")
    except (OSError, TimeoutError):
        return ("unavailable", "health_unreachable")
    except http.client.HTTPException:
        return ("unavailable", "health_malformed")
    finally:
        try:
            if conn is not None:
                conn.close()
            elif sock is not None:
                sock.close()
        except OSError:
            pass

    return _classify_health(response.status, raw)


def probe_daemon(*, sock_info_override: Optional[Path] = None) -> DaemonProbeResult:
    """Return daemon state plus a closed-vocabulary diagnostic reason."""

    path = sock_info_override or _SOCK_INFO_PATH
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return ("unavailable", "sock_info_absent")
    except OSError:
        return ("unavailable", "sock_info_malformed")
    if not stat.S_ISREG(path_stat.st_mode):
        return ("unavailable", "sock_info_malformed")

    info = _read_sock_info(path)
    if info is None:
        return ("unavailable", "sock_info_malformed")
    port = info.get("port")
    if type(port) is not int or not (1 <= port <= 65_535):
        return ("unavailable", "sock_info_malformed")
    return _fetch_health(port)


def detect_daemon_state(*, sock_info_override: Optional[Path] = None) -> DaemonState:
    """Return the stable three-state compatibility result."""

    return probe_daemon(sock_info_override=sock_info_override)[0]


def is_daemon_reachable(*, sock_info_override: Optional[Path] = None) -> bool:
    """Return whether the daemon completed a compatible health handshake."""

    return detect_daemon_state(sock_info_override=sock_info_override) == "active"


__all__ = [
    "DaemonProbeResult",
    "DaemonState",
    "DaemonStateReason",
    "detect_daemon_state",
    "is_daemon_reachable",
    "probe_daemon",
    "sock_info_path",
]
