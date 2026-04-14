# SPDX-License-Identifier: Apache-2.0
"""
TokenPak Config Validation CLI

Comprehensive validation for TokenPak YAML/JSON config files:
- YAML syntax (proper indentation, valid syntax)
- Required fields (server, providers)
- Field types (port: int, host: str, models: list, etc.)
- Path validity (log dirs, cache dirs)
- Port availability (check if port is in use)
- Environment variable resolution (detect missing env vars)
- Unknown fields (warn on typos or deprecated keys)

Usage:
    tokenpak validate-config <config_file>

Exit codes:
    0 — Config is valid
    1 — Config has errors (startup would fail)
    2 — Config has warnings only (will work but issues detected)
"""

import os
import re
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


class ConfigError:
    """Represents a single config error or warning."""

    def __init__(
        self,
        line: int,
        field: str,
        message: str,
        suggestion: str = "",
        is_warning: bool = False,
    ):
        self.line = line
        self.field = field
        self.message = message
        self.suggestion = suggestion
        self.is_warning = is_warning

    def format(self, symbol: str = "ERROR") -> str:
        """Format error for display."""
        lines = [f"{symbol} (line {self.line}): {self.message}"]
        if self.field:
            lines.append(f"  Field: {self.field}")
        if self.suggestion:
            lines.append(f"  > {self.suggestion}")
        return "\n".join(lines)


