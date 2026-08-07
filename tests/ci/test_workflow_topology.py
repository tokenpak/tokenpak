"""Mechanical contracts for risk-selected topology and workflow action pins."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.ci.classify_changes import CATEGORY_NAMES
from scripts.ci.verify_selected_jobs import JOB_NAMES

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "risk-selected-ci.yml"
WORKFLOWS = ROOT / ".github" / "workflows"
MAKEFILE = ROOT / "Makefile"
GITHUB_RELEASE_DOWNLOAD = re.compile(r"https://github\.com/[^\s\"']+/[^\s\"']+/releases/download/")
CHECKSUM_COMMAND = re.compile(r"\bsha256sum\s+(?:--check|-c)\b")
FETCH_COMMAND = re.compile(r"^\s*(?:curl|wget)\b")
FETCH_OUTPUT = re.compile(r"(?:^|\s)(?:-o|--output)\s+(?P<path>\"[^\"]+\"|'[^']+'|\S+)")
FETCH_OUTPUT_EQUALS = re.compile(r"(?:^|\s)--output=(?P<path>\"[^\"]+\"|'[^']+'|\S+)")
ARCHIVE_CONSUMER = re.compile(r"^\s*(?:sudo\s+)?(?:tar|unzip|install|chmod)\b")


def _workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _commands(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_changes_job_exports_complete_classifier_contract() -> None:
    jobs = _workflow()["jobs"]
    outputs = jobs["changes"]["outputs"]
    expected = {
        "base",
        "head",
        "classifier_ok",
        *CATEGORY_NAMES,
        "shared_core",
        "large_diff",
        "multi_surface",
        "full_conservative",
        *(f"run_{name}" for name in JOB_NAMES),
    }

    assert set(outputs) == expected
    assert all(
        "steps.classify.outputs" in value or "steps.range.outputs" in value
        for value in outputs.values()
    )


def test_manual_dispatch_requires_a_real_ancestor_range_and_forces_full_coverage() -> None:
    workflow = _workflow()
    commands = _commands(workflow["jobs"]["changes"])

    dispatch = workflow["on"]["workflow_dispatch"]
    assert dispatch["inputs"]["base_sha"]["required"] == "true"
    assert 'base="${{ inputs.base_sha }}"' in commands
    assert 'base" == "$head' in commands
    assert "^[0-9a-fA-F]{40}$" in commands
    assert "git merge-base --is-ancestor" in commands
    assert "--force-full-conservative" in commands


def test_pull_request_range_uses_the_feature_heads_merge_base() -> None:
    commands = _commands(_workflow()["jobs"]["changes"])

    assert 'base_tip="${{ github.event.pull_request.base.sha }}"' in commands
    assert 'base="$(git merge-base "$base_tip" "$head")"' in commands


def test_trust_baseline_is_always_on_and_runs_canonical_baselines() -> None:
    job = _workflow()["jobs"]["trust_baseline"]
    commands = _commands(job)

    assert job["needs"] == "changes"
    assert job["if"] == "${{ always() }}"
    assert 'python -m pip install -e ".[dev]"' in commands
    assert "scripts/ci/trust_baseline.py" in commands
    assert "scripts/release_check/release_check.py" in commands
    assert "public_safety_scan.py" in commands
    assert "license_policy_scan.py" in commands
    assert "gitleaks detect" in commands


def test_every_selectable_job_uses_a_job_level_condition() -> None:
    jobs = _workflow()["jobs"]
    for name in JOB_NAMES:
        job = jobs[name]
        assert job["needs"] == "changes"
        assert job["if"] == ("${{ always() && needs.changes.outputs.run_" + name + " == 'true' }}")


def test_aggregator_depends_on_and_verifies_every_job() -> None:
    job = _workflow()["jobs"]["ci_required"]
    assert job["if"] == "${{ always() }}"
    assert set(job["needs"]) == {"changes", "trust_baseline", *JOB_NAMES}

    env = job["steps"][-1]["env"]
    for name in JOB_NAMES:
        key = name.upper()
        assert env[f"SELECTED_{key}"] == f"${{{{ needs.changes.outputs.run_{name} }}}}"
        assert env[f"RESULT_{key}"] == f"${{{{ needs.{name}.result }}}}"
    assert job["steps"][-1]["run"] == "python3 scripts/ci/verify_selected_jobs.py"


def test_javascript_and_workflow_jobs_carry_the_required_commands() -> None:
    jobs = _workflow()["jobs"]
    javascript_job = jobs["javascript_sdk"]
    javascript = _commands(javascript_job)
    workflow = _commands(jobs["workflow_release"])

    for command in ("npm ci", "npm run build", "npm test", "npm audit --omit=dev"):
        assert command in javascript
    matrix = javascript_job["strategy"]["matrix"]["include"]
    assert {entry["package-directory"] for entry in matrix} == {
        "sdk",
        "packages/tokenpak-js",
    }
    assert javascript_job["defaults"]["run"]["working-directory"] == (
        "${{ matrix.package-directory }}"
    )
    assert any(
        step.get("uses", "").startswith("actions/setup-go@")
        for step in jobs["workflow_release"]["steps"]
    )
    for command in ("actionlint", "check-action-pins.sh", "tests/ci/"):
        assert command in workflow
    assert re.search(
        r"go run github\.com/rhysd/actionlint/cmd/actionlint@[0-9a-f]{40}",
        workflow,
    )


def test_every_third_party_action_in_every_workflow_uses_a_full_sha() -> None:
    full_sha = re.compile(r"^[0-9a-f]{40}$")
    action = re.compile(
        r"^\s*-?\s*uses:\s*([A-Za-z0-9_.-]+/[^@\s]+)@([^\s#]+)",
        re.MULTILINE,
    )
    refs: list[tuple[str, str, str]] = []
    for workflow in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        for name, ref in action.findall(workflow.read_text(encoding="utf-8")):
            refs.append((workflow.name, name, ref))

    assert refs
    assert all(full_sha.fullmatch(ref) for _workflow_name, _action, ref in refs), refs


def test_every_external_github_release_download_is_digest_verified_before_use() -> None:
    observed: set[tuple[str, str, str]] = set()

    for workflow in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        data = yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        for job_name, job in data.get("jobs", {}).items():
            for step in job.get("steps", []):
                run = step.get("run", "")
                downloads = GITHUB_RELEASE_DOWNLOAD.findall(run)
                if not downloads:
                    continue

                assert len(downloads) == 1, (workflow.name, job_name, step.get("name"))
                env = step.get("env", {})
                digest_names = {
                    name
                    for name, value in env.items()
                    if name.endswith("_SHA256") and re.fullmatch(r"[0-9a-f]{64}", value)
                }
                assert digest_names, (workflow.name, job_name, step.get("name"))

                lines = run.splitlines()
                fetch_index = next(
                    index for index, line in enumerate(lines) if FETCH_COMMAND.search(line)
                )
                output_match = FETCH_OUTPUT.search(
                    lines[fetch_index]
                ) or FETCH_OUTPUT_EQUALS.search(lines[fetch_index])
                assert output_match is not None, (workflow.name, job_name, step.get("name"))
                output_path = output_match.group("path").strip("\"'")

                checksum_index = next(
                    index
                    for index, line in enumerate(lines[fetch_index + 1 :], start=fetch_index + 1)
                    if CHECKSUM_COMMAND.search(line)
                )
                consumers = [
                    index
                    for index, line in enumerate(lines[fetch_index + 1 :], start=fetch_index + 1)
                    if ARCHIVE_CONSUMER.search(line)
                ]
                assert not consumers or checksum_index < min(consumers), (
                    workflow.name,
                    job_name,
                    step.get("name"),
                )
                checksum_line = lines[checksum_index]
                assert output_path in checksum_line
                assert any(
                    f"${name}" in checksum_line or f"${{{name}}}" in checksum_line
                    for name in digest_names
                )
                observed.add((workflow.name, job_name, step.get("name", "")))

    assert observed == {
        ("risk-selected-ci.yml", "trust_baseline", "Install gitleaks"),
        ("secret-scan.yml", "gitleaks", "Install gitleaks"),
    }


@pytest.mark.parametrize(
    ("workflow_run", "expected_error"),
    [
        (
            'url="https://github.com/example/tool/releases/download/v1/tool.tar.gz"\n'
            'curl -sSfL "$url" -o /tmp/tool.tar.gz\n'
            "tar -xzf /tmp/tool.tar.gz -C /tmp\n",
            "no literal 64-character *_SHA256 declaration",
        ),
        (
            'url="https://github.com/example/tool/releases/download/v1/tool.tar.gz"\n'
            'curl -sSfL "$url" -o /tmp/tool.tar.gz\n'
            "tar -xzf /tmp/tool.tar.gz -C /tmp\n"
            "printf '%s  %s\\n' \"$TOOL_SHA256\" /tmp/tool.tar.gz | sha256sum --check -\n",
            "digest check occurs after archive use",
        ),
    ],
)
def test_action_pin_checker_rejects_unverified_release_fetches(
    tmp_path: Path,
    workflow_run: str,
    expected_error: str,
) -> None:
    script_dir = tmp_path / "scripts"
    workflow_dir = tmp_path / ".github" / "workflows"
    script_dir.mkdir()
    workflow_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "check-action-pins.sh", script_dir)
    digest_env = (
        "        env:\n"
        "          TOOL_SHA256: "
        '"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"\n'
        if "TOOL_SHA256" in workflow_run
        else ""
    )
    indented_run = "\n".join(f"          {line}" for line in workflow_run.splitlines())
    (workflow_dir / "unsafe.yml").write_text(
        "name: Unsafe external fetch fixture\n"
        "on: workflow_dispatch\n"
        "jobs:\n"
        "  fetch:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Download tool\n"
        f"{digest_env}"
        "        run: |\n"
        f"{indented_run}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script_dir / "check-action-pins.sh")],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert expected_error in result.stderr


def test_packaging_job_installs_the_backend_used_by_no_isolation_checks() -> None:
    commands = _commands(_workflow()["jobs"]["packaging_dependencies"])

    assert '"setuptools>=64"' in commands
    assert "scripts/check-dist-contents.py" in commands


def test_release_check_jobs_fetch_the_comparison_history() -> None:
    jobs = _workflow()["jobs"]

    for name, job in jobs.items():
        if "scripts/release_check/release_check.py" not in _commands(job):
            continue
        checkout = next(
            step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")
        )
        assert checkout.get("with", {}).get("fetch-depth") == "0", name


def test_python_proxy_job_uses_current_partitions_not_legacy_trees() -> None:
    commands = _commands(_workflow()["jobs"]["python_proxy"])

    assert "tokenpak/tests/ tests/proxy/" not in commands
    assert "tests/_internal/" not in commands
    for path in (
        "tokenpak/tests/test_custom_providers.py",
        "tokenpak/tests/test_proxy_config.py",
        "tests/proxy/",
        "tests/config/",
        "tests/services/",
    ):
        assert path in commands


def test_full_and_long_running_python_jobs_partition_all_applicable_tests() -> None:
    jobs = _workflow()["jobs"]
    full_python = _commands(jobs["full_python"])
    long_running = _commands(jobs["integration_chaos"])

    assert "not integration and not chaos and not slow and not needs_fast_host" in full_python
    assert "tests/" in long_running
    assert "(integration or chaos or slow) and not needs_fast_host" in long_running
    assert "tests/integrations/" not in long_running
    assert "tests/chaos/" not in long_running


def test_release_umbrella_quarantines_only_runner_sensitive_timing() -> None:
    source = MAKEFILE.read_text(encoding="utf-8")
    recipe = source.split("test-release-applicable:", 1)[1].split("\n\n", 1)[0]
    release_rule = next(line for line in source.splitlines() if line.startswith("release-check:"))

    assert "RELEASE_APPLICABLE_MARKERS := not needs_fast_host" in source
    assert 'tests/ -m "$(RELEASE_APPLICABLE_MARKERS)"' in recipe
    for retained_partition in ("integration", "chaos", "slow"):
        assert f"not {retained_partition}" not in recipe
    assert "test-release-applicable" in release_rule
    assert " format-check test " not in release_rule
