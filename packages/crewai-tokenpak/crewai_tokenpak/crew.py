"""CrewAI hook and wrapper for TokenPak context compression."""

from __future__ import annotations

from dataclasses import dataclass
from types import MethodType
from typing import Any, Mapping, Optional, Protocol, Sequence

from .context import AgentContextConfig, CompressionResult, TokenPakContext, estimate_tokens


class SupportsRawOutput(Protocol):
    """Minimal protocol for CrewAI-like task outputs."""

    raw: str


@dataclass(frozen=True)
class TokenPakCompressionReport:
    """Compression metadata for one assembled task context."""

    task_name: str
    agent_name: str
    budget: int
    original_tokens: int
    compressed_tokens: int
    was_compressed: bool
    injected_sections: tuple[str, ...]

    @property
    def savings_tokens(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)

    @property
    def savings_percent(self) -> float:
        if self.original_tokens == 0:
            return 0.0
        return round((self.savings_tokens / self.original_tokens) * 100, 2)


class CompletionHook:
    """Minimal completion-hook pattern for context assembly interception."""

    def apply_to_crew(self, crew: Any) -> Any:
        raise NotImplementedError


class TokenPakCrewAIHook(CompletionHook):
    """Intercept CrewAI context assembly and apply deterministic compression."""

    def __init__(
        self,
        total_budget: int = 8000,
        per_agent_budget: Optional[int] = None,
        shared_context: Optional[str] = None,
        task_context: Optional[Mapping[str, str]] = None,
        agent_overrides: Optional[Mapping[str, AgentContextConfig]] = None,
    ) -> None:
        self.context_manager = TokenPakContext(
            total_budget=total_budget,
            per_agent_budget=per_agent_budget,
            agent_overrides=agent_overrides,
        )
        self.shared_context = (shared_context or "").strip()
        self.task_context = dict(task_context or {})
        self.agent_overrides = dict(agent_overrides or {})
        self._reports: list[TokenPakCompressionReport] = []

    @property
    def reports(self) -> list[TokenPakCompressionReport]:
        """Return all reports for the current hook instance."""
        return list(self._reports)

    @property
    def last_report(self) -> Optional[TokenPakCompressionReport]:
        """Return the most recent compression report."""
        if not self._reports:
            return None
        return self._reports[-1]

    def reset_reports(self) -> None:
        """Clear accumulated reports."""
        self._reports.clear()

    def build_context(
        self,
        task: Any,
        task_outputs: Sequence[SupportsRawOutput],
    ) -> str:
        """Build and compress context for a task."""
        agent_name = self._agent_name(task)
        task_name = self._task_name(task)
        budget = self.context_manager.allocate_budget(agent_name)

        sections: list[tuple[str, str]] = []
        if self.shared_context:
            sections.append(("shared", self.shared_context))

        agent_override = self.agent_overrides.get(agent_name)
        if agent_override and agent_override.prefix:
            sections.append(("agent_prefix", agent_override.prefix))

        base_context = self._resolve_base_context(task, task_outputs)
        if base_context:
            sections.append(("crew_context", base_context))

        injected_task_context = self.task_context.get(task_name)
        if injected_task_context:
            sections.append(("task_context", injected_task_context.strip()))

        if agent_override and agent_override.suffix:
            sections.append(("agent_suffix", agent_override.suffix))

        assembled = "\n\n".join(content for _, content in sections if content)
        result = self.context_manager.compress_text(assembled, budget=budget)
        self.context_manager.record_usage(agent_name, result.compressed_tokens)
        self._reports.append(
            TokenPakCompressionReport(
                task_name=task_name,
                agent_name=agent_name,
                budget=budget,
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                was_compressed=result.was_compressed,
                injected_sections=tuple(name for name, _ in sections),
            )
        )
        return result.text

    def compression_report(self) -> dict[str, Any]:
        """Return an aggregate report across all intercepted tasks."""
        original_tokens = sum(report.original_tokens for report in self._reports)
        compressed_tokens = sum(report.compressed_tokens for report in self._reports)
        return {
            "tasks": len(self._reports),
            "original_tokens": original_tokens,
            "compressed_tokens": compressed_tokens,
            "saved_tokens": max(0, original_tokens - compressed_tokens),
            "agents": sorted({report.agent_name for report in self._reports}),
        }

    def apply_to_crew(self, crew: Any) -> Any:
        """Patch one crew instance to use TokenPak during `_get_context`."""
        if hasattr(crew, "_tokenpak_original_get_context"):
            return crew

        original = getattr(crew, "_get_context", None)
        crew._tokenpak_original_get_context = original
        crew._tokenpak_hook = self

        def _patched_get_context(_: Any, task: Any, task_outputs: Sequence[SupportsRawOutput]) -> str:
            return self.build_context(task, task_outputs)

        crew._get_context = MethodType(_patched_get_context, crew)
        return crew

    def restore_crew(self, crew: Any) -> Any:
        """Restore the original `_get_context` implementation if it was patched."""
        original = getattr(crew, "_tokenpak_original_get_context", None)
        if original is not None:
            crew._get_context = original
            del crew._tokenpak_original_get_context
        if hasattr(crew, "_tokenpak_hook"):
            del crew._tokenpak_hook
        return crew

    def _resolve_base_context(
        self,
        task: Any,
        task_outputs: Sequence[SupportsRawOutput],
    ) -> str:
        context_tasks = getattr(task, "context", None)
        if not context_tasks:
            return ""

        if isinstance(context_tasks, list):
            resolved: list[str] = []
            for context_task in context_tasks:
                output = getattr(context_task, "output", None)
                raw = getattr(output, "raw", "")
                if raw:
                    resolved.append(str(raw).strip())
            return "\n\n".join(chunk for chunk in resolved if chunk)

        return "\n\n".join(
            str(getattr(task_output, "raw", "")).strip()
            for task_output in task_outputs
            if getattr(task_output, "raw", "")
        )

    @staticmethod
    def _agent_name(task: Any) -> str:
        agent = getattr(task, "agent", None)
        for attr in ("role", "name", "id"):
            value = getattr(agent, attr, None)
            if value:
                return str(value)
        return "agent"

    @staticmethod
    def _task_name(task: Any) -> str:
        for attr in ("name", "description", "id"):
            value = getattr(task, attr, None)
            if value:
                return str(value)
        return "task"


