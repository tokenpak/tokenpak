"""
Startup config validator for TokenPak proxy.

Runs validation on proxy startup and logs warnings for any config issues.
Does NOT block startup (warning mode only) to allow graceful degradation.

Usage:
    from tokenpak.startup_validator import validate_on_startup

    # Call once on proxy initialization
    validate_on_startup()  # Logs warnings, never raises exceptions
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("tokenpak.startup")


def validate_on_startup(
    config_path: str = "~/.tokenpak/config.yaml", warn_only: bool = True
) -> bool:
    """
    Validate config at proxy startup.

    Args:
        config_path: Path to config file (default: ~/.tokenpak/config.yaml)
        warn_only: If True, log warnings but don't block startup. If False, raise on errors.

    Returns:
        True if valid, False if errors (but True if warn_only=True and errors exist)
    """
    from tokenpak.cli_validate_config import format_errors, validate_config_file

    expanded_path = str(Path(config_path).expanduser())

    try:
        is_valid, errors = validate_config_file(config_path)
    except Exception as e:
        msg = f"Failed to validate config at startup: {e}"
        if warn_only:
            logger.warning(msg)
            return True  # Don't block startup
        else:
            logger.error(msg)
            raise

    if is_valid:
        logger.info(f"Config validation passed: {expanded_path}")
        return True

    # We have errors
    error_text = format_errors(errors, config_path)

    if warn_only:
        logger.warning(
            f"Config validation warnings (proxy will start with defaults):\n{error_text}"
        )
        return True  # Don't block startup
    else:
        logger.error(f"Config validation failed (blocking startup):\n{error_text}")
        raise ValueError(f"Invalid config: {len(errors)} error(s)")


def setup_validation_logging(log_level: str = "INFO") -> None:
    """Set up logging for config validation."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(numeric_level)
    formatter = logging.Formatter("[%(name)s] %(levelname)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(numeric_level)
