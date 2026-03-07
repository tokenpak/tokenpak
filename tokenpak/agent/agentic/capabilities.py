"""
Capability Matching — Route tasks to capable agents.

Each agent registers with capabilities (GPU, memory, specialties).
This module matches task requirements against agent capabilities
and returns a ranked list of suitable agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .registry import AgentInfo, AgentRegistry, get_registry


@dataclass
class AgentCapabilities:
    """
    Standard capability schema for agents.

    Attributes:
        gpu: Whether agent has GPU access
        memory_gb: Available memory in GB
        specialties: List of specialty tags (e.g., "code", "research", "data")
        max_concurrent: Maximum concurrent tasks
        provider_access: List of providers agent can use (e.g., ["anthropic", "openai"])
        custom: Additional custom capabilities
    """

    gpu: bool = False
    memory_gb: float = 4.0
    specialties: List[str] = field(default_factory=list)
    max_concurrent: int = 1
    provider_access: List[str] = field(default_factory=lambda: ["anthropic"])
    custom: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gpu": self.gpu,
            "memory_gb": self.memory_gb,
            "specialties": self.specialties,
            "max_concurrent": self.max_concurrent,
            "provider_access": self.provider_access,
            "custom": self.custom,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentCapabilities":
        return cls(
            gpu=data.get("gpu", False),
            memory_gb=data.get("memory_gb", 4.0),
            specialties=data.get("specialties", []),
            max_concurrent=data.get("max_concurrent", 1),
            provider_access=data.get("provider_access", ["anthropic"]),
            custom=data.get("custom", {}),
        )


@dataclass
class TaskRequirements:
    """
    Requirements a task has for agent capabilities.

    All fields are optional — unset means "any".
    """

    requires_gpu: Optional[bool] = None
    min_memory_gb: Optional[float] = None
    required_specialties: List[str] = field(default_factory=list)
    required_providers: List[str] = field(default_factory=list)
    prefer_idle: bool = True  # Prefer agents not currently working
    max_heartbeat_age_seconds: Optional[float] = None  # Filter out stale agents

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requires_gpu": self.requires_gpu,
            "min_memory_gb": self.min_memory_gb,
            "required_specialties": self.required_specialties,
            "required_providers": self.required_providers,
            "prefer_idle": self.prefer_idle,
            "max_heartbeat_age_seconds": self.max_heartbeat_age_seconds,
        }


@dataclass
class MatchResult:
    """Result of capability matching."""

    agent: AgentInfo
    score: float  # Higher is better
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent.agent_id,
            "name": self.agent.name,
            "hostname": self.agent.hostname,
            "score": self.score,
            "reasons": self.reasons,
        }


class CapabilityMatcher:
    """
    Match task requirements against registered agents.

    Usage:
        matcher = CapabilityMatcher()
        requirements = TaskRequirements(requires_gpu=True, min_memory_gb=8)
        matches = matcher.match(requirements)
        # matches is List[MatchResult], sorted by score descending
    """

    def __init__(self, registry: Optional[AgentRegistry] = None):
        self.registry = registry or get_registry()

    def match(
        self,
        requirements: TaskRequirements,
        include_stale: bool = False,
    ) -> List[MatchResult]:
        """
        Find agents matching the requirements.

        Returns a list of MatchResult sorted by score (best first).
        Agents that don't meet hard requirements are excluded.
        """
        if include_stale:
            agents = self.registry.list_all()
        else:
            agents = self.registry.list_active()

        results: List[MatchResult] = []

        for agent in agents:
            score, reasons, excluded = self._evaluate(agent, requirements)
            if not excluded:
                results.append(MatchResult(agent=agent, score=score, reasons=reasons))

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def _evaluate(
        self,
        agent: AgentInfo,
        req: TaskRequirements,
    ) -> tuple[float, List[str], bool]:
        """
        Evaluate an agent against requirements.

        Returns (score, reasons, excluded).
        If excluded=True, agent doesn't meet hard requirements.
        """
        caps = AgentCapabilities.from_dict(agent.capabilities)
        score = 50.0  # Base score
        reasons: List[str] = []

        # --- Hard requirements (exclusion) ---

        # GPU requirement
        if req.requires_gpu is True and not caps.gpu:
            return 0, ["missing GPU"], True

        # Memory requirement
        if req.min_memory_gb is not None and caps.memory_gb < req.min_memory_gb:
            return 0, [f"insufficient memory ({caps.memory_gb}GB < {req.min_memory_gb}GB)"], True

        # Specialty requirements (all must be present)
        if req.required_specialties:
            agent_specs = set(caps.specialties)
            missing = set(req.required_specialties) - agent_specs
            if missing:
                return 0, [f"missing specialties: {missing}"], True

        # Provider requirements (at least one must be present)
        if req.required_providers:
            agent_providers = set(caps.provider_access)
            if not agent_providers.intersection(req.required_providers):
                return 0, [f"no matching providers (need {req.required_providers})"], True

        # Heartbeat freshness
        if req.max_heartbeat_age_seconds is not None:
            age = agent.heartbeat_age_seconds()
            if age > req.max_heartbeat_age_seconds:
                return 0, [f"stale heartbeat ({age:.0f}s > {req.max_heartbeat_age_seconds}s)"], True

        # --- Soft scoring ---

        # Bonus for GPU when not required but available
        if req.requires_gpu is None and caps.gpu:
            score += 5
            reasons.append("has GPU")

        # Bonus for extra memory
        if caps.memory_gb >= 16:
            score += 10
            reasons.append("high memory")
        elif caps.memory_gb >= 8:
            score += 5
            reasons.append("good memory")

        # Bonus for matching specialties beyond minimum
        if req.required_specialties:
            extra = len(set(caps.specialties) - set(req.required_specialties))
            if extra > 0:
                score += extra * 2
                reasons.append(f"+{extra} extra specialties")

        # Bonus for more provider access
        if len(caps.provider_access) > 1:
            score += len(caps.provider_access) * 2
            reasons.append(f"{len(caps.provider_access)} providers")

        # Prefer idle agents
        if req.prefer_idle and agent.status == "active" and agent.current_task is None:
            score += 20
            reasons.append("idle")
        elif agent.status == "busy":
            score -= 10
            reasons.append("busy")

        # Fresher heartbeat is better
        age = agent.heartbeat_age_seconds()
        if age < 60:
            score += 10
            reasons.append("recent heartbeat")
        elif age < 300:
            score += 5

        return score, reasons, False

    def find_best(self, requirements: TaskRequirements) -> Optional[AgentInfo]:
        """Find the single best agent for requirements, or None if no match."""
        matches = self.match(requirements)
        return matches[0].agent if matches else None

    def find_by_specialty(self, specialty: str) -> List[AgentInfo]:
        """Find all agents with a given specialty."""
        agents = self.registry.list_active()
        results = []
        for agent in agents:
            caps = AgentCapabilities.from_dict(agent.capabilities)
            if specialty in caps.specialties:
                results.append(agent)
        return results

    def find_with_provider(self, provider: str) -> List[AgentInfo]:
        """Find all agents that can access a given provider."""
        agents = self.registry.list_active()
        results = []
        for agent in agents:
            caps = AgentCapabilities.from_dict(agent.capabilities)
            if provider in caps.provider_access:
                results.append(agent)
        return results


# Module-level singleton
_default_matcher: Optional[CapabilityMatcher] = None


def get_matcher() -> CapabilityMatcher:
    """Get the default capability matcher."""
    global _default_matcher
    if _default_matcher is None:
        _default_matcher = CapabilityMatcher()
    return _default_matcher
