"""Informational wrappers around the governed health benchmark harness.

This module intentionally owns no scheduler, readiness loop, percentile
implementation, or release threshold.  It reuses the governed runner to keep
the historical local proxy smoke useful without creating a second benchmark
contract.
"""

from __future__ import annotations

import importlib.util
import socket
import time
from pathlib import Path
from typing import Any

import pytest

from tokenpak.proxy.server import ProxyServer

pytestmark = [pytest.mark.needs_proxy, pytest.mark.needs_fast_host]

RUNNER_PATH = Path(__file__).with_name("neutral_health_benchmark.py")
SPEC = importlib.util.spec_from_file_location("neutral_health_benchmark_load", RUNNER_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import bootstrap guard
    raise RuntimeError("unable to import the governed health benchmark runner")
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


class _RunningProxyView:
    """Duck-typed process view used only by the governed readiness function."""

    def poll(self) -> None:
        return None


@pytest.fixture(scope="module")
def proxy():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = ProxyServer(host="127.0.0.1", port=port)
    server.start(blocking=False)
    BENCHMARK.readiness_barrier("127.0.0.1", server.port, _RunningProxyView(), 30.0)
    yield server
    server.stop()


def _informational_measurement(
    proxy: ProxyServer, endpoint: str, phase: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, elapsed = BENCHMARK.open_loop(
        "127.0.0.1",
        proxy.port,
        time.monotonic_ns(),
        500,
        100.0,
        20,
        5.0,
        phase,
        endpoint,
    )
    return rows, BENCHMARK.summarize(rows, elapsed)


@pytest.fixture(scope="module")
def health_measurement(proxy):
    return _informational_measurement(proxy, "/health", "informational_health")


@pytest.fixture(scope="module")
def stats_measurement(proxy):
    return _informational_measurement(proxy, "/stats", "informational_stats")


class TestHealthEndpointLoad:
    """Wire-level health checks; these do not issue a V11 verdict."""

    def test_health_retains_all_informational_observations(self, health_measurement):
        rows, summary = health_measurement
        assert len(rows) == 500
        assert summary["planned_requests"] == 500
        assert summary["completed_observations"] == 500

    def test_health_uses_only_canonical_states(self, health_measurement):
        rows, summary = health_measurement
        assert summary["request_errors"] == 0
        assert all(BENCHMARK.valid_health(row)[0] for row in rows)
        assert {row["json_status"] for row in rows} <= BENCHMARK.KNOWN_HEALTH_STATES

    def test_health_reports_informational_latency(self, health_measurement):
        _rows, summary = health_measurement
        latency = summary["service_latency_ms"]
        print(
            "\nInformational /health: "
            f"p50={latency['p50']:.1f}ms "
            f"p95={latency['p95']:.1f}ms "
            f"p99={latency['p99']:.1f}ms "
            f"throughput={summary['achieved_throughput_rps']:.1f}rps"
        )
        assert latency["p50"] is not None
        assert latency["p99"] is not None


class TestStatsEndpointLoad:
    """Informational `/stats` wire check using the same governed scheduler."""

    def test_stats_retains_successful_observations(self, stats_measurement):
        rows, summary = stats_measurement
        assert len(rows) == 500
        assert summary["completed_observations"] == 500
        assert summary["request_errors"] == 0
        assert all(row["status_code"] == 200 for row in rows)

    def test_stats_reports_informational_latency(self, stats_measurement):
        _rows, summary = stats_measurement
        latency = summary["service_latency_ms"]
        print(f"\nInformational /stats: p50={latency['p50']:.1f}ms p99={latency['p99']:.1f}ms")
        assert latency["p50"] is not None
        assert latency["p99"] is not None
