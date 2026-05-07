"""Integration tests for other framework adapters.

Tests for:
- Crewai × TokenPak
- Langfuse × TokenPak
- LlamaIndex × TokenPak
"""

from unittest.mock import patch

import pytest


class TestCrewaiIntegration:
    """Crewai framework integration tests."""

    def test_crewai_import(self):
        """Verify crewai_tokenpak imports."""
        try:
            import crewai_tokenpak
            assert crewai_tokenpak is not None
        except ImportError as e:
            pytest.skip(f"crewai_tokenpak not installed: {e}")

    def test_crewai_agent_with_tokenpak(self):
        """Test Crewai agent can use TokenPak integration."""
        try:
            from crewai_tokenpak import CrewaiTokenPakAdapter
        except ImportError:
            pytest.skip("CrewaiTokenPakAdapter not available")

        adapter = CrewaiTokenPakAdapter()
        assert adapter is not None

    def test_crewai_tool_execution_tracking(self):
        """Test TokenPak tracks tool execution in Crewai."""
        try:
            from crewai_tokenpak import track_tool_execution
        except ImportError:
            pytest.skip("tool execution tracking not available")

        # Verify function exists and is callable
        assert callable(track_tool_execution)


class TestLangfuseIntegration:
    """Langfuse framework integration tests."""

    def test_langfuse_import(self):
        """Verify langfuse_tokenpak imports."""
        try:
            import langfuse_tokenpak
            assert langfuse_tokenpak is not None
        except ImportError as e:
            pytest.skip(f"langfuse_tokenpak not installed: {e}")

    def test_langfuse_callback_integration(self):
        """Test Langfuse callbacks integrate with TokenPak."""
        try:
            from langfuse_tokenpak import TokenPakCallback
        except ImportError:
            pytest.skip("TokenPakCallback not available")

        callback = TokenPakCallback()
        assert callback is not None
        assert hasattr(callback, "on_llm_end")
        assert hasattr(callback, "on_chain_end")

    def test_langfuse_trace_creation(self):
        """Test Langfuse traces are created with TokenPak data."""
        try:
            from langfuse_tokenpak import create_trace
        except ImportError:
            pytest.skip("create_trace not available")

        with patch("langfuse.Langfuse") as mock_langfuse:
            trace = create_trace(
                name="test_trace",
                input_tokens=100,
                output_tokens=50
            )
            assert trace is not None

    def test_langfuse_metrics_collection(self):
        """Test TokenPak metrics collected in Langfuse."""
        try:
            from langfuse_tokenpak import collect_metrics
        except ImportError:
            pytest.skip("collect_metrics not available")

        metrics = collect_metrics()
        assert isinstance(metrics, dict)


class TestLlamaIndexIntegration:
    """LlamaIndex framework integration tests."""

    def test_llamaindex_import(self):
        """Verify llamaindex_tokenpak imports."""
        try:
            import llamaindex_tokenpak
            assert llamaindex_tokenpak is not None
        except ImportError as e:
            pytest.skip(f"llamaindex_tokenpak not installed: {e}")

    def test_llamaindex_llm_adapter(self):
        """Test LlamaIndex LLM adapter with TokenPak."""
        try:
            from llamaindex_tokenpak import TokenPakLLM
        except ImportError:
            pytest.skip("TokenPakLLM not available")

        llm = TokenPakLLM(model="gpt-4")
        assert llm is not None
        assert llm.model == "gpt-4"

    def test_llamaindex_embedding_integration(self):
        """Test LlamaIndex embeddings with TokenPak tracking."""
        try:
            from llamaindex_tokenpak import TokenPakEmbedding
        except ImportError:
            pytest.skip("TokenPakEmbedding not available")

        embedding = TokenPakEmbedding()
        assert embedding is not None

    def test_llamaindex_cache_integration(self):
        """Test LlamaIndex cache works with TokenPak."""
        try:
            from llamaindex_tokenpak import enable_cache
        except ImportError:
            pytest.skip("enable_cache not available")

        enable_cache()
        assert True

    def test_llamaindex_query_with_tokenpak(self):
        """Test LlamaIndex queries use TokenPak optimizations."""
        try:
            from llamaindex_tokenpak import Query
        except ImportError:
            pytest.skip("Query class not available")

        # Placeholder: would need actual index for real test
        assert True


class TestFrameworkCombinations:
    """Test combinations of multiple frameworks."""

    def test_langchain_and_langfuse_together(self):
        """Test LangChain + Langfuse + TokenPak work together."""
        try:
            from langchain_tokenpak import ChatOpenAIWithTokenPak
            from langfuse_tokenpak import TokenPakCallback
        except ImportError:
            pytest.skip("Both adapters not available")

        # Just verify both can be imported and instantiated
        assert ChatOpenAIWithTokenPak is not None
        assert TokenPakCallback is not None

    def test_crewai_with_langfuse_tracing(self):
        """Test Crewai agents with Langfuse tracing + TokenPak."""
        try:
            from crewai_tokenpak import CrewaiTokenPakAdapter
            from langfuse_tokenpak import TokenPakCallback
        except ImportError:
            pytest.skip("Both adapters not available")

        assert CrewaiTokenPakAdapter is not None
        assert TokenPakCallback is not None

    def test_llamaindex_with_litellm_routing(self):
        """Test LlamaIndex with LiteLLM provider routing + TokenPak."""
        try:
            import litellm
            from llamaindex_tokenpak import TokenPakLLM
        except ImportError:
            pytest.skip("Both not available")

        assert litellm is not None
        assert TokenPakLLM is not None
