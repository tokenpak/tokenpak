"""Tests for TokenPakOpenAICompat and TokenPakLMStudio."""

from unittest.mock import MagicMock, patch

import pytest
from tokenpak_local.utils import Block, TokenPak


def _make_pack(instructions="", num_blocks=1):
    pack = TokenPak(instructions=instructions)
    for i in range(num_blocks):
        pack.add(Block(type="evidence", content=f"Evidence {i}"))
    return pack


# ---------------------------------------------------------------------------
# OpenAI compat
# ---------------------------------------------------------------------------


class TestTokenPakOpenAICompatInit:
    def test_raises_without_openai(self):
        with patch.dict("sys.modules", {"openai": None}):
            import importlib

            import tokenpak_local.openai_compat as mod

            importlib.reload(mod)
            mod._OPENAI_AVAILABLE = False
            with pytest.raises(ImportError, match="openai package"):
                mod.TokenPakOpenAICompat()

    def test_init_sets_base_url(self):
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            import importlib

            import tokenpak_local.openai_compat as mod

            importlib.reload(mod)
            mod._OPENAI_AVAILABLE = True
            mod.OpenAI = mock_openai.OpenAI
            client = mod.TokenPakOpenAICompat(base_url="http://localhost:1234/v1")
            mock_openai.OpenAI.assert_called_once()
            call_kwargs = mock_openai.OpenAI.call_args[1]
            assert call_kwargs["base_url"] == "http://localhost:1234/v1"


class TestBuildMessages:
    def _make_client(self):
        mock_openai = MagicMock()
        mock_inner = MagicMock()
        mock_openai.OpenAI.return_value = mock_inner

        with patch.dict("sys.modules", {"openai": mock_openai}):
            import importlib

            import tokenpak_local.openai_compat as mod

            importlib.reload(mod)
            mod._OPENAI_AVAILABLE = True
            mod.OpenAI = mock_openai.OpenAI
            client = mod.TokenPakOpenAICompat()
            client._client = mock_inner
            return client

    def test_tokenpak_adds_system_message(self):
        client = self._make_client()
        pack = _make_pack(instructions="Be concise.")
        msgs = client._build_messages("llama3", pack, None, None)
        assert any(m["role"] == "system" for m in msgs)

    def test_user_message_added(self):
        client = self._make_client()
        msgs = client._build_messages("llama3", None, None, "Hello?")
        assert msgs[-1] == {"role": "user", "content": "Hello?"}

    def test_extra_messages_appended(self):
        client = self._make_client()
        pack = _make_pack()
        extra = [{"role": "user", "content": "What?"}]
        msgs = client._build_messages("llama3", pack, extra, None)
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "What?"

    def test_empty_fallback_message(self):
        client = self._make_client()
        msgs = client._build_messages("llama3", None, None, None)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_budget_auto_set(self):
        client = self._make_client()
        pack = _make_pack()
        assert pack.budget is None
        client._build_messages("llama3", pack, None, None)
        assert pack.budget == 6144

    def test_complete_calls_create(self):
        client = self._make_client()
        mock_resp = MagicMock()
        client._client.chat.completions.create.return_value = mock_resp

        result = client.complete(
            model="llama3", tokenpak=_make_pack(), user_message="test"
        )

        client._client.chat.completions.create.assert_called_once()
        assert result is mock_resp

    def test_budget_for_llama3(self):
        client = self._make_client()
        assert client.budget_for("llama3") == 6144

    def test_budget_for_phi3(self):
        client = self._make_client()
        assert client.budget_for("phi3") == 3072

    def test_context_length_override(self):
        mock_openai = MagicMock()
        mock_inner = MagicMock()
        mock_openai.OpenAI.return_value = mock_inner

        with patch.dict("sys.modules", {"openai": mock_openai}):
            import importlib

            import tokenpak_local.openai_compat as mod

            importlib.reload(mod)
            mod._OPENAI_AVAILABLE = True
            mod.OpenAI = mock_openai.OpenAI
            client = mod.TokenPakOpenAICompat(context_length=16384)
            assert client.budget_for("any-model") == 12288


class TestTokenPakLMStudio:
    def _make_client(self, **kwargs):
        mock_openai = MagicMock()
        mock_inner = MagicMock()
        mock_openai.OpenAI.return_value = mock_inner

        with patch.dict("sys.modules", {"openai": mock_openai}):
            import importlib

            import tokenpak_local.lmstudio as mod

            importlib.reload(mod)
            mod._OPENAI_AVAILABLE = True
            mod.OpenAI = mock_openai.OpenAI

            import tokenpak_local.openai_compat as compat_mod

            importlib.reload(compat_mod)
            compat_mod._OPENAI_AVAILABLE = True
            compat_mod.OpenAI = mock_openai.OpenAI

            client = mod.TokenPakLMStudio(**kwargs)
            client._client = mock_inner
            return client, mock_inner

    def test_default_port(self):
        client, mock_inner = self._make_client()
        assert client._port == 1234

    def test_server_url(self):
        client, mock_inner = self._make_client()
        assert client.server_url == "http://localhost:1234"

    def test_custom_port(self):
        client, mock_inner = self._make_client(port=8080)
        assert client._port == 8080
        assert client.server_url == "http://localhost:8080"

    def test_list_models(self):
        client, mock_inner = self._make_client()
        model_a = MagicMock()
        model_a.id = "llama3"
        model_b = MagicMock()
        model_b.id = "mistral"
        mock_inner.models.list.return_value.data = [model_a, model_b]

        models = client.list_models()
        assert models == ["llama3", "mistral"]

    def test_context_length_override(self):
        client, _ = self._make_client(context_length=8192)
        assert client.budget_for("any-model") == 6144
