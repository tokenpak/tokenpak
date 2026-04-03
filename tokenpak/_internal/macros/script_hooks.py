"""
Script hook system for TokenPak.

Supports hooks that run shell scripts at key proxy lifecycle points:
  ~/.tokenpak/hooks/on_request.sh    — before each proxy request
  ~/.tokenpak/hooks/on_response.sh   — after each successful response
  ~/.tokenpak/hooks/on_error.sh      — on proxy error
  ~/.tokenpak/hooks/on_budget_alert.sh — when budget threshold is hit

Each hook receives JSON context via stdin with relevant event fields.
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_HOOKS_DIR = Path.home() / ".tokenpak" / "hooks"

# Hook names and their descriptions
HOOK_NAMES = {
    "on_request": "Runs before each proxy request (stdin: {model, provider, messages_count, timestamp})",
    "on_response": "Runs after each successful response (stdin: {model, provider, tokens_used, cost_usd, latency_ms, timestamp})",
    "on_error": "Runs on proxy error (stdin: {model, provider, error_type, error_message, timestamp})",
    "on_budget_alert": "Runs when budget threshold is hit (stdin: {budget_id, limit_usd, spent_usd, pct_used, timestamp})",
}


def _hooks_dir() -> Path:
    """Return the hooks directory path."""
    return DEFAULT_HOOKS_DIR


def get_hook_path(hook_name: str) -> Path:
    """Return the full path for a given hook script."""
    return _hooks_dir() / f"{hook_name}.sh"


def hook_exists(hook_name: str) -> bool:
    """Check if a hook script exists and is executable."""
    path = get_hook_path(hook_name)
    return path.exists() and os.access(path, os.X_OK)


def list_hooks() -> Dict[str, Dict[str, Any]]:
    """
    List all possible hooks and their current status.

    Returns:
        Dict mapping hook_name → {path, exists, executable, description}
    """
    result = {}
    for name, desc in HOOK_NAMES.items():
        path = get_hook_path(name)
        result[name] = {
            "path": str(path),
            "exists": path.exists(),
            "executable": path.exists() and os.access(path, os.X_OK),
            "description": desc,
        }
    return result


def install_hook(hook_name: str, script_content: Optional[str] = None) -> Path:
    """
    Install a hook script.

    Args:
        hook_name: Hook name (e.g., "on_request")
        script_content: Shell script content. If None, installs a stub.

    Returns:
        Path to the installed hook file.

    Raises:
        ValueError: If hook_name is not recognized.
    """
    if hook_name not in HOOK_NAMES:
        raise ValueError(f"Unknown hook: {hook_name}. Valid hooks: {list(HOOK_NAMES.keys())}")

    hooks_dir = _hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)

    path = get_hook_path(hook_name)

    if script_content is None:
        # Install a stub
        desc = HOOK_NAMES[hook_name]
        script_content = f"""#!/usr/bin/env bash
# TokenPak hook: {hook_name}
# {desc}
#
# Context is provided via stdin as JSON.
# Example: read context with: context=$(cat)

context=$(cat)
echo "[tokenpak:{hook_name}] $context" >> ~/.tokenpak/hooks/{hook_name}.log
"""

    path.write_text(script_content)
    path.chmod(0o755)
    return path


def fire_hook(
    hook_name: str,
    context: Dict[str, Any],
    timeout: int = 30,
) -> Optional[Dict[str, Any]]:
    """
    Fire a hook script with the given context.

    Args:
        hook_name: Hook name (e.g., "on_request")
        context: Dictionary passed as JSON to hook's stdin
        timeout: Timeout in seconds

    Returns:
        Dict with {success, stdout, stderr, returncode} or None if hook doesn't exist.
    """
    from datetime import datetime

    # Always inject timestamp
    ctx = {"timestamp": datetime.now().isoformat(), **context}

    path = get_hook_path(hook_name)
    if not path.exists():
        return None

    if not os.access(path, os.X_OK):
        logger.warning(f"Hook {hook_name} exists but is not executable: {path}")
        return None

    try:
        stdin_data = json.dumps(ctx)
        result = subprocess.run(
            [str(path)],
            input=stdin_data,
            text=True,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "TOKENPAK_HOOK": hook_name},
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        logger.warning(f"Hook {hook_name} timed out after {timeout}s")
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Hook timed out ({timeout}s)",
            "returncode": -1,
        }
    except Exception as e:
        logger.error(f"Hook {hook_name} failed: {e}")
        return {"success": False, "stdout": "", "stderr": str(e), "returncode": -1}


def fire_on_request(model: str, provider: str, messages_count: int, **extra) -> Optional[Dict]:
    """Fire the on_request hook."""
    return fire_hook(
        "on_request",
        {
            "model": model,
            "provider": provider,
            "messages_count": messages_count,
            **extra,
        },
    )


def fire_on_response(
    model: str,
    provider: str,
    tokens_used: int,
    cost_usd: float,
    latency_ms: int,
    **extra,
) -> Optional[Dict]:
    """Fire the on_response hook."""
    return fire_hook(
        "on_response",
        {
            "model": model,
            "provider": provider,
            "tokens_used": tokens_used,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            **extra,
        },
    )


def fire_on_error(
    model: str, provider: str, error_type: str, error_message: str, **extra
) -> Optional[Dict]:
    """Fire the on_error hook."""
    return fire_hook(
        "on_error",
        {
            "model": model,
            "provider": provider,
            "error_type": error_type,
            "error_message": error_message,
            **extra,
        },
    )


def fire_on_budget_alert(
    budget_id: str,
    limit_usd: float,
    spent_usd: float,
    **extra,
) -> Optional[Dict]:
    """Fire the on_budget_alert hook."""
    pct_used = round((spent_usd / limit_usd * 100) if limit_usd > 0 else 0, 1)
    return fire_hook(
        "on_budget_alert",
        {
            "budget_id": budget_id,
            "limit_usd": limit_usd,
            "spent_usd": spent_usd,
            "pct_used": pct_used,
            **extra,
        },
    )
