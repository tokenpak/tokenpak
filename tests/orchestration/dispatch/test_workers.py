"""Tests for the DispatchWorker registry + prompt-overlay loader.

Covers Standards Delta v0 §5.1 (worker profiles) + §16 (additive overlays):

  * registry load of the packaged builder/reviewer profiles, with the §5.1
    capabilities + default_loop_policy asserted;
  * fail-loud rejection of a worker profile declaring an unknown capability
    string (§5.2 governance rule), exercised from a YAML file on disk;
  * overlay loading with user-dir (``~/.tpk/dispatch/overlays/``) override
    shadowing the packaged default, simulated via tmp_path + TOKENPAK_HOME;
  * additive prompt composition (base directives preserved in full, overlay
    instructions appended; overlay cannot remove a base directive);
  * capability-intersection route binding (bind succeeds when the worker has
    every overlay/station capability, fails loud otherwise).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Dispatch is pydantic-native; deps ship via the opt-in `dispatch` extra.
# Skip cleanly on slim installs that lack pydantic rather than erroring at
# collection time. PyYAML is a hard dependency of the worker loader.
pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from tokenpak.orchestration.dispatch.models.worker import DispatchWorker
from tokenpak.orchestration.dispatch.registry import workers as wk
from tokenpak.orchestration.dispatch.registry.workers import (
    DispatchWorkerRegistry,
    OverlayLoader,
    PromptOverlay,
    RouteBindError,
    WorkerProfileError,
    assert_route_binding,
    bind_overlay,
    compose_prompt,
    missing_capabilities,
)

_PACKAGED_REGISTRY_DIR = Path(wk.__file__).resolve().parent
_PACKAGED_OVERLAY_DIR = _PACKAGED_REGISTRY_DIR / "overlays"


# ---------------------------------------------------------------------------
# Registry load — valid packaged profiles
# ---------------------------------------------------------------------------


def test_registry_loads_packaged_workers():
    reg = DispatchWorkerRegistry.from_dir()
    assert reg.ids() == [
        "worker.builder.default.v1",
        "worker.reviewer.default.v1",
    ]


def test_builder_profile_matches_standards_delta_5_1():
    reg = DispatchWorkerRegistry.from_dir()
    builder = reg.get("worker.builder.default.v1")
    assert builder.roles == ["builder"]
    assert builder.capabilities == [
        "answer_generation",
        "code_drafting",
        "patch_generation",
        "doc_drafting",
    ]
    loop = builder.default_loop_policy
    assert (loop.max_iterations, loop.max_tool_calls, loop.max_wall_seconds) == (
        3,
        8,
        900,
    )


def test_reviewer_profile_matches_standards_delta_5_1():
    reg = DispatchWorkerRegistry.from_dir()
    reviewer = reg.get("worker.reviewer.default.v1")
    assert reviewer.roles == ["reviewer"]
    assert reviewer.capabilities == ["semantic_review", "doc_review"]
    loop = reviewer.default_loop_policy
    assert (loop.max_iterations, loop.max_tool_calls, loop.max_wall_seconds) == (
        1,
        4,
        600,
    )
    # Reviewer is read-only: no file modification, no command execution.
    assert reviewer.permission_profile.modify_files.value == "never"
    assert reviewer.permission_profile.run_commands.value == "never"
    assert reviewer.permission_profile.install_dependencies is False


def test_registry_for_role_is_dynamic():
    reg = DispatchWorkerRegistry.from_dir()
    builders = reg.for_role("builder")
    assert [w.id for w in builders] == ["worker.builder.default.v1"]
    assert reg.for_role("nonexistent_role") == []


# ---------------------------------------------------------------------------
# Registry load — fail-loud on unknown capability string
# ---------------------------------------------------------------------------


_BAD_WORKER_YAML = """\
id: worker.rogue.v1
kind: tip_worker_profile
roles:
  - builder
capabilities:
  - code_drafting
  - exfiltrate_secrets
system_directives:
  - do a thing
allowed_tools:
  - read_context
input_schema: station_input.v1
output_schema: station_result.v1
default_loop_policy:
  max_iterations: 1
  max_tool_calls: 1
  max_wall_seconds: 60
permission_profile:
  read_files: true
