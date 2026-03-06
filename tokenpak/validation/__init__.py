"""
TokenPak Validation — Response contract validation for the proxy pipeline.

Usage:
    from tokenpak.validation import validate_response, is_valid, ResponseValidator
    
    # Quick validation
    result = validate_response(response_dict)
    if not result.valid:
        print(result.errors)
    
    # Or just check validity
    if is_valid(response_dict):
        cache.store(response_dict)
    
    # Custom validator with strict mode
    validator = ResponseValidator(strict=True)
    result = validator.validate(response_dict)
"""

from .response_schema import RESPONSE_SCHEMA, get_schema
from .validator import (
    ResponseValidator,
    ValidationResult,
    validate_response,
    is_valid,
    get_validator,
)

__all__ = [
    "RESPONSE_SCHEMA",
    "get_schema",
    "ResponseValidator",
    "ValidationResult", 
    "validate_response",
    "is_valid",
    "get_validator",
]