class TokenPakCrew:
    """Thin wrapper that applies a TokenPak hook before delegating to a crew."""

    def __init__(self, crew: Any, hook: Optional[TokenPakCrewAIHook] = None) -> None:
        self.crew = crew
        self.hook = hook or TokenPakCrewAIHook()

    def kickoff(self, **inputs: Any) -> Any:
        """Patch the crew and delegate to its synchronous kickoff."""
        self.hook.apply_to_crew(self.crew)
        return self.crew.kickoff(**inputs)

    async def akickoff(self, **inputs: Any) -> Any:
        """Patch the crew and delegate to its async kickoff."""
        self.hook.apply_to_crew(self.crew)
        return await self.crew.akickoff(**inputs)

    @classmethod
    def create(
        cls,
        *,
        agents: Sequence[Any],
        tasks: Sequence[Any],
        hook: Optional[TokenPakCrewAIHook] = None,
        **crew_kwargs: Any,
    ) -> "TokenPakCrew":
        """Lazily create a real CrewAI `Crew` and wrap it."""
        Crew = _import_crewai_crew()
        crew = Crew(agents=list(agents), tasks=list(tasks), **crew_kwargs)
        return cls(crew=crew, hook=hook)


def _import_crewai_crew() -> Any:
    """Import CrewAI lazily to keep module import safe in constrained envs."""
    from crewai import Crew

    return Crew
