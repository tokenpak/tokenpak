"""Tests for TokenPakOllama — uses mocks, no live Ollama required."""

from unittest.mock import MagicMock, patch

import pytest
from tokenpak_local.utils import Block, TokenPak

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pack(instructions="Answer based on the evidence.", num_blocks=2):
    pack = TokenPak(instructions=instructions)
    for i in range(num_blocks):
        pack.add(Block(type="evidence", content=f"Document {i}: some content here"))
    return pack


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTokenPakOllamaInit:
    def test_raises_without_ollama(self):
        with patch.dict("sys.modules", {"ollama": None}):
            # Force re-import
            import importlib

            import tokenpak_local.ollama as mod

            importlib.reload(mod)
            mod._OLLAMA_AVAILABLE = False
            with pytest.raises(ImportError, match="ollama package"):
                mod.TokenPakOllama()

    def test_init_creates_client(self):
        mock_ollama = MagicMock()
        mock_client = MagicMock()
        mock_ollama.Client.return_value = mock_client

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            import importlib

            import tokenpak_local.ollama as mod

            importlib.reload(mod)
            mod._OLLAMA_AVAILABLE = True
            mod._ollama_sdk = mock_ollama

            client = mod.TokenPakOllama(host="http://localhost:11434")
            mock_ollama.Client.assert_called_once_with(host="http://localhost:11434")


class TestTokenPakOllamaChat:
    def _make_client(self):
        """Return a TokenPakOllama with a mocked underlying ollama client."""
        mock_ollama = MagicMock()
        mock_inner = MagicMock()
        mock_ollama.Client.return_value = mock_inner
        mock_inner.show.return_value = {}  # Returns empty, falls back to registry

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            import importlib

            import tokenpak_local.ollama as mod

            importlib.reload(mod)
            mod._OLLAMA_AVAILABLE = True
            mod._ollama_sdk = mock_ollama
            client = mod.TokenPakOllama()
            client._client = mock_inner
            return client, mock_inner

    def test_chat_calls_ollama_client(self):
        client, mock_inner = self._make_client()
        pack = _make_pack()
        mock_inner.chat.return_value = {"message": {"content": "response"}}

        result = client.chat(model="llama3", tokenpak=pack)

        mock_inner.chat.assert_called_once()
        call_kwargs = mock_inner.chat.call_args[1]
        assert call_kwargs["model"] == "llama3"
        assert isinstance(call_kwargs["messages"], list)

    def test_chat_injects_system_message(self):
        client, mock_inner = self._make_client()
        pack = _make_pack(instructions="Be helpful.")
        mock_inner.chat.return_value = {"message": {"content": "ok"}}

        client.chat(model="llama3", tokenpak=pack)

        call_kwargs = mock_inner.chat.call_args[1]
        messages = call_kwargs["messages"]
        assert any(m.get("role") == "system" for m in messages)
        system = next(m for m in messages if m.get("role") == "system")
        assert "Be helpful." in system["content"]

    def test_chat_auto_sets_budget(self):
        client, mock_inner = self._make_client()
        pack = _make_pack()
        assert pack.budget is None  # Not set initially
        mock_inner.chat.return_value = {"message": {"content": "ok"}}

        client.chat(model="llama3", tokenpak=pack)

        # Budget should now be set (llama3 = 8192, 75% = 6144)
        assert pack.budget == 6144

    def test_chat_respects_existing_budget(self):
        client, mock_inner = self._make_client()
        pack = _make_pack()
        pack.budget = 2000  # Pre-set
        mock_inner.chat.return_value = {"message": {"content": "ok"}}

        client.chat(model="llama3", tokenpak=pack)

        assert pack.budget == 2000  # Should not be overridden

    def test_chat_without_tokenpak(self):
        client, mock_inner = self._make_client()
        mock_inner.chat.return_value = {"message": {"content": "hi"}}

        client.chat(model="llama3", messages=[{"role": "user", "content": "Hello"}])

        call_kwargs = mock_inner.chat.call_args[1]
        messages = call_kwargs["messages"]
        assert messages == [{"role": "user", "content": "Hello"}]

    def test_chat_appends_extra_messages(self):
        client, mock_inner = self._make_client()
        pack = _make_pack()
        mock_inner.chat.return_value = {"message": {"content": "ok"}}

        client.chat(
            model="llama3",
            tokenpak=pack,
            messages=[{"role": "user", "content": "Question?"}],
        )

        call_kwargs = mock_inner.chat.call_args[1]
        messages = call_kwargs["messages"]
        assert messages[-1] == {"role": "user", "content": "Question?"}

    def test_chat_stream_passthrough(self):
        client, mock_inner = self._make_client()
        mock_inner.chat.return_value = iter([{"message": {"content": "chunk"}}])
        pack = _make_pack()

        client.chat(model="llama3", tokenpak=pack, stream=True)

        call_kwargs = mock_inner.chat.call_args[1]
        assert call_kwargs["stream"] is True


class TestBudgetFor:
    def _make_client(self):
        mock_ollama = MagicMock()
        mock_inner = MagicMock()
        mock_ollama.Client.return_value = mock_inner
        mock_inner.show.return_value = {}

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            import importlib

            import tokenpak_local.ollama as mod

            importlib.reload(mod)
            mod._OLLAMA_AVAILABLE = True
            mod._ollama_sdk = mock_ollama
            client = mod.TokenPakOllama()
            client._client = mock_inner
            return client

    def test_budget_for_llama3(self):
        client = self._make_client()
        assert client.budget_for("llama3") == 6144

    def test_budget_for_phi3(self):
        client = self._make_client()
        assert client.budget_for("phi3") == 3072

    def test_budget_info_dict(self):
        client = self._make_client()
        info = client.budget_info("llama3")
        assert "context_length" in info
        assert "input_budget" in info
        assert info["input_budget"] == 6144


class TestContextDetection:
    def _make_client_with_show(self, show_return):
        mock_ollama = MagicMock()
        mock_inner = MagicMock()
        mock_ollama.Client.return_value = mock_inner
        mock_inner.show.return_value = show_return

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            import importlib

            import tokenpak_local.ollama as mod

            importlib.reload(mod)
            mod._OLLAMA_AVAILABLE = True
            mod._ollama_sdk = mock_ollama
            client = mod.TokenPakOllama(auto_detect_context=True)
            client._client = mock_inner
            return client

    def test_context_from_api_dict(self):
        client = self._make_client_with_show({"context_length": 16384})
        ctx = client._get_context_length("some-model")
        assert ctx == 16384

    def test_context_from_registry_fallback(self):
        client = self._make_client_with_show({})  # No context_length in response
        ctx = client._get_context_length("llama3")
        assert ctx == 8192  # From registry

    def test_context_cached(self):
        client = self._make_client_with_show({"context_length": 4096})
        client._get_context_length("cached-model")
        client._get_context_length("cached-model")
        # show() called only once
        assert client._client.show.call_count == 1
