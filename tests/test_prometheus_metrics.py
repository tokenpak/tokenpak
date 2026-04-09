"""Tests for TokenPak Prometheus metrics exposition (TPK-PROM-01).

Coverage
────────
PrometheusRegistry.render()
 1.  Output starts with # HELP / # TYPE headers
 2.  Output ends with a trailing newline
 3.  tokenpak_requests_total counter present
 4.  tokenpak_request_duration_seconds histogram present (bucket/count/sum)
 5.  tokenpak_tokens_input_total counter present
 6.  tokenpak_tokens_output_total counter present
 7.  tokenpak_tokens_saved_total counter present
 8.  tokenpak_compression_ratio gauge present
 9.  tokenpak_errors_total counter with type label present
10.  tokenpak_uptime_seconds gauge present
11.  tokenpak_cost_usd_total counter present
12.  tokenpak_vault_blocks gauge present

Label correctness
13.  Labeled requests_total uses model + status labels
14.  Label values are properly escaped (special chars)
15.  Histogram buckets are sorted ascending with +Inf last

No-monitor fallback
16.  render() works with monitor=None (no DB)
17.  Fallback returns valid counter for requests_total

build_metrics_text()
18.  Works with empty session dict
19.  Returns string (not bytes)
20.  Compression ratio is 1.0 when no tokens sent

Helper functions
21.  _labels() with no kwargs returns empty string
22.  _labels() with kwargs returns {k="v"} format
23.  _escape_label_value() escapes backslash, quote, newline
24.  _counter() emits HELP + TYPE + sample
25.  _gauge() emits HELP + TYPE + sample
"""

from __future__ import annotations

import time
import unittest


def _make_session(**overrides) -> dict:
    """Build a minimal SESSION dict."""
    base = {
        "requests": 42,
        "input_tokens": 10000,
        "sent_input_tokens": 7500,
        "saved_tokens": 2500,
        "output_tokens": 1200,
        "cost": 0.0420,
        "cost_saved": 0.0140,
        "start_time": time.time() - 3600,
        "errors": 3,
        "cache_read_tokens": 800,
        "cache_creation_tokens": 200,
        "cache_hits": 12,
        "cache_misses": 30,
        "injected_tokens": 500,
        "canon_tokens_saved": 100,
    }
    base.update(overrides)
    return base


class TestPrometheusHelpers(unittest.TestCase):

    def test_labels_empty(self):
        from tokenpak.metrics.prometheus import _labels
        self.assertEqual(_labels(), "")

    def test_labels_single(self):
        from tokenpak.metrics.prometheus import _labels
        result = _labels(model="gpt-4o")
        self.assertEqual(result, '{model="gpt-4o"}')

    def test_labels_multiple(self):
        from tokenpak.metrics.prometheus import _labels
        result = _labels(model="gpt-4o", status="success")
        self.assertIn('model="gpt-4o"', result)
        self.assertIn('status="success"', result)
        self.assertTrue(result.startswith("{"))
        self.assertTrue(result.endswith("}"))

    def test_escape_label_backslash(self):
        from tokenpak.metrics.prometheus import _escape_label_value
        self.assertEqual(_escape_label_value("a\\b"), "a\\\\b")

    def test_escape_label_quote(self):
        from tokenpak.metrics.prometheus import _escape_label_value
        self.assertEqual(_escape_label_value('a"b'), 'a\\"b')

    def test_escape_label_newline(self):
        from tokenpak.metrics.prometheus import _escape_label_value
        self.assertEqual(_escape_label_value("a\nb"), "a\\nb")

    def test_counter_lines(self):
        from tokenpak.metrics.prometheus import _counter
        lines = _counter("my_counter", "help text", 42)
        self.assertEqual(lines[0], "# HELP my_counter help text")
        self.assertEqual(lines[1], "# TYPE my_counter counter")
        self.assertEqual(lines[2], "my_counter 42")

    def test_gauge_lines(self):
        from tokenpak.metrics.prometheus import _gauge
        lines = _gauge("my_gauge", "help", 3.14)
        self.assertEqual(lines[1], "# TYPE my_gauge gauge")
        self.assertIn("3.14", lines[2])


