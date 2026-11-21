"""Subprocess entry point for the proxy memory replay (tests/proxy/_memory_replay.py).

Runs a real ProxyServer against a local fake upstream, like
``_stub_proxy_launcher.py``, and adds three observers that never change
product behaviour:

- a network guard: an audit hook refuses any socket connection or name lookup
  that is not loopback, so the replay can never reach a real provider;
- with ``TOKENPAK_REPLAY_TRACE=1``, ``tracemalloc`` runs from before the
  proxy is imported, and the traced current/peak bytes are rewritten to
  ``TOKENPAK_REPLAY_STATS`` (JSON) every quarter second;
- with ``TOKENPAK_REPLAY_SQLITE_TRACK=1``, every ``sqlite3.connect`` is
  recorded by opening site and open mode (read-write or read-only URI), with
  the most connections ever open at once per database file and mode; closed
  connections are seen through a subclass of the caller's factory, as in the
  leak soak launcher;
- command files next to the stats file: ``<stats>.snap-<name>`` takes a
  tracemalloc snapshot (kept in memory; ``<stats>.diff-<a>-<b>`` writes the
  top allocation-site differences between two of them), ``<stats>.holders``
  lists which live objects hold large ``bytes``/``str`` values (request
  bodies), and ``<stats>.trim`` calls glibc ``malloc_trim(0)`` and records RSS
  before and after. The driver uses them only after the measured phase.

This is a test fixture, not a test module (the leading underscore keeps
pytest from collecting it).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_DENIED: list[str] = []


def _host_of(address: object) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def _net_guard(event: str, args: tuple) -> None:
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, (str, bytes)):  # AF_UNIX
            return
        host = _host_of(address)
    elif event == "socket.getaddrinfo":
        if args[0] in (None, "", b""):  # passive/local bind lookups
            return
        host = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
    else:
        return
    if host not in _LOOPBACK:
        _DENIED.append(f"{event}:{host}")
        raise OSError(f"replay network guard: {event} to {host!r} refused")


_SQL_LOCK = threading.Lock()
_SQL_OPEN: dict[tuple[str, str], int] = {}
_SQL_PEAK: dict[str, int] = {}
_SQL_SITES: dict[str, int] = {}
_SQL_FACTORIES: dict[type, type] = {}


def _sql_site() -> str:
    import traceback

    for frame in reversed(traceback.extract_stack(limit=20)[:-3]):
        marker = "/tokenpak/"
        if marker in frame.filename and "/tests/" not in frame.filename:
            return f"{frame.filename.rsplit(marker, 1)[1]}:{frame.name}"
    return "other"


def _install_sqlite_tracker() -> None:
    import sqlite3
    import weakref

    original = sqlite3.connect

    def released(key: tuple[str, str]) -> None:
        with _SQL_LOCK:
            _SQL_OPEN[key] = _SQL_OPEN.get(key, 1) - 1

    def tracked_connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        if len(args) > 5:  # factory passed positionally: leave untouched
            return original(*args, **kwargs)
        database = str(args[0] if args else kwargs.get("database", ""))
        uri = bool(kwargs.get("uri"))
        mode = "ro" if uri and "mode=ro" in database else "rw"
        name = os.path.basename(database.split("?", 1)[0].removeprefix("file:"))
        key = (name, mode)
        base = kwargs.pop("factory", sqlite3.Connection)
        sub = _SQL_FACTORIES.get(base)
        if sub is None:

            def close(self, _base=base):  # type: ignore[no-untyped-def]
                done = self.__dict__.pop("_replay_release", None)
                if done is not None:
                    done()
                return _base.close(self)

            sub = type(f"ReplayTracked{base.__name__}", (base,), {"close": close})
            _SQL_FACTORIES[base] = sub
        conn = original(*args, factory=sub, **kwargs)
        site = f"{name} {mode} {_sql_site()}"
        with _SQL_LOCK:
            _SQL_OPEN[key] = _SQL_OPEN.get(key, 0) + 1
            peak_key = f"{name} {mode}"
            _SQL_PEAK[peak_key] = max(_SQL_PEAK.get(peak_key, 0), _SQL_OPEN[key])
            _SQL_SITES[site] = _SQL_SITES.get(site, 0) + 1
        once = weakref.finalize(conn, released, key)
        conn.__dict__["_replay_release"] = once
        return conn

    sqlite3.connect = tracked_connect  # type: ignore[assignment]


def _sqlite_stats() -> dict:
    with _SQL_LOCK:
        return {
            "open": {f"{n} {m}": c for (n, m), c in _SQL_OPEN.items() if c},
            "peak_open": dict(_SQL_PEAK),
            "sites": dict(sorted(_SQL_SITES.items(), key=lambda kv: -kv[1])[:40]),
        }


def _rss_kb() -> int:
    with open("/proc/self/status", encoding="ascii") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return 0


def _write_json(path: str, payload: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def _describe(obj: object, scan: object) -> str:
    """A short name for an object that refers to a large value."""
    import gc
    import types

    if isinstance(obj, types.FrameType):
        code = obj.f_code
        return f"frame {code.co_filename.rsplit('/', 2)[-1]}:{code.co_name}"
    if isinstance(obj, types.CellType):
        return "closure cell"
    if isinstance(obj, (dict, list, tuple)):
        for owner in gc.get_referrers(obj):
            if owner is scan or isinstance(owner, types.FrameType):
                continue
            if isinstance(owner, types.ModuleType):
                return f"{type(obj).__name__} in module {owner.__name__}"
            if getattr(owner, "__dict__", None) is obj:
                return f"attribute of {type(owner).__module__}.{type(owner).__qualname__}"
            if not isinstance(owner, (dict, list, tuple)):
                kind = type(owner)
                return f"{type(obj).__name__} in {kind.__module__}.{kind.__qualname__}"
        keys = [k for k in obj][:4] if isinstance(obj, dict) else []
        return f"{type(obj).__name__} keys={keys}"
    kind = type(obj)
    return f"{kind.__module__}.{kind.__qualname__}"


def _large_value_holders(min_size: int = 128 << 10, limit: int = 20) -> list[dict]:
    import collections
    import gc

    def large(value: object) -> bool:
        return type(value) in (bytes, bytearray, str, memoryview) and len(value) >= min_size  # type: ignore[arg-type]

    gc.collect()
    counts: collections.Counter[str] = collections.Counter()
    sizes: collections.Counter[str] = collections.Counter()

    def add(label: str, value: object) -> None:
        counts[label] += 1
        sizes[label] += len(value)  # type: ignore[arg-type]

    scan = gc.get_objects()
    for obj in scan:
        try:
            referents = gc.get_referents(obj)
        except Exception:
            continue
        for ref in referents:
            if large(ref):
                add(_describe(obj, scan), ref)
            elif type(ref) in (dict, tuple, list) and not gc.is_tracked(ref):
                # Containers of atomic values only are not tracked by the
                # collector; look one level into them from their owner.
                values = ref.values() if isinstance(ref, dict) else ref
                for value in values:
                    if large(value):
                        add(f"untracked {type(ref).__name__} in {_describe(obj, scan)}", value)
    del scan
    names = {t.ident: t.name for t in threading.enumerate()}
    for ident, frame in sys._current_frames().items():
        depth = 0
        while frame is not None and depth < 40:
            for local, value in frame.f_locals.items():
                if large(value):
                    code = frame.f_code
                    add(
                        f"thread {names.get(ident, ident)} local {local} in "
                        f"{code.co_filename.rsplit('/', 2)[-1]}:{code.co_name}",
                        value,
                    )
            frame = frame.f_back
            depth += 1
    return [
        {"holder": label, "count": counts[label], "bytes": size}
        for label, size in sizes.most_common(limit)
    ]


def _observer_loop(stats_path: str, tracing: bool) -> None:
    import tracemalloc

    directory = os.path.dirname(stats_path) or "."
    prefix = os.path.basename(stats_path)
    snapshots: dict[str, tracemalloc.Snapshot] = {}
    extra: dict[str, object] = {}
    while True:
        try:
            for name in os.listdir(directory):
                if not name.startswith(prefix + "."):
                    continue
                command = name[len(prefix) + 1 :]
                path = os.path.join(directory, name)
                if command.startswith("snap-") and tracing:
                    snapshots[command[5:]] = tracemalloc.take_snapshot()
                elif command.startswith("diff-") and tracing:
                    a, b = command[5:].split("-", 1)
                    if a in snapshots and b in snapshots:
                        stats = snapshots[b].compare_to(snapshots[a], "lineno")
                        extra[f"diff_{a}_{b}"] = [
                            {"site": str(s.traceback), "size_diff": s.size_diff, "size": s.size}
                            for s in stats[:15]
                        ]
                elif command == "holders":
                    extra["holders"] = _large_value_holders()
                elif command == "trim":
                    import ctypes

                    before = _rss_kb()
                    ctypes.CDLL("libc.so.6").malloc_trim(0)
                    extra["trim"] = {"rss_kb_before": before, "rss_kb_after": _rss_kb()}
                else:
                    continue
                os.unlink(path)
        except OSError:
            pass
        current, peak = tracemalloc.get_traced_memory() if tracing else (0, 0)
        payload = {
            "at": time.time(),
            "tracing": tracing,
            "traced_current": current,
            "traced_peak": peak,
            "net_denied": list(_DENIED[-20:]),
            "net_denied_count": len(_DENIED),
            "snapshots": sorted(snapshots),
            "sqlite": _sqlite_stats() if _SQL_FACTORIES or _SQL_SITES else {},
            **extra,
        }
        try:
            _write_json(stats_path, payload)
        except OSError:
            pass
        time.sleep(0.25)


def main() -> None:
    sys.addaudithook(_net_guard)
    tracing = os.environ.get("TOKENPAK_REPLAY_TRACE", "") == "1"
    if tracing:
        import tracemalloc

        tracemalloc.start(1)
    if os.environ.get("TOKENPAK_REPLAY_SQLITE_TRACK", "") == "1":
        _install_sqlite_tracker()
    stats_path = os.environ["TOKENPAK_REPLAY_STATS"]
    threading.Thread(
        target=_observer_loop, args=(stats_path, tracing), name="replay-observer", daemon=True
    ).start()

    stub = os.environ["TOKENPAK_TEST_STUB_UPSTREAM"]
    from tokenpak.proxy import router

    router.PROVIDER_URLS["anthropic"] = stub
    router.INTERCEPT_HOSTS.add("127.0.0.1")

    from tokenpak.proxy.server import ProxyServer

    server = ProxyServer(host="127.0.0.1", port=int(os.environ["TOKENPAK_PORT"]))
    server.start(blocking=True)


if __name__ == "__main__":
    main()
