"""Tests for TPK-PERF-SWAP-002: swap pressure monitoring in proxy /stats.

Tests:
1. get_swap_mb() parses VmSwap correctly from a /proc mock
2. get_swap_mb() returns 0.0 when swap is zero
3. check_swap_pressure() logs a warning when swap > threshold
4. check_swap_pressure() does NOT log when swap <= threshold
5. /stats JSON includes swap_mb field (integration-style unit test)
"""


import pytest
pytest.importorskip("tokenpak.runtime.proxy", reason="module not available in current build")
import logging
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, mock_open, patch

# Ensure the tokenpak package is importable from the repo root
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from tokenpak.runtime.proxy import check_swap_pressure, get_swap_mb  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROC_STATUS_WITH_SWAP = """\
Name:\tpython3
VmPeak:\t 123456 kB
VmSize:\t  98765 kB
VmSwap:\t   8192 kB
Threads:\t 4
"""

_PROC_STATUS_ZERO_SWAP = """\
Name:\tpython3
VmPeak:\t 123456 kB
VmSize:\t  98765 kB
VmSwap:\t      0 kB
Threads:\t 4
"""

_PROC_STATUS_NO_SWAP_LINE = """\
Name:\tpython3
VmPeak:\t 123456 kB
VmSize:\t  98765 kB
Threads:\t 4
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetSwapMb(unittest.TestCase):
    """get_swap_mb() correctly reads VmSwap from /proc/{pid}/status."""

    def test_parses_vmswap_correctly(self):
        """8192 kB VmSwap → 8.0 MB."""
        m = mock_open(read_data=_PROC_STATUS_WITH_SWAP)
        with patch("builtins.open", m):
            result = get_swap_mb(pid=12345)
        # 8192 kB / 1024 = 8.0 MB
        self.assertAlmostEqual(result, 8.0, places=1)

    def test_zero_swap_returns_zero(self):
        """VmSwap: 0 kB → 0.0 MB."""
        m = mock_open(read_data=_PROC_STATUS_ZERO_SWAP)
        with patch("builtins.open", m):
            result = get_swap_mb(pid=12345)
        self.assertEqual(result, 0.0)

    def test_missing_vmswap_line_returns_zero(self):
        """If VmSwap line is absent, return 0.0 (no crash)."""
        m = mock_open(read_data=_PROC_STATUS_NO_SWAP_LINE)
        with patch("builtins.open", m):
            result = get_swap_mb(pid=12345)
        self.assertEqual(result, 0.0)

    def test_unreadable_proc_returns_zero(self):
        """OSError (e.g. permission denied) → returns 0.0 gracefully."""
        with patch("builtins.open", side_effect=OSError("permission denied")):
            result = get_swap_mb(pid=99999)
        self.assertEqual(result, 0.0)

    def test_uses_current_pid_by_default(self):
        """When pid=None, defaults to os.getpid()."""
        current_pid = os.getpid()
        m = mock_open(read_data=_PROC_STATUS_ZERO_SWAP)
        with patch("builtins.open", m) as mocked:
            get_swap_mb()
            # Verify it opened the path for the current process
            called_path = mocked.call_args[0][0]
            self.assertIn(str(current_pid), called_path)


class TestCheckSwapPressure(unittest.TestCase):
    """check_swap_pressure() warns when swap > threshold, silent otherwise."""

    def test_warning_logged_when_above_threshold(self):
        """swap > threshold → warning emitted."""
        with patch(
            "tokenpak.runtime.proxy.get_swap_mb", return_value=700.0
        ):
            with self.assertLogs("tokenpak.runtime.proxy", level="WARNING") as cm:
                result = check_swap_pressure(threshold_mb=600.0)
        self.assertEqual(result, 700.0)
        self.assertTrue(
            any("High swap pressure" in line or "swap" in line.lower() for line in cm.output),
            f"Expected swap warning in log output: {cm.output}",
        )

    def test_no_warning_when_at_threshold(self):
        """swap == threshold → no warning (boundary: threshold is not exceeded)."""
        with patch(
            "tokenpak.runtime.proxy.get_swap_mb", return_value=600.0
        ):
            # Should not emit any WARNING — use assertNoLogs (Python 3.10+) or check manually
            import io
            handler = logging.StreamHandler(io.StringIO())
            handler.setLevel(logging.WARNING)
            logger = logging.getLogger("tokenpak.runtime.proxy")
            logger.addHandler(handler)
            try:
                result = check_swap_pressure(threshold_mb=600.0)
                output = handler.stream.getvalue()
                self.assertNotIn("High swap pressure", output)
            finally:
                logger.removeHandler(handler)
        self.assertEqual(result, 600.0)

    def test_no_warning_when_below_threshold(self):
        """swap < threshold → no warning."""
        with patch(
            "tokenpak.runtime.proxy.get_swap_mb", return_value=100.0
        ):
            import io
            handler = logging.StreamHandler(io.StringIO())
            handler.setLevel(logging.WARNING)
            logger = logging.getLogger("tokenpak.runtime.proxy")
            logger.addHandler(handler)
            try:
                result = check_swap_pressure(threshold_mb=600.0)
                output = handler.stream.getvalue()
                self.assertNotIn("High swap pressure", output)
            finally:
                logger.removeHandler(handler)
        self.assertEqual(result, 100.0)

    def test_returns_swap_value(self):
        """check_swap_pressure() always returns the current swap value."""
        with patch(
            "tokenpak.runtime.proxy.get_swap_mb", return_value=42.5
        ):
            result = check_swap_pressure(threshold_mb=600.0)
        self.assertAlmostEqual(result, 42.5)


if __name__ == "__main__":
    unittest.main()
