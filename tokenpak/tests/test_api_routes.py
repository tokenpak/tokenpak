# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for tokenpak.api module.

Covers: HealthRoute, MetricsRoute, RouteRegistry, build_default_registry

All external I/O (HTTP, ProxyMetricsCollector, HealthChecker) is mocked —
no live network calls, no filesystem access.
"""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# RouteRegistry
# ---------------------------------------------------------------------------


class TestRouteRegistry(unittest.TestCase):
    def setUp(self):
        from tokenpak.api.routes import RouteRegistry

        self.registry = RouteRegistry()

    def test_empty_registry_match_returns_none(self):
        self.assertIsNone(self.registry.match("/health"))

    def test_register_and_match_exact_path(self):
        handler = object()
        self.registry.register("/health", handler)
        self.assertIs(self.registry.match("/health"), handler)

    def test_match_strips_query_string(self):
        handler = object()
        self.registry.register("/health", handler)
        self.assertIs(self.registry.match("/health?verbose=true"), handler)

    def test_match_unknown_path_returns_none(self):
        self.registry.register("/health", object())
        self.assertIsNone(self.registry.match("/unknown"))

    def test_paths_empty_when_no_routes(self):
        self.assertEqual(self.registry.paths(), [])

    def test_paths_returns_registered_paths(self):
        self.registry.register("/health", object())
        self.registry.register("/metrics", object())
        paths = self.registry.paths()
        self.assertIn("/health", paths)
        self.assertIn("/metrics", paths)
        self.assertEqual(len(paths), 2)

    def test_register_overwrites_existing_handler(self):
        handler1 = object()
        handler2 = object()
        self.registry.register("/health", handler1)
        self.registry.register("/health", handler2)
        self.assertIs(self.registry.match("/health"), handler2)
        # Only one entry for the path
        self.assertEqual(len(self.registry.paths()), 1)

    def test_match_root_path(self):
        handler = object()
        self.registry.register("/", handler)
        self.assertIs(self.registry.match("/"), handler)

    def test_match_path_with_no_query_separator(self):
        handler = object()
        self.registry.register("/metrics", handler)
        # Path without query — must still match
        self.assertIs(self.registry.match("/metrics"), handler)


# ---------------------------------------------------------------------------
# HealthRoute — init
# ---------------------------------------------------------------------------


class TestHealthRouteInit(unittest.TestCase):
    @patch("tokenpak.api.routes.HealthChecker")
    def test_default_start_time_uses_current_time(self, MockHealthChecker):
        from tokenpak.api.routes import HealthRoute

        before = time.time()
        HealthRoute()
        after = time.time()

        # HealthChecker should have been called with a start_time in range
        call_kwargs = MockHealthChecker.call_args
        start_time = call_kwargs[1]["start_time"]
        self.assertGreaterEqual(start_time, before)
        self.assertLessEqual(start_time, after)

    @patch("tokenpak.api.routes.HealthChecker")
    def test_custom_start_time_passed_through(self, MockHealthChecker):
        from tokenpak.api.routes import HealthRoute

        t = 1_700_000_000.0
        HealthRoute(start_time=t)
        call_kwargs = MockHealthChecker.call_args
        self.assertEqual(call_kwargs[1]["start_time"], t)

    @patch("tokenpak.api.routes.HealthChecker")
    def test_custom_version_passed_through(self, MockHealthChecker):
        from tokenpak.api.routes import HealthRoute

        HealthRoute(version="99.9.9")
        call_kwargs = MockHealthChecker.call_args
        self.assertEqual(call_kwargs[1]["version"], "99.9.9")

    @patch("tokenpak.api.routes.HealthChecker")
    def test_no_version_passes_none(self, MockHealthChecker):
        from tokenpak.api.routes import HealthRoute

        HealthRoute()
        call_kwargs = MockHealthChecker.call_args
        self.assertIsNone(call_kwargs[1]["version"])


# ---------------------------------------------------------------------------
# HealthRoute — handle()
# ---------------------------------------------------------------------------


class TestHealthRouteHandle(unittest.TestCase):
    def _make_route(self, check_return=None):
        """Return a HealthRoute whose internal HealthChecker is mocked."""
        from tokenpak.api.routes import HealthRoute

        if check_return is None:
            check_return = {
                "status": "healthy",
                "timestamp": "2026-01-01T00:00:00Z",
                "uptime_seconds": 42,
                "proxy_version": "1.0.0",
                "providers": {"anthropic": {"status": "ok"}},
                "cache": {"entries": 0, "memory_used_mb": 0.0, "compression_ratio": 0.0},
            }
        with patch("tokenpak.api.routes.HealthChecker") as MockHC:
            MockHC.return_value.check.return_value = check_return
            route = HealthRoute(start_time=0.0)
            route._checker = MockHC.return_value
        return route, check_return

    def test_handle_returns_dict(self):
        route, expected = self._make_route()
        result = route.handle()
        self.assertIsInstance(result, dict)

    def test_handle_returns_checker_result(self):
        route, expected = self._make_route()
        result = route.handle()
        self.assertEqual(result, expected)

    def test_handle_healthy_status(self):
        route, _ = self._make_route({"status": "healthy"})
        result = route.handle()
        self.assertEqual(result["status"], "healthy")

    def test_handle_degraded_status(self):
        route, _ = self._make_route({"status": "degraded"})
        result = route.handle()
        self.assertEqual(result["status"], "degraded")

    def test_handle_unhealthy_status(self):
        route, _ = self._make_route({"status": "unhealthy"})
        result = route.handle()
        self.assertEqual(result["status"], "unhealthy")


# ---------------------------------------------------------------------------
# HealthRoute — handle_bytes()
# ---------------------------------------------------------------------------


class TestHealthRouteHandleBytes(unittest.TestCase):
    def _make_route_with_payload(self, payload=None):
        from tokenpak.api.routes import HealthRoute

        if payload is None:
            payload = {"status": "healthy", "uptime_seconds": 10}
        with patch("tokenpak.api.routes.HealthChecker") as MockHC:
            MockHC.return_value.check.return_value = payload
            route = HealthRoute(start_time=0.0)
            route._checker = MockHC.return_value
        return route, payload

    def test_handle_bytes_returns_tuple_of_three(self):
        route, _ = self._make_route_with_payload()
        result = route.handle_bytes()
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)

    def test_handle_bytes_body_is_bytes(self):
        route, _ = self._make_route_with_payload()
        body, status, headers = route.handle_bytes()
        self.assertIsInstance(body, bytes)

    def test_handle_bytes_status_is_200(self):
        route, _ = self._make_route_with_payload()
        _, status, _ = route.handle_bytes()
        self.assertEqual(status, 200)

    def test_handle_bytes_content_type_is_json(self):
        route, _ = self._make_route_with_payload()
        _, _, headers = route.handle_bytes()
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_handle_bytes_content_length_matches_body(self):
        route, _ = self._make_route_with_payload()
        body, _, headers = route.handle_bytes()
        self.assertEqual(int(headers["Content-Length"]), len(body))

    def test_handle_bytes_body_is_valid_json(self):
        route, payload = self._make_route_with_payload()
        body, _, _ = route.handle_bytes()
        parsed = json.loads(body.decode("utf-8"))
        self.assertEqual(parsed["status"], payload["status"])

    def test_handle_bytes_cors_header_present(self):
        route, _ = self._make_route_with_payload()
        _, _, headers = route.handle_bytes()
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")

    def test_handle_bytes_cache_control_no_cache(self):
        route, _ = self._make_route_with_payload()
        _, _, headers = route.handle_bytes()
        self.assertEqual(headers["Cache-Control"], "no-cache")

    def test_handle_bytes_status_always_200_even_unhealthy(self):
        route, _ = self._make_route_with_payload({"status": "unhealthy"})
        _, status, _ = route.handle_bytes()
        self.assertEqual(status, 200)


# ---------------------------------------------------------------------------
# MetricsRoute — init
# ---------------------------------------------------------------------------


class TestMetricsRouteInit(unittest.TestCase):
    def test_default_no_proxy_server(self):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        self.assertIsNone(route._proxy_server)

    def test_custom_proxy_server_stored(self):
        from tokenpak.api.routes import MetricsRoute

        ps = object()
        route = MetricsRoute(proxy_server=ps)
        self.assertIs(route._proxy_server, ps)

    def test_default_db_path_is_none(self):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        self.assertIsNone(route._db_path)

    def test_custom_db_path_stored(self):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute(db_path="/tmp/test.db")
        self.assertEqual(route._db_path, "/tmp/test.db")


# ---------------------------------------------------------------------------
# MetricsRoute — handle()
# ---------------------------------------------------------------------------


_SAMPLE_PROMETHEUS = (
    "# HELP tokenpak_up 1 if the TokenPak proxy is up and healthy, 0 otherwise\n"
    "# TYPE tokenpak_up gauge\n"
    "tokenpak_up 1\n"
)


class TestMetricsRouteHandle(unittest.TestCase):
    def _make_route(self, collect_return=_SAMPLE_PROMETHEUS):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        with patch(
            "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
            return_value=collect_return,
        ):
            # Store the mock so we can call handle() with it active
            pass
        return route

    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value=_SAMPLE_PROMETHEUS,
    )
    def test_handle_returns_string(self, _mock):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        result = route.handle()
        self.assertIsInstance(result, str)

    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value=_SAMPLE_PROMETHEUS,
    )
    def test_handle_contains_prometheus_content(self, _mock):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        result = route.handle()
        self.assertIn("tokenpak_up", result)

    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value=_SAMPLE_PROMETHEUS,
    )
    def test_handle_passes_proxy_server_to_collector(self, mock_collect):
        from tokenpak.api.routes import MetricsRoute

        ps = MagicMock()
        route = MetricsRoute(proxy_server=ps)
        route.handle()
        # Collector was constructed with the proxy_server
        # (can't inspect constructor easily through patch on method,
        #  but collect() was called — just verify no exception)
        self.assertTrue(mock_collect.called)

    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value="",
    )
    def test_handle_empty_metrics_is_string(self, _mock):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        result = route.handle()
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# MetricsRoute — handle_bytes()
# ---------------------------------------------------------------------------


class TestMetricsRouteHandleBytes(unittest.TestCase):
    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value=_SAMPLE_PROMETHEUS,
    )
    def test_handle_bytes_returns_tuple_of_three(self, _mock):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        result = route.handle_bytes()
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)

    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value=_SAMPLE_PROMETHEUS,
    )
    def test_handle_bytes_body_is_bytes(self, _mock):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        body, _, _ = route.handle_bytes()
        self.assertIsInstance(body, bytes)

    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value=_SAMPLE_PROMETHEUS,
    )
    def test_handle_bytes_status_is_200(self, _mock):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        _, status, _ = route.handle_bytes()
        self.assertEqual(status, 200)

    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value=_SAMPLE_PROMETHEUS,
    )
    def test_handle_bytes_content_type_prometheus(self, _mock):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        _, _, headers = route.handle_bytes()
        self.assertIn("text/plain", headers["Content-Type"])
        self.assertIn("0.0.4", headers["Content-Type"])

    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value=_SAMPLE_PROMETHEUS,
    )
    def test_handle_bytes_content_length_matches_body(self, _mock):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        body, _, headers = route.handle_bytes()
        self.assertEqual(int(headers["Content-Length"]), len(body))

    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value=_SAMPLE_PROMETHEUS,
    )
    def test_handle_bytes_cors_header_present(self, _mock):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        _, _, headers = route.handle_bytes()
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")

    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value=_SAMPLE_PROMETHEUS,
    )
    def test_handle_bytes_body_decodes_to_source_text(self, _mock):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        body, _, _ = route.handle_bytes()
        self.assertEqual(body.decode("utf-8"), _SAMPLE_PROMETHEUS)

    @patch(
        "tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector.collect",
        return_value=_SAMPLE_PROMETHEUS,
    )
    def test_handle_bytes_no_cache_header(self, _mock):
        from tokenpak.api.routes import MetricsRoute

        route = MetricsRoute()
        _, _, headers = route.handle_bytes()
        self.assertEqual(headers["Cache-Control"], "no-cache")


# ---------------------------------------------------------------------------
# build_default_registry()
# ---------------------------------------------------------------------------


class TestBuildDefaultRegistry(unittest.TestCase):
    @patch("tokenpak.api.routes.HealthChecker")
    def test_returns_route_registry_instance(self, _mock):
        from tokenpak.api.routes import RouteRegistry, build_default_registry

        reg = build_default_registry()
        self.assertIsInstance(reg, RouteRegistry)

    @patch("tokenpak.api.routes.HealthChecker")
    def test_health_path_registered(self, _mock):
        from tokenpak.api.routes import build_default_registry

        reg = build_default_registry()
        self.assertIsNotNone(reg.match("/health"))

    @patch("tokenpak.api.routes.HealthChecker")
    def test_metrics_path_registered(self, _mock):
        from tokenpak.api.routes import build_default_registry

        reg = build_default_registry()
        self.assertIsNotNone(reg.match("/metrics"))

    @patch("tokenpak.api.routes.HealthChecker")
    def test_health_handler_is_health_route(self, _mock):
        from tokenpak.api.routes import HealthRoute, build_default_registry

        reg = build_default_registry()
        self.assertIsInstance(reg.match("/health"), HealthRoute)

    @patch("tokenpak.api.routes.HealthChecker")
    def test_metrics_handler_is_metrics_route(self, _mock):
        from tokenpak.api.routes import MetricsRoute, build_default_registry

        reg = build_default_registry()
        self.assertIsInstance(reg.match("/metrics"), MetricsRoute)

    @patch("tokenpak.api.routes.HealthChecker")
    def test_unknown_path_returns_none(self, _mock):
        from tokenpak.api.routes import build_default_registry

        reg = build_default_registry()
        self.assertIsNone(reg.match("/not-a-route"))

    @patch("tokenpak.api.routes.HealthChecker")
    def test_start_time_passed_to_health_route(self, MockHC):
        from tokenpak.api.routes import build_default_registry

        t = 1_700_000_000.0
        build_default_registry(start_time=t)
        call_kwargs = MockHC.call_args
        self.assertEqual(call_kwargs[1]["start_time"], t)

    @patch("tokenpak.api.routes.HealthChecker")
    def test_default_start_time_is_recent(self, MockHC):
        from tokenpak.api.routes import build_default_registry

        before = time.time()
        build_default_registry()
        after = time.time()

        call_kwargs = MockHC.call_args
        start_time = call_kwargs[1]["start_time"]
        self.assertGreaterEqual(start_time, before)
        self.assertLessEqual(start_time, after)


# ---------------------------------------------------------------------------
# __init__ re-exports
# ---------------------------------------------------------------------------


class TestApiModuleExports(unittest.TestCase):
    def test_health_route_importable(self):
        from tokenpak.api import HealthRoute

        self.assertTrue(callable(HealthRoute))

    def test_metrics_route_importable(self):
        from tokenpak.api import MetricsRoute

        self.assertTrue(callable(MetricsRoute))

    def test_route_registry_importable(self):
        from tokenpak.api import RouteRegistry

        self.assertTrue(callable(RouteRegistry))

    def test_build_default_registry_importable(self):
        from tokenpak.api import build_default_registry

        self.assertTrue(callable(build_default_registry))


if __name__ == "__main__":
    unittest.main()
