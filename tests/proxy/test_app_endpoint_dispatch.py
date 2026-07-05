# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for proxy-owned app endpoint dispatch failures."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from tokenpak.proxy import server as proxy_server


def _fake_handler(path: str):
    handler = object.__new__(proxy_server._ProxyHandler)
    handler.path = path
    handler.headers = {}
    handler.rfile = io.BytesIO()
    handler.wfile = io.BytesIO()
    handler.client_address = ("127.0.0.1", 12345)
    handler._sent_headers = []
    handler.server = SimpleNamespace(
        proxy_server=SimpleNamespace(
            health=lambda deep=False: {"status": "ok", "deep": deep},
            shutdown=SimpleNamespace(is_shutting_down=False),
        )
    )

    handler._enforce_proxy_auth = lambda: True
    handler._proxy_to = lambda *a, **k: pytest.fail("fell through to proxying")
    handler.send_error = lambda *a, **k: pytest.fail("fell through to send_error")

    def send_response(code: int, *args, **kwargs) -> None:
        handler._status = code

    def send_header(name: str, value: str) -> None:
        handler._sent_headers.append((name, value))

    handler.send_response = send_response
    handler.send_header = send_header
    handler.end_headers = lambda: None
    return handler


@pytest.mark.parametrize(
    "path",
    [
        "/tpk/v1/journal/session-id/entry",
        "/pak/v1/promote",
    ],
)
def test_post_app_endpoint_dispatch_error_is_terminal(monkeypatch, path):
    def raise_dispatch_error(_handler):
        raise BrokenPipeError("simulated client disconnect")

    monkeypatch.setattr(
        "tokenpak.proxy.app_endpoints.try_handle_post",
        raise_dispatch_error,
    )

    handler = _fake_handler(path)
    proxy_server._ProxyHandler.do_POST(handler)

    assert handler._status == 500
    assert json.loads(handler.wfile.getvalue().decode()) == {
        "error": "app_endpoint_dispatch_failed",
        "detail": "BrokenPipeError",
    }


def test_get_app_endpoint_dispatch_error_is_terminal(monkeypatch):
    def raise_dispatch_error(_handler):
        raise RuntimeError("simulated app endpoint failure")

    monkeypatch.setattr(
        "tokenpak.proxy.app_endpoints.try_handle_get",
        raise_dispatch_error,
    )

    handler = _fake_handler("/tpk/v1/session/info")
    proxy_server._ProxyHandler.do_GET(handler)

    assert handler._status == 500
    assert json.loads(handler.wfile.getvalue().decode()) == {
        "error": "app_endpoint_dispatch_failed",
        "detail": "RuntimeError",
    }


def test_non_app_dispatch_error_still_allows_health_fallback(monkeypatch):
    def raise_dispatch_error(_handler):
        raise RuntimeError("simulated non-app failure")

    monkeypatch.setattr(
        "tokenpak.proxy.app_endpoints.try_handle_get",
        raise_dispatch_error,
    )

    handler = _fake_handler("/health")
    proxy_server._ProxyHandler.do_GET(handler)

    assert handler._status == 200
    assert json.loads(handler.wfile.getvalue().decode()) == {
        "status": "ok",
        "deep": False,
    }
