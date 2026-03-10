"""Integration-style tests with a sample two-agent crew."""

from __future__ import annotations

from dataclasses import dataclass, field

from crewai_tokenpak import AgentContextConfig, TokenPakCrewAIHook


@dataclass
class Output:
    raw: str


@dataclass
class Agent:
    role: str


@dataclass
class Task:
    name: str
    description: str
    agent: Agent
    context: object = None
    output: Output | None = None


@dataclass
class SampleCrew:
    tasks: list[Task]
    assembled_contexts: list[tuple[str, str]] = field(default_factory=list)

    def _get_context(self, task: Task, task_outputs: list[Output]) -> str:
        return "\n\n".join(output.raw for output in task_outputs)

    def kickoff(self) -> dict[str, object]:
        outputs: list[Output] = []
        for task in self.tasks:
            context = self._get_context(task, outputs)
            self.assembled_contexts.append((task.name, context))
            task.output = Output(raw=f"{task.agent.role}: completed {task.name}")
            outputs.append(task.output)
        return {"contexts": list(self.assembled_contexts), "outputs": outputs}


def test_sample_two_agent_crew_context_is_compressed_and_injected() -> None:
    researcher = Agent(role="researcher")
    writer = Agent(role="writer")
    reviewer = Agent(role="reviewer")

    research = Task(name="research", description="Research the topic", agent=researcher, context=None)
    draft = Task(name="draft", description="Draft the brief", agent=writer, context=[research])
    review = Task(name="review", description="Review the brief", agent=reviewer, context=[draft])

    research.output = Output(raw="\n".join(["fact"] * 60))
    draft.output = Output(raw="\n".join(["draft paragraph"] * 40))

    crew = SampleCrew(tasks=[research, draft, review])
    hook = TokenPakCrewAIHook(
        total_budget=300,
        per_agent_budget=20,
        shared_context="House style: concise and factual.",
        task_context={"review": "Review for accuracy and tone."},
        agent_overrides={"reviewer": AgentContextConfig(prefix="Reviewer priority.", budget=40)},
    )
    hook.apply_to_crew(crew)

    draft_context = crew._get_context(draft, [research.output])
    review_context = crew._get_context(review, [research.output, draft.output])

    assert "House style: concise and factual." in draft_context
    assert draft_context.count("fact") <= 2
    assert "Reviewer priority." in review_context
    assert "Review for accuracy and tone." in review_context
    assert review_context.count("draft paragraph") <= 2


def test_sample_two_agent_crew_wrapper_flow_records_reports() -> None:
    researcher = Agent(role="researcher")
    writer = Agent(role="writer")

    research = Task(name="research", description="Research", agent=researcher, context=True)
    draft = Task(name="draft", description="Draft", agent=writer, context=True)
    crew = SampleCrew(tasks=[research, draft])
    hook = TokenPakCrewAIHook(shared_context="Shared playbook", per_agent_budget=12)
    hook.apply_to_crew(crew)

    result = crew.kickoff()

    assert len(result["contexts"]) == 2
    assert result["contexts"][1][1].startswith("Shared playbook")
    aggregate = hook.compression_report()
    assert aggregate["tasks"] == 2
