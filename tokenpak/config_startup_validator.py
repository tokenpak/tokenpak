"""
Startup config validator for TokenPak proxy.

Validates config on proxy startup in warning mode (doesn't crash).
Logs validation warnings but allows proxy to start with degraded config.

Usage:
    from tokenpak.config_startup_validator import validate_config_on_startup
    
    validate_config_on_startup()  # Logs warnings if config issues found
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

try:
    import jsonschema
    from jsonschema import Draft202012Validator
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

try:
    import yaml as _yaml
    HAS_YAML = True

    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return _yaml.safe_load(f) or {}

except ImportError:
    HAS_YAML = False

    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return json.load(f)


CONFIG_PATH = Path.home() / ".tokenpak" / "config.yaml"
SCHEMA_PATH = Path(__file__).parent / "config" / "schema.json"


def _load_schema() -> Optional[dict]:
    """Load schema. Returns None if schema missing or jsonschema not installed."""
    if not HAS_JSONSCHEMA:
        return None
    
    if not SCHEMA_PATH.exists():
        return None
    
    with open(SCHEMA_PATH, "r") as f:
        return json.load(f)


def _load_config(path: Optional[Path] = None) -> Optional[dict]:
    """Load config file. Returns None if missing."""
    config_path = path or CONFIG_PATH
    
    if not config_path.exists():
        return None
    
    try:
        if config_path.suffix in (".yaml", ".yml"):
            if not HAS_YAML:
                return None
            return _load_yaml(str(config_path))
        else:
            with open(config_path, "r") as f:
                return json.load(f)
    except Exception:
        return None


def validate_config_on_startup(config_path: Optional[Path] = None) -> bool:
    """
    Validate config on proxy startup (warning mode).
    
    Returns:
        True if valid or no config file found, False if validation errors (but continues anyway)
    """
    config_path = config_path or CONFIG_PATH
    
    # No config file = ok (use defaults)
    config = _load_config(config_path)
    if config is None:
        return True
    
    # No jsonschema = ok (can't validate, but don't fail)
    schema = _load_schema()
    if schema is None:
        return True
    
    # Validate
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(config))
    
    if not errors:
        # Config is valid
        print(f"[tokenpak] Config validation: OK ({config_path})", file=sys.stderr)
        return True
    
    # Config has errors — log warnings but continue
    print(f"[tokenpak] Config validation: WARNING ({len(errors)} issue(s))", file=sys.stderr)
    for i, error in enumerate(errors, 1):
        path = list(error.absolute_path)
        field = ".".join(str(p) for p in path) if path else "(root)"
        print(f"  [{i}] Field '{field}': {error.message}", file=sys.stderr)
    
    print(f"[tokenpak] Proxy continues with defaults for invalid fields", file=sys.stderr)
    return False
