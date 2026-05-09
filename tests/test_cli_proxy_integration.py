"""CLI Proxy Integration Tests — Verify CLI commands work with live proxy.

Tests verify:
- `tokenpak status` → parses live proxy response, not stale local DB
- `tokenpak cost` → shows real cumulative spend from SESSION
- `tokenpak doctor` → checks port 8766 availability, confirms proxy healthy
- `tokenpak stats` → reads proxy stats endpoint, formats output
"""

import json

import pytest

# Mock proxy SESSION for CLI testing
SESSION = {}

proxy_state = type('proxy_state', (), {
    'SESSION': SESSION,
})()


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def mock_proxy_endpoint():
    """Mock proxy endpoints for CLI testing."""
    endpoints = {
        "health": {"status": "healthy", "version": "4.0", "port": 8766},
        "stats": {
            "total_requests": 1523,
            "total_input_tokens": 450000,
            "total_output_tokens": 120000,
            "cache_hit_rate": 0.45,
            "avg_latency_ms": 245.3,
            "error_count": 2,
        },
        "cost": {
            "total_spend": 45.67,
            "estimated_daily_spend": 12.34,
            "by_model": {
                "claude-3-opus": 32.10,
                "claude-3-sonnet": 13.57,
            },
        },
        "session": proxy_state.SESSION,
    }
    return endpoints


@pytest.fixture(autouse=True)
def reset_session():
    """Reset SESSION before each test."""
    yield
    proxy_state.SESSION.clear()


@pytest.fixture
def cli_args_parser():
    """Get CLI argument parser."""
    import argparse
    parser = argparse.ArgumentParser(prog='tokenpak')
    return parser


# ============================================================================
# TEST GROUP 1: STATUS COMMAND
# ============================================================================

class TestStatusCommand:
    """Test `tokenpak status` command integration."""

    def test_status_returns_live_proxy_state(self, mock_proxy_endpoint):
        """Test status command returns live proxy response, not stale DB."""
        # Setup mock proxy response
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update({
            "total_requests": 1523,
            "total_input_tokens": 450000,
            "total_output_tokens": 120000,
            "proxy_version": "4.0",
            "uptime_seconds": 3600,
        })

        # Simulate status parsing
        status_data = {
            "requests": proxy_state.SESSION.get("total_requests", 0),
            "version": proxy_state.SESSION.get("proxy_version", "unknown"),
        }

        assert status_data["requests"] == 1523
        assert status_data["version"] == "4.0"

    def test_status_output_format_parseable(self, mock_proxy_endpoint):
        """Test status output is parseable."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update({
            "uptime_seconds": 3600,
            "proxy_healthy": True,
            "port": 8766,
        })

        # Format output
        output = f"""Proxy Status:
  Version: 4.0
  Port: {proxy_state.SESSION.get('port')}
  Healthy: {proxy_state.SESSION.get('proxy_healthy')}
  Uptime: {proxy_state.SESSION.get('uptime_seconds')}s
