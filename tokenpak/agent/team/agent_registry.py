"""TokenPak Team Agent Registry (5.3)

Tracks all team agents: name, status, capabilities, last heartbeat.
Agents call POST /v1/agents/heartbeat to register/update.
Server background thread marks stale agents.
GET /v1/agents returns current agent list.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

STALE_TIMEOUT_SECONDS = 60  # mark agent stale after this many seconds of no heartbeat


@dataclass
class AgentRecord:
    """A registered team agent."""

    name: str
    status: str = "online"  # "online" | "stale" | "offline"
    capabilities: List[str] = field(default_factory=list)
    last_heartbeat: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["seconds_since_heartbeat"] = round(time.time() - self.last_heartbeat, 1)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentRecord":
        data = {k: v for k, v in data.items() if k != "seconds_since_heartbeat"}
        return cls(**data)


class AgentRegistry:
    """Thread-safe registry for team agents.

    Persists to a JSON file. A background thread marks stale agents.

    Usage::

        registry = AgentRegistry("~/.tokenpak/team/agents.json")
        registry.register("cali", capabilities=["compression", "tools"])
        registry.heartbeat("cali")
        agents = registry.list_agents()
        registry.start_health_checker()
    """

    def __init__(
        self,
        store_path: str = ":memory:",
        stale_timeout: float = STALE_TIMEOUT_SECONDS,
    ) -> None:
        self._path = store_path
        self._stale_timeout = stale_timeout
        self._agents: Dict[str, AgentRecord] = {}
        self._lock = threading.Lock()
        self._checker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        if store_path != ":memory:":
            self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        capabilities: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentRecord:
        """Register or re-register an agent."""
        with self._lock:
            record = AgentRecord(
                name=name,
                status="online",
                capabilities=capabilities or [],
                last_heartbeat=time.time(),
                metadata=metadata or {},
            )
            self._agents[name] = record
            self._persist()
            return record

    def heartbeat(self, name: str) -> bool:
        """Update last_heartbeat for an agent; marks online if was stale.

        Returns True if agent exists, False if unknown.
        """
        with self._lock:
            if name not in self._agents:
                return False
            agent = self._agents[name]
            agent.last_heartbeat = time.time()
            agent.status = "online"
            self._persist()
            return True

    def deregister(self, name: str) -> bool:
        """Remove an agent from the registry."""
        with self._lock:
            if name not in self._agents:
                return False
            del self._agents[name]
            self._persist()
            return True

    def get(self, name: str) -> Optional[AgentRecord]:
        with self._lock:
            return self._agents.get(name)

    def list_agents(self) -> List[AgentRecord]:
        """Return all agents (with current status)."""
        with self._lock:
            return list(self._agents.values())

    def list_agents_dict(self) -> List[Dict[str, Any]]:
        """Return agents as serialisable dicts (for API responses)."""
        with self._lock:
            return [a.to_dict() for a in self._agents.values()]

    def mark_stale(self) -> List[str]:
        """Check all agents; mark stale if heartbeat has timed out.

        Returns list of agent names that were marked stale.
        """
        stale = []
        now = time.time()
        with self._lock:
            for name, agent in self._agents.items():
                if agent.status == "online" and (now - agent.last_heartbeat) > self._stale_timeout:
                    agent.status = "stale"
                    stale.append(name)
            if stale:
                self._persist()
        return stale

    # ------------------------------------------------------------------
    # Background health checker
    # ------------------------------------------------------------------

    def start_health_checker(self, interval: float = 15.0) -> None:
        """Start background thread that periodically marks stale agents."""
        if self._checker_thread and self._checker_thread.is_alive():
            return
        self._stop_event.clear()

        def _run():
            while not self._stop_event.wait(timeout=interval):
                self.mark_stale()

        self._checker_thread = threading.Thread(
            target=_run, daemon=True, name="tokenpak-agent-health-checker"
        )
        self._checker_thread.start()

    def stop_health_checker(self) -> None:
        self._stop_event.set()
        if self._checker_thread:
            self._checker_thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        if self._path == ":memory:":
            return
        path = Path(self._path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {name: agent.to_dict() for name, agent in self._agents.items()}
        path.write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        path = Path(self._path).expanduser()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for name, record_data in data.items():
                self._agents[name] = AgentRecord.from_dict(record_data)
        except (json.JSONDecodeError, KeyError, TypeError):
            self._agents = {}

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            agents = list(self._agents.values())
        return {
            "total": len(agents),
            "online": sum(1 for a in agents if a.status == "online"),
            "stale": sum(1 for a in agents if a.status == "stale"),
            "offline": sum(1 for a in agents if a.status == "offline"),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_registry: Optional[AgentRegistry] = None
_registry_lock = threading.Lock()


def get_agent_registry(store_path: str = ":memory:") -> AgentRegistry:
    """Return the process-level singleton agent registry."""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = AgentRegistry(store_path)
    return _registry
