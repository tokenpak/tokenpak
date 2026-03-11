"""
TokenPak Validation — Request + Response contract validation for the proxy pipeline.

Response validation:
    from tokenpak.validation import validate_response, is_valid, ResponseValidator

    result = validate_response(response_dict)
    if not result.valid:
        print(result.errors)

Request validation:
    from tokenpak.validation import validate_request, RequestValidator

    result = validate_request(body_bytes, provider="anthropic")
    if not result.valid:
        error_payload = result.to_error_response()  # 400-ready dict
"""

from .request_schema import (
    ANTHROPIC_MESSAGE_SCHEMA,
    GOOGLE_GENERATE_CONTENT_SCHEMA,
    OPENAI_CHAT_SCHEMA,
    OPENAI_RESPONSES_SCHEMA,
    get_request_schema,
)
from .request_validator import (
    RequestValidationResult,
    RequestValidator,
    get_request_validator,
    get_validation_mode,
    validate_request,
)
from .response_schema import RESPONSE_SCHEMA, get_schema
from .validator import (
    ResponseValidator,
    ValidationResult,
    get_validator,
    is_valid,
    validate_response,
)

__all__ = [
    # Response validation
    "RESPONSE_SCHEMA",
    "get_schema",
    "ResponseValidator",
    "ValidationResult",
    "validate_response",
    "is_valid",
    "get_validator",
    # Request schemas
    "ANTHROPIC_MESSAGE_SCHEMA",
    "OPENAI_CHAT_SCHEMA",
    "OPENAI_RESPONSES_SCHEMA",
    "GOOGLE_GENERATE_CONTENT_SCHEMA",
    "get_request_schema",
    # Request validation
    "RequestValidator",
    "RequestValidationResult",
    "validate_request",
    "get_request_validator",
    "get_validation_mode",
]
