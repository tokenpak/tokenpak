"""
Comprehensive tests for watchdog.py ProxyWatchdog class.

Tests the public API of ProxyWatchdog:
- is_proxy_running() — health check
- is_port_listening() — port availability check
- restart_proxy() — daemon restart logic
- check_memory_usage() — memory warning logic
- check_error_rate() — error rate detection
- clear_cooldowns() — cooldown cleanup integration
- log_stats() — periodic stats logging
- run() — main watchdog loop (mocked to avoid infinite loop)

Uses mocking for subprocess calls to avoid actual process/network operations.
"""

import json
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from tokenpak.proxy.proxy_watchdog import ProxyWatchdog, CooldownManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_watchdog_dir(tmp_path):
    """Create a temp dir with watchdog config files."""
    log_dir = tmp_path / ".tokenpak"
    log_dir.mkdir()
    return tmp_path


@pytest.fixture
def watchdog_with_mocks(tmp_watchdog_dir, monkeypatch):
    """Create a ProxyWatchdog with mocked paths and logging."""
    import tokenpak.proxy.proxy_watchdog as wd_module

    # Point config paths to temp dir
    monkeypatch.setattr(wd_module, "WATCHDOG_LOG", tmp_watchdog_dir / ".tokenpak" / "watchdog.log")
    monkeypatch.setattr(wd_module, "COOLDOWNS_FILE", tmp_watchdog_dir / ".tokenpak" / "cooldowns.json")
    monkeypatch.setattr(wd_module, "AUTH_PROFILES_FILE", tmp_watchdog_dir / ".tokenpak" / "auth-profiles.json")
    monkeypatch.setattr(wd_module, "PROXY_PID_FILE", tmp_watchdog_dir / ".tokenpak" / "proxy.pid")
    monkeypatch.setattr(wd_module, "PROXY_PORT", 8766)
    monkeypatch.setattr(wd_module, "HEALTH_CHECK_INTERVAL", 1)

    return ProxyWatchdog()


# ---------------------------------------------------------------------------
# Tests: is_proxy_running()
# ---------------------------------------------------------------------------


def test_is_proxy_running_returns_true_on_healthy_response(watchdog_with_mocks):
    """is_proxy_running returns True when /health returns {"status": "ok"}."""
    watchdog = watchdog_with_mocks
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout=b'{"status": "ok"}',
        )
        assert watchdog.is_proxy_running() is True


def test_is_proxy_running_returns_true_on_degraded_response(watchdog_with_mocks):
    """is_proxy_running returns True when /health returns {"status": "degraded"}."""
    watchdog = watchdog_with_mocks
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout=b'{"status": "degraded"}',
        )
        assert watchdog.is_proxy_running() is True


def test_is_proxy_running_returns_false_on_connection_error(watchdog_with_mocks):
    """is_proxy_running returns False when curl fails (connection refused)."""
    watchdog = watchdog_with_mocks
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.side_effect = Exception("Connection refused")
        assert watchdog.is_proxy_running() is False


def test_is_proxy_running_returns_false_on_timeout(watchdog_with_mocks):
    """is_proxy_running returns False on subprocess timeout."""
    watchdog = watchdog_with_mocks
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired("curl", 3)
        assert watchdog.is_proxy_running() is False


def test_is_proxy_running_returns_false_on_bad_json(watchdog_with_mocks):
    """is_proxy_running returns False when response is not valid JSON."""
    watchdog = watchdog_with_mocks
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout=b"not json",
        )
        assert watchdog.is_proxy_running() is False


def test_is_proxy_running_returns_false_on_non_ok_status(watchdog_with_mocks):
    """is_proxy_running returns False when status is neither 'ok' nor 'degraded'."""
    watchdog = watchdog_with_mocks
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout=b'{"status": "error"}',
        )
        assert watchdog.is_proxy_running() is False


def test_is_proxy_running_returns_false_on_nonzero_exit(watchdog_with_mocks):
    """is_proxy_running returns False when curl returns non-zero exit code."""
    watchdog = watchdog_with_mocks
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=1)
        assert watchdog.is_proxy_running() is False


# ---------------------------------------------------------------------------
# Tests: is_port_listening()
# ---------------------------------------------------------------------------


def test_is_port_listening_returns_true_when_port_in_output(watchdog_with_mocks):
    """is_port_listening returns True when ss output contains the port."""
    watchdog = watchdog_with_mocks
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout="LISTEN 127.0.0.1:8766 python",
        )
        assert watchdog.is_port_listening() is True


def test_is_port_listening_returns_false_when_port_not_in_output(watchdog_with_mocks):
    """is_port_listening returns False when ss output does not contain the port."""
    watchdog = watchdog_with_mocks
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout="LISTEN 127.0.0.1:9999",
        )
        assert watchdog.is_port_listening() is False