"""


def test_registry_rejects_unknown_capability_fail_loud(tmp_path):
    bad_dir = tmp_path / "registry"
    bad_dir.mkdir()
    (bad_dir / "worker.rogue.v1.yaml").write_text(_BAD_WORKER_YAML)
    with pytest.raises(WorkerProfileError) as exc:
        DispatchWorkerRegistry.from_dir(bad_dir)
    # The offending capability surfaces in the chained error message.
    assert "exfiltrate_secrets" in str(exc.value)


def test_registry_accepts_valid_capability_yaml(tmp_path):
    good_dir = tmp_path / "registry"
    good_dir.mkdir()
    good = _BAD_WORKER_YAML.replace("  - exfiltrate_secrets\n", "")
    (good_dir / "worker.ok.v1.yaml").write_text(good)
    reg = DispatchWorkerRegistry.from_dir(good_dir)
    assert reg.ids() == ["worker.rogue.v1"]
    assert reg.get("worker.rogue.v1").capabilities == ["code_drafting"]


def test_registry_rejects_duplicate_worker_id(tmp_path):
    d = tmp_path / "registry"
    d.mkdir()
    good = _BAD_WORKER_YAML.replace("  - exfiltrate_secrets\n", "")
    (d / "worker.a.yaml").write_text(good)
    (d / "worker.b.yaml").write_text(good)  # same id inside
    with pytest.raises(WorkerProfileError):
        DispatchWorkerRegistry.from_dir(d)


# ---------------------------------------------------------------------------
# Overlay loading — packaged defaults + user-dir override
# ---------------------------------------------------------------------------


def test_overlay_loader_lists_packaged_overlays():
    loader = OverlayLoader(packaged_dir=_PACKAGED_OVERLAY_DIR, user_dir=Path("/nonexistent"))
    assert loader.ids() == [
        "overlay.code_builder.v1",
        "overlay.doc_builder.v1",
        "overlay.quick_answer.v1",
    ]


def test_overlay_loader_loads_packaged_overlay():
    loader = OverlayLoader(packaged_dir=_PACKAGED_OVERLAY_DIR, user_dir=Path("/nonexistent"))
    overlay = loader.load("overlay.code_builder.v1")
    assert isinstance(overlay, PromptOverlay)
    assert overlay.mode == "additive"
    assert overlay.applies_to_role == "builder"
    assert overlay.required_capabilities == ["code_drafting", "patch_generation"]


def test_user_overlay_shadows_packaged_default(tmp_path):
    user_dir = tmp_path / "overlays"
    user_dir.mkdir()
    (user_dir / "overlay.code_builder.v1.yaml").write_text(
        "id: overlay.code_builder.v1\n"
        "applies_to_role: builder\n"
        "mode: additive\n"
        "instructions:\n"
        "  - user-customized directive\n"
        "required_capabilities:\n"
        "  - code_drafting\n"
    )
    loader = OverlayLoader(packaged_dir=_PACKAGED_OVERLAY_DIR, user_dir=user_dir)
    overlay = loader.load("overlay.code_builder.v1")
    assert overlay.instructions == ["user-customized directive"]
    # The packaged overlays the user did NOT override are still discoverable.
    assert "overlay.doc_builder.v1" in loader.ids()


def test_overlay_loader_resolves_user_dir_via_tpk_home(tmp_path, monkeypatch):
    """User dir defaults to <TOKENPAK_HOME>/dispatch/overlays/ (no hardcoded path)."""

    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    overlay_dir = tmp_path / "dispatch" / "overlays"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / "overlay.quick_answer.v1.yaml").write_text(
        "id: overlay.quick_answer.v1\n"
        "applies_to_role: builder\n"
        "mode: additive\n"
        "instructions:\n"
        "  - home-resolved override\n"
        "required_capabilities:\n"
        "  - answer_generation\n"
    )
    # user_dir=None => resolve via tokenpak._paths.under("dispatch", "overlays").
    loader = OverlayLoader(packaged_dir=_PACKAGED_OVERLAY_DIR)
    assert wk.user_overlay_dir() == overlay_dir
    overlay = loader.load("overlay.quick_answer.v1")
    assert overlay.instructions == ["home-resolved override"]


def test_overlay_loader_unknown_id_raises():
    loader = OverlayLoader(packaged_dir=_PACKAGED_OVERLAY_DIR, user_dir=Path("/nonexistent"))
    with pytest.raises(wk.OverlayError):
        loader.load("overlay.does_not_exist.v1")


def test_overlay_mode_must_be_additive():
    with pytest.raises(Exception):  # pydantic ValidationError (Literal["additive"])
        PromptOverlay(
            id="overlay.bad.v1",
            applies_to_role="builder",
            mode="replace",
            instructions=["x"],
            required_capabilities=["code_drafting"],
        )


def test_overlay_rejects_unknown_required_capability():
    with pytest.raises(Exception):  # registry-bound validator -> ValidationError
        PromptOverlay(
            id="overlay.bad.v1",
            applies_to_role="builder",
            mode="additive",
            instructions=["x"],
            required_capabilities=["not_a_capability"],
        )


# ---------------------------------------------------------------------------
# Additive prompt composition
# ---------------------------------------------------------------------------


def _builder() -> DispatchWorker:
    return DispatchWorkerRegistry.from_dir().get("worker.builder.default.v1")


def test_compose_prompt_is_additive_base_preserved():
    builder = _builder()
    overlay = OverlayLoader(packaged_dir=_PACKAGED_OVERLAY_DIR, user_dir=Path("/nonexistent")).load(
        "overlay.code_builder.v1"
    )

    composed = compose_prompt(builder, overlay)

    # Every base directive is present, in original order, at the front.
    assert composed[: len(builder.system_directives)] == builder.system_directives
    # Overlay instructions are appended after the base directives.
    assert composed[len(builder.system_directives) :] == overlay.instructions
    # Nothing was removed: composed length == base + overlay.
    assert len(composed) == len(builder.system_directives) + len(overlay.instructions)


def test_compose_prompt_without_overlay_returns_base():
    builder = _builder()
    assert compose_prompt(builder, None) == builder.system_directives
    # Returns a copy, not the model's list, so mutation cannot corrupt the model.
    composed = compose_prompt(builder, None)
    composed.append("mutation")
    assert "mutation" not in builder.system_directives


# ---------------------------------------------------------------------------
# Capability-intersection route binding
# ---------------------------------------------------------------------------


def test_bind_overlay_succeeds_when_worker_has_capabilities():
    builder = _builder()
    overlay = OverlayLoader(packaged_dir=_PACKAGED_OVERLAY_DIR, user_dir=Path("/nonexistent")).load(
        "overlay.code_builder.v1"
    )
    composed = bind_overlay(builder, overlay)
    assert composed == compose_prompt(builder, overlay)


def test_bind_overlay_fails_when_worker_lacks_overlay_capabilities():
    reg = DispatchWorkerRegistry.from_dir()
    reviewer = reg.get("worker.reviewer.default.v1")  # has no code_drafting
    overlay = OverlayLoader(packaged_dir=_PACKAGED_OVERLAY_DIR, user_dir=Path("/nonexistent")).load(
        "overlay.code_builder.v1"
    )
    with pytest.raises(RouteBindError) as exc:
        bind_overlay(reviewer, overlay)
    assert "code_drafting" in exc.value.missing
    assert "patch_generation" in exc.value.missing
    assert exc.value.worker_id == "worker.reviewer.default.v1"
    assert exc.value.overlay_id == "overlay.code_builder.v1"


def test_assert_route_binding_intersects_station_capabilities():
    builder = _builder()  # has code_drafting + patch_generation, no semantic_review
    overlay = OverlayLoader(packaged_dir=_PACKAGED_OVERLAY_DIR, user_dir=Path("/nonexistent")).load(
        "overlay.code_builder.v1"
    )
    # Station demands a capability the builder lacks -> binding must fail.
    with pytest.raises(RouteBindError) as exc:
        assert_route_binding(builder, overlay, station_required_capabilities=["semantic_review"])
    assert exc.value.missing == ["semantic_review"]


def test_missing_capabilities_helper():
    builder = _builder()
    assert missing_capabilities(builder, ["code_drafting"]) == []
    assert missing_capabilities(builder, ["semantic_review", "code_drafting"]) == [
        "semantic_review"
    ]
