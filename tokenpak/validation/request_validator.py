"""
TokenPak Request Validator — validates incoming LLM API requests before forwarding.

Validates Anthropic /v1/messages and OpenAI /v1/chat/completions requests against
their respective schemas, returning structured 400-ready error payloads.

Configuration (env var or config.json):
    TOKENPAK_REQUEST_VALIDATION=strict   # reject bad requests with HTTP 400
    TOKENPAK_REQUEST_VALIDATION=warn     # log but forward (default)
    TOKENPAK_REQUEST_VALIDATION=off      # skip validation entirely

Usage:
    from tokenpak.validation.request_validator import RequestValidator

    validator = RequestValidator(mode="strict")
    result = validator.validate(body_bytes, provider="anthropic")
    if not result.valid:
        # Return 400 to client
        error_payload = result.to_error_response()
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from .request_schema import get_request_schema

logger = logging.getLogger(__name__)

# Validation modes
VALIDATION_MODES = ("strict", "warn", "off")

# Try to import jsonschema for full validation
try:
    import jsonschema  # noqa: F401
    from jsonschema import Draft202012Validator

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False
    Draft202012Validator = None  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------


class RequestValidationResult:
    """Result of a request validation check."""

    def __init__(
        self,
        valid: bool,
        provider: str = "unknown",
        errors: Optional[List[Dict[str, Any]]] = None,
        warnings: Optional[List[Dict[str, Any]]] = None,
    ):
        self.valid = valid
        self.provider = provider
        self.errors = errors or []
        self.warnings = warnings or []

    def __bool__(self) -> bool:
        return self.valid

    def __repr__(self) -> str:
        if self.valid:
            return f"RequestValidationResult(valid=True, provider={self.provider!r})"
        return (
            f"RequestValidationResult(valid=False, provider={self.provider!r}, "
            f"errors={len(self.errors)})"
        )

    def to_error_response(self, docs_base: str = "https://docs.tokenpak.dev/api") -> Dict[str, Any]:
        """Build a structured 400 error body (matches OpenAI/Anthropic style).

        Returns:
            Dict ready to JSON-serialize as the HTTP response body.
        """
        provider_path = {
            "anthropic": "messages",
            "openai": "chat-completions",
            "openai-codex": "responses",
            "google": "google-generate-content",
        }.get(self.provider, "requests")

        return {
            "error": {
                "type": "validation_error",
                "message": "Request validation failed",
                "details": self.errors,
                "hint": f"{docs_base}/{provider_path}",
            }
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "provider": self.provider,
            "errors": self.errors,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# RequestValidator
# ---------------------------------------------------------------------------


class RequestValidator:
    """Validates incoming LLM proxy requests against provider schemas.

    Args:
        mode: "strict" | "warn" | "off"
              strict → reject invalid requests (caller must return HTTP 400)
              warn   → log errors but treat as valid (default)
              off    → always return valid=True, skip all work
    """

    def __init__(self, mode: str = "warn"):
        if mode not in VALIDATION_MODES:
            raise ValueError(f"Invalid validation mode {mode!r}. Choose from {VALIDATION_MODES}")
        self.mode = mode
        # Cache compiled jsonschema validators keyed by provider
        self._compiled: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        body: bytes,
        provider: str,
    ) -> RequestValidationResult:
        """Validate a raw request body for the given provider.

        Args:
            body: Raw request bytes (JSON).
            provider: "anthropic" | "openai" | other (skipped gracefully).

        Returns:
            RequestValidationResult — check .valid and .errors.
        """
        if self.mode == "off":
            return RequestValidationResult(valid=True, provider=provider)

        # Parse JSON
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            err = {
                "field": "(body)",
                "error": f"Invalid JSON: {exc}",
            }
            result = RequestValidationResult(valid=False, provider=provider, errors=[err])
            self._log_result(result)
            return result

        if not isinstance(data, dict):
            err = {"field": "(body)", "error": "Request body must be a JSON object"}
            result = RequestValidationResult(valid=False, provider=provider, errors=[err])
            self._log_result(result)
            return result

        schema = get_request_schema(provider)

        # Use jsonschema if available, else fall back to manual
        if HAS_JSONSCHEMA:
            errors = self._validate_jsonschema(data, schema, provider)
        else:
            errors = self._validate_manual(data, schema)

        # Semantic checks (provider-specific)
        semantic_errors, warnings = self._validate_semantics(data, provider)
        errors.extend(semantic_errors)

        valid = len(errors) == 0
        result = RequestValidationResult(
            valid=valid,
            provider=provider,
            errors=errors,
            warnings=warnings,
        )
        self._log_result(result)
        return result

    def validate_bytes(
        self,
        body: bytes,
        target_url: str,
        provider: str,
    ) -> RequestValidationResult:
        """Convenience method — infers whether to validate based on URL pattern.

        Validates adapter request endpoints:
        - /v1/messages (Anthropic)
        - /v1/chat/completions (OpenAI Chat)
        - /v1/responses (OpenAI Responses/Codex)
        - /models/*:generateContent (Google Gemini)

        Other endpoints are passed through as valid.
        """
        is_messages_endpoint = (
            "/v1/messages" in target_url
            or "/chat/completions" in target_url
            or "/v1/responses" in target_url
            or ("/models/" in target_url and "generateContent" in target_url)
        )
        if not is_messages_endpoint or not body:
            return RequestValidationResult(valid=True, provider=provider)

        return self.validate(body, provider)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _validate_jsonschema(
        self,
        data: Dict[str, Any],
        schema: Dict[str, Any],
        provider: str,
    ) -> List[Dict[str, Any]]:
        """Run jsonschema validation, return structured error list."""
        if provider not in self._compiled:
            self._compiled[provider] = Draft202012Validator(schema)
        validator = self._compiled[provider]

        import re as _re

        errors: List[Dict[str, Any]] = []
        for err in validator.iter_errors(data):
            field = ".".join(str(p) for p in err.absolute_path)
            if not field and err.validator == "required":
                # jsonschema reports missing required fields with empty path;
                # extract the field name from the message: "'fieldname' is a required property"
                m = _re.match(r"'([^']+)' is a required property", err.message)
                field = m.group(1) if m else "(root)"
            elif not field:
                field = "(root)"
            errors.append({"field": field, "error": err.message})
        return errors

    def _validate_manual(
        self,
        data: Dict[str, Any],
        schema: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Lightweight manual validation (no jsonschema dependency)."""
        errors: List[Dict[str, Any]] = []
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        # Check required fields
        for field in required:
            if field not in data:
                errors.append(
                    {
                        "field": field,
                        "error": "required field missing",
                    }
                )

        # Basic type checks for present fields
        for field, value in data.items():
            prop = properties.get(field)
            if prop is None:
                continue
            expected_type = prop.get("type")
            if expected_type and not self._check_type(value, expected_type):
                errors.append(
                    {
                        "field": field,
                        "error": f"expected {expected_type}, got {type(value).__name__}",
                    }
                )
                continue
            # Numeric bounds
            if expected_type in ("integer", "number") and isinstance(value, (int, float)):
                if "minimum" in prop and value < prop["minimum"]:
                    errors.append(
                        {
                            "field": field,
                            "error": f"value {value} is below minimum {prop['minimum']}",
                        }
                    )
                if "maximum" in prop and value > prop["maximum"]:
                    errors.append(
                        {
                            "field": field,
                            "error": f"value {value} exceeds maximum {prop['maximum']}",
                        }
                    )
            # String length
            if expected_type == "string" and isinstance(value, str):
                if "minLength" in prop and len(value) < prop["minLength"]:
                    errors.append(
                        {
                            "field": field,
                            "error": f"string too short (min {prop['minLength']} chars)",
                        }
                    )
            # Array min items
            if expected_type == "array" and isinstance(value, list):
                if "minItems" in prop and len(value) < prop["minItems"]:
                    errors.append(
                        {
                            "field": field,
                            "error": f"array must have at least {prop['minItems']} item(s)",
                        }
                    )
            # Enum
            if "enum" in prop and value not in prop["enum"]:
                errors.append(
                    {
                        "field": field,
                        "error": f"must be one of {prop['enum']}",
                    }
                )

        # Validate messages array items (role + content required)
        messages = data.get("messages", [])
        if isinstance(messages, list):
            for i, msg in enumerate(messages):
                if not isinstance(msg, dict):
                    errors.append(
                        {
                            "field": f"messages[{i}]",
                            "error": "must be an object",
                        }
                    )
                    continue
                for required_key in ("role", "content"):
                    if required_key not in msg:
                        errors.append(
                            {
                                "field": f"messages[{i}].{required_key}",
                                "error": "required field missing",
                            }
                        )

        return errors

    def _validate_semantics(
        self,
        data: Dict[str, Any],
        provider: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Provider-specific semantic checks beyond schema validation."""
        errors: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []

        if provider == "anthropic":
            errors.extend(self._anthropic_semantics(data, warnings))
        elif provider == "openai":
            errors.extend(self._openai_semantics(data, warnings))
        elif provider == "openai-codex":
            errors.extend(self._openai_responses_semantics(data, warnings))
        elif provider == "google":
            errors.extend(self._google_semantics(data, warnings))

        return errors, warnings

    def _anthropic_semantics(
        self,
        data: Dict[str, Any],
        warnings: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        errors: List[Dict[str, Any]] = []

        # Messages must start with 'user' role
        messages = data.get("messages", [])
        if messages and isinstance(messages, list):
            first = messages[0]
            if isinstance(first, dict) and first.get("role") == "assistant":
                errors.append(
                    {
                        "field": "messages[0].role",
                        "error": "first message must have role 'user', not 'assistant'",
                    }
                )

            # Roles must alternate
            for i in range(1, len(messages)):
                prev = messages[i - 1]
                curr = messages[i]
                if (
                    isinstance(prev, dict)
                    and isinstance(curr, dict)
                    and prev.get("role") == curr.get("role")
                ):
                    errors.append(
                        {
                            "field": f"messages[{i}].role",
                            "error": (
                                f"consecutive messages with same role '{curr.get('role')}'; "
                                "Anthropic requires alternating user/assistant turns"
                            ),
                        }
                    )

        # Model should look like claude-*
        model = data.get("model", "")
        if model and not model.startswith("claude"):
            warnings.append(
                {
                    "field": "model",
                    "error": f"model '{model}' doesn't look like an Anthropic model (expected claude-*)",
                }
            )

        return errors

    def _openai_semantics(
        self,
        data: Dict[str, Any],
        warnings: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        errors: List[Dict[str, Any]] = []

        # Validate message roles
        messages = data.get("messages", [])
        valid_roles = {"system", "user", "assistant", "tool", "function"}
        if isinstance(messages, list):
            for i, msg in enumerate(messages):
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                if role and role not in valid_roles:
                    errors.append(
                        {
                            "field": f"messages[{i}].role",
                            "error": f"invalid role '{role}'; must be one of {sorted(valid_roles)}",
                        }
                    )

        # Model should look like gpt-* or a known name
        model = data.get("model", "")
        if model and not (
            model.startswith("gpt-")
            or model.startswith("o1")
            or model.startswith("o3")
            or "ft:" in model
        ):
            warnings.append(
                {
                    "field": "model",
                    "error": f"model '{model}' doesn't look like an OpenAI model (expected gpt-* or o1/o3)",
                }
            )

        return errors

    def _openai_responses_semantics(
        self,
        data: Dict[str, Any],
        warnings: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        errors: List[Dict[str, Any]] = []

        model = data.get("model", "")
        if model and not (
            model.startswith("gpt-")
            or model.startswith("o1")
            or model.startswith("o3")
            or "codex" in str(model).lower()
            or "ft:" in model
        ):
            warnings.append(
                {
                    "field": "model",
                    "error": (
                        f"model '{model}' doesn't look like an OpenAI Responses model "
                        "(expected gpt-*/o1/o3/codex)"
                    ),
                }
            )

        return errors

    def _google_semantics(
        self,
        data: Dict[str, Any],
        warnings: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        errors: List[Dict[str, Any]] = []

        contents = data.get("contents", [])
        if isinstance(contents, list):
            for i, content in enumerate(contents):
                if not isinstance(content, dict):
                    errors.append({"field": f"contents[{i}]", "error": "must be an object"})
                    continue
                parts = content.get("parts")
                if not isinstance(parts, list) or len(parts) == 0:
                    errors.append(
                        {
                            "field": f"contents[{i}].parts",
                            "error": "must be a non-empty array",
                        }
                    )

        model = data.get("model", "")
        if model and not str(model).startswith("gemini"):
            warnings.append(
                {
                    "field": "model",
                    "error": f"model '{model}' doesn't look like a Gemini model (expected gemini-*)",
                }
            )

        return errors

    def _check_type(self, value: Any, expected: str) -> bool:
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "object": dict,
            "array": list,
            "null": type(None),
        }
        expected_types = type_map.get(expected)
        if expected_types is None:
            return True
        # Booleans are ints in Python — treat bool as NOT matching integer/number
        if expected in ("integer", "number") and isinstance(value, bool):
            return False
        return isinstance(value, expected_types)  # type: ignore

    def _log_result(self, result: RequestValidationResult) -> None:
        """Log validation results based on mode."""
        if result.valid:
            if result.warnings and self.mode != "off":
                for w in result.warnings:
                    logger.debug(
                        "tokenpak.validation: [%s] warning %s: %s",
                        result.provider,
                        w.get("field"),
                        w.get("error"),
                    )
        else:
            log_fn = logger.warning if self.mode == "strict" else logger.debug
            for e in result.errors:
                log_fn(
                    "tokenpak.validation: [%s] %s: %s",
                    result.provider,
                    e.get("field"),
                    e.get("error"),
                )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def get_validation_mode() -> str:
    """Read validation mode from env var or config.json.

    Priority:
      1. TOKENPAK_REQUEST_VALIDATION env var
      2. ~/.tokenpak/config.json "request_validation" key
      3. Default: "warn"
    """
    env_val = os.environ.get("TOKENPAK_REQUEST_VALIDATION", "").lower().strip()
    if env_val in VALIDATION_MODES:
        return env_val

    # Fall back to config file
    try:
        from tokenpak.agent.config import _load as _load_config

        cfg = _load_config()
        file_val = str(cfg.get("request_validation", "")).lower().strip()
        if file_val in VALIDATION_MODES:
            return file_val
    except Exception:
        pass

    return "warn"


# Module-level singleton
_validator: Optional[RequestValidator] = None


def get_request_validator() -> RequestValidator:
    """Return (or create) the shared RequestValidator instance."""
    global _validator
    if _validator is None:
        _validator = RequestValidator(mode=get_validation_mode())
    return _validator


def validate_request(
    body: bytes,
    provider: str,
    target_url: str = "",
) -> RequestValidationResult:
    """Validate an incoming request body.

    Args:
        body: Raw request bytes.
        provider: "anthropic" or "openai".
        target_url: Optional URL — if provided, skips non-messages endpoints.

    Returns:
        RequestValidationResult
    """
    v = get_request_validator()
    if target_url:
        return v.validate_bytes(body, target_url, provider)
    return v.validate(body, provider)