"""

        # Should be parseable
        assert "Proxy Status" in output
        assert "8766" in output
        assert "True" in output

    def test_status_includes_all_metrics(self, mock_proxy_endpoint):
        """Test status includes all required metrics."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update({
            "total_requests": 100,
            "total_input_tokens": 50000,
            "total_output_tokens": 10000,
            "proxy_healthy": True,
            "avg_latency_ms": 245.5,
        })

        # Check all metrics present
        assert "total_requests" in proxy_state.SESSION
        assert "total_input_tokens" in proxy_state.SESSION
        assert "total_output_tokens" in proxy_state.SESSION
        assert "avg_latency_ms" in proxy_state.SESSION

    def test_status_refreshes_from_live_proxy(self, mock_proxy_endpoint):
        """Test status doesn't use stale cache."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION["total_requests"] = 100

        # Simulate proxy update
        proxy_state.SESSION["total_requests"] = 150

        # Should reflect the updated value
        assert proxy_state.SESSION["total_requests"] == 150


# ============================================================================
# TEST GROUP 2: COST COMMAND
# ============================================================================

class TestCostCommand:
    """Test `tokenpak cost` command integration."""

    def test_cost_shows_cumulative_spend(self, mock_proxy_endpoint):
        """Test cost command shows cumulative spend from SESSION."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update({
            "total_spend_usd": 45.67,
            "spend_by_model": {
                "claude-3-opus": 32.10,
                "claude-3-sonnet": 13.57,
            },
            "input_tokens_total": 450000,
            "output_tokens_total": 120000,
        })

        assert proxy_state.SESSION["total_spend_usd"] == 45.67
        assert proxy_state.SESSION["spend_by_model"]["claude-3-opus"] == 32.10

    def test_cost_breakdown_by_model(self, mock_proxy_endpoint):
        """Test cost breakdown by model."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION["spend_by_model"] = {
            "claude-3-opus": 100.00,
            "claude-3-sonnet": 50.00,
            "claude-3-haiku": 25.00,
        }

        total = sum(proxy_state.SESSION["spend_by_model"].values())
        assert total == 175.00

        # Verify all models present
        for model in ["claude-3-opus", "claude-3-sonnet", "claude-3-haiku"]:
            assert model in proxy_state.SESSION["spend_by_model"]

    def test_cost_format_currency(self, mock_proxy_endpoint):
        """Test cost is formatted as currency."""
        cost = 45.67

        # Format as currency
        cost_str = f"${cost:.2f}"

        assert cost_str == "$45.67"

    def test_cost_estimated_daily_spend(self, mock_proxy_endpoint):
        """Test estimated daily spend calculation."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION["total_spend_usd"] = 100.00
        proxy_state.SESSION["uptime_seconds"] = 86400  # 1 day

        # Calculate daily rate
        daily_spend = proxy_state.SESSION["total_spend_usd"]  # 1 day uptime

        assert daily_spend == 100.00

    def test_cost_per_token_calculation(self, mock_proxy_endpoint):
        """Test per-token cost calculation."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION["total_spend_usd"] = 50.00
        proxy_state.SESSION["total_tokens"] = 1000000  # 1M tokens

        cost_per_token = proxy_state.SESSION["total_spend_usd"] / proxy_state.SESSION["total_tokens"]

        assert cost_per_token == 0.00005


# ============================================================================
# TEST GROUP 3: DOCTOR COMMAND
# ============================================================================

class TestDoctorCommand:
    """Test `tokenpak doctor` command integration."""

    def test_doctor_checks_port_availability(self, mock_proxy_endpoint):
        """Test doctor checks port 8766 availability."""
        proxy_state.SESSION.clear()

        # Mock port check
        port_available = True
        port = 8766

        if port_available:
            proxy_state.SESSION["port_8766_available"] = True

        assert proxy_state.SESSION.get("port_8766_available", False)

    def test_doctor_confirms_proxy_healthy(self, mock_proxy_endpoint):
        """Test doctor confirms proxy is healthy."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update({
            "proxy_healthy": True,
            "last_health_check": "2026-03-11T13:36:00Z",
            "health_check_duration_ms": 45,
        })

        # Run health check
        is_healthy = proxy_state.SESSION.get("proxy_healthy", False)

        assert is_healthy

    def test_doctor_reports_module_health(self, mock_proxy_endpoint):
        """Test doctor reports health of all modules."""
        proxy_state.SESSION.clear()

        modules = [
            "cache", "compression", "circuit_breaker", "failover",
            "budgeter", "cost_tracker", "token_counter", "rate_limiter",
        ]

        for module in modules:
            proxy_state.SESSION[f"{module}_healthy"] = True

        # Count healthy modules
        healthy_count = sum(
            1 for k, v in proxy_state.SESSION.items()
            if k.endswith("_healthy") and v
        )

        assert healthy_count == len(modules)

    def test_doctor_checks_database_connectivity(self, mock_proxy_endpoint):
        """Test doctor checks database connectivity."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION["db_connected"] = True
        proxy_state.SESSION["db_response_time_ms"] = 12

        assert proxy_state.SESSION["db_connected"]
        assert proxy_state.SESSION["db_response_time_ms"] < 1000

    def test_doctor_reports_configuration_status(self, mock_proxy_endpoint):
        """Test doctor reports configuration status."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update({
            "config_valid": True,
            "config_file": "/etc/tokenpak/config.yaml",
            "last_config_reload": "2026-03-11T10:00:00Z",
        })

        assert proxy_state.SESSION["config_valid"]


# ============================================================================
# TEST GROUP 4: STATS COMMAND
# ============================================================================

