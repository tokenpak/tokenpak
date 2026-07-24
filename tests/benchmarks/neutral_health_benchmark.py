#!/usr/bin/env python3
"""Governed, provider-free TokenPak ``/health`` benchmark runner.

The runner keeps cold readiness (O3a/O3b), startup admission (O6a/O6b),
warm-up, and warmed sustained capacity (V11) in independent datasets.  It
uses only the Python standard library and writes complete, hash-manifested
machine-readable receipts.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import http.client
import io
import json
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

SCHEMA = "tokenpak-health-benchmark/v1"
MATRIX_SCHEMA = "tokenpak-health-benchmark-matrix/v1"
PROVENANCE_SCHEMA = "tokenpak-build-provenance/v1"
SUITE_VERSION = "bench-suite-v1.0.0"
PROFILE_ID = "tokenpak-health-reference-v1"
KNOWN_HEALTH_STATES = {"ok", "degraded", "shutting_down"}
RUN_MODES = ("o3", "o6a", "o6b", "v11")
MODE_VECTOR_NAMES = {
    "o3": ("o3a_listener", "o3b_health"),
    "o6a": ("o6a_startup",),
    "o6b": ("o6b_listener",),
    "v11": ("warmup", "v11"),
}

# V11 is deliberately not configurable from the environment or command line.
V11_WARMUP_REQUESTS = 20
V11_WARMUP_RPS = 25.0
V11_REQUESTS = 500
V11_RPS = 100.0
V11_WORKERS = 20
V11_REQUEST_TIMEOUT_S = 5.0
V11_P50_CEILING_MS = 15.0
V11_P99_CEILING_MS = 500.0
V11_MINIMUM_THROUGHPUT_RPS = 85.0
V11_SUBMIT_LAG_P99_CEILING_MS = 10.0
V11_SUBMIT_LAG_MAXIMUM_CEILING_MS = 50.0

O3_OBSERVATION_WINDOW_S = 30.0
O6A_REQUESTS = 500
O6A_RPS = 100.0
O6B_REQUESTS = 100
O6B_RPS = 1000.0
READINESS_RESPONSES = 3
READINESS_INTERVAL_S = 0.050

PROVIDER_SECRET_ENV = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "FIREWORKS_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
    "PERPLEXITY_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
    "VOYAGE_API_KEY",
    "JINA_API_KEY",
    "LITELLM_API_KEY",
    "GITHUB_TOKEN",
    "HF_TOKEN",
    "TOKENPAK_API_KEY",
    "TOKENPAK_PROXY_AUTH_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_OPENAI_API_KEY",
}
RELEVANT_ENV = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "TMPDIR",
    "TZ",
    "PYTHONHASHSEED",
    "PYTHONNOUSERSITE",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "TOKENPAK_HOME",
}
CHILD_ENV_INHERITED_ALLOWLIST = {"LANG", "LC_ALL", "LC_CTYPE", "TZ"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40,64}$")


class ContractError(RuntimeError):
    """Raised when evidence cannot satisfy the benchmark contract."""


class ObservationTimeout(TimeoutError):
    """A bounded subject observation ended without silently losing its vector."""

    def __init__(self, message: str, observations: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.observations = observations


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def safe_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"unavailable:{type(exc).__name__}:{exc}"


def command_text(command: Sequence[str], timeout: float = 15.0, *, cwd: Path | None = None) -> str:
    try:
        return subprocess.check_output(
            list(command),
            cwd=cwd,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable:{type(exc).__name__}:{exc}"


def read_suite_version(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ContractError(f"suite version unavailable: {exc}") from exc
    if lines != [SUITE_VERSION]:
        raise ContractError(f"suite version must be exactly {SUITE_VERSION!r}")
    return lines[0]


def read_reference_profile(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    marker = "```json tokenpak-health-reference-profile"
    if text.count(marker) != 1:
        raise ContractError("reference profile must contain one machine-readable block")
    body = text.split(marker, 1)[1]
    if "```" not in body:
        raise ContractError("reference profile block is unterminated")
    try:
        profile = json.loads(body.split("```", 1)[0])
    except json.JSONDecodeError as exc:
        raise ContractError(f"reference profile JSON is invalid: {exc}") from exc
    if not isinstance(profile, dict) or profile.get("profile_id") != PROFILE_ID:
        raise ContractError("reference profile identity mismatch")
    expected_v11 = {
        "warmup_requests": V11_WARMUP_REQUESTS,
        "warmup_rps": V11_WARMUP_RPS,
        "measured_requests": V11_REQUESTS,
        "measured_rps": V11_RPS,
        "workers": V11_WORKERS,
        "request_timeout_s": V11_REQUEST_TIMEOUT_S,
        "p50_ceiling_ms": V11_P50_CEILING_MS,
        "p99_ceiling_ms": V11_P99_CEILING_MS,
        "minimum_throughput_rps": V11_MINIMUM_THROUGHPUT_RPS,
        "maximum_request_errors": 0,
        "maximum_listener_drops": 0,
        "maximum_listener_overflows": 0,
    }
    if profile.get("v11") != expected_v11:
        raise ContractError("reference profile V11 contract drift")
    return profile


def verify_governed_inputs(args: argparse.Namespace) -> dict[str, Any]:
    runner = Path(__file__).resolve()
    actual = {
        "runner_sha256": sha256_file(runner),
        "suite_sha256": sha256_file(args.suite_file),
        "reference_sha256": sha256_file(args.reference_spec),
        "artifact_sha256": sha256_file(args.artifact),
        "artifact_provenance_sha256": sha256_file(args.artifact_provenance),
    }
    expected = {
        "runner_sha256": args.expected_runner_sha256,
        "suite_sha256": args.expected_suite_sha256,
        "reference_sha256": args.expected_reference_sha256,
        "artifact_sha256": args.artifact_sha256,
        "artifact_provenance_sha256": args.artifact_provenance_sha256,
    }
    malformed = [name for name, value in expected.items() if not SHA256_RE.fullmatch(value)]
    mismatches = [name for name in expected if expected[name] != actual[name]]
    if malformed or mismatches:
        raise ContractError(
            f"governed input mismatch: malformed={malformed}, mismatches={mismatches}"
        )
    read_suite_version(args.suite_file)
    profile = read_reference_profile(args.reference_spec)
    if not IMAGE_DIGEST_RE.fullmatch(args.runtime_image_digest):
        raise ContractError("a content-addressed sha256 runtime image digest is required")
    return {"actual": actual, "expected": expected, "reference_profile": profile}


def sanitized_environment(source: dict[str, str]) -> dict[str, str]:
    return {key: source[key] for key in sorted(RELEVANT_ENV & source.keys())}


def subject_environment(
    output_dir: Path,
) -> tuple[dict[str, str], list[str], list[str]]:
    """Build a deterministic deny-by-default child environment.

    Only locale/timezone inputs are inherited.  Execution paths and writable
    homes are runner-owned so arbitrary credentials, proxy configuration,
    Python injection variables, and secret-shaped variables cannot leak into
    the measured process.
    """

    inherited = {
        key: os.environ[key] for key in sorted(CHILD_ENV_INHERITED_ALLOWLIST) if key in os.environ
    }
    denied = sorted(set(os.environ) - set(inherited))
    removed_secrets = sorted(key for key in PROVIDER_SECRET_ENV if key in os.environ)
    subject_home = output_dir / "subject-home"
    temporary = output_dir / "tmp"
    tokenpak_home = output_dir / "tokenpak-home"
    for path in (subject_home, temporary, tokenpak_home):
        path.mkdir(parents=True, exist_ok=True)
    env = dict(inherited)
    env.update(
        {
            "HOME": str(subject_home),
            "PATH": os.defpath,
            "TMPDIR": str(temporary),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "TOKENPAK_HOME": str(tokenpak_home),
        }
    )
    return env, denied, removed_secrets


def selected_numeric(path: Path, prefixes: Iterable[str]) -> dict[str, int]:
    wanted = set(prefixes)
    result: dict[str, int] = {}
    for line in safe_text(path).splitlines():
        fields = line.replace(":", " ").split()
        if len(fields) < 2 or fields[0] not in wanted:
            continue
        try:
            result[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return result


def proc_netstat() -> dict[str, int]:
    result: dict[str, int] = {}
    lines = safe_text(Path("/proc/net/netstat")).splitlines()
    for offset in range(0, len(lines) - 1, 2):
        names = lines[offset].split()
        values = lines[offset + 1].split()
        if not names or not values or names[0] != values[0]:
            continue
        prefix = names[0].rstrip(":")
        for name, value in zip(names[1:], values[1:]):
            try:
                result[f"{prefix}.{name}"] = int(value)
            except ValueError:
                continue
    return result


def listener_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    keys = ("TcpExt.ListenOverflows", "TcpExt.ListenDrops")
    return {key: after.get(key, 0) - before.get(key, 0) for key in keys}


def cpu_counters() -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for line in safe_text(Path("/proc/stat")).splitlines():
        fields = line.split()
        if not fields or not re.fullmatch(r"cpu\d*", fields[0]):
            continue
        try:
            result[fields[0]] = [int(value) for value in fields[1:]]
        except ValueError:
            continue
    return result


def throttle_counters() -> dict[str, int]:
    result: dict[str, int] = {}
    root = Path("/sys/devices/system/cpu")
    for cpu in sorted(root.glob("cpu[0-9]*")):
        for name, relative in (
            ("core", "thermal_throttle/core_throttle_count"),
            ("package", "thermal_throttle/package_throttle_count"),
        ):
            path = cpu / relative
            if not path.is_file():
                continue
            try:
                result[f"{cpu.name}.{name}"] = int(path.read_text().strip())
            except (OSError, ValueError):
                continue
    return result


def frequency_snapshot() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    root = Path("/sys/devices/system/cpu")
    for cpu in sorted(root.glob("cpu[0-9]*")):
        fields: dict[str, str] = {}
        for name in (
            "scaling_cur_freq",
            "scaling_min_freq",
            "scaling_max_freq",
            "scaling_governor",
        ):
            path = cpu / "cpufreq" / name
            if path.is_file():
                fields[name] = safe_text(path).strip()
        if fields:
            result[cpu.name] = fields
    return result


def sample_system(pid: int | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "utc": utcnow(),
        "monotonic_ns": time.monotonic_ns(),
        "affinity": (sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None),
        "loadavg": safe_text(Path("/proc/loadavg")).strip(),
        "cpu": cpu_counters(),
        "cpu_frequency": frequency_snapshot(),
        "cpu_throttle": throttle_counters(),
        "meminfo_kib": selected_numeric(
            Path("/proc/meminfo"),
            ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "Dirty", "Writeback"),
        ),
        "vmstat": selected_numeric(
            Path("/proc/vmstat"),
            ("pgfault", "pgmajfault", "pswpin", "pswpout", "pgscan_kswapd", "pgsteal_kswapd"),
        ),
        "diskstats": safe_text(Path("/proc/diskstats")).splitlines(),
    }
    if pid is not None:
        row["process"] = {
            "pid": pid,
            "stat": safe_text(Path(f"/proc/{pid}/stat")).strip(),
            "status": safe_text(Path(f"/proc/{pid}/status")).splitlines(),
            "io": safe_text(Path(f"/proc/{pid}/io")).splitlines(),
        }
    return row


def competing_processes() -> list[str]:
    raw = command_text(["ps", "-eo", "pid=,ppid=,stat=,pcpu=,pmem=,comm=", "--sort=-pcpu"])
    return raw.splitlines()[:50]


class Sampler:
    def __init__(self, path: Path, pid: int, interval_s: float = 0.25) -> None:
        self.path = path
        self.pid = pid
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            while not self._stop.is_set():
                handle.write(json.dumps(sample_system(self.pid), sort_keys=True) + "\n")
                handle.flush()
                self._stop.wait(self.interval_s)


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int((len(ordered) - 1) * fraction)
    return ordered[max(0, min(len(ordered) - 1, index))]


def fetch_endpoint(
    host: str, port: int, timeout_s: float, endpoint: str = "/health"
) -> dict[str, Any]:
    started_ns = time.monotonic_ns()
    connection = http.client.HTTPConnection(host, port, timeout=timeout_s)
    try:
        connection.request("GET", endpoint, headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read()
        content_type = response.getheader("Content-Type")
        parsed: Any = None
        parse_error: str | None = None
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            parse_error = f"{type(exc).__name__}:{exc}"
        return {
            "completed": True,
            "status_code": response.status,
            "content_type": content_type,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_length": len(body),
            "json_status": parsed.get("status") if isinstance(parsed, dict) else None,
            "json_parse_error": parse_error,
            "service_latency_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
            "error": None,
        }
    except (OSError, http.client.HTTPException) as exc:
        return {
            "completed": True,
            "status_code": 0,
            "content_type": None,
            "body_sha256": None,
            "body_length": 0,
            "json_status": None,
            "json_parse_error": None,
            "service_latency_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
            "error": f"{type(exc).__name__}:{exc}",
        }
    finally:
        connection.close()


def fetch_health(host: str, port: int, timeout_s: float) -> dict[str, Any]:
    """Fetch the governed health endpoint."""

    return fetch_endpoint(host, port, timeout_s, "/health")


def valid_health(observation: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if observation.get("status_code") != 200:
        reasons.append("http_status_not_200")
    if not str(observation.get("content_type") or "").lower().startswith("application/json"):
        reasons.append("content_type_not_application_json")
    if observation.get("json_parse_error") is not None:
        reasons.append("invalid_json")
    if observation.get("json_status") not in KNOWN_HEALTH_STATES:
        reasons.append("unknown_health_status")
    return not reasons, reasons


def open_loop(
    host: str,
    port: int,
    start_ns: int,
    request_count: int,
    rate_rps: float,
    workers: int,
    timeout_s: float,
    phase: str,
    endpoint: str = "/health",
) -> tuple[list[dict[str, Any]], float]:
    interval_ns = int(1_000_000_000 / rate_rps)
    rows: list[dict[str, Any]] = []
    futures: list[tuple[dict[str, Any], concurrent.futures.Future[dict[str, Any]]]] = []

    def operation(target_ns: int) -> dict[str, Any]:
        worker_ns = time.monotonic_ns()
        result = fetch_endpoint(host, port, timeout_s, endpoint)
        completed_ns = time.monotonic_ns()
        result.update(
            {
                "worker_start_monotonic_ns": worker_ns,
                "worker_start_lag_ms": (worker_ns - target_ns) / 1_000_000,
                "completed_monotonic_ns": completed_ns,
                "end_to_end_latency_ms": (completed_ns - target_ns) / 1_000_000,
            }
        )
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for index in range(request_count):
            target_ns = start_ns + index * interval_ns
            while True:
                remaining_ns = target_ns - time.monotonic_ns()
                if remaining_ns <= 0:
                    break
                time.sleep(min(remaining_ns / 1_000_000_000, 0.002))
            submitted_ns = time.monotonic_ns()
            row = {
                "index": index,
                "phase": phase,
                "endpoint": endpoint,
                "target_monotonic_ns": target_ns,
                "submit_monotonic_ns": submitted_ns,
                "submit_lag_ms": (submitted_ns - target_ns) / 1_000_000,
            }
            rows.append(row)
            futures.append((row, pool.submit(operation, target_ns)))
        for row, future in futures:
            try:
                row.update(future.result(timeout=max(10.0, timeout_s * 2)))
            except Exception as exc:  # future state must remain visible
                row.update(
                    {
                        "completed": False,
                        "status_code": 0,
                        "service_latency_ms": None,
                        "error": f"future:{type(exc).__name__}:{exc}",
                    }
                )
    elapsed_s = (time.monotonic_ns() - start_ns) / 1_000_000_000
    return rows, elapsed_s


def summarize(rows: Sequence[dict[str, Any]], elapsed_s: float) -> dict[str, Any]:
    completed = [row for row in rows if row.get("completed") is True]
    successes = [row for row in completed if row.get("status_code") == 200]
    service = [
        float(row["service_latency_ms"])
        for row in completed
        if row.get("service_latency_ms") is not None
    ]
    end_to_end = [
        float(row["end_to_end_latency_ms"])
        for row in completed
        if row.get("end_to_end_latency_ms") is not None
    ]
    submit_lag = [float(row["submit_lag_ms"]) for row in rows]
    worker_lag = [
        float(row["worker_start_lag_ms"])
        for row in completed
        if row.get("worker_start_lag_ms") is not None
    ]
    return {
        "planned_requests": len(rows),
        "completed_observations": len(completed),
        "successful_requests": len(successes),
        "request_errors": len(rows) - len(successes),
        "status_histogram": {
            str(code): sum(1 for row in completed if row.get("status_code") == code)
            for code in sorted({row.get("status_code") for row in completed}, key=str)
        },
        "error_histogram": {
            str(error): sum(1 for row in rows if row.get("error") == error)
            for error in sorted({row.get("error") for row in rows if row.get("error")})
        },
        "elapsed_s": elapsed_s,
        "achieved_throughput_rps": len(completed) / elapsed_s if elapsed_s else None,
        "service_latency_ms": {
            "p50": percentile(service, 0.50),
            "p95": percentile(service, 0.95),
            "p99": percentile(service, 0.99),
            "maximum": max(service) if service else None,
        },
        "target_to_completion_latency_ms": {
            "p50": percentile(end_to_end, 0.50),
            "p95": percentile(end_to_end, 0.95),
            "p99": percentile(end_to_end, 0.99),
            "maximum": max(end_to_end) if end_to_end else None,
        },
        "load_generator": {
            "submit_lag_ms_p99": percentile(submit_lag, 0.99),
            "submit_lag_ms_maximum": max(submit_lag) if submit_lag else None,
            "worker_start_lag_ms_p99": percentile(worker_lag, 0.99),
            "worker_start_lag_ms_maximum": max(worker_lag) if worker_lag else None,
        },
    }


def write_vector(directory: Path, name: str, rows: Sequence[dict[str, Any]]) -> None:
    jsonl = directory / f"{name}.jsonl"
    csv_path = directory / f"{name}.csv"
    with jsonl.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    fields = sorted({key for row in rows for key in row})
    if not fields:
        raise ContractError(f"{name} vector is empty")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def csv_scalar(value: Any) -> str:
    return "" if value is None else str(value)


def validate_vector(
    directory: Path,
    name: str,
    rows: Sequence[dict[str, Any]],
    expected_count: int,
    required_fields: set[str],
) -> list[str]:
    reasons: list[str] = []
    if len(rows) != expected_count:
        reasons.append(f"{name}:sample_count:{len(rows)}!={expected_count}")
    expected_indices = list(range(expected_count))
    indices = [row.get("index") for row in rows]
    if indices != expected_indices or len(set(indices)) != len(indices):
        reasons.append(f"{name}:non_contiguous_or_duplicate_indices")
    for index, row in enumerate(rows):
        missing = sorted(required_fields - set(row))
        if missing:
            reasons.append(f"{name}:sample:{index}:missing:{','.join(missing)}")
    jsonl_path = directory / f"{name}.jsonl"
    csv_path = directory / f"{name}.csv"
    try:
        jsonl_rows = [
            json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        ]
        with csv_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            csv_rows = list(reader)
            csv_fields = reader.fieldnames
        if len(jsonl_rows) != expected_count or len(csv_rows) != expected_count:
            reasons.append(f"{name}:serialized_sample_count")
        if [row.get("index") for row in jsonl_rows] != expected_indices:
            reasons.append(f"{name}:jsonl_indices")
        if [row.get("index") for row in csv_rows] != [str(i) for i in expected_indices]:
            reasons.append(f"{name}:csv_indices")
        fields = sorted({key for row in rows for key in row})
        if csv_fields != fields:
            reasons.append(f"{name}:csv_fields")
        for index, row in enumerate(rows):
            if index >= len(jsonl_rows) or jsonl_rows[index] != row:
                reasons.append(f"{name}:jsonl_value_mismatch:{index}")
            if index >= len(csv_rows):
                continue
            for field in fields:
                if csv_rows[index].get(field) != csv_scalar(row.get(field)):
                    reasons.append(f"{name}:csv_value_mismatch:{index}:{field}")
    except (OSError, csv.Error, json.JSONDecodeError) as exc:
        reasons.append(f"{name}:serialized_vector_error:{type(exc).__name__}")
    return reasons


REQUEST_FIELDS = {
    "index",
    "phase",
    "endpoint",
    "target_monotonic_ns",
    "submit_monotonic_ns",
    "submit_lag_ms",
    "completed",
    "status_code",
    "service_latency_ms",
    "error",
}


def validate_readiness_evidence(readiness: Any) -> list[str]:
    """Validate that V11 used active responses rather than an elapsed sleep."""

    reasons: list[str] = []
    if not isinstance(readiness, dict):
        return ["v11:readiness_evidence_missing"]
    observations = readiness.get("observations")
    if not isinstance(observations, list) or len(observations) < READINESS_RESPONSES:
        return ["v11:readiness_active_responses_missing"]
    if [row.get("index") for row in observations if isinstance(row, dict)] != list(
        range(len(observations))
    ):
        reasons.append("v11:readiness_indices_invalid")
    for index, row in enumerate(observations):
        if not isinstance(row, dict):
            reasons.append(f"v11:readiness_observation_not_object:{index}")
            continue
        required = {
            "active_probe",
            "request_started_monotonic_ns",
            "response_completed_monotonic_ns",
            "valid",
            "status_code",
            "content_type",
            "json_status",
            "json_parse_error",
        }
        if not required.issubset(row) or row.get("active_probe") is not True:
            reasons.append(f"v11:readiness_active_probe_missing:{index}")
            continue
        if not isinstance(row["request_started_monotonic_ns"], int) or not isinstance(
            row["response_completed_monotonic_ns"], int
        ):
            reasons.append(f"v11:readiness_timestamp_invalid:{index}")
        elif row["response_completed_monotonic_ns"] < row["request_started_monotonic_ns"]:
            reasons.append(f"v11:readiness_timestamp_order:{index}")
    tail = observations[-READINESS_RESPONSES:]
    if not all(
        isinstance(row, dict) and row.get("valid") is True and valid_health(row)[0] for row in tail
    ):
        reasons.append("v11:readiness_three_consecutive_valid_missing")
    else:
        completed = [int(row["response_completed_monotonic_ns"]) for row in tail]
        minimum_ns = int(READINESS_INTERVAL_S * 1_000_000_000)
        if any(later - earlier < minimum_ns for earlier, later in zip(completed, completed[1:])):
            reasons.append("v11:readiness_interval_too_short")
        if readiness.get("barrier_completed_monotonic_ns") != completed[-1]:
            reasons.append("v11:readiness_barrier_timestamp_mismatch")
    return reasons


def verify_vector_files(directory: Path, name: str) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in (directory / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    reasons = validate_vector(directory, name, rows, len(rows), set())
    if reasons:
        raise ContractError(f"vector verification failed: {reasons}")
    return rows


def write_manifest(directory: Path, excluded: set[str] | None = None) -> dict[str, str]:
    exclusions = {"SHA256SUMS"} | (excluded or set())
    files = [
        path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name not in exclusions
    ]
    hashes = {path.name: sha256_file(path) for path in files}
    (directory / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="utf-8",
    )
    return hashes


def verify_manifest(directory: Path) -> list[str]:
    manifest = directory / "SHA256SUMS"
    reasons: list[str] = []
    try:
        entries: dict[str, str] = {}
        for line in manifest.read_text(encoding="utf-8").splitlines():
            digest, name = line.split("  ", 1)
            if name in entries or not SHA256_RE.fullmatch(digest):
                reasons.append("manifest_duplicate_or_invalid_entry")
            entries[name] = digest
        actual_names = {
            path.name
            for path in directory.iterdir()
            if path.is_file() and path.name != "SHA256SUMS"
        }
        if actual_names != set(entries):
            reasons.append("manifest_inventory_mismatch")
        for name, digest in entries.items():
            path = directory / name
            if not path.is_file() or sha256_file(path) != digest:
                reasons.append(f"manifest_hash_mismatch:{name}")
    except (OSError, ValueError) as exc:
        reasons.append(f"manifest_unreadable:{type(exc).__name__}")
    return reasons


def free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def stop_child(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def spawn_server(
    target_python: Path,
    host: str,
    port: int,
    directory: Path,
    env: dict[str, str],
) -> tuple[subprocess.Popen[str], int, Any, Any]:
    stdout_handle = (directory / "server.stdout.txt").open("w", encoding="utf-8")
    stderr_handle = (directory / "server.stderr.txt").open("w", encoding="utf-8")
    command = [
        str(target_python),
        str(Path(__file__).resolve()),
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]
    launched_ns = time.monotonic_ns()
    process = subprocess.Popen(
        command,
        env=env,
        cwd=directory,
        stdout=stdout_handle,
        stderr=stderr_handle,
        text=True,
        start_new_session=True,
    )
    return process, launched_ns, stdout_handle, stderr_handle


def wait_for_listener(
    host: str, port: int, process: subprocess.Popen[str], timeout_s: float
) -> tuple[int, list[dict[str, Any]]]:
    started_ns = time.monotonic_ns()
    observations: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe_ns = time.monotonic_ns()
        accepted = False
        error: str | None = None
        if process.poll() is not None:
            error = f"server_exited:{process.returncode}"
        else:
            try:
                with socket.create_connection((host, port), timeout=0.05):
                    accepted = True
            except OSError as exc:
                error = f"{type(exc).__name__}:{exc}"
        observations.append(
            {
                "index": len(observations),
                "phase": "o3a_listener",
                "probe_monotonic_ns": probe_ns,
                "elapsed_ms": (probe_ns - started_ns) / 1_000_000,
                "accepted": accepted,
                "error": error,
            }
        )
        if accepted:
            return time.monotonic_ns(), observations
        if process.poll() is not None:
            raise ContractError(error or "server exited")
        time.sleep(0.002)
    raise ObservationTimeout("listener admission was not observed", observations)


def wait_for_first_valid_health(
    host: str,
    port: int,
    process: subprocess.Popen[str],
    timeout_s: float,
) -> tuple[int, list[dict[str, Any]]]:
    """Return the completion time of the first valid active health response."""

    deadline = time.monotonic() + timeout_s
    observations: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ContractError(f"server exited before first health response: {process.returncode}")
        request_started_ns = time.monotonic_ns()
        row = fetch_health(host, port, min(1.0, max(0.001, deadline - time.monotonic())))
        response_completed_ns = time.monotonic_ns()
        valid, invalid = valid_health(row)
        row.update(
            {
                "index": len(observations),
                "phase": "o3b_health",
                "active_probe": True,
                "request_started_monotonic_ns": request_started_ns,
                "response_completed_monotonic_ns": response_completed_ns,
                "valid": valid,
                "invalid_reasons": invalid,
            }
        )
        observations.append(row)
        if valid:
            return response_completed_ns, observations
        time.sleep(0.002)
    raise ObservationTimeout("first valid health response was not observed", observations)


def readiness_barrier(
    host: str,
    port: int,
    process: subprocess.Popen[str],
    timeout_s: float,
) -> tuple[int, list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_s
    observations: list[dict[str, Any]] = []
    consecutive = 0
    previous_valid_completed_ns: int | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ContractError(f"server exited during readiness: {process.returncode}")
        if previous_valid_completed_ns is not None:
            remaining = READINESS_INTERVAL_S - (
                (time.monotonic_ns() - previous_valid_completed_ns) / 1_000_000_000
            )
            if remaining > 0:
                time.sleep(remaining)
        request_started_ns = time.monotonic_ns()
        row = fetch_health(host, port, min(1.0, timeout_s))
        response_completed_ns = time.monotonic_ns()
        valid, invalid = valid_health(row)
        row.update(
            {
                "index": len(observations),
                "phase": "readiness",
                "active_probe": True,
                "request_started_monotonic_ns": request_started_ns,
                "response_completed_monotonic_ns": response_completed_ns,
                "valid": valid,
                "invalid_reasons": invalid,
            }
        )
        observations.append(row)
        if valid:
            consecutive += 1
            previous_valid_completed_ns = response_completed_ns
            if consecutive == READINESS_RESPONSES:
                return response_completed_ns, observations
        else:
            consecutive = 0
            previous_valid_completed_ns = None
    raise ObservationTimeout("valid health readiness barrier was not observed", observations)


def o3_success_results(
    launched_ns: int, listener_ns: int, first_health_completed_ns: int
) -> dict[str, Any]:
    """Build independent O3a/O3b measurements from response-completion time."""

    return {
        "O3a": {
            "listener_observed": True,
            "launch_to_listener_ms": (listener_ns - launched_ns) / 1_000_000,
        },
        "O3b": {
            "valid_health_observed": True,
            "listener_to_first_valid_health_ms": (first_health_completed_ns - listener_ns)
            / 1_000_000,
            "launch_to_first_valid_health_ms": (first_health_completed_ns - launched_ns)
            / 1_000_000,
            "first_valid_health_response_completed_monotonic_ns": first_health_completed_ns,
        },
    }


def dependency_receipt(target_python: Path, *, neutral_cwd: Path) -> dict[str, Any]:
    snippet = (
        "import importlib.metadata as m,json,platform;"
        "print(json.dumps({'python':platform.python_version(),"
        "'implementation':platform.python_implementation(),"
        "'packages':sorted((d.metadata.get('Name',''),d.version) for d in m.distributions())}))"
    )
    raw = command_text([str(target_python), "-c", snippet], timeout=30, cwd=neutral_cwd)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError(f"dependency inventory unavailable: {raw}") from exc
    packages = [item for item in result["packages"] if str(item[0]).lower() != "tokenpak"]
    result["environment_fingerprint"] = canonical_sha256(
        {
            "python": result["python"],
            "implementation": result["implementation"],
            "packages": packages,
        }
    )
    return result


def _archive_link_target(member_name: str, link_name: str, *, hardlink: bool) -> str:
    """Return a normalized repository-contained target for a tar link."""

    link = PurePosixPath(link_name)
    if link.is_absolute():
        raise ContractError("absolute archive link target")
    parts = [] if hardlink else list(PurePosixPath(member_name).parent.parts)
    for part in link.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ContractError("archive link target escapes repository")
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        raise ContractError("archive link target resolves to repository root")
    return PurePosixPath(*parts).as_posix()


def _git_archive_payloads(
    source_tar: bytes, requested: set[str]
) -> tuple[dict[str, bytes], dict[str, str], list[str]]:
    """Resolve requested Git-archive members without following unsafe links."""

    try:
        with tarfile.open(fileobj=io.BytesIO(source_tar), mode="r:") as archive:
            members = {member.name: member for member in archive.getmembers()}
            regular_payloads: dict[str, bytes] = {}
            for name, member in members.items():
                if not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is not None:
                    regular_payloads[name] = extracted.read()

            resolved_payloads: dict[str, bytes] = {}
            resolution_errors: dict[str, str] = {}
            resolved_links: list[str] = []

            def resolve(name: str, chain: tuple[str, ...] = ()) -> bytes:
                if name in regular_payloads:
                    return regular_payloads[name]
                if name in chain:
                    cycle = " -> ".join((*chain, name))
                    raise ContractError(f"archive link cycle: {cycle}")
                member = members.get(name)
                if member is None:
                    raise ContractError("archive member is missing")
                if not (member.issym() or member.islnk()):
                    raise ContractError(f"unsupported archive member type: {member.type!r}")
                target = _archive_link_target(
                    name,
                    member.linkname,
                    hardlink=member.islnk(),
                )
                return resolve(target, (*chain, name))

            for name in sorted(requested):
                if name not in members:
                    continue
                try:
                    resolved_payloads[name] = resolve(name)
                    if members[name].issym() or members[name].islnk():
                        resolved_links.append(name)
                except ContractError as exc:
                    resolution_errors[name] = str(exc)
            return resolved_payloads, resolution_errors, resolved_links
    except (tarfile.TarError, OSError) as exc:
        raise ContractError(f"declared source archive is unreadable: {exc}") from exc


def wheel_source_correlation(artifact: Path, repo: Path, commit: str) -> dict[str, Any]:
    """Correlate every wheel payload byte with the declared source commit.

    Wheel metadata is build output and is bound by the artifact hash.  Every
    non-metadata payload member, however, must exist byte-for-byte in the Git
    archive for the declared commit.  This makes an identically versioned wheel
    built from another revision fail closed without relying on mutable version
    strings or nonstandard wheel metadata.
    """

    try:
        source_tar = subprocess.check_output(
            ["git", "-C", str(repo), "archive", "--format=tar", commit],
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError(f"declared source archive unavailable: {exc}") from exc
    try:
        wheel = zipfile.ZipFile(artifact)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ContractError("health-release subject must be a readable wheel") from exc
    wheel_members: dict[str, bytes] = {}
    unsafe_members: list[str] = []
    with wheel:
        for name in wheel.namelist():
            parts = Path(name).parts
            if name.startswith("/") or ".." in parts:
                unsafe_members.append(name)
                continue
            if name.endswith("/") or ".dist-info/" in name:
                continue
            wheel_members[name] = wheel.read(name)

    source_members, source_resolution_errors, resolved_source_links = _git_archive_payloads(
        source_tar, set(wheel_members)
    )

    missing_from_source = sorted(
        set(wheel_members) - set(source_members) - set(source_resolution_errors)
    )
    payload_mismatches = sorted(
        name
        for name in set(wheel_members) & set(source_members)
        if wheel_members[name] != source_members[name]
    )
    wheel_hashes = {
        name: hashlib.sha256(payload).hexdigest() for name, payload in sorted(wheel_members.items())
    }
    source_hashes = {
        name: hashlib.sha256(source_members[name]).hexdigest()
        for name in sorted(wheel_members)
        if name in source_members
    }
    result = {
        "strategy": "wheel_payload_equals_declared_git_archive/v2",
        "payload_members": len(wheel_members),
        "compared_members": len(set(wheel_members) & set(source_members)),
        "unsafe_members": unsafe_members,
        "missing_from_source": missing_from_source,
        "source_resolution_errors": source_resolution_errors,
        "resolved_source_links": resolved_source_links,
        "payload_mismatches": payload_mismatches,
        "wheel_payload_manifest_sha256": canonical_sha256(wheel_hashes),
        "source_payload_manifest_sha256": canonical_sha256(source_hashes),
    }
    result["verified"] = bool(
        wheel_members
        and not unsafe_members
        and not missing_from_source
        and not source_resolution_errors
        and not payload_mismatches
        and result["wheel_payload_manifest_sha256"] == result["source_payload_manifest_sha256"]
    )
    return result


def verify_artifact_provenance(
    path: Path,
    artifact_sha256: str,
    commit: str,
    tree: str,
    wheel_payload_manifest_sha256: str,
) -> dict[str, Any]:
    """Verify an immutable build receipt binds artifact, commit, and tree."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"artifact provenance unreadable: {exc}") from exc
    required = {
        "schema",
        "artifact_sha256",
        "source_commit",
        "source_tree",
        "wheel_payload_manifest_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ContractError(f"artifact provenance fields must be exactly {sorted(required)}")
    malformed = [
        field
        for field in ("artifact_sha256", "wheel_payload_manifest_sha256")
        if not SHA256_RE.fullmatch(str(value[field]))
    ]
    malformed.extend(
        field
        for field in ("source_commit", "source_tree")
        if not GIT_OBJECT_RE.fullmatch(str(value[field]))
    )
    expected = {
        "schema": PROVENANCE_SCHEMA,
        "artifact_sha256": artifact_sha256,
        "source_commit": commit,
        "source_tree": tree,
        "wheel_payload_manifest_sha256": wheel_payload_manifest_sha256,
    }
    mismatches = [field for field in required if value.get(field) != expected[field]]
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "fields": value,
        "malformed_fields": malformed,
        "mismatches": sorted(mismatches),
        "verified": not malformed and not mismatches,
    }


