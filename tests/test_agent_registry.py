"""
Tests for agent registry + capability matching.
"""


import pytest
pytest.importorskip("tokenpak.agentic.capabilities", reason="module not available in current build")
import json
import os
import pytest
import tempfile
import time
from pathlib import Path

from tokenpak.agentic.registry import (
    AgentRegistry,
    AgentInfo,
    get_registry,
    DEFAULT_EXPIRE_SECONDS,
)
from tokenpak.agentic.capabilities import (
    CapabilityMatcher,
    AgentCapabilities,
    TaskRequirements,
    MatchResult,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_registry():
    """Create a registry with a temp file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "agents.json"
        yield AgentRegistry(path=path)


@pytest.fixture
def populated_registry(temp_registry):
    """Registry with 3 pre-registered agents."""
    temp_registry.register("agent-1", "host-1", {
        "gpu": False,
        "memory_gb": 4,
        "specialties": ["code", "execution"],
        "provider_access": ["anthropic", "openai"],
        "max_concurrent": 1,
    })
    temp_registry.register("agent-2", "host-2", {
        "gpu": False,
        "memory_gb": 8,
        "specialties": ["orchestration", "qa"],
        "provider_access": ["anthropic"],
        "max_concurrent": 2,
    })
    temp_registry.register("agent-3", "host-3", {
        "gpu": True,
        "memory_gb": 16,
        "specialties": ["data", "analysis", "code"],
        "provider_access": ["anthropic", "openai", "google"],
        "max_concurrent": 1,
    })
    return temp_registry


# ─────────────────────────────────────────────────────────────────────────────
# AgentInfo Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentInfo:
    def test_to_dict(self):
        info = AgentInfo(
            agent_id="abc123",
            name="trix",
            hostname="host-1",
            capabilities={"gpu": True},
        )
        d = info.to_dict()
        assert d["agent_id"] == "abc123"
        assert d["name"] == "trix"
        assert d["hostname"] == "host-1"
        assert d["capabilities"]["gpu"] is True

    def test_from_dict(self):
        d = {
            "agent_id": "xyz",
            "name": "sue",
            "hostname": "host-2",
            "capabilities": {},
            "registered_at": 1000.0,
            "last_heartbeat": 1000.0,
            "status": "active",
            "current_task": None,
            "metadata": {},
        }
        info = AgentInfo.from_dict(d)
        assert info.agent_id == "xyz"
        assert info.name == "sue"

    def test_is_stale(self):
        info = AgentInfo(
            agent_id="test",
            name="test",
            hostname="test",
            last_heartbeat=time.time() - 3600,  # 1 hour ago
        )
        assert info.is_stale(expire_seconds=1800)  # 30 min
        assert not info.is_stale(expire_seconds=7200)  # 2 hours

    def test_heartbeat_age_seconds(self):
        now = time.time()
        info = AgentInfo(
            agent_id="test",
            name="test",
            hostname="test",
            last_heartbeat=now - 120,
        )
        age = info.heartbeat_age_seconds()
        assert 119 < age < 122  # Allow some timing slack


# ─────────────────────────────────────────────────────────────────────────────
# AgentRegistry Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentRegistry:
    def test_register_new_agent(self, temp_registry):
        agent_id = temp_registry.register("agent-1", "host-1", {"gpu": False})
        assert agent_id is not None
        assert len(agent_id) == 8

    def test_register_returns_same_id_for_same_name_host(self, temp_registry):
        id1 = temp_registry.register("agent-1", "host-1")
        id2 = temp_registry.register("agent-1", "host-1")
        assert id1 == id2

    def test_register_different_id_for_different_host(self, temp_registry):
        id1 = temp_registry.register("agent-1", "host-1")
        id2 = temp_registry.register("agent-1", "otherhost")
        assert id1 != id2

    def test_get_agent(self, temp_registry):
        agent_id = temp_registry.register("agent-2", "host-2", {"memory_gb": 8})
        agent = temp_registry.get(agent_id)
        assert agent is not None
        assert agent.name == "sue"
        assert agent.hostname == "host-2"
        assert agent.capabilities["memory_gb"] == 8

    def test_get_nonexistent(self, temp_registry):
        assert temp_registry.get("nonexistent") is None

    def test_deregister(self, temp_registry):
        agent_id = temp_registry.register("test", "testhost")
        assert temp_registry.deregister(agent_id) is True
        assert temp_registry.get(agent_id) is None

    def test_deregister_nonexistent(self, temp_registry):
        assert temp_registry.deregister("nonexistent") is False

    def test_heartbeat_updates_timestamp(self, temp_registry):
        agent_id = temp_registry.register("test", "testhost")
        agent_before = temp_registry.get(agent_id)
        time.sleep(0.1)
        temp_registry.heartbeat(agent_id)
        agent_after = temp_registry.get(agent_id)
        assert agent_after.last_heartbeat > agent_before.last_heartbeat

    def test_heartbeat_updates_status(self, temp_registry):
        agent_id = temp_registry.register("test", "testhost")
        temp_registry.heartbeat(agent_id, status="busy", current_task="task-123")
        agent = temp_registry.get(agent_id)
        assert agent.status == "busy"
        assert agent.current_task == "task-123"

    def test_heartbeat_nonexistent(self, temp_registry):
        assert temp_registry.heartbeat("nonexistent") is False

    def test_list_all(self, populated_registry):
        agents = populated_registry.list_all()
        assert len(agents) == 3
        names = {a.name for a in agents}
        assert names == {"trix", "sue", "cali"}

    def test_list_active_excludes_stale(self, temp_registry):
        # Register with short expiry
        temp_registry.expire_seconds = 1
        temp_registry.register("fresh", "host1")
        time.sleep(1.5)
        temp_registry.register("new", "host2")
        
        all_agents = temp_registry.list_all()
        active = temp_registry.list_active()
        
        assert len(all_agents) == 2
        assert len(active) == 1
        assert active[0].name == "new"

    def test_prune_stale(self, temp_registry):
        temp_registry.expire_seconds = 1
        temp_registry.register("old1", "host1")
        temp_registry.register("old2", "host2")
        time.sleep(1.5)
        temp_registry.register("fresh", "host3")
        
        pruned = temp_registry.prune_stale()
        assert pruned == 2
        
        remaining = temp_registry.list_all()
        assert len(remaining) == 1
        assert remaining[0].name == "fresh"

    def test_find_by_name(self, populated_registry):
        found = populated_registry.find_by_name("trix")
        assert len(found) == 1
        assert found[0].hostname == "host-1"

    def test_find_by_hostname(self, populated_registry):
        found = populated_registry.find_by_hostname("host-3")
        assert len(found) == 1
        assert found[0].name == "cali"

    def test_clear(self, populated_registry):
        count = populated_registry.clear()
        assert count == 3
        assert len(populated_registry.list_all()) == 0

    def test_file_permissions(self, temp_registry):
        temp_registry.register("test", "testhost")
        mode = os.stat(temp_registry.path).st_mode & 0o777
        assert mode == 0o600


# ─────────────────────────────────────────────────────────────────────────────
# AgentCapabilities Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentCapabilities:
    def test_default_values(self):
        caps = AgentCapabilities()
        assert caps.gpu is False
        assert caps.memory_gb == 4.0
        assert caps.specialties == []
        assert caps.max_concurrent == 1
        assert caps.provider_access == ["anthropic"]

    def test_to_dict(self):
        caps = AgentCapabilities(gpu=True, memory_gb=16, specialties=["code"])
        d = caps.to_dict()
        assert d["gpu"] is True
        assert d["memory_gb"] == 16
        assert d["specialties"] == ["code"]

    def test_from_dict(self):
        d = {"gpu": True, "memory_gb": 8, "specialties": ["data"], "provider_access": ["openai"]}
        caps = AgentCapabilities.from_dict(d)
        assert caps.gpu is True
        assert caps.memory_gb == 8
        assert "data" in caps.specialties


# ─────────────────────────────────────────────────────────────────────────────
# CapabilityMatcher Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCapabilityMatcher:
    def test_match_no_requirements(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        matches = matcher.match(TaskRequirements())
        assert len(matches) == 3

    def test_match_requires_gpu(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        matches = matcher.match(TaskRequirements(requires_gpu=True))
        assert len(matches) == 1
        assert matches[0].agent.name == "cali"

    def test_match_min_memory(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        matches = matcher.match(TaskRequirements(min_memory_gb=8))
        assert len(matches) == 2
        names = {m.agent.name for m in matches}
        assert names == {"sue", "cali"}

    def test_match_required_specialty(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        matches = matcher.match(TaskRequirements(required_specialties=["code"]))
        assert len(matches) == 2
        names = {m.agent.name for m in matches}
        assert names == {"trix", "cali"}

    def test_match_multiple_specialties(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        matches = matcher.match(TaskRequirements(required_specialties=["code", "data"]))
        # Only cali has both
        assert len(matches) == 1
        assert matches[0].agent.name == "cali"

    def test_match_required_provider(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        matches = matcher.match(TaskRequirements(required_providers=["google"]))
        assert len(matches) == 1
        assert matches[0].agent.name == "cali"

    def test_match_sorted_by_score(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        matches = matcher.match(TaskRequirements())
        # Scores should be descending
        scores = [m.score for m in matches]
        assert scores == sorted(scores, reverse=True)

    def test_find_best(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        best = matcher.find_best(TaskRequirements(requires_gpu=True))
        assert best is not None
        assert best.name == "cali"

    def test_find_best_no_match(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        best = matcher.find_best(TaskRequirements(min_memory_gb=100))
        assert best is None

    def test_find_by_specialty(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        agents = matcher.find_by_specialty("orchestration")
        assert len(agents) == 1
        assert agents[0].name == "sue"

    def test_find_with_provider(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        agents = matcher.find_with_provider("openai")
        assert len(agents) == 2
        names = {a.name for a in agents}
        assert names == {"trix", "cali"}

    def test_match_result_to_dict(self, populated_registry):
        matcher = CapabilityMatcher(registry=populated_registry)
        matches = matcher.match(TaskRequirements())
        d = matches[0].to_dict()
        assert "agent_id" in d
        assert "name" in d
        assert "score" in d
        assert "reasons" in d

    def test_idle_preference(self, populated_registry):
        # Mark one agent as busy
        agents = populated_registry.list_all()
        trix = next(a for a in agents if a.name == "trix")
        populated_registry.heartbeat(trix.agent_id, status="busy", current_task="task-1")
        
        matcher = CapabilityMatcher(registry=populated_registry)
        matches = matcher.match(TaskRequirements(required_specialties=["code"]))
        
        # Cali (idle) should rank higher than trix (busy)
        names = [m.agent.name for m in matches]
        assert names.index("cali") < names.index("trix")


# ─────────────────────────────────────────────────────────────────────────────
# CLI Integration Tests (smoke tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentCLI:
    def test_list_no_agents(self, temp_registry, capsys, monkeypatch):
        from tokenpak.cli import cmd_agent_list
        from types import SimpleNamespace
        
        # Patch the registry path
        monkeypatch.setattr("tokenpak.agentic.registry.REGISTRY_PATH", temp_registry.path)
        
        args = SimpleNamespace(all=False, json=False)
        cmd_agent_list(args)
        
        captured = capsys.readouterr()
        assert "No registered agents" in captured.out

    def test_register_and_list(self, temp_registry, capsys, monkeypatch):
        from tokenpak.cli import cmd_agent_register, cmd_agent_list
        from types import SimpleNamespace
        
        monkeypatch.setattr("tokenpak.agentic.registry.REGISTRY_PATH", temp_registry.path)
        
        # Register
        args = SimpleNamespace(
            name="test",
            hostname="testhost",
            gpu=True,
            memory=8.0,
            specialties=["code"],
            providers=["anthropic"],
            json=False,
        )
        cmd_agent_register(args)
        
        captured = capsys.readouterr()
        assert "Registered" in captured.out
        
        # List
        args = SimpleNamespace(all=False, json=False)
        cmd_agent_list(args)
        
        captured = capsys.readouterr()
        assert "test" in captured.out
        assert "testhost" in captured.out

    def test_match_json_output(self, populated_registry, capsys, monkeypatch):
        from tokenpak.cli import cmd_agent_match
        from types import SimpleNamespace
        
        monkeypatch.setattr("tokenpak.agentic.registry.REGISTRY_PATH", populated_registry.path)
        
        args = SimpleNamespace(gpu=True, memory=None, specialty=[], provider=[], json=True)
        cmd_agent_match(args)
        
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["name"] == "cali"
