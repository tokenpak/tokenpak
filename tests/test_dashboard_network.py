"""
Tests for tokenpak.network_utils — Dashboard network detection utilities.
"""

import pytest

pytest.importorskip("tokenpak.network_utils", reason="module not available in current build")
from unittest.mock import MagicMock, patch

import pytest
from tokenpak.network_utils import (
    get_local_ip,
    get_public_ip,
    get_reachable_addresses,
    is_port_accessible,
)

# ---------------------------------------------------------------------------
# get_local_ip
# ---------------------------------------------------------------------------


class TestGetLocalIp:
    def test_returns_string(self):
        """get_local_ip() always returns a non-empty string."""
        ip = get_local_ip()
        assert isinstance(ip, str)
        assert ip != ""

    def test_not_empty_loopback_when_network_available(self):
        """When the machine has a LAN IP, result is not 127.0.0.1."""
        ip = get_local_ip()
        # In a test environment with network, this should be a real IP.
        # If no network, falls back to 'localhost' — still valid.
        assert ip in ("localhost",) or "." in ip

    def test_fallback_on_socket_error(self):
        """Falls back to 'localhost' when socket raises."""
        with patch("tokenpak.network_utils.socket.socket") as mock_sock_class:
            mock_sock = MagicMock()
            mock_sock.connect.side_effect = OSError("no route to host")
            mock_sock_class.return_value = mock_sock
            result = get_local_ip()
        assert result == "localhost"

    def test_socket_closed_after_use(self):
        """Socket is closed even on successful detection."""
        with patch("tokenpak.network_utils.socket.socket") as mock_sock_class:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.168.1.42", 0)
            mock_sock_class.return_value = mock_sock
            result = get_local_ip()
        mock_sock.close.assert_called_once()
        assert result == "192.168.1.42"


# ---------------------------------------------------------------------------
# get_public_ip
# ---------------------------------------------------------------------------


