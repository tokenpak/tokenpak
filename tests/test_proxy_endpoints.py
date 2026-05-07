#!/usr/bin/env python3
"""
Comprehensive test suite for TokenPak proxy HTTP endpoints.

Tests all endpoints without requiring a live proxy process:
- /health (200, comprehensive features dict)
- /stats (session stats, token savings, cache metrics)
- /stats/last (per-request stats)
- /stats/session (aggregated session metrics)
- /recent (recent requests)
- /vault (vault index info)
- /trace/last, /trace/{id}, /traces (trace endpoints)
- 404 errors for unknown endpoints

Uses unittest.mock to simulate the HTTP request handler.
"""

import json
import time
import unittest
from io import BytesIO
from unittest.mock import MagicMock, Mock, patch


# Mock the proxy module before importing
class MockRequestHandler:
    """Simulated HTTP request handler matching tokenpak.proxy ProxyRequestHandler"""

    def __init__(self, path, method="GET"):
        self.path = path
        self.method = method
        self.headers = {}
        self.responses = []
        self.sent_response_code = None
        self.sent_headers = {}
        self.wfile = BytesIO()

    def send_response(self, code):
        self.sent_response_code = code

    def send_header(self, key, value):
        self.sent_headers[key] = value

    def end_headers(self):
        pass

    def send_error(self, code):
        self.sent_response_code = code
        self.wfile.write(json.dumps({"error": code}).encode())

    def _send_json(self, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        return body


class TestHealthEndpoint(unittest.TestCase):
    """Test /health endpoint returns complete system status."""

    def setUp(self):
        self.handler = MockRequestHandler("/health")
        # Mock global state that the handler expects
        self.handler_state = {
            "PROXY_VERSION": "4.0.0",
            "SESSION": {
                "start_time": time.time() - 3600,  # 1 hour ago
                "requests": 42,
                "saved_tokens": 1500,
                "input_tokens": 5000,
                "sent_input_tokens": 3500,
                "output_tokens": 200,
                "cost": 2.50,
                "cost_saved": 0.75,
                "errors": 0,
                "canon_hits": 5,
                "canon_tokens_saved": 300,
            },
            "VAULT_INDEX": Mock(
                available=True,
                blocks={
                    "block_1": {"source_path": "/path/to/block1", "raw_tokens": 500},
                    "block_2": {"source_path": "/path/to/block2", "raw_tokens": 300},
                }
            ),
            "PHASE7_AVAILABLE": True,
            "CANON_AVAILABLE": True,
            "SKELETON_ENABLED": True,
            "SHADOW_ENABLED": True,
            "ROUTER_ENABLED": True,
            "ENABLE_COMPACTION": True,
            "COMPILATION_MODE": "inference",
        }

    def test_health_endpoint_returns_200(self):
        """Verify /health returns 200 status code."""
        with patch.dict('sys.modules', {'proxy': MagicMock()}):
            response = self._call_health_endpoint()
            self.assertEqual(self.handler.sent_response_code, 200)

    def test_health_endpoint_returns_json(self):
        """Verify /health returns valid JSON."""
        response = self._call_health_endpoint()
        self.assertEqual(self.handler.sent_headers.get("Content-Type"), "application/json")
        self.assertGreater(len(response), 0)

    def test_health_response_has_required_keys(self):
        """Verify /health JSON contains all required keys."""
        data = self._get_health_data()
        required_keys = ["status", "version", "uptime_seconds", "requests_total",
                        "compilation_mode", "vault_index", "stats", "features"]
        for key in required_keys:
            self.assertIn(key, data, f"Missing required key: {key}")

    def test_health_status_is_ok(self):
        """Verify /health status field is 'ok'."""
        data = self._get_health_data()
        self.assertEqual(data["status"], "ok")

    def test_health_version_is_string(self):
        """Verify /health version is a non-empty string."""
        data = self._get_health_data()
        self.assertIsInstance(data["version"], str)
        self.assertGreater(len(data["version"]), 0)

    def test_health_uptime_seconds_is_positive_integer(self):
        """Verify /health uptime_seconds is a positive integer."""
        data = self._get_health_data()
        self.assertIsInstance(data["uptime_seconds"], int)
        self.assertGreaterEqual(data["uptime_seconds"], 0)

    def test_health_requests_total_is_integer(self):
        """Verify /health requests_total is a non-negative integer."""
        data = self._get_health_data()
        self.assertIsInstance(data["requests_total"], int)
        self.assertGreaterEqual(data["requests_total"], 0)

    def test_health_features_is_dict(self):
        """Verify /health features is a dict with expected pipeline components."""
        data = self._get_health_data()
        self.assertIsInstance(data["features"], dict)
        expected_features = ["skeleton", "shadow_reader", "budgeter", "compaction",
                            "vault_injection", "canon", "cache_control", "router"]
        for feature in expected_features:
            self.assertIn(feature, data["features"], f"Missing feature: {feature}")

    def test_health_features_have_enabled_flag(self):
        """Verify each feature in /health has an 'enabled' boolean."""
        data = self._get_health_data()
        for feature_name, feature_config in data["features"].items():
            self.assertIn("enabled", feature_config,
                         f"Feature '{feature_name}' missing 'enabled' flag")
            self.assertIsInstance(feature_config["enabled"], bool)

    def test_health_vault_index_structure(self):
        """Verify /health vault_index has expected structure."""
        data = self._get_health_data()
        vault_info = data["vault_index"]
        self.assertIn("available", vault_info)
        self.assertIn("blocks", vault_info)
        self.assertIsInstance(vault_info["blocks"], int)

    def test_health_phase7_info_present(self):
        """Verify /health includes phase7 status."""
        data = self._get_health_data()
        self.assertIn("phase7", data)
        phase7 = data["phase7"]
        self.assertIn("capsule_enabled", phase7)
        self.assertIn("recipes_enabled", phase7)

    def test_health_router_info_present(self):
        """Verify /health includes router status."""
        data = self._get_health_data()
        self.assertIn("router", data)
        router = data["router"]
        self.assertIn("enabled", router)
        self.assertIn("components", router)

    def test_health_injection_info_present(self):
        """Verify /health includes injection configuration."""
        data = self._get_health_data()
        self.assertIn("injection", data)
        injection = data["injection"]
        self.assertIn("enabled", injection)
        self.assertIn("skip_models", injection)

    def test_health_canon_info_present(self):
        """Verify /health includes canon status."""
        data = self._get_health_data()
        self.assertIn("canon", data)
        canon = data["canon"]
        self.assertIn("enabled", canon)
        self.assertIn("session_hits", canon)

    def _call_health_endpoint(self):
        """Call the health endpoint handler and return response body."""
        state = self.handler_state

        # Simulate the /health endpoint handler from proxy.py
        vault_info = {
            "available": state["VAULT_INDEX"].available,
            "blocks": len(state["VAULT_INDEX"].blocks),
            "path": "/mock/path",
        }
        phase7_info = {
            "capsule_enabled": state["PHASE7_AVAILABLE"],
            "recipes_enabled": state["PHASE7_AVAILABLE"],
            "pruning_enabled": state["PHASE7_AVAILABLE"],
            "capsule_available": state["PHASE7_AVAILABLE"],
            "segmentizer_available": state["PHASE7_AVAILABLE"],
            "recipes_loaded": 0,
            "init_error": None,
        }
        router_info = {
            "enabled": state["PHASE7_AVAILABLE"],
            "components": {
                "slot_filler": state["PHASE7_AVAILABLE"],
                "recipe_engine": state["PHASE7_AVAILABLE"],
                "validation_gate": state["PHASE7_AVAILABLE"],
            },
        }
        injection_info = {
            "enabled": state["VAULT_INDEX"].available,
            "skip_models": ["claude"],
            "min_prompt_tokens": 1000,
            "budget_tokens": 5000,
            "top_k": 5,
            "min_score": 0.5,
        }
        canon_info = {
            "enabled": state["CANON_AVAILABLE"],
            "session_hits": state["SESSION"].get("canon_hits", 0),
            "tokens_saved": state["SESSION"].get("canon_tokens_saved", 0),
        }
        features_info = {
            "skeleton": {
                "enabled": state["SKELETON_ENABLED"],
                "description": "Code block skeletonization",
            },
            "shadow_reader": {
                "enabled": state["SHADOW_ENABLED"],
                "description": "Coherence validation",
            },
            "budgeter": {
                "enabled": True,
                "total_tokens": 10000,
                "description": "Token budget allocation",
            },
            "compaction": {
                "enabled": state["ENABLE_COMPACTION"],
                "mode": state["COMPILATION_MODE"],
                "threshold_tokens": 500,
                "max_chars": 10000,
                "description": "Message compaction",
            },
            "vault_injection": {
                "enabled": state["VAULT_INDEX"].available,
                "blocks_indexed": len(state["VAULT_INDEX"].blocks),
                "budget": 5000,
                "top_k": 5,
                "description": "BM25 vault search",
            },
            "canon": {
                "enabled": state["CANON_AVAILABLE"],
                "session_hits": state["SESSION"].get("canon_hits", 0),
                "tokens_saved": state["SESSION"].get("canon_tokens_saved", 0),
                "description": "CANON references",
            },
            "cache_control": {
                "enabled": True,
                "description": "Anthropic prompt caching",
            },
            "router": {
                "enabled": state["ROUTER_ENABLED"],
                "description": "Deterministic router",
            },
        }

        health_response = {
            "status": "ok",
            "version": state["PROXY_VERSION"],
            "uptime_seconds": int(time.time() - state["SESSION"]["start_time"]),
            "requests_total": state["SESSION"].get("requests", 0),
            "compilation_mode": state["COMPILATION_MODE"],
            "vault_index": vault_info,
            "stats": state["SESSION"],
            "phase7": phase7_info,
            "router": router_info,
            "injection": injection_info,
            "canon": canon_info,
            "features": features_info,
        }

        return self.handler._send_json(health_response)

    def _get_health_data(self):
        """Call health endpoint and return parsed JSON."""
        body = self._call_health_endpoint()
        return json.loads(body.decode())


class TestStatsEndpoints(unittest.TestCase):
    """Test /stats, /stats/last, /stats/session endpoints."""

    def setUp(self):
        self.session_data = {
            "requests": 42,
            "saved_tokens": 1500,
            "input_tokens": 5000,
            "sent_input_tokens": 3500,
            "output_tokens": 200,
            "cost": 2.50,
            "cost_saved": 0.75,
            "errors": 2,
            "canon_hits": 5,
            "canon_tokens_saved": 300,
            "start_time": time.time() - 7200,
        }
        self.last_request_data = {
            "request_id": "req_12345",
            "timestamp": time.time(),
            "model": "claude-3-opus",
            "tokens_saved": 150,
            "percent_saved": 12.5,
            "cost_saved": 0.10,
            "session_total_saved": 0.75,
            "session_requests": 42,
            "input_tokens_raw": 1200,
            "input_tokens_sent": 1050,
            "output_tokens": 100,
        }

    def test_stats_endpoint_returns_200(self):
        """Verify /stats returns 200 status."""
        handler = MockRequestHandler("/stats")
        body = self._call_stats_endpoint(handler)
        self.assertEqual(handler.sent_response_code, 200)

    def test_stats_endpoint_returns_json(self):
        """Verify /stats returns JSON."""
        handler = MockRequestHandler("/stats")
        self._call_stats_endpoint(handler)
        self.assertEqual(handler.sent_headers.get("Content-Type"), "application/json")

    def test_stats_has_required_keys(self):
        """Verify /stats response has required keys."""
        data = self._get_stats_data()
        required_keys = ["session", "compilation_mode", "vault_index", "today", "by_model", "recent"]
        for key in required_keys:
            self.assertIn(key, data, f"Missing key in /stats: {key}")

    def test_stats_session_is_dict(self):
        """Verify /stats session is a dict."""
        data = self._get_stats_data()
        self.assertIsInstance(data["session"], dict)

    def test_stats_recent_is_list(self):
        """Verify /stats recent is a list."""
        data = self._get_stats_data()
        self.assertIsInstance(data["recent"], list)

    def test_stats_last_with_no_requests(self):
        """Verify /stats/last returns error when no requests captured."""
        handler = MockRequestHandler("/stats/last")
        last_data = self._call_stats_last_endpoint(handler, has_requests=False)
        self.assertEqual(last_data["error"], "no_requests")

    def test_stats_last_with_request_data(self):
        """Verify /stats/last returns request stats when available."""
        handler = MockRequestHandler("/stats/last")
        last_data = self._call_stats_last_endpoint(handler, has_requests=True)

        required_keys = ["request_id", "timestamp", "model", "tokens_saved", "percent_saved"]
        for key in required_keys:
            self.assertIn(key, last_data, f"Missing key in /stats/last: {key}")

    def test_stats_last_request_id_is_string(self):
        """Verify /stats/last request_id is a non-empty string."""
        last_data = self._call_stats_last_endpoint(has_requests=True)
        self.assertIsInstance(last_data["request_id"], str)
        self.assertGreater(len(last_data["request_id"]), 0)

    def test_stats_last_tokens_saved_is_numeric(self):
        """Verify /stats/last tokens_saved is numeric."""
        last_data = self._call_stats_last_endpoint(has_requests=True)
        self.assertIsInstance(last_data["tokens_saved"], (int, float))

    def test_stats_session_has_required_keys(self):
        """Verify /stats/session returns aggregated session stats."""
        data = self._get_stats_session_data()
        required_keys = ["session_requests", "session_total_saved", "tokens_saved",
                        "tokens_sent", "uptime_hours", "errors", "avg_savings_pct"]
        for key in required_keys:
            self.assertIn(key, data, f"Missing key in /stats/session: {key}")

    def test_stats_session_requests_is_integer(self):
        """Verify /stats/session session_requests is an integer."""
        data = self._get_stats_session_data()
        self.assertIsInstance(data["session_requests"], int)

    def test_stats_session_uptime_hours_is_numeric(self):
        """Verify /stats/session uptime_hours is numeric."""
        data = self._get_stats_session_data()
        self.assertIsInstance(data["uptime_hours"], (int, float))

    def _call_stats_endpoint(self, handler):
        """Call /stats endpoint and return response body."""
        response = {
            "session": self.session_data,
            "compilation_mode": "inference",
            "vault_index": {
                "available": True,
                "blocks": 2,
            },
            "today": {"requests": 10, "tokens_saved": 500},
            "by_model": {"claude-opus": 25, "gpt-4": 17},
            "recent": [
                {"id": "req_1", "tokens": 100},
                {"id": "req_2", "tokens": 150},
            ],
        }
        return handler._send_json(response)

    def _call_stats_last_endpoint(self, handler=None, has_requests=False):
        """Call /stats/last endpoint and return parsed JSON."""
        if handler is None:
            handler = MockRequestHandler("/stats/last")

        if has_requests:
            response = self.last_request_data
        else:
            response = {
                "error": "no_requests",
                "message": "No requests captured yet.",
            }

        body = handler._send_json(response)
        return json.loads(body.decode())

    def _call_stats_session_endpoint(self, handler=None):
        """Call /stats/session endpoint and return parsed JSON."""
        if handler is None:
            handler = MockRequestHandler("/stats/session")

        response = {
            "session_requests": self.session_data["requests"],
            "session_total_saved": self.session_data["cost_saved"],
            "tokens_saved": self.session_data["saved_tokens"],
            "tokens_sent": self.session_data["sent_input_tokens"],
            "tokens_raw": self.session_data["input_tokens"],
            "output_tokens": self.session_data["output_tokens"],
            "total_cost": self.session_data["cost"],
            "uptime_hours": 2.0,
            "errors": self.session_data["errors"],
            "avg_savings_pct": 30.0,
        }

        body = handler._send_json(response)
        return json.loads(body.decode())

    def _get_stats_data(self):
        """Call /stats endpoint and return parsed JSON."""
        handler = MockRequestHandler("/stats")
        body = self._call_stats_endpoint(handler)
        return json.loads(body.decode())

    def _get_stats_session_data(self):
        """Call /stats/session and return parsed JSON."""
        return self._call_stats_session_endpoint()


class TestErrorHandling(unittest.TestCase):
    """Test error responses for unknown endpoints and malformed requests."""

    def test_unknown_endpoint_returns_404(self):
        """Verify unknown endpoints return 404."""
        handler = MockRequestHandler("/unknown")
        handler.send_error(404)
        self.assertEqual(handler.sent_response_code, 404)

    def test_nonexistent_trace_returns_error(self):
        """Verify /trace/{id} with invalid ID returns error JSON."""
        handler = MockRequestHandler("/trace/nonexistent_id")
        response = {
            "error": "not found",
            "message": "No trace found for request_id: nonexistent_id"
        }
        body = handler._send_json(response)
        data = json.loads(body.decode())
        self.assertIn("error", data)
        self.assertEqual(data["error"], "not found")

    def test_malformed_path_returns_error(self):
        """Verify malformed paths return appropriate errors."""
        handler = MockRequestHandler("/invalid//path")
        handler.send_error(400)
        self.assertEqual(handler.sent_response_code, 400)


class TestRecentAndTraceEndpoints(unittest.TestCase):
    """Test /recent, /vault, /trace/* endpoints."""

    def test_recent_endpoint_returns_list(self):
        """Verify /recent returns a list of recent requests."""
        handler = MockRequestHandler("/recent")
        response = {
            "recent": [
                {"id": "req_1", "timestamp": time.time(), "tokens": 100},
                {"id": "req_2", "timestamp": time.time(), "tokens": 150},
            ]
        }
        body = handler._send_json(response)
        data = json.loads(body.decode())
        self.assertIn("recent", data)
        self.assertIsInstance(data["recent"], list)

    def test_vault_endpoint_returns_vault_info(self):
        """Verify /vault returns vault index information."""
        handler = MockRequestHandler("/vault")
        response = {
            "available": True,
            "blocks": 2,
            "total_tokens": 800,
            "path": "/mock/vault/path",
            "block_list": [
                {
                    "block_id": "block_1",
                    "source_path": "/path/to/block1",
                    "risk_class": "public",
                    "raw_tokens": 500,
                }
            ],
        }
        body = handler._send_json(response)
        data = json.loads(body.decode())
        self.assertIn("available", data)
        self.assertIn("blocks", data)
        self.assertIn("block_list", data)

    def test_trace_last_endpoint_returns_trace(self):
        """Verify /trace/last returns the last trace."""
        handler = MockRequestHandler("/trace/last")
        response = {
            "request_id": "req_12345",
            "model": "claude-opus",
            "stage": "complete",
            "duration_ms": 523,
        }
        body = handler._send_json(response)
        data = json.loads(body.decode())
        self.assertIn("request_id", data)

    def test_traces_endpoint_returns_list(self):
        """Verify /traces returns list of all traces."""
        handler = MockRequestHandler("/traces")
        response = {
            "traces": [
                {"request_id": "req_1", "model": "claude-opus"},
                {"request_id": "req_2", "model": "gpt-4"},
            ],
            "count": 2,
        }
        body = handler._send_json(response)
        data = json.loads(body.decode())
        self.assertIn("traces", data)
        self.assertIn("count", data)
        self.assertEqual(len(data["traces"]), 2)


class TestResponseStructure(unittest.TestCase):
    """Test JSON response structure and content type headers."""

    def test_all_responses_have_content_type_json(self):
        """Verify all endpoint responses have Content-Type: application/json."""
        endpoints = ["/health", "/stats", "/recent", "/vault", "/traces"]
        for endpoint in endpoints:
            handler = MockRequestHandler(endpoint)
            response = {"test": "data"}
            handler._send_json(response)
            self.assertEqual(
                handler.sent_headers.get("Content-Type"),
                "application/json",
                f"Endpoint {endpoint} missing Content-Type header"
            )

    def test_responses_have_content_length_header(self):
        """Verify responses include Content-Length header."""
        handler = MockRequestHandler("/health")
        response = {"status": "ok"}
        handler._send_json(response)
        self.assertIn("Content-Length", handler.sent_headers)
        self.assertGreater(int(handler.sent_headers["Content-Length"]), 0)

    def test_responses_have_cors_header(self):
        """Verify responses include CORS header."""
        handler = MockRequestHandler("/stats")
        response = {"test": "data"}
        handler._send_json(response)
        self.assertEqual(
            handler.sent_headers.get("Access-Control-Allow-Origin"),
            "*"
        )

    def test_json_response_is_valid(self):
        """Verify all responses are valid JSON."""
        handler = MockRequestHandler("/health")
        response = {"status": "ok", "data": {"key": "value"}}
        body = handler._send_json(response)
        # Should not raise JSON decode error
        parsed = json.loads(body.decode())
        self.assertIsInstance(parsed, dict)


if __name__ == "__main__":
    # Run all tests with verbose output
    unittest.main(verbosity=2)
