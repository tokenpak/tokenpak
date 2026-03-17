"""
CLI command: tokenpak config check <file>

Validates a proxy config file against the ConfigValidator schema.
"""

import sys
import json
from pathlib import Path


def cmd_config_check(args):
    """Validate a proxy config file (JSON)."""
    from tokenpak.config_validator import ConfigValidator
    
    config_path = Path(args.file).expanduser()
    
    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}")
        sys.exit(2)
    
    # Load JSON
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {config_path}: {e}")
        sys.exit(2)
    
    # Validate
    validator = ConfigValidator()
    errors = validator.validate(config)
    
    if not errors:
        print(f"✓ Config is valid: {config_path}")
        sys.exit(0)
    
    # Print errors
    print(f"✗ Config validation failed ({len(errors)} error(s)):\n")
    for error in errors:
        print(f"  Field: {error.field}")
        print(f"    Expected: {error.expected}")
        print(f"    Actual: {error.actual}")
        print(f"    Message: {error.message}")
        print(f"    Fix: {error.suggestion}")
        print()
    
    sys.exit(1)


def register_config_check_parser(sub):
    """Register 'tokenpak config-check' command."""
    p = sub.add_parser(
        "config-check",
        help="Validate a proxy config file (JSON)"
    )
    p.add_argument("file", help="Path to config file (JSON)")
    p.set_defaults(func=cmd_config_check)
    return p