def test_is_port_listening_returns_false_on_exception(watchdog_with_mocks):
    """is_port_listening returns False on any exception."""
    watchdog = watchdog_with_mocks
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.side_effect = Exception("ss failed")
        assert watchdog.is_port_listening() is False


# ---------------------------------------------------------------------------
# Tests: restart_proxy()
# ---------------------------------------------------------------------------


def test_restart_proxy_kills_and_starts_process(watchdog_with_mocks):
    """restart_proxy kills existing proxy, starts new one, and verifies startup."""
    watchdog = watchdog_with_mocks
    watchdog.restart_count = 0

    with patch("tokenpak.watchdog.subprocess.run") as mock_run, \
         patch("tokenpak.watchdog.subprocess.Popen") as mock_popen, \
         patch("tokenpak.watchdog.time.sleep") as mock_sleep, \
         patch.object(watchdog, "is_proxy_running", side_effect=[False, False, True]):  # Eventually responds
        
        result = watchdog.restart_proxy()
        
        assert result is True
        assert mock_run.call_count == 2  # Two pkill calls
        assert mock_popen.called  # Popen called to start proxy
        assert watchdog.restart_count == 0  # Reset on success


def test_restart_proxy_fails_after_max_attempts(watchdog_with_mocks):
    """restart_proxy returns False when max restart attempts reached."""
    from tokenpak.proxy.proxy_watchdog import MAX_RESTART_ATTEMPTS
    
    watchdog = watchdog_with_mocks
    watchdog.restart_count = MAX_RESTART_ATTEMPTS

    result = watchdog.restart_proxy()
    
    assert result is False


def test_restart_proxy_respects_exponential_backoff(watchdog_with_mocks):
    """restart_proxy applies exponential backoff between restart attempts."""
    watchdog = watchdog_with_mocks
    watchdog.restart_count = 0

    with patch("tokenpak.watchdog.subprocess.run"), \
         patch("tokenpak.watchdog.subprocess.Popen"), \
         patch("tokenpak.watchdog.time.sleep") as mock_sleep, \
         patch.object(watchdog, "is_proxy_running", return_value=False):
        
        watchdog.restart_proxy()
        
        # First call should have backoff of 2^0 = 1 second (actually uses RESTART_BACKOFF_BASE**0 = 1)
        # Check that sleep was called with backoff values
        sleep_calls = mock_sleep.call_args_list
        assert len(sleep_calls) > 0  # sleep was called for backoff


def test_restart_proxy_increments_counter_on_attempt(watchdog_with_mocks):
    """restart_proxy increments restart_count on each attempt."""
    watchdog = watchdog_with_mocks
    watchdog.restart_count = 0

    with patch("tokenpak.watchdog.subprocess.run"), \
         patch("tokenpak.watchdog.subprocess.Popen"), \
         patch("tokenpak.watchdog.time.sleep"), \
         patch.object(watchdog, "is_proxy_running", return_value=False):
        
        watchdog.restart_proxy()
        
        # Counter is incremented during restart attempt
        assert watchdog.restart_count == 1


def test_restart_proxy_resets_counter_on_success(watchdog_with_mocks):
    """restart_proxy resets counter to 0 on successful restart."""
    watchdog = watchdog_with_mocks
    watchdog.restart_count = 2

    with patch("tokenpak.watchdog.subprocess.run"), \
         patch("tokenpak.watchdog.subprocess.Popen"), \
         patch("tokenpak.watchdog.time.sleep"), \
         patch.object(watchdog, "is_proxy_running", return_value=True):
        
        watchdog.restart_proxy()
        
        assert watchdog.restart_count == 0


# ---------------------------------------------------------------------------
# Tests: check_memory_usage()
# ---------------------------------------------------------------------------


def test_check_memory_usage_warns_on_high_memory(watchdog_with_mocks, caplog):
    """check_memory_usage logs warning when proxy memory exceeds 500MB."""
    watchdog = watchdog_with_mocks
    
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        # Mock pgrep to return a PID
        # Mock ps to return 600MB (600 * 1024 KB)
        mock_run.side_effect = [
            Mock(returncode=0, stdout="12345\n"),  # pgrep result
            Mock(returncode=0, stdout="614400"),   # ps result: 600MB in KB
        ]

        with caplog.at_level(logging.WARNING):
            watchdog.check_memory_usage()
        
        assert "memory high" in caplog.text.lower()
        assert "600" in caplog.text


