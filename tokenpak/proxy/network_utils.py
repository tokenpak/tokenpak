"""
Network detection utilities for TokenPak dashboard.

Provides best-effort detection of local/public IP addresses
and reachability checks for dashboard URL generation.
"""

import socket
import subprocess
from typing import Optional


def get_local_ip() -> str:
    """Get primary local IP (not 127.0.0.1).

    Uses a UDP connect trick — no data is actually sent.
    Falls back to 'localhost' on failure.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def get_public_ip(timeout: int = 2) -> Optional[str]:
    """Try to detect the public/external IP address.

    Makes a best-effort curl request to ifconfig.me.
    Returns None on timeout, error, or if curl is unavailable.
    """
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), "https://ifconfig.me"],
            timeout=timeout + 1,
            capture_output=True,
        )
        if result.returncode == 0:
            ip = result.stdout.decode().strip()
            # Basic validation: should look like an IP address
            if ip and all(c in "0123456789." for c in ip) and len(ip) <= 15:
                return ip
    except Exception:
        pass
    return None


def get_reachable_addresses(port: int, detect_public: bool = True) -> list:
    """Return list of URLs the user can potentially reach.

    Always includes localhost. Adds local network IP if detectable.
    Optionally adds public IP (best-effort, may timeout).

    Args:
        port: The port the dashboard is running on.
        detect_public: Whether to attempt public IP detection.

    Returns:
        List of URL strings (without token query param).
    """
    addresses = []

    # Always include localhost
    addresses.append(f"http://localhost:{port}")

    # Add LAN IP if different from localhost
    local_ip = get_local_ip()
    if local_ip and local_ip != "localhost" and local_ip != "127.0.0.1":
        addresses.append(f"http://{local_ip}:{port}")

    # Add public IP (best-effort)
    if detect_public:
        public_ip = get_public_ip()
        if public_ip and public_ip != local_ip:
            addresses.append(f"http://{public_ip}:{port}")

    return addresses


def is_port_accessible(host: str, port: int, timeout: int = 2) -> bool:
    """Check if a TCP port is reachable on the given host.

    Args:
        host: Hostname or IP address.
        port: Port number to check.
        timeout: Connection timeout in seconds.

    Returns:
        True if connection succeeds, False otherwise.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        result = s.connect_ex((host, port))
        s.close()
        return result == 0
    except Exception:
        return False
