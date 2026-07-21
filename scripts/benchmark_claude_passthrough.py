#!/usr/bin/env python3
"""Offline Claude Code passthrough latency regression gate.

The benchmark exercises the real byte-preserved ``process_request`` path. It
replaces vault retrieval with a deterministic no-result function so the run is
local, repeatable, and unable to contact an external service. Target batches
are interleaved with a frozen JSON/header calibration workload of similar cost;
the committed baseline uses the ratio of independently aggregated p50 values
so the same five-percent gate remains meaningful across CPU frequency changes
without amplifying pairwise scheduling noise.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import socket
import statistics
import sys
import time
import types
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tokenpak.proxy.pipeline import process_request  # noqa: E402
from tokenpak.proxy.request import ROUTE_CLAUDE_CODE, ProxyRequest  # noqa: E402
from tokenpak.proxy.route_policy import get_policy  # noqa: E402

SCHEMA_VERSION = 2
SCENARIO = "claude-code-byte-preserved-64k-v1"
NORMALIZATION_METHOD = "ratio-of-independent-round-medians-v1"
MAX_REGRESSION_PERCENT = 5.0
DEFAULT_SAMPLES = 51
DEFAULT_WARMUP = 11
DEFAULT_ITERATIONS = 10
DEFAULT_ROUNDS = 5
BASELINE_PATH = ROOT / "tests" / "benchmarks" / "claude_passthrough_baseline.json"


@dataclass(frozen=True)
class BenchmarkResult:
    scenario: str
    samples: int
    warmup_samples: int
    iterations_per_sample: int
    measurement_rounds: int
    payload_bytes: int
    raw_p50_ns: float
    raw_p95_ns: float
    calibration_p50_ns: float
    normalized_p50: float
    python: str
    machine: str


@dataclass(frozen=True)
class Comparison:
    passed: bool
    measured: float
    baseline: float
    limit: float
    regression_percent: float
    reason: str


def _fixture_body() -> bytes:
    """Return a stable Anthropic Messages body close to 64 KiB."""
    prefix = "Review the repository context and preserve exact request bytes. "
    content = (prefix * 1100)[:64_000]
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "system": [{"type": "text", "text": "You are a careful coding assistant."}],
        "messages": [{"role": "user", "content": content}],
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _fixture_headers() -> dict[str, str]:
    return {
        "authorization": "Bearer benchmark-placeholder",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
        "content-type": "application/json",
        "user-agent": "claude-code/benchmark",
        "accept": "application/json",
    }


def _no_vault_result(
    body: bytes, *, adapter: Any = None, request: ProxyRequest | None = None
) -> tuple[bytes, int, list[str], str]:
    """Deterministic replacement for retrieval; deliberately performs no I/O."""
    del adapter, request
    return body, 0, [], ""


def _calibration_once(body: bytes, headers: dict[str, str]) -> int:
    """Frozen non-product workload that tracks the target's JSON and allocation cost."""
    total = 0
    for _ in range(12):
        decoded = json.loads(body)
        encoded = json.dumps(decoded, separators=(",", ":")).encode("utf-8")
        copied_headers = {str(key): str(value) for key, value in headers.items()}
        total += len(encoded) + len(copied_headers)
    return total


def _target_once(body: bytes, headers: dict[str, str]) -> Any:
    request = ProxyRequest(
        method="POST",
        url="https://api.anthropic.com/v1/messages",
        headers=dict(headers),
        body=body,
        source_platform=ROUTE_CLAUDE_CODE,
    )
    return process_request(
        request,
        get_policy(ROUTE_CLAUDE_CODE),
        route=ROUTE_CLAUDE_CODE,
        client_has_auth=True,
    )


def _validate_passthrough_result(result: Any, body: bytes, headers: dict[str, str]) -> None:
    if result.request.body != body:
        raise RuntimeError("Claude Code passthrough did not preserve the fixture bytes")
    if result.request.headers != headers:
        raise RuntimeError("Claude Code passthrough did not preserve the fixture headers")
    expected_stages = [
        "cache_poison_removal",
        "vault_injection",
        "header_forwarding",
        "auth_injection",
        "byte_restore",
    ]
    if [stage.name for stage in result.stages] != expected_stages:
        raise RuntimeError("Claude Code passthrough did not execute the expected stage sequence")


