"""Tests for Semantic Kernel TokenPak integration."""

import pytest
from tokenpak_agents.semantic_kernel import TokenPakMemory


class TestTokenPakMemory:
    
    def test_memory_creation(self):
        """Test memory initialization."""
        memory = TokenPakMemory(budget=4000)
        assert memory.budget == 4000
    
    def test_save_and_retrieve(self):
        """Test saving and retrieving information."""
        memory = TokenPakMemory()
        
        memory.save_information("default", "research_key", "Important findings")
        retrieved = memory.retrieve_information("default", "research_key")
        
        assert retrieved == "Important findings"
    
    def test_get_stats(self):
        """Test memory statistics."""
        memory = TokenPakMemory(budget=4000)
        
        memory.save_information("default", "key1", "Value 1" * 100)
        stats = memory.get_stats()
        
        assert "total_memories" in stats
        assert "total_tokens" in stats
        assert stats["budget"] == 4000
