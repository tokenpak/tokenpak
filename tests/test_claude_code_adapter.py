"""Unit tests for the Claude Code registry adapter (CCA-01)."""
import os
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.registry.claude_code.adapter import ClaudeCodeAdapter
from tokenpak.registry.claude_code.config import ClaudeCodeConfig
from tokenpak.registry.claude_code.health import check_proxy_health
from tokenpak.registry.claude_code.launcher import build_launch_env


# ---------------------------------------------------------------------------
# ClaudeCodeConfig
# ---------------------------------------------------------------------------


class TestClaudeCodeConfig:
    def test_defaults(self):
        cfg = ClaudeCodeConfig()
        assert cfg.proxy_host == "127.0.0.1"
        assert cfg.proxy_port == 8766
        assert cfg.inject_budget == 4096
        assert cfg.min_query_tokens == 10
        assert cfg.enable_tool_search is True

    def test_proxy_url_default(self):
        cfg = ClaudeCodeConfig()
        assert cfg.proxy_url == "http://127.0.0.1:8766"

    def test_proxy_url_custom(self):
        cfg = ClaudeCodeConfig(proxy_host="10.0.0.1", proxy_port=9000)
        assert cfg.proxy_url == "http://10.0.0.1:9000"

    def test_custom_values(self):
        cfg = ClaudeCodeConfig(
            proxy_host="proxy.internal",
            proxy_port=4321,
            inject_budget=2048,
            min_query_tokens=5,
            enable_tool_search=False,
        )
        assert cfg.proxy_host == "proxy.internal"
        assert cfg.proxy_port == 4321
        assert cfg.inject_budget == 2048
        assert cfg.min_query_tokens == 5
        assert cfg.enable_tool_search is False


# ---------------------------------------------------------------------------
# ClaudeCodeAdapter
# ---------------------------------------------------------------------------


class TestClaudeCodeAdapter:
    def test_instantiation_default_config(self):
        adapter = ClaudeCodeAdapter()
        assert isinstance(adapter.config, ClaudeCodeConfig)
        assert adapter.ADAPTER_NAME == "claude-code"
        assert adapter.PLATFORM_TAG == "claude-code"

    def test_instantiation_custom_config(self):
        cfg = ClaudeCodeConfig(proxy_port=9999)
        adapter = ClaudeCodeAdapter(config=cfg)
        assert adapter.config.proxy_port == 9999

    def test_build_env_contains_base_url(self):
        cfg = ClaudeCodeConfig(proxy_host="127.0.0.1", proxy_port=8766)
        adapter = ClaudeCodeAdapter(config=cfg)
        env = adapter.build_env()
        assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8766"

    def test_build_env_enable_tool_search_true(self):
        cfg = ClaudeCodeConfig(enable_tool_search=True)
        adapter = ClaudeCodeAdapter(config=cfg)
        env = adapter.build_env()
        assert env["ENABLE_TOOL_SEARCH"] == "true"

    def test_build_env_enable_tool_search_false(self):
        cfg = ClaudeCodeConfig(enable_tool_search=False)
        adapter = ClaudeCodeAdapter(config=cfg)
        env = adapter.build_env()
        assert "ENABLE_TOOL_SEARCH" not in env

    def test_build_env_inject_budget(self):
        cfg = ClaudeCodeConfig(inject_budget=2048)
        adapter = ClaudeCodeAdapter(config=cfg)
        env = adapter.build_env()
        assert env["TOKENPAK_CC_INJECT_MAX_CHARS"] == "2048"

    def test_platform_info_keys(self):
        adapter = ClaudeCodeAdapter()
        info = adapter.platform_info
        assert "os" in info
        assert "python" in info
        assert info["adapter"] == "claude-code"

    def test_platform_info_is_copy(self):
        """Mutating the returned dict must not affect the adapter's internal state."""
        adapter = ClaudeCodeAdapter()
        info = adapter.platform_info
        info["os"] = "TAMPERED"
        assert adapter.platform_info["os"] != "TAMPERED"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestCheckProxyHealth:
    def test_healthy_proxy(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            healthy, msg = check_proxy_health(ClaudeCodeConfig())

        assert healthy is True
        assert msg == "ok"

    def test_unhealthy_proxy_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            healthy, msg = check_proxy_health(ClaudeCodeConfig())

        assert healthy is False
        assert "503" in msg

    def test_proxy_connection_refused(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            healthy, msg = check_proxy_health(ClaudeCodeConfig())

        assert healthy is False
        assert "Connection refused" in msg

    def test_proxy_http_error(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url=None, code=500, msg="Internal Server Error", hdrs=None, fp=None
            ),
        ):
            healthy, msg = check_proxy_health(ClaudeCodeConfig())

        assert healthy is False
        assert "500" in msg


# ---------------------------------------------------------------------------
# Launcher — build_launch_env
# ---------------------------------------------------------------------------


class TestBuildLaunchEnv:
    def test_inherits_os_env(self):
        cfg = ClaudeCodeConfig()
        env = build_launch_env(cfg)
        assert "PATH" in env or len(env) > 0

    def test_sets_anthropic_base_url(self):
        cfg = ClaudeCodeConfig(proxy_host="127.0.0.1", proxy_port=8766)
        env = build_launch_env(cfg)
        assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8766"

    def test_sets_enable_tool_search(self):
        cfg = ClaudeCodeConfig(enable_tool_search=True)
        env = build_launch_env(cfg)
        assert env["ENABLE_TOOL_SEARCH"] == "true"

    def test_sets_inject_budget(self):
        cfg = ClaudeCodeConfig(inject_budget=1234)
        env = build_launch_env(cfg)
        assert env["TOKENPAK_CC_INJECT_MAX_CHARS"] == "1234"

    def test_does_not_mutate_os_environ(self):
        original = os.environ.copy()
        cfg = ClaudeCodeConfig()
        build_launch_env(cfg)
        assert os.environ == original


# ---------------------------------------------------------------------------
# End-to-end: launch function (mocked exec)
# ---------------------------------------------------------------------------


class TestLaunch:
    def test_launch_execs_claude(self):
        from tokenpak.registry.claude_code.launcher import launch

        with patch("tokenpak.registry.claude_code.launcher.check_proxy_health", return_value=(True, "ok")), \
             patch("os.execvpe") as mock_exec:
            launch(args=["--print", "hello"])
            mock_exec.assert_called_once()
            cmd_args = mock_exec.call_args[0][1]
            assert cmd_args == ["claude", "--print", "hello"]
            env = mock_exec.call_args[0][2]
            assert "ANTHROPIC_BASE_URL" in env

    def test_launch_warns_if_proxy_down(self, capsys):
        from tokenpak.registry.claude_code.launcher import launch

        with patch("tokenpak.registry.claude_code.launcher.check_proxy_health", return_value=(False, "Connection refused")), \
             patch("os.execvpe"):
            launch()
            captured = capsys.readouterr()
            assert "not responding" in captured.err

    def test_launch_falls_back_to_subprocess(self):
        from tokenpak.registry.claude_code.launcher import launch

        with patch("tokenpak.registry.claude_code.launcher.check_proxy_health", return_value=(True, "ok")), \
             patch("os.execvpe", side_effect=OSError("not supported")), \
             patch("subprocess.run") as mock_run:
            launch(args=["--print", "test"])
            mock_run.assert_called_once()
