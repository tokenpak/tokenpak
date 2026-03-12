"""Tests for TokenPakCrewAIHook and TokenPakCrew."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from crewai_tokenpak import AgentContextConfig, TokenPakCrew, TokenPakCrewAIHook


@dataclass
class FakeOutput:
    raw: str


@dataclass
class FakeAgent:
    role: str


@dataclass
class FakeTask:
    description: str
    agent: FakeAgent
    name: str | None = None
    context: Any = None
    output: FakeOutput | None = None


@dataclass
class FakeCrew:
    tasks: list[FakeTask]
    seen_contexts: list[str] = field(default_factory=list)

    def _get_context(self, task: FakeTask, task_outputs: list[FakeOutput]) -> str:
        return "\n\n".join(output.raw for output in task_outputs)

    def kickoff(self, **inputs: Any) -> dict[str, Any]:
        outputs: list[FakeOutput] = []
        for task in self.tasks:
            context = self._get_context(task, outputs)
            self.seen_contexts.append(context)
            task.output = FakeOutput(raw=f"{task.description} :: done")
            outputs.append(task.output)
        return {"ok": True, "inputs": inputs, "contexts": list(self.seen_contexts)}

    async def akickoff(self, **inputs: Any) -> dict[str, Any]:
        return self.kickoff(**inputs)


def test_hook_builds_context_from_prior_outputs_when_context_is_truthy_marker() -> None:
    hook = TokenPakCrewAIHook(shared_context="shared policy")
    task = FakeTask(
        name="write",
        description="Write summary",
        agent=FakeAgent(role="writer"),
        context=True,
    )
    outputs = [FakeOutput(raw="finding one"), FakeOutput(raw="finding two")]

    context = hook.build_context(task, outputs)

    assert "shared policy" in context
    assert "finding one" in context
    assert "finding two" in context
    assert hook.last_report is not None
    assert hook.last_report.injected_sections == ("shared", "crew_context")


def test_hook_uses_explicit_task_context_list() -> None:
    previous = FakeTask(
        name="research",
        description="Research",
        agent=FakeAgent(role="researcher"),
        output=FakeOutput(raw="important research facts"),
    )
    task = FakeTask(
        name="write",
        description="Write brief",
        agent=FakeAgent(role="writer"),
        context=[previous],
    )
    hook = TokenPakCrewAIHook()

    context = hook.build_context(task, [])

    assert context == "important research facts"


def test_hook_applies_task_and_agent_injections() -> None:
    hook = TokenPakCrewAIHook(
        shared_context="global",
        task_context={"draft": "task injection"},
        agent_overrides={
            "writer": AgentContextConfig(
                budget=50,
                prefix="prefix",
                suffix="suffix",
            )
        },
    )
    task = FakeTask(
        name="draft",
        description="Draft",
        agent=FakeAgent(role="writer"),
        context=True,
    )
    context = hook.build_context(task, [FakeOutput(raw="prior output")])

    assert "global" in context
    assert "prefix" in context
    assert "prior output" in context
    assert "task injection" in context
    assert "suffix" in context
    assert hook.last_report is not None
    assert hook.last_report.agent_name == "writer"
    assert hook.last_report.budget == 50


def test_hook_compresses_large_context_and_records_usage() -> None:
    repeated = "\n".join(["duplicate"] * 50)
    hook = TokenPakCrewAIHook(
        per_agent_budget=5,
        task_context={"draft": repeated},
    )
    task = FakeTask(
        name="draft",
        description="Draft",
        agent=FakeAgent(role="writer"),
        context=True,
    )

    context = hook.build_context(task, [FakeOutput(raw=repeated)])

    assert context.count("duplicate") <= 2
    report = hook.last_report
    assert report is not None
    assert report.was_compressed is True
    assert hook.context_manager.get_usage()["writer"] == report.compressed_tokens


def test_apply_to_crew_patches_instance_and_restore_undoes_it() -> None:
    hook = TokenPakCrewAIHook(shared_context="shared")
    task = FakeTask(
        name="write",
        description="Write",
        agent=FakeAgent(role="writer"),
        context=True,
    )
    crew = FakeCrew(tasks=[task])

    hook.apply_to_crew(crew)
    context = crew._get_context(task, [FakeOutput(raw="prior")])
    hook.restore_crew(crew)
    restored = crew._get_context(task, [FakeOutput(raw="prior")])

    assert "shared" in context
    assert restored == "prior"


def test_tokenpak_crew_wrapper_delegates_sync_kickoff() -> None:
    hook = TokenPakCrewAIHook(shared_context="shared")
    task = FakeTask(
        name="write",
        description="Write",
        agent=FakeAgent(role="writer"),
        context=True,
    )
    crew = FakeCrew(tasks=[task])
    wrapped = TokenPakCrew(crew=crew, hook=hook)

    result = wrapped.kickoff(topic="x")

    assert result["ok"] is True
    assert result["inputs"] == {"topic": "x"}
    assert "shared" in result["contexts"][0]


async def test_tokenpak_crew_wrapper_delegates_async_kickoff() -> None:
    hook = TokenPakCrewAIHook(shared_context="shared")
    task = FakeTask(
        name="write",
        description="Write",
        agent=FakeAgent(role="writer"),
        context=True,
    )
    crew = FakeCrew(tasks=[task])
    wrapped = TokenPakCrew(crew=crew, hook=hook)

    result = await wrapped.akickoff(topic="x")

    assert result["ok"] is True
    assert "shared" in result["contexts"][0]


def test_compression_report_aggregates_multiple_tasks() -> None:
    hook = TokenPakCrewAIHook(shared_context="shared")
    writer = FakeAgent(role="writer")
    researcher = FakeAgent(role="researcher")

    hook.build_context(FakeTask(name="a", description="A", agent=researcher, context=True), [FakeOutput(raw="one")])
    hook.build_context(FakeTask(name="b", description="B", agent=writer, context=True), [FakeOutput(raw="two")])

    report = hook.compression_report()

    assert report["tasks"] == 2
    assert report["agents"] == ["researcher", "writer"]
