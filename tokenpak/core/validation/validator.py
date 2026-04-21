"""
TokenPak Response Validator — validates responses against the contract schema.

Lightweight validation without heavy dependencies (uses jsonschema if available,
falls back to manual validation).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .response_schema import RESPONSE_SCHEMA

logger = logging.getLogger(__name__)

# Try to import jsonschema for full validation
try:
    import jsonschema  # noqa: F401
    from jsonschema import Draft202012Validator, ValidationError

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False
    # Use type: ignore comments to handle the conditional imports
    Draft202012Validator = None  # type: ignore
    ValidationError = Exception  # type: ignore


class ValidationResult:
    """Result of a validation check."""

    def __init__(
        self,
        valid: bool,
        errors: Optional[List[Dict[str, Any]]] = None,
        warnings: Optional[List[Dict[str, Any]]] = None,
    ):
        self.valid = valid
        self.errors = errors or []
        self.warnings = warnings or []

    def __bool__(self) -> bool:
        return self.valid

    def __repr__(self) -> str:
        if self.valid:
            return "ValidationResult(valid=True)"
        return f"ValidationResult(valid=False, errors={len(self.errors)})"

    def to_dict(self) -> Dict[str, Any]:
        return {"valid": self.valid, "errors": self.errors, "warnings": self.warnings}


class ResponseValidator:
    """Validates TokenPak responses against the schema contract.

    Usage:
        validator = ResponseValidator()
        result = validator.validate(response_dict)
        if not result.valid:
            for error in result.errors:
                print(f"{error['field']}: {error['reason']}")
    """

    def __init__(
        self, schema: Optional[Dict[str, Any]] = None, strict: bool = False, log_errors: bool = True
    ):
        """Initialize validator.

        Args:
            schema: Custom schema (defaults to RESPONSE_SCHEMA)
            strict: If True, treat warnings as errors
            log_errors: If True, log validation errors
        """
        self.schema = schema or RESPONSE_SCHEMA
        self.strict = strict
        self.log_errors = log_errors

        # Pre-compile jsonschema validator if available
        self._json_validator: Optional[Any] = None
        if HAS_JSONSCHEMA:
            self._json_validator = Draft202012Validator(self.schema)

    def validate(self, response: Dict[str, Any]) -> ValidationResult:
        """Validate a response against the schema.

        Args:
            response: Response dictionary to validate

        Returns:
            ValidationResult with valid flag and any errors
        """
        errors: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []

        # Use jsonschema if available for comprehensive validation
        if self._json_validator:
            errors = self._validate_with_jsonschema(response)
        else:
            errors = self._validate_manually(response)

        # Additional semantic checks
        semantic_errors, semantic_warnings = self._validate_semantics(response)
        errors.extend(semantic_errors)
        warnings.extend(semantic_warnings)

        # In strict mode, warnings become errors
        if self.strict:
            errors.extend(warnings)
            warnings = []

        valid = len(errors) == 0

        # Log errors if enabled
        if not valid and self.log_errors:
            for error in errors:
                logger.warning(f"Response validation error: {error['field']} - {error['reason']}")

        return ValidationResult(valid=valid, errors=errors, warnings=warnings)

    def _validate_with_jsonschema(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Validate using jsonschema library."""
        errors: List[Dict[str, Any]] = []

        if self._json_validator is None:  # type: ignore
            return errors

        for error in self._json_validator.iter_errors(response):  # type: ignore
            field = ".".join(str(p) for p in error.absolute_path) or "(root)"
            errors.append(
                {
                    "field": field,
                    "value": error.instance,
                    "reason": error.message,
                    "schema_path": list(error.schema_path),
                }
            )

        return errors

    def _validate_manually(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Manual validation fallback when jsonschema is not available."""
        errors = []
        required = self.schema.get("required", [])
        properties = self.schema.get("properties", {})

        # Check required fields
        for field in required:
            if field not in response:
                errors.append(
                    {
                        "field": field,
                        "value": None,
                        "reason": f"Required field '{field}' is missing",
                    }
                )

        # Type checks for present fields
        for field, value in response.items():
            if field not in properties:
                continue

            prop_schema = properties[field]
            expected_type = prop_schema.get("type")

            # Type validation
            if expected_type:
                if not self._check_type(value, expected_type):
                    errors.append(
                        {
                            "field": field,
                            "value": value,
                            "reason": f"Expected type '{expected_type}', got '{type(value).__name__}'",
                        }
                    )
                    continue

            # Minimum validation for numbers
            if expected_type in ("integer", "number"):
                minimum = prop_schema.get("minimum")
                if minimum is not None and value < minimum:
                    errors.append(
                        {
                            "field": field,
                            "value": value,
                            "reason": f"Value {value} is below minimum {minimum}",
                        }
                    )

            # MinLength for strings
            if expected_type == "string":
                min_length = prop_schema.get("minLength")
                if min_length is not None and len(value) < min_length:
                    errors.append(
                        {
                            "field": field,
                            "value": value,
                            "reason": f"String length {len(value)} is below minimum {min_length}",
                        }
                    )

            # Enum validation
            enum = prop_schema.get("enum")
            if enum is not None and value not in enum:
                errors.append(
                    {"field": field, "value": value, "reason": f"Value must be one of: {enum}"}
                )

        return errors

    def _check_type(self, value: Any, expected: str) -> bool:
        """Check if value matches expected JSON Schema type."""
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
            return True  # Unknown type, skip
        # Ensure expected_types is a tuple for isinstance
        if isinstance(expected_types, tuple):
            return isinstance(value, expected_types)
        return isinstance(value, (expected_types,))  # type: ignore

    def _validate_semantics(
        self, response: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Additional semantic validation beyond schema."""
        errors = []
        warnings = []

        # Timestamp format check
        timestamp = response.get("timestamp")
        if timestamp and isinstance(timestamp, str):
            try:
                datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                errors.append(
                    {
                        "field": "timestamp",
                        "value": timestamp,
                        "reason": "Invalid ISO8601 timestamp format",
                    }
                )

        # Tokens consistency check (only if both are integers)
        tokens_sent = response.get("tokens_sent", 0)
        tokens_saved = response.get("tokens_saved", 0)

        if isinstance(tokens_saved, int) and isinstance(tokens_sent, int):
            if tokens_saved > tokens_sent:
                warnings.append(
                    {
                        "field": "tokens_saved",
                        "value": tokens_saved,
                        "reason": f"tokens_saved ({tokens_saved}) exceeds tokens_sent ({tokens_sent})",
                    }
                )

        # Cost sanity check (only if cost is a number)
        cost = response.get("cost", 0)
        if isinstance(cost, (int, float)) and cost > 100:
            warnings.append(
                {"field": "cost", "value": cost, "reason": f"Unusually high cost: ${cost}"}
            )

        # Model name check (only if model is a string)
        model = response.get("model", "")
        if isinstance(model, str) and model:
            if not any(p in model.lower() for p in ["claude", "gpt", "gemini", "llama", "mistral"]):
                warnings.append(
                    {
                        "field": "model",
                        "value": model,
                        "reason": f"Unrecognized model family: {model}",
                    }
                )

        return errors, warnings


# Module-level singleton for convenience
_default_validator: Optional[ResponseValidator] = None


def get_validator() -> ResponseValidator:
    """Get the default validator instance."""
    global _default_validator
    if _default_validator is None:
        _default_validator = ResponseValidator()
    return _default_validator


def validate_response(response: Dict[str, Any], strict: bool = False) -> ValidationResult:
    """Validate a response using the default validator.

    Args:
        response: Response dictionary to validate
        strict: If True, treat warnings as errors

    Returns:
        ValidationResult
    """
    validator = get_validator()
    if strict and not validator.strict:
        validator = ResponseValidator(strict=True)
    return validator.validate(response)


def is_valid(response: Dict[str, Any]) -> bool:
    """Quick check if a response is valid.

    Args:
        response: Response dictionary to validate

    Returns:
        True if valid, False otherwise
    """
    return validate_response(response).valid
