from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_spec = spec_from_file_location("proxy_v4", Path(__file__).resolve().parents[1] / "proxy_v4.py")
proxy_v4 = module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(proxy_v4)


def test_cache_stats_payload_includes_required_fields():
    old_session = dict(proxy_v4.SESSION)
    try:
        proxy_v4.SESSION["cache_hits"] = 3
        proxy_v4.SESSION["cache_misses"] = 1
        proxy_v4.SESSION["cache_read_tokens"] = 1200
        proxy_v4.SESSION["cache_creation_tokens"] = 300
        proxy_v4.SESSION["cache_miss_reasons"] = {
            "timestamp_poison": 1,
            "uuid_request_id_poison": 0,
            "schema_tool_change": 0,
            "retrieval_order_drift_or_unknown": 0,
        }

        payload = proxy_v4._build_cache_stats_payload()
        assert payload["hit_rate"] == 0.75
        assert payload["cache_read_tokens"] == 1200
        assert payload["cache_creation_tokens"] == 300
        assert "miss_reasons" in payload
        assert payload["miss_reasons"]["timestamp_poison"] == 1
    finally:
        proxy_v4.SESSION.clear()
        proxy_v4.SESSION.update(old_session)


def test_cache_miss_reason_classifier_triggers_multiple_categories():
    ts_body = b'{"messages":[{"role":"user","content":"Current time: 2026-03-10T16:30:00Z"}]}'
    uuid_body = b'{"messages":[{"role":"user","content":"request_id: 123e4567-e89b-12d3-a456-426614174000"}]}'

    ts_reason = proxy_v4._classify_cache_miss_reason(
        raw_body=ts_body,
        cache_poison_scrubbed=True,
        tools_schema_changed=False,
        final_body=ts_body,
    )
    uuid_reason = proxy_v4._classify_cache_miss_reason(
        raw_body=uuid_body,
        cache_poison_scrubbed=True,
        tools_schema_changed=False,
        final_body=uuid_body,
    )

    assert ts_reason == "timestamp_poison"
    assert uuid_reason == "uuid_request_id_poison"
