"""Subprocess entry point for the proxy leak soak (tests/proxy/_leak_soak.py).

Runs a real ProxyServer against a local fake upstream, exactly like
``_stub_proxy_launcher.py``. When ``TOKENPAK_SOAK_DIAG`` names a file, it also
records which code opened each SQLite connection that is still open, and the
live thread population by name, and rewrites that JSON file once a second.
The diagnostics only observe: connections are opened with a subclass of the
caller's factory whose ``close`` reports back, so product behaviour is
unchanged.

This is a test fixture, not a test module (the leading underscore keeps
pytest from collecting it).
"""

from __future__ import annotations

import collections
import json
import os
import re
import sqlite3
import threading
import time
import traceback
import weakref

_OPEN: dict[int, str] = {}
_OPEN_LOCK = threading.Lock()
_FACTORIES: dict[type, type] = {}


def _site() -> str:
    for frame in reversed(traceback.extract_stack(limit=16)[:-2]):
        marker = "/tokenpak/"
        if marker in frame.filename and "/tests/" not in frame.filename:
            return f"{frame.filename.rsplit(marker, 1)[1]}:{frame.name}"
    return "other"


def _forget(key: int) -> None:
    with _OPEN_LOCK:
        _OPEN.pop(key, None)


def _install_connection_tracker() -> None:
    original = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        if len(args) > 5:  # factory passed positionally: leave untouched
            return original(*args, **kwargs)
        base = kwargs.pop("factory", sqlite3.Connection)
        sub = _FACTORIES.get(base)
        if sub is None:

            def close(self, _base=base):
                _forget(id(self))
                return _base.close(self)

            sub = type(f"SoakTracked{base.__name__}", (base,), {"close": close})
            _FACTORIES[base] = sub
        conn = original(*args, factory=sub, **kwargs)
        key = id(conn)
        with _OPEN_LOCK:
            _OPEN[key] = _site()
        weakref.finalize(conn, _forget, key)
        return conn

    sqlite3.connect = tracked_connect  # type: ignore[assignment]


def _diag_loop(path: str) -> None:
    digits = re.compile(r"\d+")
    while True:
        threads = collections.Counter(digits.sub("N", t.name) for t in threading.enumerate())
        with _OPEN_LOCK:
            sites = collections.Counter(_OPEN.values())
        payload = {
            "at": time.time(),
            "threads": dict(threads.most_common()),
            "open_connections_by_site": dict(sites.most_common()),
        }
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        except OSError:
            pass
        time.sleep(1.0)


def main() -> None:
    diag = os.environ.get("TOKENPAK_SOAK_DIAG", "")
    if diag:
        import faulthandler
        import signal

        # `kill -USR1 <pid>` appends every thread's stack to <diag>.stacks.
        stacks = open(f"{diag}.stacks", "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.register(signal.SIGUSR1, file=stacks, all_threads=True)
        _install_connection_tracker()
        threading.Thread(target=_diag_loop, args=(diag,), name="soak-diag", daemon=True).start()

    stub = os.environ["TOKENPAK_TEST_STUB_UPSTREAM"]
    from tokenpak.proxy import router

    router.PROVIDER_URLS["anthropic"] = stub
    router.INTERCEPT_HOSTS.add("127.0.0.1")

    from tokenpak.proxy.server import ProxyServer

    server = ProxyServer(host="127.0.0.1", port=int(os.environ["TOKENPAK_PORT"]))
    server.start(blocking=True)


if __name__ == "__main__":
    main()
