"""Unit tests for swap pressure monitoring (TPK-PERF-SWAP-ALERT-001)."""

import os
import time
from unittest.mock import mock_open, patch

import pytest


def _import_fns():
    import importlib
    import sys
    # Clear any cached proxy module to pick up fresh state
    for k in list(sys.modules.keys()):
        if k == "proxy" or k.startswith("proxy."):
            del sys.modules[k]
    import proxy as p
    return p.get_swap_mb, p.check_swap_pressure


class TestGetSwapMb:
    def test_parses_vmswap_correctly(self):
        """VmSwap: 2048 kB → 2 MB."""
        get_swap_mb, _ = _import_fns()
        proc_content = (
            "Name:\ttokenpak\n"
            "VmRSS:\t102400 kB\n"
            "VmSwap:\t2048 kB\n"
            "Threads:\t4\n"
        )
        with patch("builtins.open", mock_open(read_data=proc_content)):
            result = get_swap_mb(pid=12345)
        assert result == 2

    def test_zero_swap(self):
        """VmSwap: 0 kB → 0 MB."""
        get_swap_mb, _ = _import_fns()
        proc_content = "VmSwap:\t0 kB\n"
        with patch("builtins.open", mock_open(read_data=proc_content)):
            result = get_swap_mb(pid=12345)
        assert result == 0

    def test_missing_proc_returns_zero(self):
        """If /proc/{pid}/status doesn't exist, return 0 gracefully."""
        get_swap_mb, _ = _import_fns()
        with patch("builtins.open", side_effect=OSError("no such file")):
            result = get_swap_mb(pid=99999)
        assert result == 0

    def test_large_swap_value(self):
        """VmSwap: 1048576 kB → 1024 MB."""
        get_swap_mb, _ = _import_fns()
        proc_content = "VmSwap:\t1048576 kB\n"
        with patch("builtins.open", mock_open(read_data=proc_content)):
            result = get_swap_mb(pid=1)
        assert result == 1024


class TestCheckSwapPressure:
    def test_above_threshold_triggers_warning(self, caplog):
        """When swap > threshold, a warning is logged."""
        import logging
        get_swap_mb, check_swap_pressure = _import_fns()

        import proxy as p
        p._last_swap_warn = 0.0  # reset cooldown

        with patch.object(p, "get_swap_mb", return_value=800):
            with caplog.at_level(logging.WARNING, logger="tokenpak"):
                result = check_swap_pressure()

        assert result == 800
        assert any("High swap pressure" in r.message for r in caplog.records)
        assert any("800" in r.message for r in caplog.records)

    def test_below_threshold_no_warning(self, caplog):
        """When swap < threshold, no warning is logged."""
        import logging
        _, check_swap_pressure = _import_fns()
        import proxy as p

        with patch.object(p, "get_swap_mb", return_value=100):
            with caplog.at_level(logging.WARNING, logger="tokenpak"):
                result = check_swap_pressure()

        assert result == 100
        assert not any("swap" in r.message.lower() for r in caplog.records)

    def test_warning_rate_limited(self, caplog):
        """Repeated calls above threshold only warn once per interval."""
        import logging
        _, check_swap_pressure = _import_fns()
        import proxy as p

        p._last_swap_warn = 0.0  # reset

        with patch.object(p, "get_swap_mb", return_value=700):
            with caplog.at_level(logging.WARNING, logger="tokenpak"):
                check_swap_pressure()  # first call — should warn
                first_warn_count = len(caplog.records)
                check_swap_pressure()  # second call — should be suppressed
                second_warn_count = len(caplog.records)

        assert first_warn_count >= 1
        assert second_warn_count == first_warn_count  # no additional warning
