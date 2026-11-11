# SPDX-License-Identifier: Apache-2.0
"""Where this install's TokenPak proxy listens, and how a launcher probes it.

Every companion launcher (Claude and Codex, POSIX and native Windows) reads
the proxy address through :func:`proxy_base_url`, so one configured value
routes all of them:

1. ``TOKENPAK_PROXY_URL``: the full base URL, used as given;
2. ``TOKENPAK_PORT``: a port on the loopback address;
3. otherwise the default port on the loopback address.

Nothing is cached. Each call reads the environment it is given (by default
the process environment), so a value the caller or a local profile sets just
before launch is honoured.

:func:`bounded_call` gives a probe a wall-clock limit. HTTP client timeouts
bound each network operation, not the whole exchange, so a proxy that sends
its headers or body a few bytes at a time could otherwise hold a launch for
as long as it keeps sending.
"""

from __future__ import annotations

import contextlib
import math
import os
import threading
from collections.abc import Callable, Mapping
from typing import TypeVar
from urllib.parse import urlsplit

PROXY_URL_ENV = "TOKENPAK_PROXY_URL"
PROXY_PORT_ENV = "TOKENPAK_PORT"
PROBE_TIMEOUT_ENV = "TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS"
# Defaults only: the address ``tokenpak start`` listens on when nothing else
# is configured.
DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 8766

_T = TypeVar("_T")


def _environ(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def proxy_base_url(environ: Mapping[str, str] | None = None) -> str:
    """Base URL of this install's proxy, without a trailing slash."""
    env = _environ(environ)
    explicit = (env.get(PROXY_URL_ENV) or "").strip()
    if explicit:
        return explicit.rstrip("/")
    port = (env.get(PROXY_PORT_ENV) or "").strip() or str(DEFAULT_PROXY_PORT)
    return f"http://{DEFAULT_PROXY_HOST}:{port}"


def proxy_port(environ: Mapping[str, str] | None = None) -> int:
    """Port of :func:`proxy_base_url`, for starting a local proxy on it.

    A URL that names no port falls back to the configured or default proxy
    port. Raises ``ValueError`` when the configured URL or port is malformed.
    """
    env = _environ(environ)
    port = urlsplit(proxy_base_url(env)).port
    if port is not None:
        return port
    return int((env.get(PROXY_PORT_ENV) or "").strip() or DEFAULT_PROXY_PORT)


def seconds_setting(
    name: str,
    default: float,
    *,
    allow_zero: bool = True,
    environ: Mapping[str, str] | None = None,
) -> float:
    """A finite, non-negative duration in seconds from *name*, else *default*."""
    raw = (_environ(environ).get(name) or "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        return default
    return value


def probe_timeout(default: float, environ: Mapping[str, str] | None = None) -> float:
    """Seconds one health probe may take: ``TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS``, else *default*."""
    return seconds_setting(PROBE_TIMEOUT_ENV, default, allow_zero=False, environ=environ)


def bounded_call(fn: Callable[[], _T], timeout: float) -> _T | None:
    """Return ``fn()`` if it finishes within *timeout* seconds of wall-clock time.

    Returns ``None`` when *fn* raises or is still running at the limit. A
    call that overruns is left on a daemon thread, so give *fn* its own
    network timeouts as well; it then ends soon after the limit.
    """
    outcome: list[_T] = []

    def run() -> None:
        with contextlib.suppress(Exception):
            outcome.append(fn())

    worker = threading.Thread(target=run, name="tokenpak-proxy-probe", daemon=True)
    worker.start()
    worker.join(max(timeout, 0.0))
    if worker.is_alive() or not outcome:
        return None
    return outcome[0]