def verify_subject(args: argparse.Namespace, *, neutral_cwd: Path) -> dict[str, Any]:
    commit_result = subprocess.run(
        ["git", "-C", str(args.repo), "cat-file", "-e", f"{args.commit}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    verified_tree = command_text(
        ["git", "-C", str(args.repo), "rev-parse", f"{args.commit}^{{tree}}"]
    )
    status = command_text(["git", "-C", str(args.repo), "status", "--porcelain"])
    distribution_raw = command_text(
        [
            str(args.target_python),
            "-c",
            (
                "import importlib.metadata as m,json;d=m.distribution('tokenpak');"
                "print(json.dumps({'version':d.version,'root':str(d.locate_file(''))}))"
            ),
        ],
        cwd=neutral_cwd,
    )
    try:
        distribution = json.loads(distribution_raw)
    except json.JSONDecodeError as exc:
        raise ContractError(f"installed distribution unavailable: {distribution_raw}") from exc
    installed_root = Path(distribution["root"])
    payload = {
        "members_total": 0,
        "members_compared": 0,
        "excluded_members": [],
        "missing_members": [],
        "payload_mismatches": [],
    }
    wheel_version: str | None = None
    try:
        archive = zipfile.ZipFile(args.artifact)
    except zipfile.BadZipFile as exc:
        raise ContractError("health-release subject must be a wheel") from exc
    with archive:
        names = archive.namelist()
        payload["members_total"] = len(names)
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ContractError("wheel must contain exactly one METADATA file")
        for line in archive.read(metadata_names[0]).decode(errors="replace").splitlines():
            if line.startswith("Version: "):
                wheel_version = line.removeprefix("Version: ").strip()
                break
        for name in names:
            if name.endswith("/") or name.endswith(".dist-info/RECORD"):
                payload["excluded_members"].append(name)
                continue
            installed = installed_root / name
            if not installed.is_file():
                payload["missing_members"].append(name)
                continue
            payload["members_compared"] += 1
            if (
                hashlib.sha256(installed.read_bytes()).digest()
                != hashlib.sha256(archive.read(name)).digest()
            ):
                payload["payload_mismatches"].append(name)
    source_correlation = wheel_source_correlation(args.artifact, args.repo, args.commit)
    provenance = verify_artifact_provenance(
        args.artifact_provenance,
        sha256_file(args.artifact),
        args.commit,
        args.tree,
        source_correlation["wheel_payload_manifest_sha256"],
    )
    result = {
        "repository": str(args.repo.resolve()),
        "commit": args.commit,
        "commit_exists": commit_result.returncode == 0,
        "declared_tree": args.tree,
        "verified_tree": verified_tree,
        "tree_matches": verified_tree == args.tree,
        "repository_clean": status == "",
        "repository_status": status.splitlines(),
        "artifact": str(args.artifact.resolve()),
        "artifact_sha256": sha256_file(args.artifact),
        "wheel_version": wheel_version,
        "installed_distribution": distribution,
        "installed_version_matches_wheel": distribution.get("version") == wheel_version,
        "installed_payload": payload,
        "source_correlation": source_correlation,
        "artifact_provenance": provenance,
    }
    result["verified"] = bool(
        result["commit_exists"]
        and result["tree_matches"]
        and result["repository_clean"]
        and result["installed_version_matches_wheel"]
        and not payload["missing_members"]
        and not payload["payload_mismatches"]
        and result["source_correlation"]["verified"]
        and result["artifact_provenance"]["verified"]
    )
    return result


def steal_delta(before: dict[str, Any], after: dict[str, Any]) -> int | None:
    before_cpu = before.get("cpu", {}).get("cpu")
    after_cpu = after.get("cpu", {}).get("cpu")
    if not before_cpu or not after_cpu or len(before_cpu) < 8 or len(after_cpu) < 8:
        return None
    return int(after_cpu[7]) - int(before_cpu[7])


def counter_delta(
    before: dict[str, Any], after: dict[str, Any], section: str, keys: Sequence[str]
) -> int | None:
    before_values = before.get(section, {})
    after_values = after.get(section, {})
    if any(key not in before_values or key not in after_values for key in keys):
        return None
    return sum(int(after_values[key]) - int(before_values[key]) for key in keys)


def throttle_delta(before: dict[str, Any], after: dict[str, Any]) -> int | None:
    before_values = before.get("cpu_throttle", {})
    after_values = after.get("cpu_throttle", {})
    if not before_values or set(before_values) != set(after_values):
        return None
    return sum(int(after_values[key]) - int(before_values[key]) for key in before_values)


def reference_qualification(
    profile: dict[str, Any],
    args: argparse.Namespace,
    before: dict[str, Any],
    after: dict[str, Any],
    telemetry_rows: Sequence[dict[str, Any]],
    dependency: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    affinity_before = before.get("affinity") or []
    affinity_after = after.get("affinity") or []
    memory_bytes = before.get("meminfo_kib", {}).get("MemTotal", 0) * 1024
    python_minor = ".".join(platform.python_version().split(".")[:2])
    subject_minor = ".".join(str(dependency.get("python", "")).split(".")[:2])
    if platform.system() != profile["os"]:
        reasons.append("operating_system_mismatch")
    if platform.machine().lower() not in profile["machines"]:
        reasons.append("machine_architecture_mismatch")
    if python_minor != profile["python_major_minor"] or subject_minor != python_minor:
        reasons.append("python_version_mismatch")
    if len(affinity_before) < int(profile["minimum_affinity_cpus"]):
        reasons.append("insufficient_cpu_affinity")
    if affinity_before != affinity_after:
        reasons.append("cpu_affinity_changed")
    if memory_bytes < int(profile["minimum_memory_bytes"]):
        reasons.append("insufficient_memory")
    if args.runtime_image_digest is None:
        reasons.append("runtime_image_digest_missing")
    if len(telemetry_rows) < int(profile["minimum_telemetry_samples"]):
        reasons.append("telemetry_missing")
    observed_steal = steal_delta(before, after)
    if observed_steal is None:
        reasons.append("cpu_steal_unavailable")
    elif observed_steal > int(profile["maximum_steal_delta_jiffies"]):
        reasons.append("cpu_steal_observed")
    swap_delta = counter_delta(before, after, "vmstat", ("pswpin", "pswpout"))
    if swap_delta is None:
        reasons.append("swap_activity_unavailable")
    elif swap_delta > int(profile["maximum_swap_io_delta_pages"]):
        reasons.append("swap_activity_observed")
    observed_throttle = throttle_delta(before, after)
    if observed_throttle is None:
        reasons.append("throttling_counters_unavailable")
    elif observed_throttle > int(profile["maximum_throttle_delta"]):
        reasons.append("cpu_throttling_observed")
    return {
        "profile_id": PROFILE_ID,
        "qualified": not reasons,
        "failure_reasons": reasons,
        "observations": {
            "controller_python": platform.python_version(),
            "subject_python": dependency.get("python"),
            "machine": platform.machine(),
            "affinity_before": affinity_before,
            "affinity_after": affinity_after,
            "memory_bytes": memory_bytes,
            "steal_delta_jiffies": observed_steal,
            "swap_io_delta_pages": swap_delta,
            "throttle_delta": observed_throttle,
            "telemetry_samples": len(telemetry_rows),
            "runtime_image_digest": args.runtime_image_digest,
        },
    }


def telemetry_rows(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"telemetry unreadable: {exc}") from exc
    required = {"utc", "monotonic_ns", "cpu", "meminfo_kib", "vmstat", "diskstats", "process"}
    if any(not required.issubset(row) for row in rows):
        raise ContractError("telemetry sample is incomplete")
    return rows


def initial_receipt(
    args: argparse.Namespace,
    governed: dict[str, Any],
    env: dict[str, str],
    denied_environment: list[str],
    removed_secrets: list[str],
    *,
    neutral_cwd: Path,
) -> dict[str, Any]:
    dependencies = dependency_receipt(args.target_python, neutral_cwd=neutral_cwd)
    return {
        "schema": SCHEMA,
        "suite_version": SUITE_VERSION,
        "mode": args.mode,
        "created_at": utcnow(),
        "exact_command": shlex.join([sys.executable, *sys.argv]),
        "runner": {
            "path": str(Path(__file__).resolve()),
            "sha256": governed["actual"]["runner_sha256"],
        },
        "governed_inputs": governed["actual"],
        "reference_profile": {
            "path": str(args.reference_spec.resolve()),
            "profile_id": governed["reference_profile"]["profile_id"],
            "runtime_image_digest": args.runtime_image_digest,
        },
        "subject": {
            "label": args.label,
            "dependencies": dependencies,
        },
        "host": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "kernel": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "cpu_model": command_text(["lscpu"]),
            "affinity": (
                sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
            ),
            "virtualization": command_text(["systemd-detect-virt"]),
            "cgroup": safe_text(Path("/proc/self/cgroup")),
            "power_mode": (
                command_text(["powerprofilesctl", "get"])
                if shutil.which("powerprofilesctl")
                else "unavailable"
            ),
            "competing_processes": competing_processes(),
        },
        "environment": sanitized_environment(env),
        "parent_environment_denied": denied_environment,
        "provider_credentials_removed": removed_secrets,
        "network_scope": {
            "target": "http://127.0.0.1:<ephemeral>/health",
            "provider_requests_permitted": False,
        },
        "configuration": {
            "o3_observation_window_s": O3_OBSERVATION_WINDOW_S,
            "o6a": {"requests": O6A_REQUESTS, "rate_rps": O6A_RPS, "workers": V11_WORKERS},
            "o6b": {"requests": O6B_REQUESTS, "rate_rps": O6B_RPS, "workers": V11_WORKERS},
            "v11_readiness": {
                "consecutive_valid_responses": READINESS_RESPONSES,
                "minimum_interval_ms": READINESS_INTERVAL_S * 1000,
            },
            "v11": governed["reference_profile"]["v11"],
        },
    }


def evaluate_run(
    args: argparse.Namespace,
    receipt: dict[str, Any],
    directory: Path,
    vectors: dict[str, list[dict[str, Any]]],
    phase_before: dict[str, Any],
    phase_after: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    expected_vector_names = set(MODE_VECTOR_NAMES[args.mode])
    if set(vectors) != expected_vector_names:
        reasons.append(
            "vector_set_mismatch:"
            f"expected={','.join(sorted(expected_vector_names))}:"
            f"actual={','.join(sorted(vectors))}"
        )
    for name, rows in vectors.items():
        if name not in expected_vector_names:
            continue
        expected = {
            "o3a_listener": len(rows),
            "o3b_health": len(rows),
            "o6a_startup": O6A_REQUESTS,
            "o6b_listener": O6B_REQUESTS,
            "warmup": V11_WARMUP_REQUESTS,
            "v11": V11_REQUESTS,
        }[name]
        required = (
            REQUEST_FIELDS
            if name in {"o6a_startup", "o6b_listener", "warmup", "v11"}
            else {"index", "phase"}
        )
        reasons.extend(validate_vector(directory, name, rows, expected, required))
        for index, row in enumerate(rows):
            if row.get("phase") != name:
                reasons.append(f"{name}:sample:{index}:phase_mismatch")
    try:
        telemetry = telemetry_rows(directory / "telemetry.jsonl")
    except ContractError as exc:
        telemetry = []
        reasons.append(str(exc))
    if len(telemetry) < int(profile["minimum_telemetry_samples"]):
        reasons.append("telemetry:insufficient_samples")
    qualification = reference_qualification(
        profile,
        args,
        phase_before,
        phase_after,
        telemetry,
        receipt["subject"]["dependencies"],
    )
    verdicts = receipt.setdefault("verdicts", {})
    if args.mode == "o3":
        verdicts["O3a"] = {"posture": "tracked", "vector": "o3a_listener"}
        verdicts["O3b"] = {"posture": "tracked", "vector": "o3b_health"}
    elif args.mode == "o6a":
        verdicts["O6a"] = {"posture": "tracked", "vector": "o6a_startup"}
    elif args.mode == "o6b":
        verdicts["O6b"] = {"posture": "tracked", "vector": "o6b_listener"}
    elif args.mode == "v11":
        result = receipt["results"]["V11"]
        generator = result["load_generator"]
        reasons.extend(validate_readiness_evidence(receipt["results"].get("readiness")))
        for row in vectors["v11"]:
            valid, invalid = valid_health(row)
            if not valid or row.get("error") is not None:
                reasons.append(f"v11:wire_invalid:{row.get('index')}:{','.join(invalid)}")
        if (
            generator["submit_lag_ms_p99"] is None
            or generator["submit_lag_ms_p99"] > V11_SUBMIT_LAG_P99_CEILING_MS
        ):
            reasons.append("v11:generator_submit_lag_p99")
        if (
            generator["submit_lag_ms_maximum"] is None
            or generator["submit_lag_ms_maximum"] > V11_SUBMIT_LAG_MAXIMUM_CEILING_MS
        ):
            reasons.append("v11:generator_submit_lag_maximum")
        phase_listener = receipt.get("listener_counters", {}).get("measured_phase_delta")
        required_listener_keys = {"TcpExt.ListenOverflows", "TcpExt.ListenDrops"}
        if not isinstance(phase_listener, dict) or not required_listener_keys.issubset(
            phase_listener
        ):
            reasons.append("v11:listener_counters_missing")
            phase_listener = {}
        threshold_checks = {
            "p50": result["service_latency_ms"]["p50"] is not None
            and result["service_latency_ms"]["p50"] < V11_P50_CEILING_MS,
            "p99": result["service_latency_ms"]["p99"] is not None
            and result["service_latency_ms"]["p99"] < V11_P99_CEILING_MS,
            "errors": result["request_errors"] == 0,
            "throughput": result["achieved_throughput_rps"] is not None
            and result["achieved_throughput_rps"] >= V11_MINIMUM_THROUGHPUT_RPS,
            "listener_overflows": phase_listener.get("TcpExt.ListenOverflows") == 0,
            "listener_drops": phase_listener.get("TcpExt.ListenDrops") == 0,
        }
        if reasons:
            absolute = "invalid"
        elif not qualification["qualified"]:
            absolute = "informational_non_reference"
        elif all(threshold_checks.values()):
            absolute = "pass"
        else:
            absolute = "fail"
        verdicts["V11"] = {
            "posture": "release_blocking_on_reference_profile",
            "warmup_vector": "warmup",
            "measured_vector": "v11",
            "thresholds": {
                "p50_ceiling_ms": V11_P50_CEILING_MS,
                "p99_ceiling_ms": V11_P99_CEILING_MS,
                "minimum_throughput_rps": V11_MINIMUM_THROUGHPUT_RPS,
                "maximum_request_errors": 0,
                "maximum_listener_drops": 0,
                "maximum_listener_overflows": 0,
            },
            "checks": threshold_checks,
            "absolute_verdict": absolute,
        }
    return {
        "valid": not reasons,
        "classification": "valid_measurement" if not reasons else "invalid_contract_measurement",
        "reference_qualification": qualification,
        "failure_reasons": reasons,
    }


def run_one(args: argparse.Namespace) -> int:
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "tokenpak-home").mkdir()
    receipt: dict[str, Any] = {"schema": SCHEMA, "status": "initializing", "mode": args.mode}
    vectors: dict[str, list[dict[str, Any]]] = {}
    process: subprocess.Popen[str] | None = None
    sampler: Sampler | None = None
    stdout_handle = stderr_handle = None
    phase_before = sample_system(None)
    phase_after = phase_before
    exit_code = 1
    try:
        governed = verify_governed_inputs(args)
        env, denied_environment, removed_secrets = subject_environment(directory)
        neutral_cwd = directory / "subject-home"
        receipt = initial_receipt(
            args,
            governed,
            env,
            denied_environment,
            removed_secrets,
            neutral_cwd=neutral_cwd,
        )
        subject = verify_subject(args, neutral_cwd=neutral_cwd)
        receipt["subject"].update(subject)
        if not subject["verified"]:
            raise ContractError("subject source/artifact/interpreter identity did not verify")
        host = "127.0.0.1"
        port = free_port(host)
        receipt["configuration"]["host"] = host
        receipt["configuration"]["port"] = port
        process, launched_ns, stdout_handle, stderr_handle = spawn_server(
            args.target_python, host, port, directory, env
        )
        sampler = Sampler(directory / "telemetry.jsonl", process.pid)
        sampler.start()
        receipt["server_pid"] = process.pid
        receipt["launch_monotonic_ns"] = launched_ns
        host_listener_before = proc_netstat()
        phase_before = sample_system(None)

        if args.mode == "o3":
            try:
                listener_ns, listener_rows = wait_for_listener(
                    host, port, process, O3_OBSERVATION_WINDOW_S
                )
            except ObservationTimeout as exc:
                vectors["o3a_listener"] = exc.observations or [
                    {
                        "index": 0,
                        "phase": "o3a_listener",
                        "accepted": False,
                        "right_censored_ms": 30000.0,
                    }
                ]
                vectors["o3b_health"] = [
                    {
                        "index": 0,
                        "phase": "o3b_health",
                        "valid": False,
                        "right_censored_ms": 30000.0,
                    }
                ]
                receipt["results"] = {
                    "O3a": {
                        "listener_observed": False,
                        "right_censored_ms": O3_OBSERVATION_WINDOW_S * 1000,
                    },
                    "O3b": {
                        "valid_health_observed": False,
                        "right_censored_ms": O3_OBSERVATION_WINDOW_S * 1000,
                        "not_attempted_reason": "listener_not_observed",
                    },
                    "observation_error": str(exc),
                }
            else:
                vectors["o3a_listener"] = listener_rows
                try:
                    remaining_s = max(
                        0.001,
                        O3_OBSERVATION_WINDOW_S
                        - ((time.monotonic_ns() - launched_ns) / 1_000_000_000),
                    )
                    first_health_ns, health_rows = wait_for_first_valid_health(
                        host, port, process, remaining_s
                    )
                except ObservationTimeout as exc:
                    vectors["o3b_health"] = exc.observations or [
                        {
                            "index": 0,
                            "phase": "o3b_health",
                            "valid": False,
                            "right_censored_ms": 30000.0,
                        }
                    ]
                    receipt["results"] = {
                        "O3a": {
                            "listener_observed": True,
                            "launch_to_listener_ms": (listener_ns - launched_ns) / 1_000_000,
                        },
                        "O3b": {
                            "valid_health_observed": False,
                            "right_censored_ms": O3_OBSERVATION_WINDOW_S * 1000,
                            "listener_to_first_valid_health_ms": None,
                            "launch_to_first_valid_health_ms": None,
                        },
                        "observation_error": str(exc),
                    }
                else:
                    vectors["o3b_health"] = health_rows
                    receipt["results"] = o3_success_results(
                        launched_ns, listener_ns, first_health_ns
                    )
        elif args.mode == "o6a":
            rows, elapsed = open_loop(
                host,
                port,
                launched_ns,
                O6A_REQUESTS,
                O6A_RPS,
                V11_WORKERS,
                V11_REQUEST_TIMEOUT_S,
                "o6a_startup",
            )
            vectors["o6a_startup"] = rows
            receipt["results"] = {"O6a": summarize(rows, elapsed)}
        elif args.mode == "o6b":
            listener_ns, listener_rows = wait_for_listener(
                host, port, process, O3_OBSERVATION_WINDOW_S
            )
            burst_ns = time.monotonic_ns()
            rows, elapsed = open_loop(
                host,
                port,
                burst_ns,
                O6B_REQUESTS,
                O6B_RPS,
                V11_WORKERS,
                V11_REQUEST_TIMEOUT_S,
                "o6b_listener",
            )
            vectors["o6b_listener"] = rows
            receipt["results"] = {
                "O6b": {
                    "launch_to_listener_ms": (listener_ns - launched_ns) / 1_000_000,
                    "first_target_after_listener_ms": (burst_ns - listener_ns) / 1_000_000,
                    "listener_probe_count": len(listener_rows),
                    **summarize(rows, elapsed),
                }
            }
        elif args.mode == "v11":
            listener_ns, _listener_rows = wait_for_listener(
                host, port, process, O3_OBSERVATION_WINDOW_S
            )
            barrier_ns, readiness_rows = readiness_barrier(
                host, port, process, O3_OBSERVATION_WINDOW_S
            )
            warmup_start_ns = time.monotonic_ns()
            warmup_rows, warmup_elapsed = open_loop(
                host,
                port,
                warmup_start_ns,
                V11_WARMUP_REQUESTS,
                V11_WARMUP_RPS,
                V11_WORKERS,
                V11_REQUEST_TIMEOUT_S,
                "warmup",
            )
            vectors["warmup"] = warmup_rows
            measured_listener_before = proc_netstat()
            measured_start_ns = time.monotonic_ns()
            measured_rows, measured_elapsed = open_loop(
                host,
                port,
                measured_start_ns,
                V11_REQUESTS,
                V11_RPS,
                V11_WORKERS,
                V11_REQUEST_TIMEOUT_S,
                "v11",
            )
            measured_listener_after = proc_netstat()
            vectors["v11"] = measured_rows
            receipt["results"] = {
                "readiness": {
                    "launch_to_listener_ms": (listener_ns - launched_ns) / 1_000_000,
                    "launch_to_barrier_ms": (barrier_ns - launched_ns) / 1_000_000,
                    "barrier_completed_monotonic_ns": barrier_ns,
                    "observations": readiness_rows,
                },
                "warmup": summarize(warmup_rows, warmup_elapsed),
                "V11": summarize(measured_rows, measured_elapsed),
            }
            receipt["listener_counters"] = {
                "measured_phase_before": measured_listener_before,
                "measured_phase_after": measured_listener_after,
                "measured_phase_delta": listener_delta(
                    measured_listener_before, measured_listener_after
                ),
            }
        else:  # parser prevents this
            raise ContractError(f"unknown mode {args.mode}")

        phase_after = sample_system(None)
        for name, rows in vectors.items():
            write_vector(directory, name, rows)
        receipt.setdefault("listener_counters", {})["whole_run_delta"] = listener_delta(
            host_listener_before, proc_netstat()
        )
        receipt["measurement_validity"] = evaluate_run(
            args,
            receipt,
            directory,
            vectors,
            phase_before,
            phase_after,
            governed["reference_profile"],
        )
        v11_verdict = receipt.get("verdicts", {}).get("V11", {}).get("absolute_verdict")
        if not receipt["measurement_validity"]["valid"]:
            receipt["status"] = "contract_failed"
        elif v11_verdict == "fail":
            receipt["status"] = "release_gate_failed"
        elif v11_verdict == "informational_non_reference":
            receipt["status"] = "informational_non_reference"
        else:
            receipt["status"] = "completed"
        exit_code = 1 if receipt["status"] in {"contract_failed", "release_gate_failed"} else 0
    except Exception as exc:
        receipt["status"] = "contract_failed"
        receipt["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        receipt["measurement_validity"] = {
            "valid": False,
            "classification": "invalid_contract_measurement",
            "failure_reasons": [f"{type(exc).__name__}:{exc}"],
        }
        exit_code = 1
    finally:
        if sampler is not None:
            sampler.stop()
        if process is not None:
            stop_child(process)
            receipt["server_returncode"] = process.returncode
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()
        receipt["host_postrun"] = {
            "system": sample_system(None),
            "competing_processes": competing_processes(),
        }
        receipt["completed_at"] = utcnow()
        (directory / "receipt.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        write_manifest(directory)
        manifest_reasons = verify_manifest(directory)
        if manifest_reasons:
            exit_code = 1
    print(json.dumps({"status": receipt["status"], "receipt": str(directory / "receipt.json")}))
    return exit_code


def load_subject_spec(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "label",
        "target_python",
        "artifact",
        "artifact_sha256",
        "artifact_provenance",
        "artifact_provenance_sha256",
        "repo",
        "commit",
        "tree",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ContractError(f"subject spec fields must be exactly {sorted(required)}")
    if not SHA256_RE.fullmatch(str(value["artifact_sha256"])):
        raise ContractError("subject artifact hash is malformed")
    if not SHA256_RE.fullmatch(str(value["artifact_provenance_sha256"])):
        raise ContractError("subject artifact provenance hash is malformed")
    return value


def load_run_receipt(path: Path) -> dict[str, Any]:
    """Load a child receipt strictly; malformed evidence never becomes `{}`."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"run receipt unreadable: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ContractError("run receipt schema mismatch")
    return value


def dependency_environment_fingerprint(receipt: dict[str, Any]) -> str:
    fingerprint = receipt.get("subject", {}).get("dependencies", {}).get("environment_fingerprint")
    if not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint):
        raise ContractError("dependency environment fingerprint missing or malformed")
    return fingerprint


def dependency_environment_failures(fingerprints: Sequence[str], expected_runs: int) -> list[str]:
    reasons: list[str] = []
    if len(fingerprints) != expected_runs:
        reasons.append("dependency_environment_fingerprint_missing")
    if len(set(fingerprints)) != 1:
        reasons.append("dependency_environment_drift")
    return reasons


def alternating_sequence(iterations: int) -> list[str]:
    if iterations < 5:
        raise ContractError("at least five runs per artifact are required")
    return [label for _ in range(iterations) for label in ("control", "candidate")]


def matrix_run_command(
    runner: Path,
    mode: str,
    subject: dict[str, Any],
    run_dir: Path,
    args: argparse.Namespace,
) -> list[str]:
    """Build one governed child command without duplicating or widening inputs."""

    return [
        sys.executable,
        str(runner),
        "run",
        "--mode",
        mode,
        "--target-python",
        subject["target_python"],
        "--artifact",
        subject["artifact"],
        "--artifact-sha256",
        subject["artifact_sha256"],
        "--artifact-provenance",
        subject["artifact_provenance"],
        "--artifact-provenance-sha256",
        subject["artifact_provenance_sha256"],
        "--repo",
        subject["repo"],
        "--label",
        subject["label"],
        "--commit",
        subject["commit"],
        "--tree",
        subject["tree"],
        "--output-dir",
        str(run_dir),
        "--suite-file",
        str(args.suite_file),
        "--reference-spec",
        str(args.reference_spec),
        "--expected-runner-sha256",
        args.expected_runner_sha256,
        "--expected-suite-sha256",
        args.expected_suite_sha256,
        "--expected-reference-sha256",
        args.expected_reference_sha256,
        "--runtime-image-digest",
        args.runtime_image_digest,
    ]


def run_matrix(args: argparse.Namespace) -> int:
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runner = Path(__file__).resolve()
    actual_runner = sha256_file(runner)
    if actual_runner != args.expected_runner_sha256:
        raise ContractError("runner hash mismatch before matrix output")
    if sha256_file(args.suite_file) != args.expected_suite_sha256:
        raise ContractError("suite hash mismatch before matrix output")
    if sha256_file(args.reference_spec) != args.expected_reference_sha256:
        raise ContractError("reference hash mismatch before matrix output")
    subject_spec_hashes = {
        "control": sha256_file(args.control_spec),
        "candidate": sha256_file(args.candidate_spec),
    }
    expected_subject_spec_hashes = {
        "control": args.control_spec_sha256,
        "candidate": args.candidate_spec_sha256,
    }
    if any(not SHA256_RE.fullmatch(value) for value in expected_subject_spec_hashes.values()):
        raise ContractError("subject spec hash is malformed")
    if subject_spec_hashes != expected_subject_spec_hashes:
        raise ContractError("subject spec hash mismatch before matrix output")
    read_suite_version(args.suite_file)
    read_reference_profile(args.reference_spec)
    subjects = {
        "control": load_subject_spec(args.control_spec),
        "candidate": load_subject_spec(args.candidate_spec),
    }
    sequence = alternating_sequence(args.iterations)
    modes = tuple(part.strip() for part in args.modes.split(",") if part.strip())
    if not modes or any(mode not in RUN_MODES for mode in modes) or len(set(modes)) != len(modes):
        raise ContractError("matrix modes must be unique governed mode names")
    matrix: dict[str, Any] = {
        "schema": MATRIX_SCHEMA,
        "created_at": utcnow(),
        "suite_version": SUITE_VERSION,
        "runner_sha256": actual_runner,
        "suite_sha256": args.expected_suite_sha256,
        "reference_sha256": args.expected_reference_sha256,
        "subject_spec_sha256": subject_spec_hashes,
        "runtime_image_digest": args.runtime_image_digest,
        "iterations_per_artifact": args.iterations,
        "sequence_per_mode": sequence,
        "modes": list(modes),
        "runs": [],
    }
    failure_reasons: list[str] = []
    dependency_fingerprints: list[str] = []
    for mode in modes:
        counts = {"control": 0, "candidate": 0}
        for ordinal, subject_key in enumerate(sequence, start=1):
            counts[subject_key] += 1
            subject = subjects[subject_key]
            run_name = f"{mode}-{ordinal:02d}-{subject_key}-{counts[subject_key]}"
            run_dir = output / run_name
            command = matrix_run_command(runner, mode, subject, run_dir, args)
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            receipt_path = run_dir / "receipt.json"
            receipt_error: str | None = None
            try:
                receipt = load_run_receipt(receipt_path)
            except ContractError as exc:
                receipt = {}
                receipt_error = str(exc)
                failure_reasons.append(f"{run_name}:receipt_invalid")
            manifest_reasons = (
                verify_manifest(run_dir) if run_dir.is_dir() else ["run_output_missing"]
            )
            status = receipt.get("status")
            if result.returncode != 0 or manifest_reasons:
                failure_reasons.append(f"{run_name}:run_or_manifest_failed")
            if args.require_reference_verdict and mode == "v11":
                if receipt.get("verdicts", {}).get("V11", {}).get("absolute_verdict") != "pass":
                    failure_reasons.append(f"{run_name}:reference_v11_pass_missing")
            try:
                fingerprint = dependency_environment_fingerprint(receipt)
            except ContractError:
                failure_reasons.append(f"{run_name}:dependency_environment_missing")
            else:
                dependency_fingerprints.append(fingerprint)
            matrix["runs"].append(
                {
                    "name": run_name,
                    "mode": mode,
                    "subject": subject_key,
                    "returncode": result.returncode,
                    "status": status,
                    "receipt_error": receipt_error,
                    "receipt_sha256": sha256_file(receipt_path) if receipt_path.is_file() else None,
                    "manifest_sha256": (
                        sha256_file(run_dir / "SHA256SUMS")
                        if (run_dir / "SHA256SUMS").is_file()
                        else None
                    ),
                    "manifest_valid": not manifest_reasons,
                    "manifest_failure_reasons": manifest_reasons,
                }
            )
            if args.cooldown_s:
                time.sleep(args.cooldown_s)
    expected_runs = len(modes) * args.iterations * 2
    failure_reasons.extend(dependency_environment_failures(dependency_fingerprints, expected_runs))
    if len(matrix["runs"]) != expected_runs:
        failure_reasons.append("matrix_run_count_mismatch")
    matrix["valid"] = not failure_reasons
    matrix["failure_reasons"] = failure_reasons
    matrix["completed_at"] = utcnow()
    (output / "matrix.receipt.json").write_text(
        json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_manifest(output)
    matrix_manifest_reasons = verify_manifest(output)
    print(
        json.dumps(
            {
                "valid": matrix["valid"] and not matrix_manifest_reasons,
                "receipt": str(output / "matrix.receipt.json"),
                "manifest_failure_reasons": matrix_manifest_reasons,
            }
        )
    )
    return 0 if matrix["valid"] and not matrix_manifest_reasons else 1


def serve(args: argparse.Namespace) -> int:
    from tokenpak.proxy.server import ProxyServer

    server = ProxyServer(host=args.host, port=args.port)
    stopped = threading.Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    server.start(blocking=False)
    try:
        while not stopped.wait(0.1):
            pass
    finally:
        server.stop()
    return 0


def add_governed_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--suite-file", type=Path, required=True)
    parser.add_argument("--reference-spec", type=Path, required=True)
    parser.add_argument("--expected-runner-sha256", required=True)
    parser.add_argument("--expected-suite-sha256", required=True)
    parser.add_argument("--expected-reference-sha256", required=True)
    parser.add_argument("--runtime-image-digest", required=True)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", required=True)
    serve_parser.add_argument("--port", required=True, type=int)
    serve_parser.set_defaults(function=serve)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--mode", choices=RUN_MODES, required=True)
    run_parser.add_argument("--target-python", type=Path, required=True)
    run_parser.add_argument("--artifact", type=Path, required=True)
    run_parser.add_argument("--artifact-sha256", required=True)
    run_parser.add_argument("--artifact-provenance", type=Path, required=True)
    run_parser.add_argument("--artifact-provenance-sha256", required=True)
    run_parser.add_argument("--repo", type=Path, required=True)
    run_parser.add_argument("--label", required=True)
    run_parser.add_argument("--commit", required=True)
    run_parser.add_argument("--tree", required=True)
    run_parser.add_argument("--output-dir", type=Path, required=True)
    add_governed_arguments(run_parser)
    run_parser.set_defaults(function=run_one)

    matrix_parser = subparsers.add_parser("matrix")
    matrix_parser.add_argument("--control-spec", type=Path, required=True)
    matrix_parser.add_argument("--control-spec-sha256", required=True)
    matrix_parser.add_argument("--candidate-spec", type=Path, required=True)
    matrix_parser.add_argument("--candidate-spec-sha256", required=True)
    matrix_parser.add_argument("--output-dir", type=Path, required=True)
    matrix_parser.add_argument("--iterations", type=int, default=5)
    matrix_parser.add_argument("--modes", default=",".join(RUN_MODES))
    matrix_parser.add_argument("--cooldown-s", type=float, default=1.0)
    matrix_parser.add_argument("--require-reference-verdict", action="store_true")
    add_governed_arguments(matrix_parser)
    matrix_parser.set_defaults(function=run_matrix)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.function(args))
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"benchmark contract error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
