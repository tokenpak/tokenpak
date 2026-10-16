"""Regression: optional job tags do not become guard agent identities."""

import logging
import sqlite3

import pytest

from tokenpak.proxy import monitor as monitor_module
from tokenpak.proxy.monitor import Monitor
from tokenpak.proxy.request_pipeline import _resolve_agent_id


@pytest.mark.parametrize("tag", ["orca:job_example:0", "orca:job_0123456789ab:12", "x" * 128])
def test_monitor_records_job_separately(tmp_path, tag):
    db = tmp_path / "monitor.db"
    monitor = Monitor(db_path=str(db))
    try:
        monitor.log(
            model="test",
            input_tokens=10,
            output_tokens=2,
            cost=0,
            latency_ms=1,
            status_code=200,
            endpoint="/v1/responses",
            work_tag=tag,
        )
        assert monitor_module._stop_db_write_queue(timeout=20)
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT work_tag, agent_id FROM requests").fetchone() == (tag, "")
        assert _resolve_agent_id({"X-Tokenpak-Tag": tag}) == ""
    finally:
        monitor_module._stop_db_write_queue(timeout=20)


def test_legacy_rows_and_malformed_tag(tmp_path, caplog):
    db = tmp_path / "monitor.db"
    monitor = Monitor(db_path=str(db))
    with caplog.at_level(logging.WARNING):
        monitor.log(
            model="test",
            input_tokens=1,
            output_tokens=2,
            cost=0,
            latency_ms=1,
            status_code=200,
            endpoint="/",
            work_tag="bad/tag\r\nsecret",
        )
    assert monitor_module._stop_db_write_queue(timeout=20)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT work_tag FROM requests").fetchone() == ("",)
    assert len(caplog.records) == 1
    assert "secret" not in caplog.text


@pytest.mark.parametrize("tag", ["orca:job_example:1", "bad%20tag"])
def test_prefix_is_removed_before_routing_and_identity_resets(tag, caplog):
    from types import SimpleNamespace

    from tokenpak.proxy.headers import forward_headers
    from tokenpak.proxy.server import _ProxyHandler

    handler = object.__new__(_ProxyHandler)
    handler._enforce_proxy_auth = lambda: True
    handler.headers = {"session-id": "native-session"}
    handler.path = f"/_tokenpak/t/{tag}/v1/responses"
    routes = []

    def route(path, headers):
        routes.append(path)
        return SimpleNamespace(full_url="https://provider.example" + path)

    handler.server = SimpleNamespace(
        proxy_server=SimpleNamespace(
            shutdown=SimpleNamespace(is_shutting_down=False), router=SimpleNamespace(route=route)
        )
    )
    forwarded = []
    handler._proxy_to = lambda url, method: forwarded.append(url)
    with caplog.at_level(logging.WARNING):
        handler.do_POST()
    assert routes == ["/v1/responses"]
    assert forwarded == ["https://provider.example/v1/responses"]
    assert handler._tokenpak_work_tag == (tag if tag.startswith("orca:") else "")
    assert len(caplog.records) == (0 if tag.startswith("orca:") else 1)
    handler.path = "/v1/responses"
    handler.do_POST()
    assert handler._tokenpak_work_tag == ""
    assert "X-Tokenpak-Tag" not in forward_headers({"X-Tokenpak-Tag": "opaque"}, "sdk")


def test_existing_rows_receive_empty_tag_without_data_loss(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER)"
        )
        conn.execute("INSERT INTO requests VALUES (1,'2026-10-01','old-model',42,7)")
    Monitor(db_path=str(db))
    assert monitor_module._stop_db_write_queue(timeout=20)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT model,input_tokens,output_tokens,work_tag FROM requests"
        ).fetchone() == ("old-model", 42, 7, "")


@pytest.mark.parametrize("tag", [".", "..", "plain", "task-name_1:v2", "x" * 128])
def test_opaque_tags_round_trip_without_route_changes(tag):
    from urllib.parse import urlsplit

    import httpx

    from tokenpak.core.work_tag import extract_job_path, tagged_proxy_url

    url = tagged_proxy_url("http://127.0.0.1:8766/v1", tag) + "/responses"
    request = httpx.Request("POST", url)
    assert extract_job_path(urlsplit(str(request.url)).path) == ("/v1/responses", tag)
