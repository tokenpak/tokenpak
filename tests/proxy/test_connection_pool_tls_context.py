"""Upstream clients share one verified TLS context.

Claude Code requests carry no header the pool keys sessions by, so each new
client TCP connection gets its own upstream ``httpx.Client``. Building a TLS
context per client reloaded the CA bundle every time, a few hundred
milliseconds of CPU per new connection.
"""

from __future__ import annotations

import ssl

from tokenpak.proxy.connection_pool import ConnectionPool, PoolConfig


def test_session_clients_load_the_ca_bundle_once(monkeypatch) -> None:
    loads = []
    real_load = ssl.SSLContext.load_verify_locations

    def counting_load(self, *args, **kwargs):
        loads.append(1)
        return real_load(self, *args, **kwargs)

    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", counting_load)
    pool = ConnectionPool(PoolConfig(http2=False))
    try:
        clients = [
            pool._get_session_client("api.anthropic.com", f"127.0.0.1:{50000 + i}")
            for i in range(5)
        ]
        assert len({id(c) for c in clients}) == 5
        assert len(loads) == 1
    finally:
        pool.close()


def test_shared_context_still_verifies_certificates() -> None:
    pool = ConnectionPool(PoolConfig(http2=False))
    try:
        pool._get_session_client("api.anthropic.com", "127.0.0.1:50001")
        ctx = pool._ssl_context
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
    finally:
        pool.close()