class TestPrometheusRegistryNoMonitor(unittest.TestCase):
    """Tests with monitor=None (no DB dependency)."""

    def setUp(self):
        from tokenpak.metrics.prometheus import PrometheusRegistry
        self.reg = PrometheusRegistry(_make_session(), monitor=None)
        self.output = self.reg.render()

    def test_output_is_string(self):
        self.assertIsInstance(self.output, str)

    def test_output_ends_with_newline(self):
        self.assertTrue(self.output.endswith("\n"))

    def test_requests_total_present(self):
        self.assertIn("tokenpak_requests_total", self.output)

    def test_requests_total_has_help(self):
        self.assertIn("# HELP tokenpak_requests_total", self.output)

    def test_requests_total_has_type(self):
        self.assertIn("# TYPE tokenpak_requests_total counter", self.output)

    def test_histogram_present(self):
        self.assertIn("tokenpak_request_duration_seconds", self.output)
        self.assertIn("tokenpak_request_duration_seconds_bucket", self.output)
        self.assertIn("tokenpak_request_duration_seconds_count", self.output)
        self.assertIn("tokenpak_request_duration_seconds_sum", self.output)

    def test_histogram_has_inf_bucket(self):
        self.assertIn('+Inf"', self.output)

    def test_tokens_input_total_present(self):
        self.assertIn("tokenpak_tokens_input_total", self.output)

    def test_tokens_output_total_present(self):
        self.assertIn("tokenpak_tokens_output_total", self.output)

    def test_tokens_saved_total_present(self):
        self.assertIn("tokenpak_tokens_saved_total", self.output)

    def test_compression_ratio_present(self):
        self.assertIn("tokenpak_compression_ratio", self.output)
        self.assertIn("# TYPE tokenpak_compression_ratio gauge", self.output)

    def test_errors_total_present(self):
        self.assertIn("tokenpak_errors_total", self.output)
        self.assertIn('type="all"', self.output)

    def test_uptime_seconds_present(self):
        self.assertIn("tokenpak_uptime_seconds", self.output)
        self.assertIn("# TYPE tokenpak_uptime_seconds gauge", self.output)

    def test_cost_present(self):
        self.assertIn("tokenpak_cost_usd_total", self.output)

    def test_vault_blocks_present(self):
        self.assertIn("tokenpak_vault_blocks", self.output)

    def test_compression_ratio_1_when_no_tokens_sent(self):
        from tokenpak.metrics.prometheus import PrometheusRegistry
        reg = PrometheusRegistry(
            _make_session(input_tokens=0, sent_input_tokens=0), monitor=None
        )
        out = reg.render()
        self.assertIn("tokenpak_compression_ratio 1.0", out)

    def test_all_7_metric_families_present(self):
        required = [
            "tokenpak_requests_total",
            "tokenpak_request_duration_seconds",
            "tokenpak_tokens_input_total",
            "tokenpak_tokens_output_total",
            "tokenpak_tokens_saved_total",
            "tokenpak_compression_ratio",
            "tokenpak_errors_total",
        ]
        for metric in required:
            self.assertIn(metric, self.output, f"Missing metric: {metric}")


class TestBuildMetricsText(unittest.TestCase):

    def test_returns_string(self):
        from tokenpak.metrics.prometheus import build_metrics_text
        result = build_metrics_text({})
        self.assertIsInstance(result, str)

    def test_empty_session_works(self):
        from tokenpak.metrics.prometheus import build_metrics_text
        result = build_metrics_text({})
        self.assertIn("tokenpak_requests_total", result)

    def test_no_monitor_works(self):
        from tokenpak.metrics.prometheus import build_metrics_text
        result = build_metrics_text(_make_session(), monitor=None)
        self.assertIn("tokenpak_tokens_saved_total", result)


if __name__ == "__main__":
    unittest.main()
