"""Adversarial tests for the governed ``/health`` benchmark contract."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

RUNNER_PATH = Path(__file__).with_name("neutral_health_benchmark.py")
SUITE_PATH = Path(__file__).with_name("SUITE_VERSION")
REFERENCE_PATH = Path(__file__).with_name("REFERENCE.md")
SPEC = importlib.util.spec_from_file_location("neutral_health_benchmark", RUNNER_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import bootstrap guard
    raise RuntimeError("unable to import the governed health benchmark runner")
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


def request_sample(index: int, phase: str = "probe") -> dict[str, object]:
    target = 1_000_000 + index
    return {
        "index": index,
        "phase": phase,
        "endpoint": "/health",
        "target_monotonic_ns": target,
        "submit_monotonic_ns": target + 1,
        "submit_lag_ms": 0.001,
        "worker_start_monotonic_ns": target + 2,
        "worker_start_lag_ms": 0.002,
        "completed_monotonic_ns": target + 3,
        "end_to_end_latency_ms": 0.003,
        "completed": True,
        "status_code": 200,
        "content_type": "application/json",
        "body_sha256": "0" * 64,
        "body_length": 16,
        "json_status": "ok",
        "json_parse_error": None,
        "service_latency_ms": 1.0,
        "error": None,
    }


def governed_args(tmp_path: Path) -> argparse.Namespace:
    artifact = tmp_path / "subject.whl"
    artifact.write_bytes(b"artifact")
    provenance = tmp_path / "subject.provenance.json"
    provenance.write_text("{}\n", encoding="utf-8")
    return argparse.Namespace(
        expected_runner_sha256=BENCHMARK.sha256_file(RUNNER_PATH),
        expected_suite_sha256=BENCHMARK.sha256_file(SUITE_PATH),
        expected_reference_sha256=BENCHMARK.sha256_file(REFERENCE_PATH),
        artifact_sha256=BENCHMARK.sha256_file(artifact),
        artifact_provenance_sha256=BENCHMARK.sha256_file(provenance),
        suite_file=SUITE_PATH,
        reference_spec=REFERENCE_PATH,
        artifact=artifact,
        artifact_provenance=provenance,
        runtime_image_digest="sha256:" + "1" * 64,
    )


def test_suite_bootstrap_is_exact() -> None:
    assert BENCHMARK.read_suite_version(SUITE_PATH) == "bench-suite-v1.0.0"
    assert SUITE_PATH.read_text(encoding="utf-8") == "bench-suite-v1.0.0\n"


def test_reference_profile_binds_fixed_v11_contract() -> None:
    profile = BENCHMARK.read_reference_profile(REFERENCE_PATH)
    assert profile["profile_id"] == "tokenpak-health-reference-v1"
    assert profile["v11"] == {
        "warmup_requests": 20,
        "warmup_rps": 25.0,
        "measured_requests": 500,
        "measured_rps": 100.0,
        "workers": 20,
        "request_timeout_s": 5.0,
        "p50_ceiling_ms": 15.0,
        "p99_ceiling_ms": 500.0,
        "minimum_throughput_rps": 85.0,
        "maximum_request_errors": 0,
        "maximum_listener_drops": 0,
        "maximum_listener_overflows": 0,
    }


def test_reference_profile_threshold_drift_fails_closed(tmp_path: Path) -> None:
    altered = tmp_path / "REFERENCE.md"
    altered.write_text(
        REFERENCE_PATH.read_text(encoding="utf-8").replace(
            '"p99_ceiling_ms": 500.0', '"p99_ceiling_ms": 2000.0'
        ),
        encoding="utf-8",
    )
    with pytest.raises(BENCHMARK.ContractError, match="V11 contract drift"):
        BENCHMARK.read_reference_profile(altered)


def test_cli_exposes_no_threshold_widening_flags() -> None:
    parser = BENCHMARK.parser()
    option_strings = {
        option
        for action in parser._subparsers._group_actions[0].choices["run"]._actions
        for option in action.option_strings
    }
    assert "--p99-ceiling" not in option_strings
    assert "--ci-ceiling" not in option_strings
    assert BENCHMARK.V11_P99_CEILING_MS == 500.0


def test_governed_input_hashes_verify(tmp_path: Path) -> None:
    result = BENCHMARK.verify_governed_inputs(governed_args(tmp_path))
    assert result["reference_profile"]["profile_id"] == BENCHMARK.PROFILE_ID


@pytest.mark.parametrize(
    "field",
    [
        "expected_runner_sha256",
        "expected_suite_sha256",
        "expected_reference_sha256",
        "artifact_sha256",
        "artifact_provenance_sha256",
    ],
)
def test_any_governed_input_hash_drift_fails_closed(tmp_path: Path, field: str) -> None:
    args = governed_args(tmp_path)
    setattr(args, field, "f" * 64)
    with pytest.raises(BENCHMARK.ContractError, match="governed input mismatch"):
        BENCHMARK.verify_governed_inputs(args)


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *arguments], text=True).strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=TokenPak",
        "-c",
        "user.email=hello@tokenpak.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _write_test_wheel(path: Path, payload: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in payload.items():
            archive.writestr(name, value)
        archive.writestr(
            "tokenpak-1.0.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: tokenpak\nVersion: 1.0.0\n",
        )
        archive.writestr("tokenpak-1.0.0.dist-info/RECORD", "")


def test_wheel_payload_is_bound_to_declared_source_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    package = repo / "tokenpak"
    package.mkdir(parents=True)
    _git(repo, "init", "-q")
    payload = {
        "tokenpak/__init__.py": b'__version__ = "1.0.0"\n',
        "tokenpak/core.py": b'VALUE = "first"\n',
    }
    for name, value in payload.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    first_commit = _commit(repo, "first")
    wheel = tmp_path / "tokenpak-1.0.0-py3-none-any.whl"
    _write_test_wheel(wheel, payload)

    first = BENCHMARK.wheel_source_correlation(wheel, repo, first_commit)
    assert first["verified"] is True

    first_tree = _git(repo, "rev-parse", f"{first_commit}^{{tree}}")
    provenance_path = tmp_path / "tokenpak.provenance.json"
    provenance_path.write_text(
        json.dumps(
            {
                "schema": BENCHMARK.PROVENANCE_SCHEMA,
                "artifact_sha256": BENCHMARK.sha256_file(wheel),
                "source_commit": first_commit,
                "source_tree": first_tree,
                "wheel_payload_manifest_sha256": first["wheel_payload_manifest_sha256"],
            }
        ),
        encoding="utf-8",
    )
    provenance = BENCHMARK.verify_artifact_provenance(
        provenance_path,
        BENCHMARK.sha256_file(wheel),
        first_commit,
        first_tree,
        first["wheel_payload_manifest_sha256"],
    )
    assert provenance["verified"] is True

    (repo / "README.md").write_text("source-only change\n", encoding="utf-8")
    second_commit = _commit(repo, "second-same-wheel")
    second_tree = _git(repo, "rev-parse", f"{second_commit}^{{tree}}")
    identical_payload = BENCHMARK.wheel_source_correlation(wheel, repo, second_commit)
    assert identical_payload["verified"] is True
    wrong_revision = BENCHMARK.verify_artifact_provenance(
        provenance_path,
        BENCHMARK.sha256_file(wheel),
        second_commit,
        second_tree,
        identical_payload["wheel_payload_manifest_sha256"],
    )
    assert wrong_revision["verified"] is False
    assert wrong_revision["mismatches"] == ["source_commit", "source_tree"]

    (package / "core.py").write_text('VALUE = "second"\n', encoding="utf-8")
    third_commit = _commit(repo, "third-same-version")
    third = BENCHMARK.wheel_source_correlation(wheel, repo, third_commit)
    assert third["verified"] is False
    assert third["payload_mismatches"] == ["tokenpak/core.py"]


def test_wheel_payload_accepts_tracked_relative_symlink_bytes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    target = repo / "tokenpak/models/data/seed_catalog.json"
    link = repo / "tokenpak/telemetry/data/pricing_catalog.json"
    target.parent.mkdir(parents=True)
    link.parent.mkdir(parents=True)
    _git(repo, "init", "-q")
    payload = b'{"model":"test"}\n'
    target.write_bytes(payload)
    link.symlink_to("../../models/data/seed_catalog.json")
    commit = _commit(repo, "tracked-relative-symlink")
    wheel = tmp_path / "tokenpak-1.0.0-py3-none-any.whl"
    _write_test_wheel(
        wheel,
        {
            "tokenpak/models/data/seed_catalog.json": payload,
            "tokenpak/telemetry/data/pricing_catalog.json": payload,
        },
    )

    result = BENCHMARK.wheel_source_correlation(wheel, repo, commit)

    assert result["verified"] is True
    assert result["resolved_source_links"] == ["tokenpak/telemetry/data/pricing_catalog.json"]
    assert result["source_resolution_errors"] == {}


@pytest.mark.parametrize(
    ("links", "expected_error"),
    [
        ({"tokenpak/absolute.py": "/outside.py"}, "absolute archive link target"),
        ({"tokenpak/escape.py": "../../outside.py"}, "escapes repository"),
        ({"tokenpak/dangling.py": "missing.py"}, "archive member is missing"),
        (
            {"tokenpak/a.py": "b.py", "tokenpak/b.py": "a.py"},
            "archive link cycle",
        ),
    ],
)
def test_wheel_payload_rejects_unsafe_tracked_symlinks(
    tmp_path: Path, links: dict[str, str], expected_error: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for name, target in links.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
    commit = _commit(repo, "unsafe-symlink")
    wheel = tmp_path / "tokenpak-1.0.0-py3-none-any.whl"
    first_name = next(iter(links))
    _write_test_wheel(wheel, {first_name: b"untrusted\n"})

    result = BENCHMARK.wheel_source_correlation(wheel, repo, commit)

    assert result["verified"] is False
    assert expected_error in result["source_resolution_errors"][first_name]
    assert first_name not in result["missing_from_source"]


def test_provider_credentials_are_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in BENCHMARK.PROVIDER_SECRET_ENV:
        monkeypatch.setenv(key, "must-not-survive")
    env, denied, removed = BENCHMARK.subject_environment(tmp_path)
    assert set(removed) == BENCHMARK.PROVIDER_SECRET_ENV
    assert not (set(env) & BENCHMARK.PROVIDER_SECRET_ENV)
    assert BENCHMARK.PROVIDER_SECRET_ENV <= set(denied)
    assert BENCHMARK.sanitized_environment(env).keys() <= BENCHMARK.RELEVANT_ENV


def test_child_environment_is_deny_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arbitrary = {
        "DATABASE_URL": "postgres://secret",
        "MY_PRIVATE_SECRET": "hidden",
        "UNLISTED_VENDOR_TOKEN": "hidden",
        "PYTHONPATH": "/inject",
        "HTTPS_PROXY": "http://credential@example.invalid",
    }
    for key, value in arbitrary.items():
        monkeypatch.setenv(key, value)
    env, denied, _removed = BENCHMARK.subject_environment(tmp_path)
    assert not (set(arbitrary) & set(env))
    assert set(arbitrary) <= set(denied)
    assert set(env) == (BENCHMARK.CHILD_ENV_INHERITED_ALLOWLIST & set(BENCHMARK.os.environ)) | {
        "HOME",
        "PATH",
        "TMPDIR",
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "TOKENPAK_HOME",
    }


def test_target_interpreter_probes_ignore_controller_working_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contaminated = tmp_path / "controller-checkout"
    neutral = tmp_path / "neutral"
    metadata = contaminated / "controller_only_drift.egg-info"
    metadata.mkdir(parents=True)
    neutral.mkdir()
    (metadata / "PKG-INFO").write_text(
        "Metadata-Version: 2.1\nName: controller-only-drift\nVersion: 9.9.9\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(contaminated)
    probe = (
        "import importlib.metadata as m;"
        "print(any((d.metadata.get('Name') or '') == 'controller-only-drift' "
        "for d in m.distributions()))"
    )

    assert BENCHMARK.command_text([sys.executable, "-c", probe]) == "True"
    assert BENCHMARK.command_text([sys.executable, "-c", probe], cwd=neutral) == "False"
    receipt = BENCHMARK.dependency_receipt(Path(sys.executable), neutral_cwd=neutral)
    assert all(name != "controller-only-drift" for name, _version in receipt["packages"])


def test_matrix_child_command_binds_each_governed_argument_once(tmp_path: Path) -> None:
    subject = {
        "target_python": "/venv/bin/python",
        "artifact": "/dist/tokenpak.whl",
        "artifact_sha256": "a" * 64,
        "artifact_provenance": "/dist/tokenpak.provenance.json",
        "artifact_provenance_sha256": "9" * 64,
        "repo": "/repo",
        "label": "candidate",
        "commit": "b" * 40,
        "tree": "c" * 40,
    }
    args = argparse.Namespace(
        suite_file=SUITE_PATH,
        reference_spec=REFERENCE_PATH,
        expected_runner_sha256="d" * 64,
        expected_suite_sha256="e" * 64,
        expected_reference_sha256="f" * 64,
        runtime_image_digest="sha256:" + "1" * 64,
    )
    command = BENCHMARK.matrix_run_command(RUNNER_PATH, "v11", subject, tmp_path / "run", args)
    governed_options = {
        "--mode",
        "--target-python",
        "--artifact",
        "--artifact-sha256",
        "--artifact-provenance",
        "--artifact-provenance-sha256",
        "--repo",
        "--label",
        "--commit",
        "--tree",
        "--output-dir",
        "--suite-file",
        "--reference-spec",
        "--expected-runner-sha256",
        "--expected-suite-sha256",
        "--expected-reference-sha256",
        "--runtime-image-digest",
    }
    assert all(command.count(option) == 1 for option in governed_options)
    parsed = BENCHMARK.parser().parse_args(command[2:])
    assert parsed.target_python == Path(subject["target_python"])
    assert parsed.mode == "v11"


def test_governed_health_states_match_canonical_contract() -> None:
    assert BENCHMARK.KNOWN_HEALTH_STATES == {"ok", "degraded", "shutting_down"}


def test_valid_serialized_vector_round_trips(tmp_path: Path) -> None:
    rows = [request_sample(0), request_sample(1)]
    BENCHMARK.write_vector(tmp_path, "probe", rows)
    assert BENCHMARK.validate_vector(tmp_path, "probe", rows, 2, BENCHMARK.REQUEST_FIELDS) == []


def test_missing_sample_fails_closed(tmp_path: Path) -> None:
    rows = [request_sample(0)]
    BENCHMARK.write_vector(tmp_path, "probe", rows)
    reasons = BENCHMARK.validate_vector(tmp_path, "probe", rows, 2, BENCHMARK.REQUEST_FIELDS)
    assert "probe:sample_count:1!=2" in reasons
    assert "probe:serialized_sample_count" in reasons


def test_duplicate_sample_index_fails_closed(tmp_path: Path) -> None:
    rows = [request_sample(0), request_sample(0)]
    BENCHMARK.write_vector(tmp_path, "probe", rows)
    reasons = BENCHMARK.validate_vector(tmp_path, "probe", rows, 2, BENCHMARK.REQUEST_FIELDS)
    assert "probe:non_contiguous_or_duplicate_indices" in reasons


def test_jsonl_value_corruption_fails_closed(tmp_path: Path) -> None:
    rows = [request_sample(0), request_sample(1)]
    BENCHMARK.write_vector(tmp_path, "probe", rows)
    path = tmp_path / "probe.jsonl"
    serialized = [json.loads(line) for line in path.read_text().splitlines()]
    serialized[1]["service_latency_ms"] = 999.0
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in serialized),
        encoding="utf-8",
    )
    reasons = BENCHMARK.validate_vector(tmp_path, "probe", rows, 2, BENCHMARK.REQUEST_FIELDS)
    assert "probe:jsonl_value_mismatch:1" in reasons


def test_csv_value_corruption_fails_closed(tmp_path: Path) -> None:
    rows = [request_sample(0), request_sample(1)]
    BENCHMARK.write_vector(tmp_path, "probe", rows)
    path = tmp_path / "probe.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        serialized = list(csv.DictReader(handle))
        fields = list(serialized[0])
    serialized[0]["service_latency_ms"] = "999.0"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(serialized)
    reasons = BENCHMARK.validate_vector(tmp_path, "probe", rows, 2, BENCHMARK.REQUEST_FIELDS)
    assert "probe:csv_value_mismatch:0:service_latency_ms" in reasons


def test_manifest_rejects_changed_missing_and_extra_files(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    first.write_text("{}\n", encoding="utf-8")
    BENCHMARK.write_manifest(tmp_path)
    assert BENCHMARK.verify_manifest(tmp_path) == []
    first.write_text('{"changed":true}\n', encoding="utf-8")
    assert "manifest_hash_mismatch:first.json" in BENCHMARK.verify_manifest(tmp_path)
    first.write_text("{}\n", encoding="utf-8")
    extra = tmp_path / "extra.json"
    extra.write_text("{}\n", encoding="utf-8")
    assert "manifest_inventory_mismatch" in BENCHMARK.verify_manifest(tmp_path)


def test_five_run_alternating_controller_sequence() -> None:
    assert BENCHMARK.alternating_sequence(5) == ["control", "candidate"] * 5
    with pytest.raises(BENCHMARK.ContractError, match="at least five"):
        BENCHMARK.alternating_sequence(4)


def test_dependency_environment_drift_fails_closed() -> None:
    assert BENCHMARK.dependency_environment_failures(["a" * 64, "b" * 64], 2) == [
        "dependency_environment_drift"
    ]
    assert BENCHMARK.dependency_environment_failures(["a" * 64], 2) == [
        "dependency_environment_fingerprint_missing"
    ]


def test_malformed_run_receipt_fails_closed(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"schema":', encoding="utf-8")
    with pytest.raises(BENCHMARK.ContractError, match="run receipt unreadable"):
        BENCHMARK.load_run_receipt(receipt)
    receipt.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    with pytest.raises(BENCHMARK.ContractError, match="schema mismatch"):
        BENCHMARK.load_run_receipt(receipt)


def test_o3b_uses_first_valid_response_completion_and_separate_origins() -> None:
    result = BENCHMARK.o3_success_results(
        launched_ns=1_000_000,
        listener_ns=3_000_000,
        first_health_completed_ns=8_000_000,
    )
    assert result["O3a"]["launch_to_listener_ms"] == 2.0
    assert result["O3b"]["listener_to_first_valid_health_ms"] == 5.0
    assert result["O3b"]["launch_to_first_valid_health_ms"] == 7.0
    assert result["O3b"]["first_valid_health_response_completed_monotonic_ns"] == 8_000_000


def test_o3b_stops_on_first_valid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Running:
        def poll(self) -> None:
            return None

    def fake_fetch(_host: str, _port: int, _timeout: float) -> dict[str, object]:
        nonlocal calls
        calls += 1
        row = request_sample(calls - 1, "o3b_health")
        if calls == 1:
            row["status_code"] = 503
            row["json_status"] = None
        return row

    monkeypatch.setattr(BENCHMARK, "fetch_health", fake_fetch)
    completed_ns, rows = BENCHMARK.wait_for_first_valid_health("127.0.0.1", 1, Running(), 1.0)
    assert completed_ns == rows[-1]["response_completed_monotonic_ns"]
    assert len(rows) == 2
    assert calls == 2
    assert rows[-1]["valid"] is True


def test_modes_keep_contract_vectors_disjoint() -> None:
    assert BENCHMARK.RUN_MODES == ("o3", "o6a", "o6b", "v11")
    vector_names = [name for names in BENCHMARK.MODE_VECTOR_NAMES.values() for name in names]
    assert len(vector_names) == len(set(vector_names))
    assert set(vector_names) == {
        "o3a_listener",
        "o3b_health",
        "o6a_startup",
        "o6b_listener",
        "warmup",
        "v11",
    }


def _readiness_observations() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(3):
        completed = 100_000_000 + (index * 50_000_000)
        row = request_sample(index, "readiness")
        row.update(
            {
                "active_probe": True,
                "request_started_monotonic_ns": completed - 1_000_000,
                "response_completed_monotonic_ns": completed,
                "valid": True,
                "invalid_reasons": [],
            }
        )
        rows.append(row)
    return rows


def _v11_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[argparse.Namespace, dict[str, object], dict[str, list[dict[str, object]]]]:
    monkeypatch.setattr(
        BENCHMARK,
        "reference_qualification",
        lambda *_args, **_kwargs: {
            "profile_id": BENCHMARK.PROFILE_ID,
            "qualified": True,
            "failure_reasons": [],
            "observations": {},
        },
    )
    warmup = [request_sample(index, "warmup") for index in range(20)]
    measured = [request_sample(index, "v11") for index in range(500)]
    readiness = _readiness_observations()
    receipt: dict[str, object] = {
        "subject": {"dependencies": {"environment_fingerprint": "a" * 64}},
        "results": {
            "readiness": {
                "barrier_completed_monotonic_ns": readiness[-1]["response_completed_monotonic_ns"],
                "observations": readiness,
            },
            "warmup": BENCHMARK.summarize(warmup, 0.8),
            "V11": BENCHMARK.summarize(measured, 5.0),
        },
        "listener_counters": {
            "measured_phase_delta": {
                "TcpExt.ListenOverflows": 0,
                "TcpExt.ListenDrops": 0,
            }
        },
    }
    telemetry = {
        "utc": "2026-01-01T00:00:00Z",
        "monotonic_ns": 1,
        "cpu": {},
        "meminfo_kib": {},
        "vmstat": {},
        "diskstats": {},
        "process": {},
    }
    (tmp_path / "telemetry.jsonl").write_text(
        json.dumps(telemetry) + "\n" + json.dumps({**telemetry, "monotonic_ns": 2}) + "\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(mode="v11", runtime_image_digest="sha256:" + "1" * 64)
    return args, receipt, {"warmup": warmup, "v11": measured}


def _evaluate_v11(
    tmp_path: Path,
    args: argparse.Namespace,
    receipt: dict[str, object],
    vectors: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    for name, rows in vectors.items():
        BENCHMARK.write_vector(tmp_path, name, rows)
    return BENCHMARK.evaluate_run(
        args,
        receipt,
        tmp_path,
        vectors,
        {},
        {},
        BENCHMARK.read_reference_profile(REFERENCE_PATH),
    )


def test_evaluate_run_fails_closed_on_generator_saturation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, receipt, vectors = _v11_case(tmp_path, monkeypatch)
    for row in vectors["v11"][494:]:
        row["submit_lag_ms"] = 10.001
    vectors["v11"][-1]["submit_lag_ms"] = 50.001
    receipt["results"]["V11"] = BENCHMARK.summarize(vectors["v11"], 5.0)
    result = _evaluate_v11(tmp_path, args, receipt, vectors)
    assert result["valid"] is False
    assert "v11:generator_submit_lag_p99" in result["failure_reasons"]
    assert "v11:generator_submit_lag_maximum" in result["failure_reasons"]
    assert receipt["verdicts"]["V11"]["absolute_verdict"] == "invalid"


def test_evaluate_run_blocks_throughput_shortfall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, receipt, vectors = _v11_case(tmp_path, monkeypatch)
    receipt["results"]["V11"] = BENCHMARK.summarize(vectors["v11"], 10.0)
    result = _evaluate_v11(tmp_path, args, receipt, vectors)
    assert result["valid"] is True
    assert receipt["verdicts"]["V11"]["checks"]["throughput"] is False
    assert receipt["verdicts"]["V11"]["absolute_verdict"] == "fail"


def test_evaluate_run_fails_closed_without_listener_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, receipt, vectors = _v11_case(tmp_path, monkeypatch)
    receipt.pop("listener_counters")
    result = _evaluate_v11(tmp_path, args, receipt, vectors)
    assert result["valid"] is False
    assert "v11:listener_counters_missing" in result["failure_reasons"]
    assert receipt["verdicts"]["V11"]["absolute_verdict"] == "invalid"


def test_evaluate_run_rejects_fixed_sleep_as_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, receipt, vectors = _v11_case(tmp_path, monkeypatch)
    receipt["results"]["readiness"] = {
        "barrier_completed_monotonic_ns": 150_000_000,
        "observations": [{"index": index, "slept_ms": 50, "valid": True} for index in range(3)],
    }
    result = _evaluate_v11(tmp_path, args, receipt, vectors)
    assert result["valid"] is False
    assert any(
        reason.startswith("v11:readiness_active_probe_missing")
        for reason in result["failure_reasons"]
    )


def test_evaluate_run_rejects_cold_warm_vector_mixing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, receipt, vectors = _v11_case(tmp_path, monkeypatch)
    vectors["v11"] = [request_sample(index, "warmup") for index in range(500)]
    receipt["results"]["V11"] = BENCHMARK.summarize(vectors["v11"], 5.0)
    result = _evaluate_v11(tmp_path, args, receipt, vectors)
    assert result["valid"] is False
    assert "v11:sample:0:phase_mismatch" in result["failure_reasons"]


def test_generator_saturation_limits_are_fixed() -> None:
    rows = [request_sample(index) for index in range(500)]
    for row in rows[494:]:
        row["submit_lag_ms"] = 10.001
    rows[499]["submit_lag_ms"] = 50.001
    summary = BENCHMARK.summarize(rows, elapsed_s=5.0)
    assert summary["load_generator"]["submit_lag_ms_p99"] > 10.0
    assert summary["load_generator"]["submit_lag_ms_maximum"] > 50.0


def test_missing_telemetry_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    path.write_text(json.dumps({"utc": "now"}) + "\n", encoding="utf-8")
    with pytest.raises(BENCHMARK.ContractError, match="incomplete"):
        BENCHMARK.telemetry_rows(path)


def test_nonreference_profile_can_never_qualify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = BENCHMARK.read_reference_profile(REFERENCE_PATH)
    before = {
        "cpu": {"cpu": [1, 1, 1, 1, 1, 1, 1, 0]},
        "cpu_throttle": {"cpu0.core": 0},
        "meminfo_kib": {"MemTotal": 16 * 1024 * 1024},
        "vmstat": {"pswpin": 0, "pswpout": 0},
    }
    after = json.loads(json.dumps(before))
    dependency = {"python": "3.12.3"}
    args = argparse.Namespace(runtime_image_digest="sha256:" + "1" * 64)
    monkeypatch.setattr(BENCHMARK.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(BENCHMARK.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(BENCHMARK.platform, "python_version", lambda: "3.12.3")
    monkeypatch.setattr(BENCHMARK.os, "sched_getaffinity", lambda _pid: {0, 1, 2, 3})
    qualification = BENCHMARK.reference_qualification(
        profile, args, before, after, [{}, {}], dependency
    )
    assert qualification["qualified"] is False
    assert "operating_system_mismatch" in qualification["failure_reasons"]
    assert "machine_architecture_mismatch" in qualification["failure_reasons"]