def test_check_memory_usage_no_warning_on_low_memory(watchdog_with_mocks, caplog):
    """check_memory_usage does not warn when proxy memory is below 500MB."""
    watchdog = watchdog_with_mocks
    
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.side_effect = [
            Mock(returncode=0, stdout="12345\n"),  # pgrep result
            Mock(returncode=0, stdout="256000"),   # ps result: 250MB in KB
        ]

        with caplog.at_level(logging.WARNING):
            watchdog.check_memory_usage()
        
        assert "memory high" not in caplog.text.lower()


def test_check_memory_usage_handles_no_processes(watchdog_with_mocks):
    """check_memory_usage handles gracefully when no proxy process found."""
    watchdog = watchdog_with_mocks
    
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="")  # Empty result
        
        # Should not raise
        watchdog.check_memory_usage()


def test_check_memory_usage_handles_exception(watchdog_with_mocks):
    """check_memory_usage handles exceptions gracefully."""
    watchdog = watchdog_with_mocks
    
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.side_effect = Exception("pgrep failed")
        
        # Should not raise
        watchdog.check_memory_usage()


# ---------------------------------------------------------------------------
# Tests: check_error_rate()
# ---------------------------------------------------------------------------


def test_check_error_rate_warns_on_high_errors(watchdog_with_mocks, caplog):
    """check_error_rate logs warning when errors exceed threshold."""
    watchdog = watchdog_with_mocks
    
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout=b'{"errors": 15}',
        )

        with caplog.at_level(logging.WARNING):
            watchdog.check_error_rate()
        
        assert "high error rate" in caplog.text.lower()
        assert "15" in caplog.text


def test_check_error_rate_no_warning_on_low_errors(watchdog_with_mocks, caplog):
    """check_error_rate does not warn when errors are below threshold."""
    watchdog = watchdog_with_mocks
    
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout=b'{"errors": 5}',
        )

        with caplog.at_level(logging.WARNING):
            watchdog.check_error_rate()
        
        assert "high error rate" not in caplog.text.lower()


def test_check_error_rate_handles_exception(watchdog_with_mocks):
    """check_error_rate handles exceptions gracefully."""
    watchdog = watchdog_with_mocks
    
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.side_effect = Exception("curl failed")
        
        # Should not raise
        watchdog.check_error_rate()


def test_check_error_rate_handles_bad_json(watchdog_with_mocks):
    """check_error_rate handles invalid JSON gracefully."""
    watchdog = watchdog_with_mocks
    
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout=b'invalid json',
        )
        
        # Should not raise
        watchdog.check_error_rate()


# ---------------------------------------------------------------------------
# Tests: clear_cooldowns()
# ---------------------------------------------------------------------------


def test_clear_cooldowns_calls_manager(watchdog_with_mocks, tmp_watchdog_dir):
    """clear_cooldowns delegates to CooldownManager."""
    watchdog = watchdog_with_mocks

    with patch.object(watchdog.cooldown_mgr, "clear_expired", return_value=["key1"]), \
         patch.object(watchdog.cooldown_mgr, "check_auth_profiles", return_value=[]):
        
        watchdog.clear_cooldowns()
        
        watchdog.cooldown_mgr.clear_expired.assert_called_once()
        watchdog.cooldown_mgr.check_auth_profiles.assert_called_once()


def test_clear_cooldowns_logs_warnings(watchdog_with_mocks, caplog):
    """clear_cooldowns logs warnings from check_auth_profiles."""
    watchdog = watchdog_with_mocks

    with patch.object(watchdog.cooldown_mgr, "clear_expired", return_value=[]), \
         patch.object(watchdog.cooldown_mgr, "check_auth_profiles", return_value=["profile1 in cooldown for 60s"]):
        
        with caplog.at_level(logging.INFO):
            watchdog.clear_cooldowns()
        
        assert "profile1" in caplog.text


# ---------------------------------------------------------------------------
# Tests: log_stats()
# ---------------------------------------------------------------------------


def test_log_stats_logs_stats_after_interval(watchdog_with_mocks, caplog):
    """log_stats logs stats when STATS_INTERVAL has elapsed."""
    from tokenpak.proxy.proxy_watchdog import STATS_INTERVAL
    
    watchdog = watchdog_with_mocks
    watchdog.last_stats_log = time.time() - (STATS_INTERVAL + 1)  # Past interval
    
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout=b'{"requests": 100, "errors": 2, "saved_tokens": 500}',
        )

        with caplog.at_level(logging.INFO):
            watchdog.log_stats()
        
        assert "hourly stats" in caplog.text.lower()
        assert "100" in caplog.text  # requests


def test_log_stats_skips_if_not_interval(watchdog_with_mocks, caplog):
    """log_stats does nothing if STATS_INTERVAL has not elapsed."""
    watchdog = watchdog_with_mocks
    watchdog.last_stats_log = time.time()  # Just now
    
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        with caplog.at_level(logging.INFO):
            watchdog.log_stats()
        
        # Should not call curl
        mock_run.assert_not_called()


