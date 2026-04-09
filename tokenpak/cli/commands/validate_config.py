"""validate-config command — validate TokenPak configuration files against schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import yaml as _yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

try:
    import jsonschema
    from jsonschema import Draft202012Validator, ValidationError

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def load_schema() -> dict:
    """Load the JSON schema from tokenpak/config/schema.json."""
    # Try both possible locations (tokenpak/config or tokenpak/agent/config)
    possible_paths = [
        Path(__file__).parent.parent.parent / "config" / "schema.json",  # tokenpak/config
        Path(__file__).parent.parent.parent.parent
        / "config"
        / "schema.json",  # go up one more level
    ]

    for schema_path in possible_paths:
        if schema_path.exists():
            with open(schema_path, "r") as f:
                return json.load(f)

    raise FileNotFoundError(f"Schema file not found. Tried: {[str(p) for p in possible_paths]}")


def format_error(error: ValidationError, config_path: str) -> str:
    """Format a JSON schema validation error into a human-friendly message."""
    path = list(error.absolute_path)

    if not path:
        field = "(root)"
    else:
        field = ".".join(str(p) for p in path)

    # Get the schema constraint that failed
    schema = error.schema
    validator = error.validator

    # Build suggestion based on validation type
    suggestion = ""
    if validator == "type":
        expected = schema.get("type", "unknown")
        actual = type(error.instance).__name__
        suggestion = f"Expected {expected}, got {actual}"
    elif validator == "enum":
        allowed = schema.get("enum", [])
        suggestion = f"Must be one of: {', '.join(str(x) for x in allowed)}"
    elif validator == "minimum":
        min_val = schema.get("minimum")
        suggestion = f"Value must be >= {min_val}"
    elif validator == "maximum":
        max_val = schema.get("maximum")
        suggestion = f"Value must be <= {max_val}"
    elif validator == "minLength":
        min_len = schema.get("minLength")
        suggestion = f"String must be at least {min_len} characters"
    elif validator == "additionalProperties":
        suggestion = f"Unknown property '{list(error.instance.keys())[0] if isinstance(error.instance, dict) else 'unknown'}'"
    else:
        suggestion = error.message

    return f"  Field '{field}': {error.message}\n  → {suggestion}"


def validate_file(config_path: str, strict: bool = False) -> tuple[bool, list[str]]:
    """
    Validate a config file against schema.

    Returns:
        (is_valid, messages)
    """
    if not HAS_JSONSCHEMA:
        return False, ["jsonschema library not installed. Install with: pip install jsonschema"]

    path = Path(config_path).expanduser()

    # Check file exists
    if not path.exists():
        return False, [f"Config file not found: {path}"]

    # Load config file
    try:
        with open(path, "r") as f:
            if path.suffix in (".yaml", ".yml"):
                if not HAS_YAML:
                    return False, ["PyYAML not installed. Install with: pip install pyyaml"]
                config = _yaml.safe_load(f) or {}
            else:
                config = json.load(f)
    except json.JSONDecodeError as e:
        return False, [f"Invalid JSON in {path}:", str(e)]
    except Exception as e:
        return False, [f"Error reading {path}: {e}"]

    # Load schema
    try:
        schema = load_schema()
    except Exception as e:
        return False, [f"Error loading schema: {e}"]

    # Validate
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(config))

    if not errors:
        return True, [f"✓ Config is valid: {path}"]

    # Format errors
    messages = [f"✗ Config validation failed ({len(errors)} error(s)):"]
    for error in errors:
        messages.append(format_error(error, str(path)))

    return False, messages


def run(config_path: str, strict: bool = False) -> int:
    """
    CLI handler for `tokenpak config validate`.

    Args:
        config_path: Path to config file to validate
        strict: If True, exit 1 on any error. If False, warn but allow.

    Returns:
        Exit code (0 = valid, 1 = invalid)
    """
    is_valid, messages = validate_file(config_path, strict=strict)

    for msg in messages:
        print(msg)

    if not is_valid:
        return 1
    return 0


try:
    import click

    @click.command("validate")
    @click.argument("config_path", type=click.Path(exists=False))
    @click.option("--strict", is_flag=True, help="Fail on any validation error (default: warn)")
    def validate_config_cmd(config_path, strict):
        """Validate a TokenPak configuration file against the schema.

        Example:
            tokenpak config validate ~/.tokenpak/config.yaml
        """
        exit_code = run(config_path, strict=strict)
        sys.exit(exit_code)

except ImportError:
    pass