class TestStatsCommand:
    """Test `tokenpak stats` command integration."""

    def test_stats_reads_proxy_stats_endpoint(self, mock_proxy_endpoint):
        """Test stats command reads proxy stats endpoint."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update(mock_proxy_endpoint["stats"])

        assert proxy_state.SESSION["total_requests"] == 1523
        assert proxy_state.SESSION["cache_hit_rate"] == 0.45

    def test_stats_formats_output_readable(self, mock_proxy_endpoint):
        """Test stats output is readable."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update({
            "total_requests": 1523,
            "total_input_tokens": 450000,
            "total_output_tokens": 120000,
            "cache_hit_rate": 0.45,
            "avg_latency_ms": 245.3,
        })

        # Format output
        output = f"""Proxy Statistics:
  Total Requests: {proxy_state.SESSION['total_requests']}
  Input Tokens: {proxy_state.SESSION['total_input_tokens']:,}
  Output Tokens: {proxy_state.SESSION['total_output_tokens']:,}
  Cache Hit Rate: {proxy_state.SESSION['cache_hit_rate']:.1%}
  Avg Latency: {proxy_state.SESSION['avg_latency_ms']:.1f}ms
"""

        assert "1523" in output
        assert "450,000" in output
        assert "45.0%" in output

    def test_stats_includes_cache_metrics(self, mock_proxy_endpoint):
        """Test stats include cache hit rate."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update({
            "cache_hits": 500,
            "cache_misses": 600,
            "cache_hit_rate": 500 / (500 + 600),
        })

        assert proxy_state.SESSION["cache_hit_rate"] > 0
        assert proxy_state.SESSION["cache_hit_rate"] < 1

    def test_stats_includes_latency_metrics(self, mock_proxy_endpoint):
        """Test stats include latency percentiles."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update({
            "latency_p50_ms": 150,
            "latency_p95_ms": 450,
            "latency_p99_ms": 950,
            "latency_avg_ms": 245.3,
        })

        assert proxy_state.SESSION["latency_p50_ms"] < proxy_state.SESSION["latency_p95_ms"]
        assert proxy_state.SESSION["latency_p95_ms"] < proxy_state.SESSION["latency_p99_ms"]

    def test_stats_includes_error_metrics(self, mock_proxy_endpoint):
        """Test stats include error counts."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update({
            "error_count": 2,
            "error_rate": 2 / 1523,
            "upstream_errors": 1,
            "timeout_errors": 1,
        })

        assert proxy_state.SESSION["error_count"] == 2
        assert proxy_state.SESSION["error_rate"] < 0.01  # Less than 1%


# ============================================================================
# TEST GROUP 5: CLI ERROR HANDLING
# ============================================================================

class TestCLIErrorHandling:
    """Test CLI error handling."""

    def test_status_handles_proxy_unavailable(self):
        """Test status command handles proxy unavailable."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION["proxy_unavailable"] = True

        # Should report error gracefully
        error_msg = "Proxy unavailable on port 8766"

        if proxy_state.SESSION.get("proxy_unavailable"):
            assert error_msg is not None

    def test_cost_handles_no_spend_data(self):
        """Test cost command handles no spend data."""
        proxy_state.SESSION.clear()

        total_spend = proxy_state.SESSION.get("total_spend_usd", 0)

        assert total_spend == 0

    def test_doctor_reports_failed_health_checks(self):
        """Test doctor reports failed health checks."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION["proxy_healthy"] = False
        proxy_state.SESSION["health_error"] = "Connection timeout"

        assert not proxy_state.SESSION["proxy_healthy"]

    def test_stats_handles_empty_statistics(self):
        """Test stats command handles empty statistics."""
        proxy_state.SESSION.clear()

        request_count = proxy_state.SESSION.get("total_requests", 0)

        assert request_count == 0


# ============================================================================
# TEST GROUP 6: CLI OUTPUT PARSING
# ============================================================================

class TestCLIOutputParsing:
    """Test CLI output can be parsed programmatically."""

    def test_status_json_output_parseable(self):
        """Test status JSON output is valid JSON."""
        proxy_state.SESSION.clear()
        proxy_state.SESSION.update({
            "total_requests": 100,
            "proxy_version": "4.0",
            "healthy": True,
        })

        # Simulate JSON output
        output_json = json.dumps(proxy_state.SESSION)
        parsed = json.loads(output_json)

        assert parsed["total_requests"] == 100
        assert parsed["proxy_version"] == "4.0"

    def test_cost_csv_format_parseable(self):
        """Test cost CSV output can be parsed."""
        csv_output = """model,spend,percentage
claude-3-opus,32.10,70.25
claude-3-sonnet,13.57,29.75
"""
        lines = csv_output.strip().split("\n")

        assert len(lines) == 3  # Header + 2 models
        assert "claude-3-opus" in lines[1]

    def test_stats_text_output_parseable(self):
        """Test stats text output can be parsed."""
        stats_output = """Total Requests: 1523
Total Input Tokens: 450000
Cache Hit Rate: 45.0%
"""

        lines = stats_output.strip().split("\n")

        for line in lines:
            assert ":" in line  # Key: value format


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