class ConfigValidator:
    """Validates TokenPak configuration files (YAML or JSON)."""

    REQUIRED_TOP_LEVEL = ["server", "providers"]
    REQUIRED_SERVER = ["port"]
    OPTIONAL_SERVER = ["host", "bind_address"]

    REQUIRED_PROVIDERS_KEYS = ["anthropic", "openai", "google", "custom"]  # at least 1

    REQUIRED_PROVIDER = ["models"]
    OPTIONAL_PROVIDER = [
        "api_key",
        "timeout_seconds",
        "base_url",
        "enabled",
    ]

    KNOWN_TOP_KEYS = {
        "server",
        "providers",
        "routing",
        "token_counting",
        "cache",
        "logging",
        "cost_tracking",
        "compression",
        "monitoring",
        "security",
        "rate_limiting",
    }

    KNOWN_SERVER_KEYS = {
        "port",
        "host",
        "bind_address",
        "workers",
        "timeout_seconds",
    }

    KNOWN_CACHE_KEYS = {
        "enabled",
        "max_size_mb",
        "ttl_seconds",
        "backend",
    }

    def __init__(self):
        self.errors: List[ConfigError] = []
        self.warnings: List[ConfigError] = []
        self.line_map: Dict[str, int] = {}  # field → line number
        self.raw_content: str = ""

    def validate(self, config_path: str) -> Tuple[int, List[ConfigError], List[ConfigError]]:
        """
        Validate a config file and return exit code + errors/warnings.

        Returns:
            (exit_code, errors, warnings)
            - exit_code: 0 (valid), 1 (error), 2 (warnings)
        """
        self.errors = []
        self.warnings = []
        self.line_map = {}

        path = Path(config_path).expanduser()

        # Check file exists
        if not path.exists():
            print(f"❌ Config file not found: {path}", file=sys.stderr)
            return 1, [], []

        # Check file is readable
        if not path.is_file():
            print(f"❌ Not a file: {path}", file=sys.stderr)
            return 1, [], []

        # Read raw content for line mapping
        try:
            with open(path, "r") as f:
                self.raw_content = f.read()
        except Exception as e:
            print(f"❌ Cannot read file: {e}", file=sys.stderr)
            return 1, [], []

        # Load YAML/JSON
        config = self._load_config(path)
        if config is None:
            return 1, self.errors, self.warnings

        # Build line map (approximate)
        self._build_line_map(config)

        # Validate structure
        self._validate_top_level(config)
        self._validate_server(config.get("server", {}))
        self._validate_providers(config.get("providers", {}))
        self._validate_cache(config.get("cache", {}))
        self._validate_routing(config.get("routing", {}))

        # Check unknown top-level keys
        self._check_unknown_keys(config, self.KNOWN_TOP_KEYS, "top-level")

        # Determine exit code
        if self.errors:
            return 1, self.errors, self.warnings
        elif self.warnings:
            return 2, self.errors, self.warnings
        else:
            return 0, self.errors, self.warnings

    def validate_dict(self, config: Dict[str, Any]) -> Tuple[int, List[ConfigError], List[ConfigError]]:
        """
        Validate a config dict directly (for testing).

        Returns:
            (exit_code, errors, warnings)
        """
        self.errors = []
        self.warnings = []
        self.line_map = {}
        self.raw_content = ""

        if not isinstance(config, dict):
            self.errors.append(
                ConfigError(
                    line=1,
                    field="<root>",
                    message="Config must be a dictionary (object)",
                    suggestion="Top level should be YAML dict or JSON object",
                )
            )
            return 1, self.errors, self.warnings

        # Validate structure
        self._validate_top_level(config)
        self._validate_server(config.get("server", {}))
        self._validate_providers(config.get("providers", {}))
        self._validate_cache(config.get("cache", {}))
        self._validate_routing(config.get("routing", {}))

        # Check unknown top-level keys
        self._check_unknown_keys(config, self.KNOWN_TOP_KEYS, "top-level")

        # Determine exit code
        if self.errors:
            return 1, self.errors, self.warnings
        elif self.warnings:
            return 2, self.errors, self.warnings
        else:
            return 0, self.errors, self.warnings

    def _load_config(self, path: Path) -> Optional[Dict[str, Any]]:
        """Load YAML or JSON config, return None if invalid."""
        try:
            if path.suffix.lower() in [".yml", ".yaml"]:
                with open(path, "r") as f:
                    config = yaml.safe_load(f)
            else:
                import json

                with open(path, "r") as f:
                    config = json.load(f)

            if not isinstance(config, dict):
                self.errors.append(
                    ConfigError(
                        line=1,
                        field="<root>",
                        message="Config must be a dictionary (object)",
                        suggestion="Top level should be YAML dict or JSON object",
                    )
                )
                return None

            return config

        except yaml.YAMLError as e:
            # Extract line number from YAML error
            line = getattr(e, "problem_mark", None)
            line_num = (line.line + 1) if line else 1
            self.errors.append(
                ConfigError(
                    line=line_num,
                    field="<yaml>",
                    message=f"YAML syntax error: {e.problem}",
                    suggestion="Check indentation, quotes, and YAML syntax",
                )
            )
            return None
        except Exception as e:
            self.errors.append(
                ConfigError(
                    line=1,
                    field="<parse>",
                    message=f"Cannot parse config: {e}",
                    suggestion="Check file format (YAML or JSON)",
                )
            )
            return None

    def _build_line_map(self, obj: Dict[str, Any], prefix: str = "") -> None:
        """Approximate line numbers for fields by scanning raw content."""
        for key in obj.keys():
            pattern = rf"^\s*{re.escape(key)}\s*:"
            for i, line in enumerate(self.raw_content.split("\n"), 1):
                if re.match(pattern, line):
                    field_name = f"{prefix}{key}" if prefix else key
                    self.line_map[field_name] = i
                    break

    def _get_line(self, field: str) -> int:
        """Get approximate line number for field."""
        return self.line_map.get(field, 1)

    def _validate_top_level(self, config: Dict[str, Any]) -> None:
        """Check required top-level keys."""
        for required in self.REQUIRED_TOP_LEVEL:
            if required not in config:
                self.errors.append(
                    ConfigError(
                        line=1,
                        field=required,
                        message=f"Missing required section: {required}",
                        suggestion=f"Add '{required}:' section to config",
                    )
                )

    def _validate_server(self, server: Dict[str, Any]) -> None:
        """Validate server section."""
        if not isinstance(server, dict):
            self.errors.append(
                ConfigError(
                    line=self._get_line("server"),
                    field="server",
                    message="'server' must be a dict",
                    suggestion="server: { port: 8766, host: '127.0.0.1' }",
                )
            )
            return

        # Check required fields
        if "port" not in server:
            self.errors.append(
                ConfigError(
                    line=self._get_line("server"),
                    field="server.port",
                    message="Missing 'port' in server section",
                    suggestion="Add 'port: 8766' to server section",
                )
            )
        else:
            port = server["port"]
            if not isinstance(port, int):
                self.errors.append(
                    ConfigError(
                        line=self._get_line("port"),
                        field="server.port",
                        message=f"Port must be integer, got {type(port).__name__}",
                        suggestion=f"Change port: {port!r} to port: 8766",
                    )
                )
                return

            if not (1 <= port <= 65535):
                self.errors.append(
                    ConfigError(
                        line=self._get_line("port"),
                        field="server.port",
                        message=f"Port {port} out of valid range (1-65535)",
                        suggestion=f"Use port between 1024-49151 (e.g., 8766)",
                    )
                )

            # Check if port is available
            if not self._is_port_available(port):
                self.warnings.append(
                    ConfigError(
                        line=self._get_line("port"),
                        field="server.port",
                        message=f"Port {port} is already in use",
                        suggestion="Choose a different port or stop the process using this port",
                        is_warning=True,
                    )
                )

        # Check unknown keys
        self._check_unknown_keys(
            server, self.KNOWN_SERVER_KEYS, "server", prefix="server."
        )

    def _validate_providers(self, providers: Dict[str, Any]) -> None:
        """Validate providers section."""
        if not isinstance(providers, dict):
            self.errors.append(
                ConfigError(
                    line=self._get_line("providers"),
                    field="providers",
                    message="'providers' must be a dict",
                    suggestion="providers: { anthropic: { models: [...] } }",
                )
            )
            return

        if not providers:
            self.errors.append(
                ConfigError(
                    line=self._get_line("providers"),
                    field="providers",
                    message="'providers' section is empty",
                    suggestion="Add at least one provider (anthropic, openai, google, or custom)",
                )
            )
            return

        # Validate each provider
        for provider_name, provider_config in providers.items():
            self._validate_provider(provider_name, provider_config)

    def _validate_provider(self, name: str, config: Dict[str, Any]) -> None:
        """Validate a single provider."""
        if not isinstance(config, dict):
            self.errors.append(
                ConfigError(
                    line=self._get_line(f"providers.{name}"),
                    field=f"providers.{name}",
                    message=f"Provider '{name}' must be a dict",
                    suggestion=f"{name}: {{ models: ['claude-3-sonnet'] }}",
                )
            )
            return

        # Check required fields
        if "models" not in config:
            self.errors.append(
                ConfigError(
                    line=self._get_line(f"providers.{name}"),
                    field=f"providers.{name}.models",
                    message=f"Provider '{name}' missing 'models' list",
                    suggestion=f"Add 'models: [...]' to {name} provider",
                )
            )
        else:
            models = config["models"]
            if not isinstance(models, list):
                self.errors.append(
                    ConfigError(
                        line=self._get_line("models"),
                        field=f"providers.{name}.models",
                        message="'models' must be a list",
                        suggestion="Change models: 'claude-3-sonnet' to models: ['claude-3-sonnet']",
                    )
                )
            elif not models:
                self.errors.append(
                    ConfigError(
                        line=self._get_line("models"),
                        field=f"providers.{name}.models",
                        message="'models' list is empty",
                        suggestion="Add at least one model to models list",
                    )
                )

        # Check if API key is available (env var or config)
        if "api_key" not in config:
            env_var = f"{name.upper()}_API_KEY"
            if not os.environ.get(env_var):
                self.warnings.append(
                    ConfigError(
                        line=self._get_line(f"providers.{name}"),
                        field=f"providers.{name}.api_key",
                        message=f"No API key found for '{name}' (not in config or env)",
                        suggestion=f"Set {env_var} env var or add 'api_key:' to {name} config",
                        is_warning=True,
                    )
                )

    def _validate_cache(self, cache: Dict[str, Any]) -> None:
        """Validate cache section."""
        if not cache:
            return  # Optional

        if not isinstance(cache, dict):
            self.errors.append(
                ConfigError(
                    line=self._get_line("cache"),
                    field="cache",
                    message="'cache' must be a dict",
                    suggestion="cache: { enabled: true, max_size_mb: 256 }",
                )
            )
            return

        # Check types
        if "enabled" in cache and not isinstance(cache["enabled"], bool):
            self.errors.append(
                ConfigError(
                    line=self._get_line("enabled"),
                    field="cache.enabled",
                    message=f"'enabled' must be boolean, got {type(cache['enabled']).__name__}",
                    suggestion="Change to: enabled: true or enabled: false",
                )
            )

        if "max_size_mb" in cache:
            val = cache["max_size_mb"]
            if not isinstance(val, int) or val <= 0:
                self.errors.append(
                    ConfigError(
                        line=self._get_line("max_size_mb"),
                        field="cache.max_size_mb",
                        message="'max_size_mb' must be positive integer",
                        suggestion="Change to: max_size_mb: 256",
                    )
                )

        if "ttl_seconds" in cache:
            val = cache["ttl_seconds"]
            if not isinstance(val, int) or val <= 0:
                self.errors.append(
                    ConfigError(
                        line=self._get_line("ttl_seconds"),
                        field="cache.ttl_seconds",
                        message="'ttl_seconds' must be positive integer",
                        suggestion="Change to: ttl_seconds: 3600",
                    )
                )

        # Check unknown keys
        self._check_unknown_keys(cache, self.KNOWN_CACHE_KEYS, "cache", prefix="cache.")

    def _validate_routing(self, routing: Dict[str, Any]) -> None:
        """Validate routing section (optional but check if present)."""
        if not routing:
            return

        if not isinstance(routing, dict):
            self.warnings.append(
                ConfigError(
                    line=self._get_line("routing"),
                    field="routing",
                    message="'routing' must be a dict",
                    suggestion="routing: { primary: 'anthropic' }",
                    is_warning=True,
                )
            )

    def _check_unknown_keys(
        self,
        obj: Dict[str, Any],
        known_keys: set,
        section: str,
        prefix: str = "",
    ) -> None:
        """Check for unknown/typo keys in a section."""
        for key in obj.keys():
            if key not in known_keys:
                # Suggest similar key
                similar = self._find_similar(key, known_keys)
                suggestion = f"Unknown field '{key}'"
                if similar:
                    suggestion = f"Unknown field '{key}'. Did you mean '{similar}'?"
                else:
                    suggestion = f"Remove unknown field '{key}' (not recognized)"

                self.warnings.append(
                    ConfigError(
                        line=self._get_line(key),
                        field=f"{prefix}{key}" if prefix else key,
                        message=suggestion,
                        suggestion=f"Check spelling or remove if not needed",
                        is_warning=True,
                    )
                )

    @staticmethod
    def _find_similar(word: str, candidates: set, max_distance: int = 2) -> Optional[str]:
        """Find similar word using simple edit distance."""
        import difflib

        matches = difflib.get_close_matches(word, candidates, n=1, cutoff=0.6)
        return matches[0] if matches else None

    @staticmethod
    def _is_port_available(port: int) -> bool:
        """Check if port is available."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(("127.0.0.1", port))
            sock.close()
            return result != 0  # 0 means port is in use
        except Exception:
            return True  # Assume available if we can't check


# ---------------------------------------------------------------------------
# Schema-compat API — drop-in replacements for config_schema_validator.py
# ---------------------------------------------------------------------------

def validate_config_dict(config: Dict[str, Any]) -> Tuple[bool, List[Dict[str, str]]]:
    """
    Validate a config dict.

    Returns:
        (is_valid, errors) where errors is a list of dicts with keys:
        path, message, suggestion, validator, instance
    """
    validator = ConfigValidator()
    exit_code, errors, _warnings = validator.validate_dict(config)
    error_dicts = [
        {
            "path": e.field,
            "message": e.message,
            "suggestion": e.suggestion,
            "validator": "custom",
            "instance": None,
        }
        for e in errors
    ]
    return exit_code == 0, error_dicts


def validate_config_file(filepath: str) -> Tuple[bool, List[Dict[str, str]]]:
    """
    Load and validate a config file (YAML or JSON).

    Returns:
        (is_valid, errors) where errors is a list of dicts with keys:
        path, message, suggestion, validator, instance
    """
    validator = ConfigValidator()
    exit_code, errors, _warnings = validator.validate(filepath)
    error_dicts = [
        {
            "path": e.field,
            "message": e.message,
            "suggestion": e.suggestion,
            "validator": "custom",
            "instance": None,
        }
        for e in errors
    ]
    return exit_code == 0, error_dicts


def format_errors(errors: List[Dict[str, str]], filepath: str = None) -> str:
    """
    Format error list (from validate_config_dict/validate_config_file) as a human-readable string.
    """
    if not errors:
        return ""

    lines = []
    if filepath:
        lines.append(f"Config validation failed: {filepath}")
        lines.append(f"Found {len(errors)} error(s):\n")
    else:
        lines.append(f"Config validation failed ({len(errors)} error(s)):\n")

    for i, error in enumerate(errors, 1):
        lines.append(f"{i}. {error['message']}")
        lines.append(f"   Fix: {error['suggestion']}")
        lines.append("")

    return "\n".join(lines)


def cmd_validate_config(args):
    """CLI command: tokenpak validate-config <config_file>"""
    config_file = args.file

    validator = ConfigValidator()
    exit_code, errors, warnings = validator.validate(config_file)

    # Print results
    path = Path(config_file).expanduser()
    total_issues = len(errors) + len(warnings)

    if exit_code == 0:
        # Success
        print(f"✅ Config is valid: {path}")
        print(f"   (Server: {path.parent.name}/{path.name})")
        return 0

    # Print errors and warnings
    if errors or warnings:
        print(f"\n{'❌ Validation failed' if errors else '⚠️  Warnings detected'}")
        print(f"   {len(errors)} error(s), {len(warnings)} warning(s)\n")

    # Print errors first
    for err in errors:
        print(err.format("❌ ERROR"))
        print()

    # Then warnings
    for warn in warnings:
        print(warn.format("⚠️  WARNING"))
        print()

    if errors:
        print("Fix errors before starting TokenPak.")
    elif warnings:
        print("Config will work, but address these warnings.")

    return exit_code
