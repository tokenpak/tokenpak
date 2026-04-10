"""Tests for tokenpak._internal.agentic.skill_compiler."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tokenpak._internal.agentic.skill_compiler import (
    ExtractedSkill,
    PROMOTION_MIN_SUCCESSFUL_EPISODES,
    SkillCompiler,
    SkillEpisode,
    SkillStore,
)
from tokenpak._internal.macros.engine import MacroDefinition, MacroEngine, MacroStep


@pytest.fixture
def skill_env(tmp_path: Path):
    pytest.importorskip("yaml")
    macro_engine = MacroEngine(macros_dir=tmp_path / "macros")
    store = SkillStore(skills_dir=tmp_path / "skills", macro_engine=macro_engine)
    compiler = SkillCompiler(store=store)
    return compiler, store, macro_engine


def _episode(
    *,
    target: Path,
    success: bool = True,
    validation_passed: bool = True,
    tokens_original: int = 1000,
    tokens_skill: int = 500,
    timestamp: str = "2026-03-11T10:00:00+00:00",
    task_type: str = "patch_bug",
) -> SkillEpisode:
    return SkillEpisode(
        task_type=task_type,
        tool_sequence=["rg", "apply_patch", "pytest"],
        file_targets=[str(target)],
        steps=[
            {
                "name": "write_result",
                "label": "Write result",
                "cmd": f"printf fixed > {target}",
            }
        ],
        validation="file contains fixed",
        success=success,
        validation_passed=validation_passed,
        tokens_original=tokens_original,
        tokens_skill=tokens_skill,
        timestamp=timestamp,
        outcome={"file": str(target), "value": "fixed"},
    )


def test_pattern_detection_identifies_repeated_tasks(skill_env) -> None:
    compiler, _, _ = skill_env
    target = Path("/tmp/a.py")

    compiler.record_episode(_episode(target=target, timestamp="2026-03-11T10:00:00+00:00"))
    compiler.record_episode(_episode(target=target, timestamp="2026-03-11T10:01:00+00:00"))
    compiler.record_episode(_episode(target=target, timestamp="2026-03-11T10:02:00+00:00"))

    repeated = compiler.detect_repeated_patterns()
    assert len(repeated) == 1
    stats = repeated[0]
    assert stats.trigger_pattern["task_type"] == "patch_bug"
    assert stats.trigger_pattern["tool_sequence"] == ["rg", "apply_patch", "pytest"]
    assert stats.trigger_pattern["file_targets"] == [str(target)]


def test_skill_generated_after_threshold_met(skill_env) -> None:
    compiler, store, macro_engine = skill_env
    target = Path("/tmp/compiler.py")

    created = None
    for idx in range(PROMOTION_MIN_SUCCESSFUL_EPISODES):
        created = compiler.record_episode(
            _episode(target=target, timestamp=f"2026-03-11T10:0{idx}:00+00:00")
        )

    assert isinstance(created, ExtractedSkill)
    assert store.get(created.skill_id) is not None
    assert (store.skills_dir / f"{created.skill_id}.json").exists()
    assert macro_engine.exists(created.skill_id) is True


def test_promotion_rules_enforced(skill_env) -> None:
    compiler, store, _ = skill_env
    target = Path("/tmp/rules.py")

    for idx in range(3):
        skill = compiler.record_episode(
            _episode(
                target=target,
                tokens_original=1000,
                tokens_skill=850,
                timestamp=f"2026-03-11T11:0{idx}:00+00:00",
            )
        )
        assert skill is None

    assert store.list_all() == []

    compiler.record_episode(_episode(target=target, timestamp="2026-03-11T12:00:00+00:00"))
    compiler.record_episode(_episode(target=target, timestamp="2026-03-11T12:01:00+00:00"))
    compiler.record_episode(_episode(target=target, timestamp="2026-03-11T12:02:00+00:00"))
    blocked = compiler.record_episode(
        _episode(
            target=target,
            success=False,
            validation_passed=False,
            timestamp="2026-03-11T12:03:00+00:00",
        )
    )

    assert blocked is None
    assert len(store.list_all()) == 1


def test_skill_execution_produces_same_outcome_as_original_reasoning(skill_env, tmp_path: Path) -> None:
    compiler, store, _ = skill_env
    target = tmp_path / "result.txt"

    original_engine = MacroEngine(macros_dir=tmp_path / "original-macros")
    original_definition = MacroDefinition(
        name="original-reasoning",
        steps=[MacroStep("write_result", f"printf fixed > {target}", "Write result")],
        description="Original reasoning",
    )
    original_result = original_engine.run_definition(original_definition)
    original_value = target.read_text()
    target.unlink()

    skill = None
    for idx in range(3):
        skill = compiler.record_episode(
            _episode(target=target, timestamp=f"2026-03-11T13:0{idx}:00+00:00")
        )

    assert skill is not None
    skill_result = store.execute(skill.skill_id)
    assert skill_result.success is True
    assert original_result.success is True
    assert target.read_text() == original_value == "fixed"


def test_token_savings_tracked(skill_env) -> None:
    compiler, store, _ = skill_env
    target = Path("/tmp/savings.py")

    for idx, tokens_skill in enumerate((400, 500, 600), start=1):
        compiler.record_episode(
            _episode(
                target=target,
                tokens_original=1000,
                tokens_skill=tokens_skill,
                timestamp=f"2026-03-11T14:0{idx}:00+00:00",
            )
        )

    skill = store.list_all()[0]
    assert skill.avg_tokens_original == pytest.approx(1000.0)
    assert skill.avg_tokens_skill == pytest.approx(500.0)
    assert skill.avg_token_savings == pytest.approx(0.5)

    raw = json.loads((store.skills_dir / f"{skill.skill_id}.json").read_text())
    assert raw["avg_tokens_original"] == pytest.approx(1000.0)
    assert raw["avg_tokens_skill"] == pytest.approx(500.0)


def test_macro_engine_integration_with_step_format_conversion(skill_env, tmp_path: Path) -> None:
    """Integration test: full path without mocking, tests step format conversion."""
    compiler, store, macro_engine = skill_env
    target = tmp_path / "integration_result.txt"

    # Test 1: Episodes with correct MacroStep format (should work)
    for idx in range(3):
        episode = SkillEpisode(
            task_type="write_file",
            tool_sequence=["write"],
            file_targets=[str(target)],
            steps=[
                {
                    "name": "write_correct_format",
                    "cmd": f"printf 'step1' > {target}",
                    "label": "Write in correct format",
                }
            ],
            validation="file exists",
            success=True,
            validation_passed=True,
            tokens_original=100,
            tokens_skill=50,
            timestamp=f"2026-03-11T15:0{idx}:00+00:00",
        )
        compiler.record_episode(episode)

    skill1 = store.list_all()[0]
    assert skill1 is not None
    assert macro_engine.exists(skill1.skill_id)

    # Verify macro was registered correctly
    macro_def = macro_engine.show(skill1.skill_id)
    assert len(macro_def.steps) == 1
    assert macro_def.steps[0].name == "write_correct_format"
    assert macro_def.steps[0].cmd == f"printf 'step1' > {target}"
    result = macro_engine.run(skill1.skill_id)
    assert result.success is True

    # Clean up for next test
    target.unlink(missing_ok=True)
    shutil.rmtree(store.skills_dir, ignore_errors=True)
    shutil.rmtree(macro_engine.macros_dir, ignore_errors=True)
    store.skills_dir.mkdir(parents=True, exist_ok=True)
    macro_engine.macros_dir.mkdir(parents=True, exist_ok=True)
    compiler._episodes.clear()
    store._index.clear()

    # Test 2: Episodes with tool format (conversion should handle)
    for idx in range(3):
        episode = SkillEpisode(
            task_type="run_tool",
            tool_sequence=["mytool"],
            file_targets=[str(target)],
            steps=[
                {
                    "tool": "echo_tool",
                    "args": {"message": "converted"},
                    "label": "Echo with tool format",
                }
            ],
            validation="output contains converted",
            success=True,
            validation_passed=True,
            tokens_original=100,
            tokens_skill=50,
            timestamp=f"2026-03-11T16:0{idx}:00+00:00",
        )
        compiler.record_episode(episode)

    skill2 = store.list_all()[0]
    assert skill2 is not None
    assert macro_engine.exists(skill2.skill_id)

    # Verify converted macro is properly formed
    macro_def2 = macro_engine.show(skill2.skill_id)
    assert len(macro_def2.steps) == 1
    assert "echo_tool" in macro_def2.steps[0].cmd
    assert "converted" in macro_def2.steps[0].cmd
