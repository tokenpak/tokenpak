"""Tests for the Dispatch Tool Registry + effect-bearing tools (P-TOOLS-01).

Verifies, against Standards Delta v0 §5.3 + §4.8:

  * the five tool descriptors and their declared flags;
  * the autonomy × tool matrix — every one of the 5 tools × 4 modes = 20 cells;
  * the invocation-time gate (DENIED → raise, APPROVAL → approval required,
    CONSTRAINED/ALLOWED → proceed);
  * ``apply_patch`` path-policy enforcement (allowed/denied globs, mandatory
    deny precedence, allow_new_files) and the DispatchEffect create→apply
    round-trip across the create / modify file-state cases;
  * ``run_command`` category allow/forbid enforcement, the mutating-command
    DispatchEffect, non-zero exit handling, and timeout handling.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

# Dispatch is pydantic-native; deps ship via the opt-in `dispatch` extra
# (pyproject [project.optional-dependencies]). Skip cleanly on slim installs
# that lack it rather than erroring at collection time.
pytest.importorskip("pydantic")

from tokenpak.orchestration.dispatch.models.common import PathPolicy
from tokenpak.orchestration.dispatch.models.enums import (
    AutonomyMode,
    EffectStatus,
    EffectTargetType,
    RollbackBehavior,
)
from tokenpak.orchestration.dispatch.tools import (
    POLICY_DEPENDENT,
    TOOL_REGISTRY,
    ApprovalRequiredError,
    CommandCategory,
    CommandCategoryError,
    PathPolicyViolation,
    ToolName,
    ToolPermission,
    ToolPolicyViolation,
    apply_patch,
    authorize_tool_call,
    check_path_policy,
    resolve_tool_permission,
    run_command,
)

_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)

# Standards Delta v0 §5.3 autonomy × tool matrix, transcribed independently of
# the implementation so the test is a real contract check, not a tautology.
_A = ToolPermission.ALLOWED
_X = ToolPermission.DENIED
_AP = ToolPermission.APPROVAL
_C = ToolPermission.CONSTRAINED

_EXPECTED_MATRIX: dict[tuple[ToolName, AutonomyMode], ToolPermission] = {
    (ToolName.READ_CONTEXT, AutonomyMode.ADVISORY): _A,
    (ToolName.READ_CONTEXT, AutonomyMode.DRAFT): _A,
    (ToolName.READ_CONTEXT, AutonomyMode.DISPATCH_WITH_APPROVAL): _A,
    (ToolName.READ_CONTEXT, AutonomyMode.AUTO_DISPATCH_LIMITED): _A,
    (ToolName.WRITE_ARTIFACT, AutonomyMode.ADVISORY): _A,
    (ToolName.WRITE_ARTIFACT, AutonomyMode.DRAFT): _A,
    (ToolName.WRITE_ARTIFACT, AutonomyMode.DISPATCH_WITH_APPROVAL): _A,
    (ToolName.WRITE_ARTIFACT, AutonomyMode.AUTO_DISPATCH_LIMITED): _A,
    (ToolName.PROPOSE_PATCH, AutonomyMode.ADVISORY): _X,
    (ToolName.PROPOSE_PATCH, AutonomyMode.DRAFT): _A,
    (ToolName.PROPOSE_PATCH, AutonomyMode.DISPATCH_WITH_APPROVAL): _A,
    (ToolName.PROPOSE_PATCH, AutonomyMode.AUTO_DISPATCH_LIMITED): _A,
    (ToolName.APPLY_PATCH, AutonomyMode.ADVISORY): _X,
    (ToolName.APPLY_PATCH, AutonomyMode.DRAFT): _X,
    (ToolName.APPLY_PATCH, AutonomyMode.DISPATCH_WITH_APPROVAL): _AP,
    (ToolName.APPLY_PATCH, AutonomyMode.AUTO_DISPATCH_LIMITED): _C,
    (ToolName.RUN_COMMAND, AutonomyMode.ADVISORY): _X,
    (ToolName.RUN_COMMAND, AutonomyMode.DRAFT): _X,
    (ToolName.RUN_COMMAND, AutonomyMode.DISPATCH_WITH_APPROVAL): _AP,
    (ToolName.RUN_COMMAND, AutonomyMode.AUTO_DISPATCH_LIMITED): _C,
}


def _open_policy(*allowed: str) -> PathPolicy:
    """A PathPolicy allowing the given globs (mandatory denies auto-injected)."""

    return PathPolicy(allowed_paths=list(allowed) or ["**"])


# ---------------------------------------------------------------------------
# Registry descriptors
# ---------------------------------------------------------------------------


def test_registry_exposes_all_five_tools():
    assert {t.value for t in TOOL_REGISTRY} == {
        "read_context",
        "write_artifact",
        "propose_patch",
        "apply_patch",
        "run_command",
    }


def test_each_tool_declares_required_attributes():
    for name, spec in TOOL_REGISTRY.items():
        assert spec.name is name
        assert hasattr(spec, "mutates_workspace")
        assert isinstance(spec.requires_dispatch_effect, bool)
        assert isinstance(spec.requires_path_policy_check, bool)
        assert spec.allowed_autonomy_modes  # non-empty for every tool


def test_descriptor_flags_match_standards_delta():
    apply_spec = TOOL_REGISTRY[ToolName.APPLY_PATCH]
    assert apply_spec.mutates_workspace is True
    assert apply_spec.requires_dispatch_effect is True
    assert apply_spec.requires_path_policy_check is True

    run_spec = TOOL_REGISTRY[ToolName.RUN_COMMAND]
    assert run_spec.mutates_workspace == POLICY_DEPENDENT
    assert run_spec.requires_dispatch_effect is True
    assert run_spec.requires_path_policy_check is False

    for non_mutating in (
        ToolName.READ_CONTEXT,
        ToolName.WRITE_ARTIFACT,
        ToolName.PROPOSE_PATCH,
    ):
        spec = TOOL_REGISTRY[non_mutating]
        assert spec.mutates_workspace is False
        assert spec.requires_dispatch_effect is False
        assert spec.requires_path_policy_check is False


def test_allowed_autonomy_modes_derive_from_matrix():
    # A tool's allowed modes == modes whose matrix grade is not DENIED.
    for name, spec in TOOL_REGISTRY.items():
        expected = {
            mode
            for mode in AutonomyMode
            if _EXPECTED_MATRIX[(name, mode)] is not ToolPermission.DENIED
        }
        assert set(spec.allowed_autonomy_modes) == expected


# ---------------------------------------------------------------------------
# Autonomy × tool matrix — 5 tools × 4 modes = 20 cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "mode"), list(_EXPECTED_MATRIX))
def test_matrix_grade_matches_standards_delta(tool: ToolName, mode: AutonomyMode):
    assert resolve_tool_permission(tool, mode) is _EXPECTED_MATRIX[(tool, mode)]


@pytest.mark.parametrize(("tool", "mode"), list(_EXPECTED_MATRIX))
def test_authorize_enforces_each_cell(tool: ToolName, mode: AutonomyMode):
    grade = _EXPECTED_MATRIX[(tool, mode)]
    if grade is ToolPermission.DENIED:
        with pytest.raises(ToolPolicyViolation):
            authorize_tool_call(tool, mode)
    elif grade is ToolPermission.APPROVAL:
        with pytest.raises(ApprovalRequiredError):
            authorize_tool_call(tool, mode)  # no approval granted
        assert authorize_tool_call(tool, mode, approval_granted=True) is grade
    else:  # ALLOWED or CONSTRAINED
        assert authorize_tool_call(tool, mode) is grade


def test_authorize_accepts_string_aliases():
    assert authorize_tool_call("read_context", "advisory") is ToolPermission.ALLOWED
    with pytest.raises(ToolPolicyViolation):
        authorize_tool_call("apply_patch", "advisory")


# ---------------------------------------------------------------------------
# apply_patch — path policy + effect lifecycle
# ---------------------------------------------------------------------------


def test_apply_patch_create_new_file(tmp_path):
    policy = _open_policy("src/**")
    result = apply_patch(
        relative_path="src/new_module.py",
        content="x = 1\n",
        path_policy=policy,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        job_id="job_01",
        station_run_id="sr_01",
        workspace_root=tmp_path,
        now=_NOW,
    )
    assert result.created is True
    assert (tmp_path / "src/new_module.py").read_text() == "x = 1\n"
    eff = result.effect
    assert eff.status is EffectStatus.APPLIED
    assert eff.before_exists is False and eff.before_hash is None
    assert eff.after_hash is not None
    assert eff.target_type is EffectTargetType.FILE
    assert eff.rollback_behavior is RollbackBehavior.DELETE_FILE_IF_AFTER_HASH_MATCHES
    assert eff.finalized_at is not None
    assert eff.rollback_available is True


def test_apply_patch_modify_existing_file(tmp_path):
    target = tmp_path / "src/mod.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\n")
    result = apply_patch(
        relative_path="src/mod.py",
        content="new\n",
        path_policy=_open_policy("src/**"),
        autonomy_mode=AutonomyMode.DISPATCH_WITH_APPROVAL,
        job_id="job_01",
        station_run_id="sr_01",
        workspace_root=tmp_path,
        approval_granted=True,
        now=_NOW,
    )
    assert result.created is False
    assert target.read_text() == "new\n"
    eff = result.effect
    assert eff.before_exists is True and eff.before_hash is not None
    assert eff.after_hash is not None and eff.after_hash != eff.before_hash
    assert (
        eff.rollback_behavior
        is RollbackBehavior.RESTORE_BEFORE_CONTENT_IF_CURRENT_HASH_MATCHES_AFTER_HASH
    )


def test_apply_patch_effect_planned_before_applied_round_trip(tmp_path):
    # The applied effect carries a created_at (planned timestamp) earlier than
    # or equal to finalized_at — the §4.8 planned→applied transition.
    result = apply_patch(
        relative_path="a.txt",
        content="hi",
        path_policy=_open_policy("**"),
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        job_id="job_01",
        station_run_id="sr_01",
        workspace_root=tmp_path,
        now=_NOW,
    )
    eff = result.effect
    assert eff.created_at == _NOW
    assert eff.finalized_at is not None and eff.finalized_at >= eff.created_at


def test_apply_patch_denied_path_rejected(tmp_path):
    # Mandatory deny globs (.env, .git/**, secrets/**, license/**) win.
    policy = _open_policy("**")  # allow everything …
    for denied in (".env", "secrets/key.txt", ".git/config", "license/LICENSE"):
        with pytest.raises(PathPolicyViolation):
            apply_patch(
                relative_path=denied,
                content="x",
                path_policy=policy,
                autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
                job_id="j",
                station_run_id="s",
                workspace_root=tmp_path,
            )
    # … and nothing was written.
    assert not (tmp_path / ".env").exists()


def test_apply_patch_not_in_allowed_paths_rejected(tmp_path):
    with pytest.raises(PathPolicyViolation):
        apply_patch(
            relative_path="docs/readme.md",
            content="x",
            path_policy=_open_policy("src/**"),  # only src/ allowed
            autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
            job_id="j",
            station_run_id="s",
            workspace_root=tmp_path,
        )


def test_apply_patch_allow_new_files_false(tmp_path):
    policy = PathPolicy(allowed_paths=["src/**"], allow_new_files=False)
    with pytest.raises(PathPolicyViolation):
        apply_patch(
            relative_path="src/brand_new.py",
            content="x",
            path_policy=policy,
            autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
            job_id="j",
            station_run_id="s",
            workspace_root=tmp_path,
        )


def test_apply_patch_denied_in_advisory_mode_before_io(tmp_path):
    # advisory DENIES apply_patch — fails at the matrix gate, not the FS.
    with pytest.raises(ToolPolicyViolation):
        apply_patch(
            relative_path="src/x.py",
            content="x",
            path_policy=_open_policy("src/**"),
            autonomy_mode=AutonomyMode.ADVISORY,
            job_id="j",
            station_run_id="s",
            workspace_root=tmp_path,
        )
    assert not (tmp_path / "src/x.py").exists()


def test_apply_patch_requires_approval_in_dispatch_with_approval(tmp_path):
    with pytest.raises(ApprovalRequiredError):
        apply_patch(
            relative_path="src/x.py",
            content="x",
            path_policy=_open_policy("src/**"),
            autonomy_mode=AutonomyMode.DISPATCH_WITH_APPROVAL,
            job_id="j",
            station_run_id="s",
            workspace_root=tmp_path,
            approval_granted=False,
        )


# ---------------------------------------------------------------------------
# check_path_policy / glob matcher unit coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "allowed", "ok"),
    [
        ("tokenpak/orchestration/dispatch/tools/x.py", ["tokenpak/**"], True),
        ("a.py", ["*.py"], True),
        ("sub/a.py", ["*.py"], False),  # * does not span '/'
        ("sub/a.py", ["**/*.py"], True),
        ("./src/a.py", ["src/**"], True),  # leading ./ normalized
    ],
)
def test_glob_matching_segment_aware(path, allowed, ok):
    policy = PathPolicy(allowed_paths=allowed)
    if ok:
        assert check_path_policy(path, policy)
    else:
        with pytest.raises(PathPolicyViolation):
            check_path_policy(path, policy)


def test_glob_deny_subtree():
    policy = PathPolicy(allowed_paths=["**"])
    for denied in (".git/config", ".git/hooks/pre-commit", "secrets/db/creds"):
        with pytest.raises(PathPolicyViolation):
            check_path_policy(denied, policy)


# ---------------------------------------------------------------------------
# run_command — categories + effect + execution
# ---------------------------------------------------------------------------


def test_run_command_read_only_inspection_no_effect():
    result = run_command(
        command=["echo", "hello"],
        category=CommandCategory.READ_ONLY_INSPECTION,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        job_id="j",
        station_run_id="s",
    )
    assert result.effect is None  # non-mutating → no effect record
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert result.timed_out is False


def test_run_command_tests_category_records_effect():
    result = run_command(
        command=["true"],
        category=CommandCategory.TESTS,
        autonomy_mode=AutonomyMode.DISPATCH_WITH_APPROVAL,
        job_id="job_01",
        station_run_id="sr_01",
        approval_granted=True,
        now=_NOW,
    )
    eff = result.effect
    assert eff is not None
    assert eff.status is EffectStatus.APPLIED
    assert eff.target_type is EffectTargetType.COMMAND_OUTPUT
    assert eff.rollback_behavior is RollbackBehavior.MANUAL_ONLY
    assert eff.created_at == _NOW and eff.finalized_at is not None


@pytest.mark.parametrize(
    "forbidden",
    [
        CommandCategory.INSTALL_DEPENDENCY,
        CommandCategory.DEPLOY,
        CommandCategory.MUTATE_SECRET,
        CommandCategory.EXTERNAL_WRITE,
        CommandCategory.RELEASE_TAG,
    ],
)
def test_run_command_rejects_forbidden_categories(forbidden):
    with pytest.raises(CommandCategoryError):
        run_command(
            command=["echo", "x"],
            category=forbidden,
            autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
            job_id="j",
            station_run_id="s",
        )


def test_run_command_nonzero_exit_is_result_not_failure():
    result = run_command(
        command=["false"],
        category=CommandCategory.TESTS,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        job_id="j",
        station_run_id="s",
    )
    assert result.returncode != 0
    assert result.timed_out is False
    # Command ran to completion → effect applied (non-zero exit is data).
    assert result.effect is not None and result.effect.status is EffectStatus.APPLIED


def test_run_command_timeout_marks_effect_failed():
    result = run_command(
        command=["sleep", "30"],
        category=CommandCategory.TESTS,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        job_id="j",
        station_run_id="s",
        timeout_seconds=1,
    )
    assert result.timed_out is True
    assert result.effect is not None and result.effect.status is EffectStatus.FAILED


def test_run_command_denied_in_draft_mode():
    with pytest.raises(ToolPolicyViolation):
        run_command(
            command=["echo", "x"],
            category=CommandCategory.READ_ONLY_INSPECTION,
            autonomy_mode=AutonomyMode.DRAFT,
            job_id="j",
            station_run_id="s",
        )


def test_run_command_respects_cwd(tmp_path):
    (tmp_path / "marker.txt").write_text("here")
    result = run_command(
        command=["ls"],
        category=CommandCategory.READ_ONLY_INSPECTION,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        job_id="j",
        station_run_id="s",
        cwd=tmp_path,
    )
    assert "marker.txt" in result.stdout