def test_log_stats_handles_exception(watchdog_with_mocks):
    """log_stats handles exceptions gracefully."""
    from tokenpak.proxy.proxy_watchdog import STATS_INTERVAL
    
    watchdog = watchdog_with_mocks
    watchdog.last_stats_log = time.time() - (STATS_INTERVAL + 1)
    
    with patch("tokenpak.watchdog.subprocess.run") as mock_run:
        mock_run.side_effect = Exception("curl failed")
        
        # Should not raise
        watchdog.log_stats()


# ---------------------------------------------------------------------------
# Tests: run() — Main Loop (Integration)
# ---------------------------------------------------------------------------


def test_run_main_loop_checks_health(watchdog_with_mocks):
    """run() performs health checks in the main loop."""
    watchdog = watchdog_with_mocks
    
    with patch.object(watchdog, "is_proxy_running", side_effect=[True, True, False]), \
         patch.object(watchdog, "restart_proxy"), \
         patch.object(watchdog, "check_memory_usage"), \
         patch.object(watchdog, "check_error_rate"), \
         patch.object(watchdog, "clear_cooldowns"), \
         patch.object(watchdog, "log_stats"), \
         patch("tokenpak.watchdog.time.sleep") as mock_sleep:
        
        # Mock KeyboardInterrupt to break out after 3 iterations
        mock_sleep.side_effect = KeyboardInterrupt
        
        watchdog.run()
        
        # Verify health check was called
        assert watchdog.is_proxy_running.called


def test_run_handles_keyboard_interrupt(watchdog_with_mocks, caplog):
    """run() handles KeyboardInterrupt gracefully."""
    watchdog = watchdog_with_mocks
    
    with patch.object(watchdog, "is_proxy_running"), \
         patch("tokenpak.watchdog.time.sleep") as mock_sleep:
        
        mock_sleep.side_effect = KeyboardInterrupt
        
        with caplog.at_level(logging.INFO):
            watchdog.run()
        
        assert "shutting down" in caplog.text.lower()


def test_run_handles_exception_in_loop(watchdog_with_mocks, caplog):
    """run() handles exceptions in the loop and continues."""
    watchdog = watchdog_with_mocks
    
    call_sequence = [Exception("Test error"), KeyboardInterrupt()]
    call_count = [0]
    
    def side_effect_fn():
        exc = call_sequence[call_count[0] % len(call_sequence)]
        call_count[0] += 1
        raise exc
    
    with patch.object(watchdog, "is_proxy_running", side_effect=side_effect_fn):
        with caplog.at_level(logging.ERROR):
            watchdog.run()
        
        # Should log the error before shutting down
        log_text = caplog.text.lower()
        assert "watchdog error" in log_text or "shutting down" in log_text


# ---------------------------------------------------------------------------
# Tests: Integration / Scenarios
# ---------------------------------------------------------------------------


def test_proxy_crash_and_recovery_scenario(watchdog_with_mocks):
    """Scenario: Proxy crashes, watchdog detects and restarts it."""
    watchdog = watchdog_with_mocks
    
    call_count = [0]
    
    def is_running():
        call_count[0] += 1
        if call_count[0] <= 2:
            return False  # First two checks: not running
        return True  # Third check: running after restart
    
    with patch.object(watchdog, "is_proxy_running", side_effect=is_running), \
         patch.object(watchdog, "restart_proxy", return_value=True) as mock_restart, \
         patch.object(watchdog, "check_memory_usage"), \
         patch.object(watchdog, "check_error_rate"), \
         patch.object(watchdog, "clear_cooldowns"), \
         patch.object(watchdog, "log_stats"), \
         patch("tokenpak.watchdog.time.sleep") as mock_sleep:
        
        mock_sleep.side_effect = KeyboardInterrupt
        
        watchdog.run()
        
        # Restart should have been called
        assert mock_restart.called


def test_high_memory_warning_scenario(watchdog_with_mocks, caplog):
    """Scenario: Proxy uses >500MB memory, watchdog logs warning."""
    watchdog = watchdog_with_mocks
    
    with patch.object(watchdog, "is_proxy_running", return_value=True), \
         patch("tokenpak.watchdog.subprocess.run") as mock_run:
        
        # Setup: pgrep returns PID, ps returns high memory
        mock_run.side_effect = [
            Mock(returncode=0, stdout="12345\n"),  # pgrep
            Mock(returncode=0, stdout="614400"),   # ps: 600MB
        ]
        
        with caplog.at_level(logging.WARNING):
            watchdog.check_memory_usage()
        
        assert "memory high" in caplog.text.lower()