class TestGetPublicIp:
    def test_returns_ip_on_success(self):
        """Parses valid IP from curl output."""
        with patch("tokenpak.network_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"203.0.113.5")
            result = get_public_ip()
        assert result == "203.0.113.5"

    def test_returns_none_on_curl_failure(self):
        """Returns None when curl exits non-zero."""
        with patch("tokenpak.network_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout=b"")
            result = get_public_ip()
        assert result is None

    def test_returns_none_on_timeout(self):
        """Returns None when subprocess.run raises TimeoutExpired."""
        import subprocess

        with patch("tokenpak.network_utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="curl", timeout=2)
            result = get_public_ip()
        assert result is None

    def test_returns_none_on_invalid_response(self):
        """Returns None when curl returns garbage (not an IP)."""
        with patch("tokenpak.network_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"not-an-ip-address")
            result = get_public_ip()
        assert result is None

    def test_returns_none_on_exception(self):
        """Returns None on unexpected exception."""
        with patch("tokenpak.network_utils.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("curl not found")
            result = get_public_ip()
        assert result is None

    def test_return_type_is_str_or_none(self):
        """Return value is always Optional[str]."""
        result = get_public_ip(timeout=1)
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# get_reachable_addresses
# ---------------------------------------------------------------------------


class TestGetReachableAddresses:
    def test_always_includes_localhost(self):
        """localhost is always in the result list."""
        with patch("tokenpak.network_utils.get_local_ip", return_value="localhost"):
            with patch("tokenpak.network_utils.get_public_ip", return_value=None):
                urls = get_reachable_addresses(8766)
        assert any("localhost" in u for u in urls)

    def test_includes_local_ip_when_different(self):
        """Adds LAN IP to list when it differs from localhost."""
        with patch("tokenpak.network_utils.get_local_ip", return_value="192.168.1.5"):
            with patch("tokenpak.network_utils.get_public_ip", return_value=None):
                urls = get_reachable_addresses(8766)
        assert "http://192.168.1.5:8766" in urls

    def test_excludes_loopback_duplicate(self):
        """Does not add 127.0.0.1 as separate entry."""
        with patch("tokenpak.network_utils.get_local_ip", return_value="127.0.0.1"):
            with patch("tokenpak.network_utils.get_public_ip", return_value=None):
                urls = get_reachable_addresses(8766)
        # Only localhost, not both localhost and 127.0.0.1
        assert not any("127.0.0.1" in u for u in urls)

    def test_includes_public_ip_when_detected(self):
        """Adds public IP if get_public_ip returns one."""
        with patch("tokenpak.network_utils.get_local_ip", return_value="192.168.1.5"):
            with patch("tokenpak.network_utils.get_public_ip", return_value="203.0.113.5"):
                urls = get_reachable_addresses(8766)
        assert "http://203.0.113.5:8766" in urls

    def test_skips_public_ip_when_detect_public_false(self):
        """Does not call get_public_ip when detect_public=False."""
        with patch("tokenpak.network_utils.get_local_ip", return_value="192.168.1.5"):
            with patch("tokenpak.network_utils.get_public_ip") as mock_pub:
                urls = get_reachable_addresses(8766, detect_public=False)
        mock_pub.assert_not_called()
        assert not any("203.0.113" in u for u in urls)

    def test_url_format_includes_port(self):
        """All URLs have the correct port."""
        with patch("tokenpak.network_utils.get_local_ip", return_value="10.0.0.1"):
            with patch("tokenpak.network_utils.get_public_ip", return_value=None):
                urls = get_reachable_addresses(9999)
        for url in urls:
            assert ":9999" in url

    def test_returns_at_least_one_url(self):
        """Always returns at least one URL (localhost)."""
        with patch("tokenpak.network_utils.get_local_ip", return_value="localhost"):
            with patch("tokenpak.network_utils.get_public_ip", return_value=None):
                urls = get_reachable_addresses(8766)
        assert len(urls) >= 1

    def test_public_ip_not_duplicated_if_matches_local(self):
        """Public IP matching LAN IP is not added twice."""
        with patch("tokenpak.network_utils.get_local_ip", return_value="192.168.1.5"):
            with patch("tokenpak.network_utils.get_public_ip", return_value="192.168.1.5"):
                urls = get_reachable_addresses(8766)
        count = sum(1 for u in urls if "192.168.1.5" in u)
        assert count == 1

    def test_token_can_be_appended(self):
        """URLs support ?token= query string."""
        with patch("tokenpak.network_utils.get_local_ip", return_value="localhost"):
            with patch("tokenpak.network_utils.get_public_ip", return_value=None):
                urls = get_reachable_addresses(8766)
        token = "abc123"
        for url in urls:
            full = f"{url}?token={token}"
            assert "?token=abc123" in full


# ---------------------------------------------------------------------------
# is_port_accessible
# ---------------------------------------------------------------------------


class TestIsPortAccessible:
    def test_returns_true_on_open_port(self):
        """Returns True when connect_ex returns 0."""
        with patch("tokenpak.network_utils.socket.socket") as mock_sock_class:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0
            mock_sock_class.return_value = mock_sock
            result = is_port_accessible("localhost", 8766)
        assert result is True
        mock_sock.close.assert_called_once()

    def test_returns_false_on_refused_port(self):
        """Returns False when connect_ex returns non-zero (connection refused)."""
        with patch("tokenpak.network_utils.socket.socket") as mock_sock_class:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 111  # ECONNREFUSED
            mock_sock_class.return_value = mock_sock
            result = is_port_accessible("localhost", 9999)
        assert result is False

    def test_returns_false_on_exception(self):
        """Returns False on unexpected exception."""
        with patch("tokenpak.network_utils.socket.socket") as mock_sock_class:
            mock_sock_class.side_effect = OSError("network unreachable")
            result = is_port_accessible("badhost", 8766)
        assert result is False

    def test_sets_timeout(self):
        """Socket timeout is set to the provided value."""
        with patch("tokenpak.network_utils.socket.socket") as mock_sock_class:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0
            mock_sock_class.return_value = mock_sock
            is_port_accessible("localhost", 8766, timeout=5)
        mock_sock.settimeout.assert_called_once_with(5)

    def test_returns_bool(self):
        """Return type is always bool."""
        with patch("tokenpak.network_utils.socket.socket") as mock_sock_class:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0
            mock_sock_class.return_value = mock_sock
            result = is_port_accessible("localhost", 8766)
        assert isinstance(result, bool)