def _time_batch(fn: Callable[[], Any], iterations: int) -> float:
    started = time.thread_time_ns()
    for _ in range(iterations):
        fn()
    return (time.thread_time_ns() - started) / iterations


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("at least one timing sample is required")
    ordered = sorted(values)
    index = min(int(len(ordered) * percentile / 100), len(ordered) - 1)
    return ordered[index]


def _normalize_p50(target_samples: Sequence[float], calibration_samples: Sequence[float]) -> float:
    """Return the ratio of independently aggregated target and control p50s."""
    if not target_samples or not calibration_samples:
        raise ValueError("target and calibration timing samples are required")
    target_p50 = statistics.median(target_samples)
    calibration_p50 = statistics.median(calibration_samples)
    if target_p50 <= 0 or calibration_p50 <= 0:
        raise RuntimeError("timing samples must produce positive p50 values")
    return target_p50 / calibration_p50


def run_benchmark(
    *,
    samples: int = DEFAULT_SAMPLES,
    warmup_samples: int = DEFAULT_WARMUP,
    iterations_per_sample: int = DEFAULT_ITERATIONS,
    measurement_rounds: int = DEFAULT_ROUNDS,
) -> BenchmarkResult:
    """Measure the real passthrough path and return raw and normalized p50s."""
    if samples < 3 or warmup_samples < 0 or iterations_per_sample < 1 or measurement_rounds < 1:
        raise ValueError("samples >= 3, warmup >= 0, iterations >= 1, and rounds >= 1 are required")

    body = _fixture_body()
    headers = _fixture_headers()

    def target() -> None:
        _target_once(body, headers)

    def calibration() -> None:
        _calibration_once(body, headers)

    target_round_p50s: list[float] = []
    target_round_p95s: list[float] = []
    calibration_round_p50s: list[float] = []
    normalized_round_p50s: list[float] = []

    offline_vault_bridge = types.ModuleType("tokenpak.proxy.vault_bridge")
    offline_vault_bridge.inject_vault_context = _no_vault_result  # type: ignore[attr-defined]

    with (
        mock.patch.dict(sys.modules, {"tokenpak.proxy.vault_bridge": offline_vault_bridge}),
        mock.patch.object(
            socket,
            "socket",
            side_effect=RuntimeError("network access is forbidden in the passthrough benchmark"),
        ),
    ):
        _validate_passthrough_result(_target_once(body, headers), body, headers)
        for round_index in range(measurement_rounds):
            target_samples: list[float] = []
            calibration_samples: list[float] = []
            for _ in range(warmup_samples):
                _time_batch(target, iterations_per_sample)
                _time_batch(calibration, iterations_per_sample)

            for index in range(samples):
                if (index + round_index) % 2:
                    calibration_ns = _time_batch(calibration, iterations_per_sample)
                    target_ns = _time_batch(target, iterations_per_sample)
                else:
                    target_ns = _time_batch(target, iterations_per_sample)
                    calibration_ns = _time_batch(calibration, iterations_per_sample)
                target_samples.append(target_ns)
                calibration_samples.append(calibration_ns)

            target_round_p50s.append(statistics.median(target_samples))
            target_round_p95s.append(_percentile(target_samples, 95))
            calibration_round_p50s.append(statistics.median(calibration_samples))
            normalized_round_p50s.append(_normalize_p50(target_samples, calibration_samples))

    return BenchmarkResult(
        scenario=SCENARIO,
        samples=samples,
        warmup_samples=warmup_samples,
        iterations_per_sample=iterations_per_sample,
        measurement_rounds=measurement_rounds,
        payload_bytes=len(body),
        raw_p50_ns=statistics.median(target_round_p50s),
        raw_p95_ns=statistics.median(target_round_p95s),
        calibration_p50_ns=statistics.median(calibration_round_p50s),
        normalized_p50=statistics.median(normalized_round_p50s),
        python=f"{sys.version_info.major}.{sys.version_info.minor}",
        machine=platform.machine() or "unknown",
    )


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "scenario",
        "normalization_method",
        "max_regression_percent",
        "normalized_p50",
        "samples",
        "warmup_samples",
        "iterations_per_sample",
        "measurement_rounds",
        "payload_bytes",
        "raw_p50_ns",
        "calibration_p50_ns",
        "capture_runs",
        "normalized_p50_captures",
    }
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError(f"baseline is missing required fields: {', '.join(missing)}")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported baseline schema: {data['schema_version']}")
    if data["scenario"] != SCENARIO:
        raise ValueError(f"baseline scenario mismatch: {data['scenario']}")
    if data["normalization_method"] != NORMALIZATION_METHOD:
        raise ValueError(f"baseline normalization mismatch: {data['normalization_method']}")
    if float(data["max_regression_percent"]) != MAX_REGRESSION_PERCENT:
        raise ValueError("baseline must enforce the fixed five-percent regression limit")
    if float(data["raw_p50_ns"]) <= 0 or float(data["calibration_p50_ns"]) <= 0:
        raise ValueError("baseline timing values must be positive")
    captures = [float(value) for value in data["normalized_p50_captures"]]
    if float(data["normalized_p50"]) <= 0 or any(value <= 0 for value in captures):
        raise ValueError("baseline normalized timing values must be positive")
    if int(data["capture_runs"]) != len(captures) or len(captures) < 3 or not len(captures) % 2:
        raise ValueError("baseline requires an odd set of at least three capture runs")
    if not math.isclose(statistics.median(captures), float(data["normalized_p50"]), rel_tol=1e-12):
        raise ValueError("baseline normalized p50 must equal the capture median")
    capture_span = (max(captures) / min(captures) - 1) * 100
    if capture_span > MAX_REGRESSION_PERCENT:
        raise ValueError("baseline capture spread exceeds the five-percent regression budget")
    return data


