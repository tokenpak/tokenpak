"""
Agent Registry — Multi-agent coordination.

Persistent registry of agents with heartbeat-based liveness tracking.
Agents register on startup, send heartbeats, and are auto-expired when stale.

Storage: ~/.tokenpak/agents.json (atomic writes, chmod 600)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default paths
REGISTRY_PATH = Path.home() / ".tokenpak" / "agents.json"
DEFAULT_EXPIRE_SECONDS = 30 * 60  # 30 minutes


@dataclass
class AgentInfo:
    """Information about a registered agent."""

    agent_id: str
    name: str
    hostname: str
    capabilities: Dict[str, Any] = field(default_factory=dict)
    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    status: str = "active"  # active, busy, draining
    current_task: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentInfo":
        return cls(**data)

    def is_stale(self, expire_seconds: int = DEFAULT_EXPIRE_SECONDS) -> bool:
        """Check if agent hasn't sent heartbeat within expire window."""
        return (time.time() - self.last_heartbeat) > expire_seconds

    def heartbeat_age_seconds(self) -> float:
        """Seconds since last heartbeat."""
        return time.time() - self.last_heartbeat


class AgentRegistry:
    """
    Persistent agent registry with heartbeat tracking.

    Usage:
        registry = AgentRegistry()
        agent_id = registry.register("trix", "trixbot", {"gpu": False, "memory_gb": 4})
        registry.heartbeat(agent_id)
        agents = registry.list_active()
        registry.deregister(agent_id)
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        expire_seconds: int = DEFAULT_EXPIRE_SECONDS,
    ):
        self.path = path or REGISTRY_PATH
        self.expire_seconds = expire_seconds
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, AgentInfo]:
        """Load registry from disk."""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
            return {k: AgentInfo.from_dict(v) for k, v in data.items()}
        except (json.JSONDecodeError, KeyError):
            return {}

    def _save(self, agents: Dict[str, AgentInfo]) -> None:
        """Save registry to disk atomically with secure permissions."""
        data = {k: v.to_dict() for k, v in agents.items()}
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2))
        os.chmod(tmp_path, 0o600)
        tmp_path.rename(self.path)

    def register(
        self,
        name: str,
        hostname: str,
        capabilities: Optional[Dict[str, Any]] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Register a new agent or update existing one.

        Returns the agent_id.
        """
        agents = self._load()

        # Generate ID if not provided
        if agent_id is None:
            agent_id = str(uuid.uuid4())[:8]

        # Check for existing agent with same name+hostname
        for existing_id, info in agents.items():
            if info.name == name and info.hostname == hostname:
                # Update existing registration
                agent_id = existing_id
                break

        agents[agent_id] = AgentInfo(
            agent_id=agent_id,
            name=name,
            hostname=hostname,
            capabilities=capabilities or {},
            metadata=metadata or {},
        )

        self._save(agents)
        return agent_id

    def deregister(self, agent_id: str) -> bool:
        """Remove an agent from registry. Returns True if found and removed."""
        agents = self._load()
        if agent_id in agents:
            del agents[agent_id]
            self._save(agents)
            return True
        return False

    def get(self, agent_id: str) -> Optional[AgentInfo]:
        """Get agent by ID."""
        agents = self._load()
        return agents.get(agent_id)

    def heartbeat(
        self, agent_id: str, status: Optional[str] = None, current_task: Optional[str] = None
    ) -> bool:
        """
        Update agent heartbeat timestamp.
        Optionally update status and current task.
        Returns True if agent exists.
        """
        agents = self._load()
        if agent_id not in agents:
            return False

        agents[agent_id].last_heartbeat = time.time()
        if status is not None:
            agents[agent_id].status = status
        if current_task is not None:
            agents[agent_id].current_task = current_task

        self._save(agents)
        return True

    def list_all(self) -> List[AgentInfo]:
        """List all registered agents (including stale)."""
        return list(self._load().values())

    def list_active(self) -> List[AgentInfo]:
        """List only active (non-stale) agents."""
        agents = self._load()
        return [a for a in agents.values() if not a.is_stale(self.expire_seconds)]

    def prune_stale(self) -> int:
        """Remove stale agents. Returns count removed."""
        agents = self._load()
        original_count = len(agents)
        agents = {k: v for k, v in agents.items() if not v.is_stale(self.expire_seconds)}
        self._save(agents)
        return original_count - len(agents)

    def find_by_name(self, name: str) -> List[AgentInfo]:
        """Find agents by name."""
        return [a for a in self._load().values() if a.name == name]

    def find_by_hostname(self, hostname: str) -> List[AgentInfo]:
        """Find agents by hostname."""
        return [a for a in self._load().values() if a.hostname == hostname]

    def clear(self) -> int:
        """Remove all agents. Returns count removed."""
        agents = self._load()
        count = len(agents)
        self._save({})
        return count


# Module-level singleton for convenience
_default_registry: Optional[AgentRegistry] = None


def get_registry() -> AgentRegistry:
    """Get the default registry instance."""
    global _default_registry
    if _default_registry is None:
        _default_registry = AgentRegistry()
    return _default_registry
