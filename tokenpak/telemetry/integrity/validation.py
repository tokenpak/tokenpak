"""
TokenPak Data Integrity & Validation

Validation layer, reconciliation system, and anomaly detection.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List


@dataclass
class ValidationError:
    """Validation failure details."""

    field: str
    error_code: str
    message: str
    value: Any = None


class EventValidator:
    """Validates telemetry events on ingestion."""

    # Known providers
    KNOWN_PROVIDERS = {"anthropic", "openai", "google", "cohere"}

    # Token field names
    TOKEN_FIELDS = {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "qmd_input_tokens",
        "qmd_output_tokens",
        "final_input_tokens",
        "final_output_tokens",
    }

    def __init__(self):
        self.errors: List[ValidationError] = []

    def validate_token_counts(self, event: Dict[str, Any]) -> bool:
        """Reject if any token count < 0."""
        for field in self.TOKEN_FIELDS:
            if field in event:
                value = event[field]
                if isinstance(value, (int, float)) and value < 0:
                    self.errors.append(
                        ValidationError(
                            field=field,
                            error_code="NEGATIVE_TOKENS",
                            message=f"{field} must be non-negative, got {value}",
                            value=value,
                        )
                    )
                    return False
        return True

    def validate_stage_progression(self, event: Dict[str, Any]) -> bool:
        """Validate: raw ≥ qmd ≥ tokenpak ≥ final."""
        stages = {
            "raw": event.get("raw_input_tokens", 0),
            "qmd": event.get("qmd_input_tokens", 0),
            "tokenpak": event.get("input_tokens", 0),
            "final": event.get("final_input_tokens", 0),
        }

        # Check progression: raw ≥ qmd ≥ tokenpak ≥ final
        if stages["raw"] < stages["qmd"]:
            self.errors.append(
                ValidationError(
                    field="stage_progression",
                    error_code="INVALID_STAGE_ORDER",
                    message=f"raw ({stages['raw']}) must be ≥ qmd ({stages['qmd']})",
                )
            )
            return False

        if stages["qmd"] < stages["tokenpak"]:
            self.errors.append(
                ValidationError(
                    field="stage_progression",
                    error_code="INVALID_STAGE_ORDER",
                    message=f"qmd ({stages['qmd']}) must be ≥ tokenpak ({stages['tokenpak']})",
                )
            )
            return False

        if stages["tokenpak"] < stages["final"]:
            self.errors.append(
                ValidationError(
                    field="stage_progression",
                    error_code="INVALID_STAGE_ORDER",
                    message=f"tokenpak ({stages['tokenpak']}) must be ≥ final ({stages['final']})",
                )
            )
            return False

        return True

    def validate_provider_model(self, event: Dict[str, Any]) -> bool:
        """Validate provider and model exist."""
        provider = event.get("provider", "").lower()
        model = event.get("model", "")

        if not provider:
            self.errors.append(
                ValidationError(
                    field="provider", error_code="MISSING_FIELD", message="provider is required"
                )
            )
            return False

        if provider not in self.KNOWN_PROVIDERS:
            self.errors.append(
                ValidationError(
                    field="provider",
                    error_code="UNKNOWN_PROVIDER",
                    message=f"Unknown provider: {provider}. Known: {self.KNOWN_PROVIDERS}",
                    value=provider,
                )
            )
            return False

        if not model:
            self.errors.append(
                ValidationError(
                    field="model", error_code="MISSING_FIELD", message="model is required"
                )
            )
            return False

        return True

    def validate_timestamp(self, event: Dict[str, Any]) -> bool:
        """Validate timestamp is reasonable."""
        timestamp_str = event.get("timestamp")

        if not timestamp_str:
            self.errors.append(
                ValidationError(
                    field="timestamp", error_code="MISSING_FIELD", message="timestamp is required"
                )
            )
            return False

        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            self.errors.append(
                ValidationError(
                    field="timestamp",
                    error_code="INVALID_FORMAT",
                    message=f"timestamp must be ISO 8601, got {timestamp_str}",
                    value=timestamp_str,
                )
            )
            return False

        now = datetime.utcnow()
        if timestamp.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=None)

        # Not in future
        if timestamp > now:
            self.errors.append(
                ValidationError(
                    field="timestamp",
                    error_code="FUTURE_TIMESTAMP",
                    message=f"timestamp cannot be in future: {timestamp_str}",
                    value=timestamp_str,
                )
            )
            return False

        # Not too old (> 1 year)
        one_year_ago = now - timedelta(days=365)
        if timestamp < one_year_ago:
            self.errors.append(
                ValidationError(
                    field="timestamp",
                    error_code="STALE_TIMESTAMP",
                    message=f"timestamp too old (>1 year): {timestamp_str}",
                    value=timestamp_str,
                )
            )
            return False

        return True

    def validate_required_fields(self, event: Dict[str, Any]) -> bool:
        """Validate required fields present."""
        required = {"trace_id", "timestamp", "provider", "model", "final_input_tokens"}

        for field in required:
            if field not in event or event[field] is None:
                self.errors.append(
                    ValidationError(
                        field=field,
                        error_code="MISSING_REQUIRED_FIELD",
                        message=f"{field} is required",
                    )
                )
                return False

        return True

    def validate(self, event: Dict[str, Any]) -> bool:
        """
        Run full validation suite.

        Returns:
            True if event is valid, False otherwise.
        """
        self.errors = []

        # Run all validators
        checks = [
            self.validate_required_fields,
            self.validate_token_counts,
            self.validate_stage_progression,
            self.validate_provider_model,
            self.validate_timestamp,
        ]

        for check in checks:
            if not check(event):
                # Stop on first error (or collect all?)
                # For now, collect all errors
                pass

        return len(self.errors) == 0

    def get_error_response(self) -> Dict[str, Any]:
        """Format validation errors for API response."""
        return {
            "valid": False,
            "error_count": len(self.errors),
            "errors": [
                {
                    "field": e.field,
                    "code": e.error_code,
                    "message": e.message,
                    "value": str(e.value) if e.value is not None else None,
                }
                for e in self.errors
            ],
        }