def compare_result(result: BenchmarkResult, baseline: dict[str, Any]) -> Comparison:
    """Compare one result to the committed baseline; fail closed on drift."""
    expected_shape = {
        "scenario": result.scenario,
        "samples": result.samples,
        "warmup_samples": result.warmup_samples,
        "iterations_per_sample": result.iterations_per_sample,
        "measurement_rounds": result.measurement_rounds,
        "payload_bytes": result.payload_bytes,
    }
    mismatches = [
        f"{key}={baseline.get(key)!r} (expected {value!r})"
        for key, value in expected_shape.items()
        if baseline.get(key) != value
    ]
    measured = result.normalized_p50
    reference = float(baseline["normalized_p50"])
    limit = reference * (1 + MAX_REGRESSION_PERCENT / 100)
    regression = ((measured / reference) - 1) * 100 if reference > 0 else float("inf")

    if mismatches:
        return Comparison(
            passed=False,
            measured=measured,
            baseline=reference,
            limit=limit,
            regression_percent=regression,
            reason="benchmark contract drift: " + "; ".join(mismatches),
        )
    if reference <= 0:
        return Comparison(
            False, measured, reference, limit, regression, "baseline must be positive"
        )
    if measured > limit:
        return Comparison(
            False,
            measured,
            reference,
            limit,
            regression,
            f"normalized p50 regressed by {regression:.2f}% (limit {MAX_REGRESSION_PERCENT:.2f}%)",
        )
    return Comparison(True, measured, reference, limit, regression, "within regression budget")


def _format_ms(nanoseconds: float) -> str:
    return f"{nanoseconds / 1_000_000:.4f} ms"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--json", action="store_true", help="emit the measured result as JSON")
    args = parser.parse_args(argv)

    try:
        baseline = load_baseline(args.baseline)
        result = run_benchmark(
            samples=int(baseline["samples"]),
            warmup_samples=int(baseline["warmup_samples"]),
            iterations_per_sample=int(baseline["iterations_per_sample"]),
            measurement_rounds=int(baseline["measurement_rounds"]),
        )
        comparison = compare_result(result, baseline)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Claude Code passthrough benchmark ERROR: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps({"result": asdict(result), "comparison": asdict(comparison)}, sort_keys=True)
        )
    else:
        print(f"scenario: {result.scenario}")
        print(f"payload: {result.payload_bytes} bytes")
        print(f"raw p50: {_format_ms(result.raw_p50_ns)}")
        print(f"raw p95: {_format_ms(result.raw_p95_ns)}")
        print(f"normalized p50: {result.normalized_p50:.6f}")
        print(f"baseline normalized p50: {comparison.baseline:.6f}")
        print(f"blocking limit (+5%): {comparison.limit:.6f}")
        print(f"delta: {comparison.regression_percent:+.2f}%")
        print(f"Claude Code passthrough benchmark: {'PASS' if comparison.passed else 'FAIL'}")
        if not comparison.passed:
            print(comparison.reason, file=sys.stderr)
    return 0 if comparison.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
