"""Subprocess entry point: run a real ProxyServer against a stub upstream.

Used by tests/proxy/test_concurrent_data_path.py and
tests/proxy/test_crash_durability.py, which need a *real* proxy process
(real sockets, real thread pool, real SQLite ledger) that can be driven
concurrently and killed with SIGKILL — none of which is possible with an
in-process ProxyServer without also killing pytest.

Environment contract (set by the test that spawns this script):
    TOKENPAK_TEST_STUB_UPSTREAM  base URL of the stub upstream
                                 (tests/proxy/conftest.py stub, replays
                                 canned SSE/JSON — no real API calls)
    TOKENPAK_PORT                port for the proxy to bind on 127.0.0.1
    TOKENPAK_DB                  monitor.db path (pre-seeded by the test)
    HOME                         isolated per-test home directory

This is a test fixture, not a test module (the leading underscore keeps
pytest from collecting it).
"""

import os


def main() -> None:
    stub = os.environ["TOKENPAK_TEST_STUB_UPSTREAM"]

    # Re-point the anthropic upstream at the stub BEFORE ProxyServer builds
    # its ProviderRouter (the router snapshots PROVIDER_URLS at __init__).
    # Register the stub's host as an intercept host so the request ledger
    # (monitor.db) records these requests exactly as it would for the real
    # provider hosts.
    from tokenpak.proxy import router

    router.PROVIDER_URLS["anthropic"] = stub
    router.INTERCEPT_HOSTS.add("127.0.0.1")

    from tokenpak.proxy.server import ProxyServer

    server = ProxyServer(host="127.0.0.1", port=int(os.environ["TOKENPAK_PORT"]))
    server.start(blocking=True)


if __name__ == "__main__":
    main()
