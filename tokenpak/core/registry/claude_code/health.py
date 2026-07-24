"""Proxy health check for the Claude Code adapter."""

import urllib.error
import urllib.request
from typing import Tuple

from tokenpak.core.registry.claude_code.config import ClaudeCodeConfig


def check_proxy_health(config: ClaudeCodeConfig, timeout: float = 5.0) -> Tuple[bool, str]:
    """Verify the TokenPak proxy is running by calling GET /health.

    Args:
        config: Adapter configuration supplying the proxy URL.
        timeout: Request timeout in seconds.

    Returns:
        Tuple of (healthy: bool, status_message: str).
        healthy is True only when the proxy responds with HTTP 200.
    """
    url = f"{config.proxy_url}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            if resp.status == 200:
                return True, "ok"
            return False, f"unexpected status {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, str(exc.reason)
    except OSError as exc:
        return False, str(exc)
