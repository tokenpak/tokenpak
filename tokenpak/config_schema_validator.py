"""
TokenPak Config Schema Validator

JSON Schema-style validator for the flat TokenPak config format used by proxy.py.
The config format uses dot-notation for nested values (e.g., compression.enabled).

Public API:
    validate_config_dict(config: dict) -> (is_valid: bool, errors: list[dict])
    validate_config_file(filepath: str) -> (is_valid: bool, errors: list[dict])
    format_errors(errors: list[dict], filepath: str = None) -> str

Each error dict has keys:
    path       — field path (e.g., "port", "compression.threshold_tokens")
    message    — human-readable error description
    suggestion — actionable fix hint
    validator  — validator type ("type", "range", "enum", "unknown_field", "custom")
    instance   — value that caused the error (may be None)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

# Top-level fields allowed in config
_KNOWN_TOP_KEYS = {
    "port",
    "mode",
    "db",
    "listen_address",
    "compression",
    "features",
    "budget",
    "capsule",
    "vault",
    "term_resolver",
    "upstream",
    "rate_limit_rpm",
    "failover",
    "routing",
    "token_counting",
    "cache",
    "logging",
    "cost_tracking",
    "monitoring",
    "security",
    "rate_limiting",
}

_KNOWN_FEATURE_KEYS = {
    "skeleton",
    "shadow_reader",
    "router",
    "validation_gate",
    "validation_gate_soft",
    "capsule_builder",
    "decision_memory",
    "cost_tracking",
}

_VALID_MODES = {"strict", "hybrid", "aggressive"}

_VALID_PROVIDERS = {"anthropic", "openai", "google", "gemini", "openrouter"}

_VALID_RETRIEVAL_BACKENDS = {"json_blocks", "sqlite"}


def _err(path: str, message: str, suggestion: str = "", validator: str = "custom", instance=None) -> dict:
    return {
        "path": path,
        "message": message,
        "suggestion": suggestion,
        "validator": validator,
        "instance": instance,
    }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_dict(config: Dict[str, Any]) -> List[dict]:
    errors: List[dict] = []

    if not isinstance(config, dict):
        errors.append(_err("<root>", "Config must be a dictionary (object)", "Use YAML dict or JSON object", "type"))
        return errors

    # --- port ---
    if "port" in config:
        port = config["port"]
        if not isinstance(port, int) or isinstance(port, bool):
            errors.append(_err(
                "port",
                "port must be an integer",
                "Set port to an integer (e.g., 8766)",
                "type",
                port,
            ))
        elif not (1024 <= port <= 65535):
            errors.append(_err(
                "port",
                f"port must be in range 1024–65535 (got {port})",
                "Use a port in the range 1024–65535 (e.g., 8766)",
                "range",
                port,
            ))

    # --- mode ---
    if "mode" in config:
        mode = config["mode"]
        if mode not in _VALID_MODES:
            errors.append(_err(
                "mode",
                f"mode must be one of: {', '.join(sorted(_VALID_MODES))} (got {mode!r})",
                f"Set mode to one of: {', '.join(sorted(_VALID_MODES))}",
                "enum",
                mode,
            ))

    # --- compression ---
    if "compression" in config:
        comp = config["compression"]
        if isinstance(comp, dict):
            if "threshold_tokens" in comp:
                val = comp["threshold_tokens"]
                if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                    errors.append(_err(
                        "compression.threshold_tokens",
                        "compression.threshold_tokens must be a non-negative integer",
                        "Set compression.threshold_tokens to a positive integer (e.g., 4500)",
                        "range",
                        val,
                    ))
            if "cache_size" in comp:
                val = comp["cache_size"]
                if not isinstance(val, int) or isinstance(val, bool) or val < 10:
                    errors.append(_err(
                        "compression.cache_size",
                        "compression.cache_size must be >= 10",
                        "Set compression.cache_size to at least 10 (e.g., 2000)",
                        "range",
                        val,
                    ))
            if "max_chars" in comp:
                val = comp["max_chars"]
                if not isinstance(val, int) or isinstance(val, bool) or val < 1:
                    errors.append(_err(
                        "compression.max_chars",
                        "compression.max_chars must be a positive integer",
                        "Set compression.max_chars to a positive integer (e.g., 120)",
                        "range",
                        val,
                    ))

    # --- budget ---
    if "budget" in config:
        budget = config["budget"]
        if isinstance(budget, dict):
            if "total_tokens" in budget:
                val = budget["total_tokens"]
                if not isinstance(val, int) or isinstance(val, bool):
                    errors.append(_err(
                        "budget.total_tokens",
                        "budget.total_tokens must be an integer",
                        "Set budget.total_tokens to an integer (e.g., 12000)",
                        "type",
                        val,
                    ))

    # --- vault ---
    if "vault" in config:
        vault = config["vault"]
        if isinstance(vault, dict):
            if "inject_min_score" in vault:
                val = vault["inject_min_score"]
                if not isinstance(val, (int, float)) or isinstance(val, bool) or not (0.0 <= val <= 10.0):
                    errors.append(_err(
                        "vault.inject_min_score",
                        "vault.inject_min_score must be between 0.0 and 10.0",
                        "Set vault.inject_min_score to a float in 0.0–10.0 (e.g., 0.5)",
                        "range",
                        val,
                    ))
            if "retrieval_backend" in vault:
                val = vault["retrieval_backend"]
                if val not in _VALID_RETRIEVAL_BACKENDS:
                    errors.append(_err(
                        "vault.retrieval_backend",
                        f"vault.retrieval_backend must be one of: {', '.join(sorted(_VALID_RETRIEVAL_BACKENDS))} (got {val!r})",
                        f"Set vault.retrieval_backend to one of: {', '.join(sorted(_VALID_RETRIEVAL_BACKENDS))}",
                        "enum",
                        val,
                    ))

    # --- rate_limit_rpm ---
    if "rate_limit_rpm" in config:
        val = config["rate_limit_rpm"]
        if not isinstance(val, int) or isinstance(val, bool) or val < 1:
            errors.append(_err(
                "rate_limit_rpm",
                "rate_limit_rpm must be an integer >= 1",
                "Set rate_limit_rpm to a positive integer (e.g., 60)",
                "range",
                val,
            ))

    # --- upstream ---
    if "upstream" in config:
        up = config["upstream"]
        if isinstance(up, dict) and "timeout" in up:
            val = up["timeout"]
            if not isinstance(val, int) or isinstance(val, bool) or not (1 <= val <= 3600):
                errors.append(_err(
                    "upstream.timeout",
                    "upstream.timeout must be an integer in 1–3600 seconds",
                    "Set upstream.timeout to a value between 1 and 3600 (e.g., 300)",
                    "range",
                    val,
                ))

    # --- features ---
    if "features" in config:
        features = config["features"]
        if isinstance(features, dict):
            for key in features:
                if key not in _KNOWN_FEATURE_KEYS:
                    errors.append(_err(
                        f"features.{key}",
                        f"Unknown feature flag: {key!r}",
                        f"Remove features.{key} or use one of: {', '.join(sorted(_KNOWN_FEATURE_KEYS))}",
                        "unknown_field",
                        key,
                    ))

    # --- failover ---
    if "failover" in config:
        failover = config["failover"]
        if isinstance(failover, dict) and "chain" in failover:
            chain = failover["chain"]
            if isinstance(chain, list):
                for i, entry in enumerate(chain):
                    if isinstance(entry, dict) and "provider" in entry:
                        prov = entry["provider"]
                        if prov not in _VALID_PROVIDERS:
                            errors.append(_err(
                                f"failover.chain[{i}].provider",
                                f"Unknown provider: {prov!r}. Must be one of: {', '.join(sorted(_VALID_PROVIDERS))}",
                                f"Use a known provider: {', '.join(sorted(_VALID_PROVIDERS))}",
                                "enum",
                                prov,
                            ))

    # --- unknown top-level keys ---
    for key in config:
        if key not in _KNOWN_TOP_KEYS:
            errors.append(_err(
                key,
                f"Unknown top-level field: {key!r}",
                f"Remove {key!r} or check spelling. Allowed fields: {', '.join(sorted(_KNOWN_TOP_KEYS))}",
                "unknown_field",
                key,
            ))

    return errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_config_dict(config: Dict[str, Any]) -> Tuple[bool, List[dict]]:
    """
    Validate a TokenPak config dict.

    Returns:
        (is_valid, errors) where errors is a list of dicts with keys:
        path, message, suggestion, validator, instance
    """
    errors = _validate_dict(config)
    return len(errors) == 0, errors


def validate_config_file(filepath: str) -> Tuple[bool, List[dict]]:
    """
    Load and validate a TokenPak config file (YAML or JSON).

    Returns:
        (is_valid, errors) where errors is a list of dicts with keys:
        path, message, suggestion, validator, instance
    """
    path = Path(filepath).expanduser()

    if not path.exists():
        return False, [_err(
            "file",
            f"Config file not found: {path}",
            f"Create the file at: {path}",
            "custom",
        )]

    if not path.is_file():
        return False, [_err("file", f"Not a file: {path}", "Provide a path to a file", "custom")]

    try:
        content = path.read_text()
    except Exception as e:
        return False, [_err("file", f"Cannot read file: {e}", "Check file permissions", "custom")]

    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
            config = yaml.safe_load(content)
        except Exception as e:
            return False, [_err("file", f"YAML parse error: {e}", "Fix YAML syntax", "custom")]
    else:
        try:
            config = json.loads(content)
        except json.JSONDecodeError as e:
            return False, [_err("file", f"JSON parse error: {e}", "Fix JSON syntax", "json")]

    if not isinstance(config, dict):
        return False, [_err("<root>", "Config must be a dictionary (object)", "Use YAML dict or JSON object", "type")]

    errors = _validate_dict(config)
    return len(errors) == 0, errors


def format_errors(errors: List[dict], filepath: Optional[str] = None) -> str:
    """Format error list as a human-readable string."""
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
        if error.get("suggestion"):
            lines.append(f"   Fix: {error['suggestion']}")
        lines.append("")

    return "\n".join(lines)


__all__ = ["validate_config_dict", "validate_config_file", "format_errors"]
