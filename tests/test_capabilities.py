"""Tests for tokenpak.agentic.capabilities module."""
import pytest
from tokenpak.agentic.capabilities import AgentCapabilities, CapabilityMatcher, AgentRegistry

class TestCapabilities:
    def test_agent_capabilities_exists(self):
        assert AgentCapabilities is not None
    
    def test_capability_matcher_exists(self):
        assert CapabilityMatcher is not None
    
    def test_agent_registry_exists(self):
        assert AgentRegistry is not None
    
    def test_agent_capabilities_instantiate(self):
        agent_cap = AgentCapabilities()
        assert agent_cap is not None
    
    def test_capability_matcher_instantiate(self):
        matcher = CapabilityMatcher()
        assert matcher is not None
    
    def test_agent_registry_instantiate(self):
        registry = AgentRegistry()
        assert registry is not None
    
    def test_multiple_instances(self):
        c1 = AgentCapabilities()
        c2 = AgentCapabilities()
        assert c1 is not None and c2 is not None
